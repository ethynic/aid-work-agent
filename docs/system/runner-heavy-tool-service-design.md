# 重工具服务化设计（2026-11-13）

> 状态：设计 v1，待评审。2026-11-13 与用户三轮讨论定稿方向：5 个重工具剥离 LLM 后放入独立「重工具容器」，轻容器经 HTTP 调用；轻容器 4 × 5 并发 = 20 执行体，通用池（池路由）退役路径见 §9。
> 关联：[Runner 容量与轻重负载分离](runner-capacity-and-load-separation-design.md)、[Excel 阻塞事故 §7.2 工具级子进程隔离方向](../incidents/excel-process-shared-runner-blocking-20261105.md)、[架构 §10](agent-application-architecture-design.md)。
> 本方案是事故报告 §7.2「工具级子进程隔离」的容器级实现：把重负载从执行面剥离到独立可回收的容器。

## 1. 背景与决策演变

agent2（8GiB 宿主机，与 agent3 同机，PG max_connections=80）试跑卡点：重任务占通用槽、并发执行体不足。用户目标：**有限内存下支撑 20 个左右并发执行体**。经 2026-11-13 三轮讨论：

1. **方案一（否决）**：全轻槽 + 重任务「执行 500ms 强制让位」。两个结构性障碍：
   - 协作式让位要求工具代码主动检查让位点，openpyxl/PyMuPDF/PaddleOCR 是库级全量调用（一次进一次出），无 chunk 边界暴露，Python 线程也无法从外部抢占；
   - 与看门狗正面冲突：重工具 CPU 段（C 扩展持 GIL 数秒）会让事件循环停跳，触发看门狗误判 → 重启容器 → 连带杀掉同容器全部并发执行体。
2. **方案二（过渡，已给出配置）**：3 轻 × 5 并发 + 1 通用 × 1 = 16 执行体，重病理隔离保留，代价仅 ~130MiB + 2~3 连接。
3. **方案三（本设计，用户决策）**：重工具服务化——5 个重工具剥离 LLM 后放入独立容器，轻容器 HTTP 调用。轻容器层面不再有「重任务」（heavy 任务的 20 分钟里只有几分钟 CPU 密集，其余在等 LLM），通用池失去存在意义，拓扑回到 4 轻容器 × 5 并发 = 20 执行体。

用户补充的关键洞察：**LLM 调用是网络等待，不属于重工具**。剥离后重工具服务成为纯计算服务，无 LLM gateway、无 DB 依赖、无凭证，上下文与计费两个风险点消失（§7）。

## 2. 方案总览

```
轻容器 ×4（AGENT_RUNNER_SLOT_CONCURRENCY=5，cgroup 1.5g，cpu.max 50%）
  └─ LLM / 第三方接口 / 渠道任务全本地执行（等待型，5 并发宽裕）
  └─ 命中 5 个重工具（经 RemoteHeavyTool 代理）→ HTTP → 重工具服务
                        ↓
重工具容器 ×1（同镜像不同入口，cgroup 2g，内部信号量限 1~2 并发）
  └─ excel handlers / paddleocr / upload_data_file 解析 / analyze_data 数据操作
  └─ 挂载同一 storage 卷 → 按 workspace 绝对路径直接读写文件
  └─ 不含：LLM gateway、DB 连接、凭证、租户模型配置
```

### 2.1 资源账（agent2）

| 项 | 值 | 说明 |
|---|---|---|
| 执行体 | 4 × 5 = 20 | 达成用户目标；通用槽 3 个移除 |
| 轻容器 cgroup | 1.5g | 重病理移出后，5 × ~0.29GiB 忙时 ≈ 1.45GiB 贴边可用；1g 会 OOM |
| 重工具容器 cgroup | 2g | 2 个并发重 job × 1GiB 病理上限（11-05 类样本） |
| DB 连接 | 4×7 + 监督 ~10 + 非 Runner ~20 ≈ **58 ≤ 80** | 重容器零 DB 连接；执行进程 pool = 并发 5 + 2（必须大于并发数，防 checkpoint/心跳竞争串行化） |
| CPU | 重容器与轻容器分开配 `cpu.max` | 重 CPU 不再影响轻容器事件循环，看门狗冲突消失 |
| 同机总量 | 最坏 ~7.5GiB | 脉冲式并发下成立；持续满忙 20 执行体超出 8GiB 物理上限，属显式降级（OOM → 看门狗自愈）。生产 16GB 不复制测试环境口径 |

