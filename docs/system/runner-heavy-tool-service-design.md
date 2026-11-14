# 重工具服务化设计（2026-11-13）

> 状态：设计 v1.2（2026-11-13 评审修订 + 同日二次修订）。2026-11-13 与用户三轮讨论定稿方向：5 个重工具剥离 LLM 后放入独立「重工具容器」，轻容器经 HTTP 调用；轻容器 4 × 5 并发 = 20 执行体，通用池（池路由）代码 B1 一次性剥离见 §9。
> 评审修正（同日）：§3 fill_template 含 LLM 结构分析的核实错误及切分方案、§4.1 重试范围与路径校验、§5 文件介导提前 B1 与产物目录双轨、§6 生产 DB 连接放大 200（[生产发布备忘](../../deploy/生产发布备忘.md) 条目 5）。
> 二次修订（同日，用户重述定调）：§1 补入「入队判型过判」出发点（旅游咨询智能体带 OCR 全量判重实证）；§9 废弃两步走——通用槽代码未发布生产，B1 一次性剥离池路由全套。
> 关联：[Runner 容量与轻重负载分离](runner-capacity-and-load-separation-design.md)、[Excel 阻塞事故 §7.2 工具级子进程隔离方向](../incidents/excel-process-shared-runner-blocking-20261105.md)、[架构 §10](agent-application-architecture-design.md)。
> 本方案是事故报告 §7.2「工具级子进程隔离」的容器级实现：把重负载从执行面剥离到独立可回收的容器。

## 1. 背景与决策演变

agent2（8GiB 宿主机，与 agent3 同机，PG max_connections=80）试跑卡点：重任务占通用槽、并发执行体不足。用户目标：**有限内存下支撑 20 个左右并发执行体**。经 2026-11-13 三轮讨论：

**判型过判问题（2026-11-13 用户重述定调，本方案的直接出发点）**：现行入队判型按「智能体装配白名单 ∩ heavy 清单」判定（`load_class.py` `classify_profile`）——只要智能体携带任一重工具，其**全部任务**入队即判 heavy。实证：旅游咨询智能体带 paddleocr_doc_parsing（OCR）工具，整个智能体的会话全量判重、占用通用槽容量，实际多数轮次并未执行到 OCR，造成通用槽资源浪费、轻池吞吐下降（agent2 实测 4/7 任务判重、重池排队 7 分钟）。**本方案的本质修正：判定下沉到工具执行层**——智能体不再判重，LLM 执行到重工具那一刻经代理远程派发重容器（§4.2），判定精确到工具调用。

1. **方案一（否决）**：全轻槽 + 重任务「执行 500ms 强制让位」。两个结构性障碍：
   - 协作式让位要求工具代码主动检查让位点，openpyxl/PyMuPDF/PaddleOCR 是库级全量调用（一次进一次出），无 chunk 边界暴露，Python 线程也无法从外部抢占；
   - 与看门狗正面冲突：重工具 CPU 段（C 扩展持 GIL 数秒）会让事件循环停跳，触发看门狗误判 → 重启容器 → 连带杀掉同容器全部并发执行体。
2. **方案二（过渡，已给出配置）**：3 轻 × 5 并发 + 1 通用 × 1 = 16 执行体，重病理隔离保留，代价仅 ~130MiB + 2~3 连接。
3. **方案三（本设计，用户决策）**：重工具服务化——重工具剥离 LLM 后放入独立容器，轻容器 HTTP 调用（评审核实修正：image_render v1 留轻容器不远程，实际远程 4 项，见 §3）。轻容器层面不再有「重任务」（heavy 任务的 20 分钟里只有几分钟 CPU 密集，其余在等 LLM），通用池失去存在意义，拓扑回到 4 轻容器 × 5 并发 = 20 执行体。

用户补充的关键洞察：**LLM 调用是网络等待，不属于重工具**。剥离后重工具服务成为纯计算服务，无 LLM gateway、无 DB 依赖、无凭证，上下文与计费两个风险点消失（§7）。

## 2. 方案总览