### 2.2 额外收益

1. **PaddleOCR 模型权重常驻一份**服务全部轻容器——现状每个通用槽各自加载，服务化后省 N-1 份。
2. **新重工具门禁变硬**：从「登记清单 + 判型」变为物理约束——不接入重工具服务，轻容器就没有本地实现可跑。
3. **安全面缩小**：重容器不含 API key / DB 凭证 / 租户模型配置（敏感信息不进计算容器）。
4. **爆炸半径改善**：重容器挂 → 4~5 个工具中性失败，轻容器其余任务不受影响；现状重病理 OOM 会带走整个通用槽执行体。

## 3. LLM 剥离：5 个重工具的切分（2026-11-13 代码核实）

| 工具 | LLM 调用点核实结果 | 切分方案 | 阶段 |
|---|---|---|---|
| excel_process | 仅 `ExcelRouter.route`（excel_router.py:213）：意图路由微判定（规则路由优先，LLM 兜底），返回 `{task, params}`；9 个 handler（read/to_md/export/modify/format/fill_template/list_templates/merge/convert）为纯 openpyxl 计算 | **Router 留本地，handler 远程**。切开点天然存在：route 结果的 params 即文件路径 + 参数，直接派发远程 | B1 |
| paddleocr_doc_parsing | **零 LLM**（ocr_tool.py 无 gateway/chat 调用） | 整体远程 | B1 |
| upload_data_file | 工具路径上无 gateway 调用（extract_schema 为规则/pandas 解析） | **解析远程，注册留本地**——解析纯计算；DB 注册（元数据写入）是轻操作，留轻容器 | B1 |
| analyze_data | `AnalysisAgent.run()` 是 LLM chat_with_tools 循环（analysis_agent.py:458）；循环内派发 DataAnalyzer 的 pandas 方法；**重内存来源是 `_store(var_name, df)` 驻留进程内存的 DataFrame 状态本身**，不是 LLM 周围的计算 | **状态边界切开 + 文件介导**（§5）：DataAnalyzer 状态改写 parquet 到共享 workspace，数据操作远程无状态化；LLM 循环原样留本地 | B2 |
| image_render | **没有 LLM 调用**——「AI 生图」走 `get_image_gen_service().render()`（DashScope 异步任务轮询，网络等待，计费内部闭环）；本地重活仅参考图解码（≤3 张，`_resolve_reference_images`） | **v1 整体留轻容器不远程**；配合 20MB 上传上限观察，大概率正式改判 light。真超预期再把参考图解析单独远程 | 不进重容器 |

依赖重技能的连带复核：`interior-render`（依赖 image_render）与 `paddleocr-doc-parsing`（依赖 ocr 工具）——前者随 image_render 留本地；后者的重负载在 paddleocr 工具远程化后消失，B1 上线后从 heavy_skills 清单移除（§9）。

## 4. 接口契约与代理

### 4.1 远程调用协议（B1：同步长调用，不建持久化队列）