```
轻容器 ×4（AGENT_RUNNER_SLOT_CONCURRENCY=5，cgroup 1.5g，cpu.max 50%）
  └─ LLM / 第三方接口 / 渠道任务全本地执行（等待型，5 并发宽裕）
  └─ 命中重工具清单（B1 经 RemoteHeavyTool 代理远程 4 项；image_render v1 留本地，§3）→ HTTP → 重工具服务
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

**overcommit 说明与水位监控（评审补充）**：cgroup 上限总和 4×1.5g + 2g = 8g 已等于 agent2 宿主物理内存（同机还有 agent3 容器与 PG/Redis），本质是 overcommit、靠「重任务脉冲式到达 + cgroup OOM 兜底」成立——而 OOM 兜底在这个拓扑里代价是杀掉单进程 worker 的全部 5 个并发执行体再走租约恢复，不是轻量自愈。故 B1 起接入宿主内存水位监控线（85% 告警，纳入每周巡检），验收项见 §10。

### 2.2 额外收益

1. **PaddleOCR 模型权重常驻一份**服务全部轻容器——现状每个通用槽各自加载，服务化后省 N-1 份。
2. **新重工具门禁变硬**：从「登记清单 + 判型」变为物理约束——不接入重工具服务，轻容器就没有本地实现可跑。
3. **安全面缩小**：重容器不含 API key / DB 凭证 / 租户模型配置（敏感信息不进计算容器）。
4. **爆炸半径改善**：重容器挂 → 4~5 个工具中性失败，轻容器其余任务不受影响；现状重病理 OOM 会带走整个通用槽执行体。

## 3. LLM 剥离：5 个重工具的切分（2026-11-13 代码核实）

| 工具 | LLM 调用点核实结果 | 切分方案 | 阶段 |
|---|---|---|---|
| excel_process | `ExcelRouter.route`（excel_router.py:213）：意图路由微判定（规则路由优先，LLM 兜底），返回 `{task, params}`。9 个 handler（read/to_md/export/modify/format/fill_template/list_templates/merge/convert）中 **8 个为纯 openpyxl 计算；fill_template 例外**——经 `fill_with_sample`（excel_process_tool.py:761）内部含阻塞式 LLM 结构分析（`excel_template_ai._default_llm` 直连 `/chat/completions`，依赖 API key 与 runner 观测链路计量），**评审核实修正：并非纯 openpyxl** | **Router 留本地，handler 远程；fill_template 拆两段**：结构分析段留轻容器（LLM），openpyxl 渲染段远程——与 Router 切分同构，模板文件在共享卷上两侧均可读。回退方案：拆分成本过高时 fill_template 整体留轻容器（病理内存另行实测评估）。另注意 `excel_extract` 的 M3 抽取 LLM 位于 excel-to-template 技能管线（skill_execute 内执行），不属 excel_process handler，本方案不触碰 | B1 |
| paddleocr_doc_parsing | **零 LLM**（ocr_tool.py 无 gateway/chat 调用） | 整体远程 | B1 |
| upload_data_file | 工具路径上无 gateway 调用（extract_schema 为规则/pandas 解析） | **解析远程，注册留本地**——解析纯计算；DB 注册（元数据写入）是轻操作，留轻容器 | B1 |
| analyze_data | `AnalysisAgent.run()` 是 LLM chat_with_tools 循环（analysis_agent.py:458）；循环内派发 DataAnalyzer 的 pandas 方法；**重内存来源是 `_store(var_name, df)` 驻留进程内存的 DataFrame 状态本身**，不是 LLM 周围的计算 | **状态边界切开 + 文件介导**（§5）：DataAnalyzer 状态改写 parquet 到共享 workspace，数据操作远程无状态化；LLM 循环原样留本地。**文件介导去内存驻留（§5.1~5.2）提前至 B1 与服务化同批落地**（纯代码改动与服务化解耦，消除 B1 观察窗内轻容器 1.5g/5 并发下的 DataFrame 驻留 OOM 风险——单进程 asyncio worker 被 cgroup OOM 一次损失 5 个并发执行体）；数据操作远程派发留 B2 | 去驻留 B1；远程派发 B2 |
| image_render | **没有 LLM 调用**——「AI 生图」走 `get_image_gen_service().render()`（DashScope 异步任务轮询，网络等待，计费内部闭环）；本地重活仅参考图解码（≤3 张，`_resolve_reference_images`） | **v1 整体留轻容器不远程**；配合 20MB 上传上限观察，大概率正式改判 light。真超预期再把参考图解析单独远程。**观察期保护备注**：留本地期间受 1.5g cgroup 约束，若实测超预期，OOM 将杀整个 worker 进程（5 执行体连带），故须保留「参考图解析段随时可远程化」的实施预案，20MB 上限落地后尽快取得实测内存数据 | 不进重容器 |

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
- **多副本重试（范围强制限定）**：副本数可按 `.env` 调整（§6），Docker 服务名 DNS round-robin 无健康感知（非 swarm 下 unhealthy 副本仍会被解析）。代理重试**仅限连接建立阶段失败**（DNS 解析失败 / connect 拒绝 / connect 超时），重试一次（重解析 DNS 即落到健康副本）；**已送达请求的读超时严禁重试**——modify 等原地写类操作双执行会损坏用户文件。配套不变量：远程操作一律写新产物文件、禁止原地改（modify handler 远程化时改为「复制后改、产物落新文件」，与 fill/export 产物命名带 uuid 的现状对齐）。
- **路径入参服务端校验**：重容器对 execute 入参中的所有文件路径做前缀校验，必须落在共享 storage 卷（`/app/storage/`）下，否则 400 拒绝——重容器挂全量租户存储卷，该 allowlist 是「权限判定留在轻容器」之外的廉价纵深防御，防止后续代理实现把用户可控路径透传。
- **超时按工具可覆盖**：`timeout_seconds` 缺省 600，paddleocr_doc_parsing 覆盖为 900（全页识别大文档可超 10 分钟，防截断合法任务）；代理 HTTP client 读超时必须等于该值。
- **busy 语义**：信号量满时不排队直接返回结构化 busy（`{"success": false, "error_code": "busy", "error": "重工具服务繁忙，请稍后重试"}`），代理透传该语义，**代理侧不做自动立即重试**（防重试烧 LLM 轮次积分），由 LLM 按 message 引导用户稍后再试，见 §8。
- **文件不走 HTTP 内容**：所有 runner 槽容器与重工具容器挂载同一 storage 卷（现状 `AGENT_RUNNER_RESOURCE_DIR=/app/storage/agent_runner`，workspace 命名空间 `workspaces/{hash}/skill_ws_*` 已是共享盘上的绝对路径），参数里的路径直接可读。重容器写产物也落同一 workspace，返回结果里只带路径与小体积摘要。
- **认证**：复用服务间凭据口径（`.env` 明文 `AGENT_RUNNER_WEB_SERVICE_TOKEN` 同款恒等比较），内网服务名互访。
- **审计**：重容器对每次 execute 落 tlog（主题「重工具服务」）与结构化日志（tool_call_id、耗时、峰值内存采样），供验收与排障。

### 4.2 RemoteHeavyTool 代理

- 代理类继承 `BaseTool`，`name` / `display_name` / `InputModel` / `description` 与本地版**完全一致**（schema 从工具类生成链路不变，LLM 无感知）；`catalog=True`、无参构造，装配层按 `heavy_tools` 白名单把远程 4 项的本地实例替换为代理（image_render 留本地不替换，§3）。
- 代理 `execute()`：装 `tool_execution_scope`（本地仍装上下文，保持日志/trace 口径）→ HTTP 调用 → 透传结果；失败按现行「中性反馈」口径返回（不红叉、错误细节进日志）。
- **本地实现保留但不在轻容器装配**（代码同仓同镜像，只是不注册），由按环境的模式开关控制装配：`HEAVY_TOOLS_MODE` 环境变量（`local` 默认 / `remote`），零代码回滚。该开关三重角色：agent2/生产设 `remote`；**agent3 开发环境设 `local` 不部署重容器**（§6）；全环境故障回退时切回 `local`。
- 可序列化核对：`ToolExecutionContext` 两个字段不进 payload——`llm_gateway`（远程段零消费）与 `knowledge_search_inventory`（B2 前核对 analyze_data 远程操作是否需要，预期不需要——表发现是本地 DB 查询）。**注意**：「远程段零 LLM」成立的前提是 §3 fill_template 拆分落地（结构分析段留轻容器）；`excel_template_ai._default_llm` 直连链路（不走 gateway）不得随渲染段进入重容器，否则破坏无凭证前提并产生计量/计费缺口（§7）。

### 4.3 取消与孤儿任务

- Runner 取消 = 杀执行进程，远程工具杀不到。MVP：重容器内存登记进行中 job（key = tool_call_id）+ cancel 端点（尽力而为，设置协作取消标志，C 扩展调用间检查）+ TTL 清扫（调用方消失后 10 分钟回收登记）。
- **同 tool_call_id 重复提交（恢复重放撞车）**：Runner 恢复重放会携带与原调用相同的 tool_call_id（checkpoint 内 tool call id 不变），重容器收到重复 id 时**先对旧登记 job 下发协作取消、登记表 upsert，再接新请求**；孤儿 job 与新请求并发写同一产物路径的竞态由 §4.1「远程操作一律写新产物文件」不变量消除。
- 调用方死亡 → 孤儿 job 跑完、结果丢弃；Runner 恢复重领后重新提交。与现状「checkpoint 不持久化工具中途状态、恢复即重跑」语义一致，无回归；AI 生图类花钱操作的重复执行代价与现有恢复语义相同。
- B1 重容器无状态，孤儿成本 = 白算一次；B2 文件介导后同样无状态（每次调用只读自己的输入文件）。

## 5. analyze_data 文件介导改造（去驻留 B1 / 远程派发 B2）

**问题定性**：`DataAnalyzer._store(var_name, df)` 把加载的大表驻留在进程内存，query/aggregate/merge/pivot 链式操作都在这份状态上——重内存的根源是状态驻留，只在 LLM 边界切开（循环留本地、计算远程）解决不了它：数据还在轻容器内存里。

**改造**（切口在状态边界，且不需要有状态服务）：

1. `_store` 改为把 DataFrame 写 parquet 到共享 workspace，变量名 → workspace 路径映射；`_resolve_source` 从 workspace 读。**现状核对**：`_store`（data_analyzer.py:308-314）目前已双写——内存 `_variables` 驻留 + CSV 落 `storage/tenants/{tid}/temp/`（受 tmp 7 天清理任务管辖），本项改造即移除内存驻留、中间变量改走 workspace parquet。
2. 远程调用无状态化：`(输入文件路径 + 操作参数) → (输出文件路径 + 小体积摘要/预览)`，每次调用内存只有当前一张表，用完释放。
3. `AnalysisAgent` 的 LLM 循环**原样留本地**（需要 gateway、需要看摘要决定下一步），仅 `_execute_tool` 对数据操作方法改为远程派发；表发现/元数据查询（`fetch_table_metadata_from_db`，DB 轻查询）留本地。
4. 连带改善：现状一次会话加载 5 张大表同时驻留内存；文件介导后同时只有 1 张在内存——对 analyze_data 本身也是内存优化。
5. **产物目录双轨口径**（评审补充）：中间变量 parquet 落 workspace（runner 生命周期管理，随 workspace 清理）；最终产物 CSV 维持 `storage/tenants/{tid}/temp/` 现状不变（`load_output` 校验与 7 天清理口径均不动）。禁止把两类产物混到同一目录造成清理归属歧义。
6. **生命周期核实结论**（评审核实，简化 §11 原开放问题）：`DataAnalyzer` 实例为工具调用级生命周期（smart_analysis_tool.py:125 每次 execute 新建，非进程常驻），var → 路径映射只需**单次 analyze_data 调用内**有效；跨轮复用现状已走产物文件 + `load_output` 校验，本改造不改变该边界。
7. **阶段拆分**（评审调整）：本节 1（去内存驻留）提前至 **B1** 与服务化同批落地——纯代码改动，消除 B1 观察窗内轻容器的 DataFrame 驻留 OOM 风险；2~3（远程派发）仍留 **B2**。

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
- **agent2 拓扑**：4 轻容器 × 5 并发 + 1 重工具容器。**角色机制整体剥离（2026-11-13 用户裁决，见 §9）**：无 `AGENT_RUNNER_ROLE`，compose 合并为单一 `runner-worker` 服务 ×4 副本，每容器并发统一走 config `slot_concurrency`——不再存在 light/general 之分，全部 worker 等价、全量领取。agent3 共享宿主机的内存余量需在部署前复测（现 6 容器 → 5 容器 + 重容器，空载略降）。
- **agent3 开发环境不部署重工具容器**（2026-11-13 用户裁决）：`HEAVY_TOOLS_MODE=local`，5 个重工具在轻容器本地执行（与现状行为一致，1GiB cgroup、病理样本偶发 OOM 爆炸半径 = 单槽，仅开发人员触发，可接受）。红利：agent3 成为重工具本身的开发调试环境（改 handler 无需先部署重容器）与全局故障回退环境（重服务异常时始终可本地跑重任务）。
- **生产拓扑**：沿用 20 执行体分档（12~16 轻容器 × 4~5 并发）+ 1~2 重工具容器（replicas 2 时信号量各自独立，天然负载分担）。**生产 DB 连接账（评审重算，用户裁决放大 max_connections 至 200）**：12~16 容器 × pool 7 = 84~112 + 监督 ~10 + 非 Runner ~30 ≈ 124~152 ≤ 200，连接预算充足无需下调 pool；放大操作见 [生产发布备忘](../../deploy/生产发布备忘.md) 条目 5——`docker-compose.postgres.yml` 的 `max_connections` 120 → 200 随发版窗口修改提交，属 PG 启动参数，**reload 无效必须重建容器（秒级中断，低峰执行）**。agent2 测试库维持 80：4×7 + 监督 ~10 + 非 Runner ~20 ≈ 58 ≤ 80 不变。
- 上传上限 50MB → 20MB 作为独立加固项同批落地（前后端同步：接口校验 + 前端提示；注意移动端 blob 下载 10MB 门槛与本上限无关联，不混改）。

## 7. 计费与安全核对

按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 自检结论：

1. **重容器零 LLM 调用**——前提是 §3 fill_template 拆分落地（结构分析段留轻容器）；`excel_template_ai._default_llm` 是**不走 gateway 的直连链路**，其计量依赖 runner 观测链路（`_observed_skill_llm_request` 回填 `_runner_receipt_id`），随渲染段进重容器会同时破坏无凭证前提与计量口径，**必须留在轻容器**。
2. **excel_router 留本地**：其 `record_background_llm_usage`（excel_router.py:222，条件 A）路径不变。
3. **image_render 留本地**：生图计费（`_record_billing` 按张计）路径不变。
4. **upload_data_file 注册留本地**：DB 写入与既有计费/审计落点不变。
5. 重容器不需要 DB 连接 → 不新增 `chat_records` 落库点，无双计面。
6. 上线前按 §3 做一次该入口的常规扫描（`grep` 重容器代码内的 gateway/embedding/sms 命中应为零），**并追加直连链路扫描**（`_default_llm` / `chat/completions` / `_observed_skill_llm_request` 命中应为零——评审核实 fill_with_sample 内含此链路，防其随渲染段混入），作为验收项。

安全：重容器无凭证（不注入 `API_KEYS`/`DATABASE_URL`）、仅内网服务名互访 + 服务 token 认证、入参中的租户身份字段仅用于日志归因不做权限判定（权限判定留在轻容器装配边界）。

## 8. 失败语义

| 故障 | 行为 |
|---|---|
| 重容器不可用 / 超时 | 代理返回中性失败（「处理未完成」口径），LLM 可引导用户重试；轻容器其余执行体不受影响 |
| 重容器 OOM（病理叠加） | cgroup 杀进程 → healthcheck 重启容器（无状态，秒级恢复）；进行中 job 全部失败，Runner 侧按工具失败处理 |
| 调用方（执行进程）取消/死亡 | cancel 端点尽力协作 + TTL 清扫；孤儿结果丢弃（§4.3） |
| 信号量排队超上限 | 返回结构化 busy（`error_code=busy`，message 带「服务繁忙、稍后重试」语义），代理透传、**不做自动立即重试**（防重试烧 LLM 轮次积分）；LLM 按 message 引导用户稍后再试。快败理由不变：排队等待 = 占用轻容器执行体 |
| 已送达请求读超时 | 代理**不重试**（§4.1 重试仅限连接建立阶段失败），按中性失败处理，由 LLM 决策后续 |

看门狗口径不变：轻容器内不再有长 CPU 段，事件循环停跳判定保持现状阈值，不因本方案放宽。

## 9. 池路由整体退役：通用槽代码一次性剥离（2026-11-13 用户裁决，废弃原两步走）

**裁决依据**：新的「轻槽 + 重工具容器」已完全替代原「轻槽 + 通用槽」设计——重负载不再需要任何本地 heavy 容量 worker；且通用槽相关代码**未发布生产环境**（仅 agent2 测试环境部署了 Phase 7 的 1 轻 ×4 + 3 通用），无兼容负担。故 B1 **一次性剥离池路由全套代码**，不做「保留判型作统计」的过渡期（原两步走方案废弃；判型机制本身要删，统计它没有行动价值——重工具调用率直接看 trace/tlog 即可）。

剥离清单（2026-11-13 代码盘点）：

| 层 | 项 | 处置 |
|---|---|---|
| 判型 | `load_class.py` `classify_profile`（入队判型）/ `state_uses_heavy`（运行时重判） | 删除 |
| 入队 | `manager.py:41` 判型调用与 `load_class` 写入 | 移除（写入停用后走 `repository.py:96` 兜底恒 light） |
| 领取 | 四处 role 过滤：`execution_repository.py:108`、`recovery_repository.py:50/153`、`timer_repository.py:43` | 删除过滤条件，全量领取 |
| 恢复 | `durable_control.py:155` 恢复期 `state_uses_heavy` 重判 | 删除 |
| 角色 | `AGENT_RUNNER_ROLE`（`worker.py:293`、`supervisor.py:371`）、`slot_capacity.py` `effective_slot_concurrency`（general 强制 1） | 删除；并发统一走 config `slot_concurrency`（`--slot-concurrency` 显式覆盖保留） |
| 配置 | `config.yaml` `agent_runner.heavy_skills` 清单 | 删除（判型退役后无消费方；interior-render 的重负载随 image_render 观察结论走 §3） |
| 配置 | `config.yaml` `agent_runner.heavy_tools` 清单 | **保留但语义切换**：从判型信号变为「远程派发白名单」——RemoteHeavyTool 代理注册依据（§4.2），名字不变避免配置漂移 |
| 部署 | `AGENT_RUNNER_LIGHT_SLOTS` / `AGENT_RUNNER_GENERAL_SLOTS` / `AGENT_RUNNER_ROLE` 环境变量；compose overlay `runner-worker-light` + `runner-worker-general` 双服务 | 合并为单一 `runner-worker` 服务（副本数 = 容器数）；`agent2_update.sh` 注释与 [agent-runner-deploy-checklist.md](../../deploy/agent-runner-deploy-checklist.md) 部署步骤同步 |
| 观测 | `system_health/collector.py:203-208` light/general slots 指标 | 改为 worker_slots + 重工具服务指标（副本/并发/busy/排队） |
| 观测 | `notifications/events.py:330` 池名映射（轻专用池/通用池）、`runner_alerts.py` `load_class` 字段 | 口径调整（不再区分池归属） |
| DB | `agent_runner` 表 `load_class` 列 | **保留不删**（避免 DB 变更、历史行可读） |

配套规范同步：`architecture.md`「添加工具/技能」的负载等级声明改为**接入重工具服务的接入清单声明**——新重工具必须实现远程 handler 并入 `heavy_tools` 白名单（代理注册清单），否则轻容器物理不可跑（§2.2 收益 2）。

回退点不变且更简单：`HEAVY_TOOLS_MODE` 切回 `local` 即全 worker 等价本地执行重工具（与 agent3 常态一致，§6），**不依赖通用槽复活**——池路由代码删除后回退形态依然成立。

## 10. 实施顺序与验收

| 阶段 | 内容 | 前置 |
|---|---|---|
| B0 | 落地方案二配置（3 轻 ×5 + 1 通用）验证 5 并发领取 + 看门狗——与重工具服务解耦，先行。**B1 紧随则可跳过**（B0 属现行已部署代码的过渡用法，与 §9 剥离不冲突） | Phase 7 试跑结论 |
| B1 | 重工具服务（excel handlers——fill_template 拆分后仅渲染段 + paddleocr + upload 解析）+ RemoteHeavyTool 代理 + **fill_with_sample 结构分析/渲染拆分** + **analyze_data 文件介导去内存驻留（§5.1）** + **通用槽池路由代码一次性剥离（§9 清单）** + 20MB 上传上限 + agent2 重配 4 容器 ×5（单一 worker 服务）+ 重容器 + 宿主内存水位告警线（§2.1） | 本设计评审通过 |
| B2 | analyze_data 数据操作远程派发（文件介导已在 B1 落地） | B1 稳定运行 |
| 收尾 | `architecture.md` 负载等级声明改接入清单声明 + `heavy_tools` 白名单语义文档化 | B1/B2 观察期满 |

验收矩阵（在容量设计 §5 之上追加）：

1. 5 个工具经代理调用的端到端结果与本地执行一致（含错误路径：参数非法、文件缺失、超时）
2. excel fill_template / paddleocr 全页识别在重容器内存峰值 ≤ cgroup 2g，轻容器 5 并发混合负载下轻容器峰值 ≤ 1.5g
3. 取消传播：runner 取消后 cancel 端点收到、协作标志生效、TTL 清扫验证
4. 重容器 kill -9 后 10 秒级恢复，进行中调用方收到中性失败且可重试
5. 计费扫描：重容器代码内 gateway/embedding/sms 命中为零（§7 第 6 条）
6. 认证：无 token 请求 401；跨服务调用带 token
7. 模式开关双向验证：agent3（长期 `local` 模式）重工具本地执行全链路回归——它同时是回退形态的常态化证据，`local`→`remote` 切换后 B1 四件套经代理返回结果一致（与第 1 条合并验证）
8. agent2 混合负载 20 执行体压测：排队 p95、DB 连接峰值 ≤ 58、看门狗零误杀
9. **池路由剥离回归**（§9）：判型/角色过滤删除后，四类领取路径（execution/recovery×2/timer）全量领取无滞队；`AGENT_RUNNER_ROLE` / `LIGHT_SLOTS` / `GENERAL_SLOTS` / `classify_profile` / `state_uses_heavy` 全仓 grep 残留为零；agent2 重配后 4 容器全部等价领取（含旅游咨询智能体会话——过判场景回归，其会话不再有任何池归属差异）
10. **重试不双执行**：modify 类 handler 连接建立阶段失败注入——代理重试一次后结果正确无叠加；已送达请求读超时注入——代理不重试
11. **fill_template 拆分端到端**：结构分析（本地 LLM）+ 渲染（远程 openpyxl）结果与现状单进程执行一致
12. **busy 语义链路**：信号量占满时调用方收到 busy 结构化响应，LLM 回复含「稍后重试」引导而非立即重试
13. **宿主内存水位**：agent2 水位 85% 告警线接入并触发一次演练告警
14. **tool_call_id 重放**：Runner 恢复重放同 id 时重容器先取消旧 job 再接新请求，产物路径无竞态（§4.3）

## 11. 开放问题

- ~~analyze_data 文件介导改造涉及 DataAnalyzer 状态语义变更（var_name → 路径），跨轮 checkpoint 中已存变量名的兼容处理~~ **评审收窄（2026-11-13 核实）**：DataAnalyzer 实例为工具调用级生命周期（每次 execute 新建），var → 路径映射只需单次调用内有效，跨轮兼容问题基本消除（§5.6）。B2 剩余待细化项：中间变量 parquet 与既有 tenant temp CSV 产物的目录/清理归属已在 §5.5 定口径，B2 设计时核对 `load_output` 校验对 parquet 的兼容。
- 重容器信号量并发终值（1 还是 2）按 agent2 实测校准：2 并发 × 病理 1GiB 贴 2g cgroup 上限，若实测互拖则降 1。
- image_render 改判 light 的最终裁决：待 20MB 上传上限落地后的实测内存数据。
- fill_with_sample 结构分析/渲染拆分的接口形态（结构分析产物 schema 如何传给远程渲染段）：B1 实施时细化，拆分成本过高则回退 §3 备选（fill_template 整体留轻容器）。
- `heavy_profiles`（容量设计 §10 草案）在池路由退役后失去意义，随 §9 一次性剥离一并废弃，不再实施。
- agent3 是否同步部署重工具容器（共用 agent2 的？独立部署？）：agent2 与 agent3 同宿主机可共用服务名；独立宿主机场景即 §6.1 跨机部署，待部署阶段决策。
- 远期跨机扩容（新购腾讯云同 VPC 服务器部署重工具容器）方向已登记 §6.1，实施前需先落地 NFS 共享挂载与 stale handle 防御回归，本期不实现。