```
POST /internal/heavy-tools/execute
{
  "tool": "excel_process",          # 工具注册 name
  "handler": "fill_template",       # 仅 excel_process 需要；其余工具为空
  "params": {...},                  # 工具 InputModel 参数（含 workspace 文件绝对路径）
  "context": {                      # ToolExecutionContext 可序列化子集
    "tenant_id", "user_id", "session_id", "channel",
    "subagent_id", "chat_record_id", "agent_execution_id", "tool_call_id",
    "profile_id", "instance_id", "skill_workdir"
  },
  "timeout_seconds": 600
}
→ 200 { "success": true, "result": {...} }    # 工具 execute 原始返回
→ 5xx { "success": false, "error": "..." }

POST /internal/heavy-tools/cancel   { "tool_call_id": "..." }   # 尽力而为
GET  /internal/heavy-tools/health
```

- **同步长调用 + 大超时**，重容器侧 FastAPI + 信号量（1~2）排队；工具本来就可跑分钟级，不引入异步 job 状态机——那是另一个迷你 Runner，MVP 不做，跑稳后按需评估。
- **多副本重试**：副本数可按 `.env` 调整（§6），Docker 服务名 DNS round-robin 无健康感知（非 swarm 下 unhealthy 副本仍会被解析），代理对连接失败**必须重试一次**（重解析 DNS 即落到健康副本）。
- **文件不走 HTTP 内容**：所有 runner 槽容器与重工具容器挂载同一 storage 卷（现状 `AGENT_RUNNER_RESOURCE_DIR=/app/storage/agent_runner`，workspace 命名空间 `workspaces/{hash}/skill_ws_*` 已是共享盘上的绝对路径），参数里的路径直接可读。重容器写产物也落同一 workspace，返回结果里只带路径与小体积摘要。
- **认证**：复用服务间凭据口径（`.env` 明文 `AGENT_RUNNER_WEB_SERVICE_TOKEN` 同款恒等比较），内网服务名互访。
- **审计**：重容器对每次 execute 落 tlog（主题「重工具服务」）与结构化日志（tool_call_id、耗时、峰值内存采样），供验收与排障。

### 4.2 RemoteHeavyTool 代理

- 代理类继承 `BaseTool`，`name` / `display_name` / `InputModel` / `description` 与本地版**完全一致**（schema 从工具类生成链路不变，LLM 无感知）；`catalog=True`、无参构造，装配层把 5 个本地实例替换为代理。
- 代理 `execute()`：装 `tool_execution_scope`（本地仍装上下文，保持日志/trace 口径）→ HTTP 调用 → 透传结果；失败按现行「中性反馈」口径返回（不红叉、错误细节进日志）。
- **本地实现保留但不在轻容器装配**（代码同仓同镜像，只是不注册），由按环境的模式开关控制装配：`HEAVY_TOOLS_MODE` 环境变量（`local` 默认 / `remote`），零代码回滚。该开关三重角色：agent2/生产设 `remote`；**agent3 开发环境设 `local` 不部署重容器**（§6）；全环境故障回退时切回 `local`。
- 可序列化核对：`ToolExecutionContext` 两个字段不进 payload——`llm_gateway`（5 个工具的远程部分均不消费，B1 核实为零依赖）与 `knowledge_search_inventory`（B2 前核对 analyze_data 远程操作是否需要，预期不需要——表发现是本地 DB 查询）。

### 4.3 取消与孤儿任务

- Runner 取消 = 杀执行进程，远程工具杀不到。MVP：重容器内存登记进行中 job（key = tool_call_id）+ cancel 端点（尽力而为，设置协作取消标志，C 扩展调用间检查）+ TTL 清扫（调用方消失后 10 分钟回收登记）。
- 调用方死亡 → 孤儿 job 跑完、结果丢弃；Runner 恢复重领后重新提交。与现状「checkpoint 不持久化工具中途状态、恢复即重跑」语义一致，无回归；AI 生图类花钱操作的重复执行代价与现有恢复语义相同。
- B1 重容器无状态，孤儿成本 = 白算一次；B2 文件介导后同样无状态（每次调用只读自己的输入文件）。

## 5. analyze_data 文件介导改造（B2）

**问题定性**：`DataAnalyzer._store(var_name, df)` 把加载的大表驻留在进程内存，query/aggregate/merge/pivot 链式操作都在这份状态上——重内存的根源是状态驻留，只在 LLM 边界切开（循环留本地、计算远程）解决不了它：数据还在轻容器内存里。

**改造**（切口在状态边界，且不需要有状态服务）：

1. `_store` 改为把 DataFrame 写 parquet 到共享 workspace，变量名 → workspace 路径映射；`_resolve_source` 从 workspace 读。
2. 远程调用无状态化：`(输入文件路径 + 操作参数) → (输出文件路径 + 小体积摘要/预览)`，每次调用内存只有当前一张表，用完释放。
3. `AnalysisAgent` 的 LLM 循环**原样留本地**（需要 gateway、需要看摘要决定下一步），仅 `_execute_tool` 对数据操作方法改为远程派发；表发现/元数据查询（`fetch_table_metadata_from_db`，DB 轻查询）留本地。
4. 连带改善：现状一次会话加载 5 张大表同时驻留内存；文件介导后同时只有 1 张在内存——对 analyze_data 本身也是内存优化。

## 6. 容器与部署

- **同镜像不同入口**：重工具容器复用现有镜像，uvicorn 入口跑重工具 FastAPI 服务，零代码分包。
- compose 新增服务 `heavy-tools`，挂载与 runner worker 相同的 storage 卷（读写）；`cgroup 2g`、`cpu.max`（建议 200%，即 2 核）、healthcheck 打 `/internal/heavy-tools/health`。
- **副本数与并发走 `.env` 旋钮**（复用 sim compose `replicas: ${AGENT_RUNNER_WORKER_SLOTS:-1}` 既有机制，遵循 deploy 脚本不 export 环境变量的既定裁决）：
  - `HEAVY_TOOLS_REPLICAS`（默认 1）+ `HEAVY_TOOLS_CONCURRENCY`（单容器信号量，默认 1），总并发 = 副本数 × 信号量；
  - 调副本数走 `docker compose up -d --scale heavy-tools=N`（只增减该服务实例，不动其他容器）；改 `CONCURRENCY` 才需重建重容器；
  - 资源线性放大：每副本 2g cgroup + 空载 ~90MiB。**agent2 固定 replicas=1**（8GiB 与 agent3 同机，多副本挤占轻容器内存余量）；生产起步 replicas=2。多副本负载均衡为 DNS round-robin 弱保证，代理重试要求见 §4.2。

### 6.1 远期扩容方向：重工具容器跨机部署到内网服务器（2026-11-13 用户提出，本期不实现）

未来扩容在腾讯云新购服务器（与现环境同一内网），重工具容器独立部署到新机，执行面与重计算面物理分离，重容器副本数不再挤占宿主机内存。无状态服务 + 文件路径交接的架构对跨机友好，但有三个跨机特有约束（均有项目前科）：

1. **同 VPC 同账号是硬前提**：不同腾讯云账号之间内网不通（生产迁移 129.211.65.243 时已确认，不做对等连接）。新购机必须落在目标环境同一账号同一 VPC，否则只能走公网（不可接受：workspace 文件走公网传输 + 5Mbps 公网出带宽约束）。
2. **跨机文件访问需共享存储**：接口契约按 workspace 绝对路径交接（§4.1），跨机后新机必须挂载同一 storage 卷——方向是 NFS 导出 runner storage 目录给新机（254 NAS 盘 / agent1 sim 共享挂载有先例）。分钟级重工具对 NFS I/O 延迟不敏感；但 **NFS stale handle 前科必须登记**（P0① 挂死链条的可见症状，OSError errno 16），跨机 NFS 下 workspace 写入/清理路径的 stale handle 防御沿用既有 P0① 整改成果，部署时重点回归。
3. **轻容器侧地址可配置**：服务发现从 compose 服务名 DNS 切换为内网地址，增加 `HEAVY_TOOLS_URL` 环境变量（默认 `http://heavy-tools:8093` 服务名，跨机时改 `http://{内网IP}:8093`）——沿用 `AGENT_RUNNER_API_URL` 的注入先例；代理重试（§4.2）在跨机形态下同样必须，覆盖单机不可达。

其余不变项：服务 token 恒等比较认证照旧（内网 + 安全组只放行 runner 容器网段）；PaddleOCR 模型权重在新机常驻一份；内网带宽千兆级，不涉及公网限速规范；多副本 DNS 负载均衡仅适用于同机 compose，跨机多实例时在 `HEAVY_TOOLS_URL` 层面配置多地址轮询（实现时定，MVP 可单地址）。
- **agent2 拓扑**：4 轻容器 × 5 并发（`AGENT_RUNNER_ROLE` 过滤保留或移除均可——重工具远程化后 heavy 入队判型不再改变容器行为，§9）+ 1 重工具容器。agent3 共享宿主机的内存余量需在部署前复测（现 6 容器 → 5 容器 + 重容器，空载略降）。
- **agent3 开发环境不部署重工具容器**（2026-11-13 用户裁决）：`HEAVY_TOOLS_MODE=local`，5 个重工具在轻容器本地执行（与现状行为一致，1GiB cgroup、病理样本偶发 OOM 爆炸半径 = 单槽，仅开发人员触发，可接受）。红利：agent3 成为重工具本身的开发调试环境（改 handler 无需先部署重容器）与全局故障回退环境（重服务异常时始终可本地跑重任务）。
- **生产拓扑**：沿用 20 执行体分档（12~16 轻容器 × 4~5 并发）+ 1~2 重工具容器（replicas 2 时信号量各自独立，天然负载分担）；PG max_connections 120 的结论不变且更宽松（执行体 DB 连接 = 容器数 × pool，与并发数解耦后可下调）。
- 上传上限 50MB → 20MB 作为独立加固项同批落地（前后端同步：接口校验 + 前端提示；注意移动端 blob 下载 10MB 门槛与本上限无关联，不混改）。

## 7. 计费与安全核对

按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 自检结论：

1. **重容器零 LLM 调用**（§3 核实表）→ 无 LLM/Embedding/ASR 计费事件跨越服务边界，通路③装饰性 no-op 问题不涉及。
2. **excel_router 留本地**：其 `record_background_llm_usage`（excel_router.py:222，条件 A）路径不变。
3. **image_render 留本地**：生图计费（`_record_billing` 按张计）路径不变。
4. **upload_data_file 注册留本地**：DB 写入与既有计费/审计落点不变。
5. 重容器不需要 DB 连接 → 不新增 `chat_records` 落库点，无双计面。
6. 上线前按 §3 做一次该入口的常规扫描（`grep` 重容器代码内的 gateway/embedding/sms 命中应为零），作为验收项。

安全：重容器无凭证（不注入 `API_KEYS`/`DATABASE_URL`）、仅内网服务名互访 + 服务 token 认证、入参中的租户身份字段仅用于日志归因不做权限判定（权限判定留在轻容器装配边界）。

## 8. 失败语义

| 故障 | 行为 |
|---|---|
| 重容器不可用 / 超时 | 代理返回中性失败（「处理未完成」口径），LLM 可引导用户重试；轻容器其余执行体不受影响 |
| 重容器 OOM（病理叠加） | cgroup 杀进程 → healthcheck 重启容器（无状态，秒级恢复）；进行中 job 全部失败，Runner 侧按工具失败处理 |
| 调用方（执行进程）取消/死亡 | cancel 端点尽力协作 + TTL 清扫；孤儿结果丢弃（§4.3） |
| 信号量排队超上限 | 直接返回失败（排队等待 = 占用轻容器执行体，宁可快败让 LLM 决策重试/换路径） |

看门狗口径不变：轻容器内不再有长 CPU 段，事件循环停跳判定保持现状阈值，不因本方案放宽。

## 9. 与池路由的关系：load_class 两步退役

1. **第一步（本方案上线）**：重工具服务化跑稳。`heavy_tools` / `heavy_skills` 清单与 `load_class` 入队判型、运行时重判**保留不动**——此时它们的作用弱化为统计与观测（重命中率、判型准确率），行为上重任务仍可能入队判 heavy 但没有专用池（通用槽已移除，claim SQL 的 heavy 过滤自然不命中任何 worker → 需同步把 light 角色过滤放开为全量领取，或全部 worker 置 general 角色，二选一在实施时定）。
2. **第二步（观察一至两周后）**：退役判型机制——`load_class` 列保留作统计维度，移除入队判型与重判逻辑；`heavy_skills` 清单收缩（paddleocr-doc-parsing 技能随 B1 移除）；`architecture.md`「添加工具/技能」的负载等级声明改为**接入重工具服务的接入清单声明**（新重工具必须实现远程 handler 并入 B1 白名单，否则轻容器物理不可跑）。
3. 回退点清晰：任一步异常，装配配置切回本地实例（§4.2）即可恢复现状拓扑，无数据迁移。

## 10. 实施顺序与验收

| 阶段 | 内容 | 前置 |
|---|---|---|
| B0 | 落地方案二配置（3 轻 ×5 + 1 通用）验证 5 并发领取 + 看门狗——与重工具服务解耦，先行 | Phase 7 试跑结论 |
| B1 | 重工具服务（excel handlers + paddleocr + upload 解析）+ RemoteHeavyTool 代理 + 20MB 上传上限 + agent2 重配 4 轻 ×5 + 重容器 | 本设计评审通过 |
| B2 | analyze_data 文件介导改造 + 数据操作远程化 | B1 稳定运行 |
| 退役 | load_class 判型机制退役（§9 第二步） | B1/B2 观察期满 |

验收矩阵（在容量设计 §5 之上追加）：

1. 5 个工具经代理调用的端到端结果与本地执行一致（含错误路径：参数非法、文件缺失、超时）
2. excel fill_template / paddleocr 全页识别在重容器内存峰值 ≤ cgroup 2g，轻容器 5 并发混合负载下轻容器峰值 ≤ 1.5g
3. 取消传播：runner 取消后 cancel 端点收到、协作标志生效、TTL 清扫验证
4. 重容器 kill -9 后 10 秒级恢复，进行中调用方收到中性失败且可重试
5. 计费扫描：重容器代码内 gateway/embedding/sms 命中为零（§7 第 6 条）
6. 认证：无 token 请求 401；跨服务调用带 token
7. 模式开关双向验证：agent3（长期 `local` 模式）重工具本地执行全链路回归——它同时是回退形态的常态化证据，`local`→`remote` 切换后 B1 四件套经代理返回结果一致（与第 1 条合并验证）
8. agent2 混合负载 20 执行体压测：排队 p95、DB 连接峰值 ≤ 58、看门狗零误杀

## 11. 开放问题

- analyze_data 文件介导改造涉及 DataAnalyzer 状态语义变更（var_name → 路径），跨轮 checkpoint 中已存变量名的兼容处理待 B2 设计细化。
- 重容器信号量并发终值（1 还是 2）按 agent2 实测校准：2 并发 × 病理 1GiB 贴 2g cgroup 上限，若实测互拖则降 1。
- image_render 改判 light 的最终裁决：待 20MB 上传上限落地后的实测内存数据。
- `heavy_profiles`（容量设计 §10 草案）在池路由退役后失去意义，随 §9 第二步一并废弃，不再实施。
- agent3 是否同步部署重工具容器（共用 agent2 的？独立部署？）：agent2 与 agent3 同宿主机可共用服务名；独立宿主机场景即 §6.1 跨机部署，待部署阶段决策。
- 远期跨机扩容（新购腾讯云同 VPC 服务器部署重工具容器）方向已登记 §6.1，实施前需先落地 NFS 共享挂载与 stale handle 防御回归，本期不实现。
