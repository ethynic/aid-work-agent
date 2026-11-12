# Runner 槽位容量与轻重负载分离开发计划

> 日期：2026-11-11。设计依据：[Runner 槽位容量与轻重负载分离方案](../system/runner-capacity-and-load-separation-design.md)（定稿 v2：纯容器拓扑 12 轻专用 + 8 通用 = 20 执行体 + 非对称双池 + PG 120）。本计划覆盖设计 §6 实施顺序 1~8；备选方案 B（§7）只登记触发条件不排期。
>
> 关联：[执行槽隔离计划](plan-runner-execution-slot-isolation-and-alerts.md)（P0①② 来源，Phase 4 待整改清单）、[Excel 读取性能计划](plan-excel-read-performance.md)（TPG80509，待部署，随 Phase 5 同窗上线）。

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | P0①② 整改（前置） | ✅ 已完成（c9b03484，2026-11-11） |
| Phase 1 | 工具/技能轻重一次性全量判定 + agent2 内存三档实测 | 🔧 部分完成（清单 1558a91a；agent2 内存三档实测待真机） |
| Phase 2 | load_class 迁移 + 判型/重判 + 领取角色过滤 | ✅ 已完成（db5c222c，2026-11-11） |
| Phase 3 | Compose 双服务改造 + agent2 全链路验证 | 🔧 部分完成（双池改造 595527d4；agent2 部署验证待真机） |
| Phase 4 | 流程规范落地（负载等级强制声明） | ✅ 已完成（2026-11-12） |
| Phase 5 | 生产部署窗口（PG 120 + DB_POOL_MAX=3 + 12 轻/8 通用） | ⬜ 待部署 |
| Phase 6 | 上线后复测、校准与文档同步 | ⬜ 待上线后 |

依赖关系：
- Phase 0 与 Phase 1/2 可并行开发；**合并验收在部署前**（Phase 5 必须包含 Phase 0 回归）。
- Phase 1 的清单是 Phase 2 判型实现的输入；Phase 3 依赖 Phase 0~2 完成。
- Excel 修复（TPG80509）已部署生产（2026-11-11 核对 243 四容器），重负载典型内存回到数百 MiB 的前提已满足；Phase 1 三档实测可直接进行。

## Phase 0：P0①② 整改（前置，2026-11-11 用户确认必须）

来源：执行槽隔离计划 Phase 4 发现的 3 个 P0 中的前两个（P0③ upload_data_tool 假异步已在 master 修复，仅差部署）。

### P0① 取消/中断后槽位进程挂死

- 现象：2/2 复现，取消后槽位永久损失须人工重启容器；挂死进程持有 ~870MB 不释放且随取消累积。
- 根因链：收尾 cleanup 挂死 -> PID 仍在 -> healthcheck 只 pgrep 判活 -> 监督补租把 attempt 维持 active -> 两分钟收尾回收永不触发。
- 整改范围：**含 NFS 工作区 stale handle（OSError errno 16）**——它是挂死链条的可见症状（cancel -> 收尾 rmtree 遇 stale handle -> 挂死），并入本项不单列（设计 §3 补充）。
- 整改方向：cleanup 路径（rmtree 等）对 stale handle/异常容错或限时；监督进程对「取消已确认但执行进程长期不退出」的槽位强制回收（kill 进程组），不依赖 pgrep 类健康检查判定可回收。
- 验收：取消/中断回归场景不再挂死，槽位回收后可立即接新任务；NFS stale handle 用例覆盖。

### P0② 长工具期间心跳/监督 LockNotAvailable 误判 interrupted

- 现象：2/2 复现；长工具调用持有行锁期间，租约 heartbeat 与监督扫描报 `psycopg2.errors.LockNotAvailable`，运行中任务被误判 `interrupted`。
- 整改范围：**须涵盖监督进程补租扫描路径**（设计 §3 补充），扩容放大锁竞争面，不整改则 20 容器下放大。
- 验收：Excel 重负载长工具期间心跳与监督扫描回归通过，无误中断。

### 通用要求

- 均为服务端内部行为缺陷，旧客户端 wire/恢复/计费零改动；单测覆盖两条整改路径。

## Phase 1：轻重清单一次性全量判定 + agent2 内存三档实测

### 1.1 工具/技能轻重一次性全量判定（AI 执行，设计 §4.2）

- 扫描范围：`src/tools/` 各分类 + `src/skills/` 技能脚本全量。
- 判定标准：本地 CPU/内存密集计算（转换/渲染/大文件解析）为重；网络等待/小文本操作为轻。
- 产出：初始清单写入 `configs/config.yaml` 的 `agent_runner.heavy_tools` / `heavy_skills`。初始候选（预判，开发时全量核对）：excel_process、md_to_word、ppt_process 渲染、pdf_process 渲染、x_to_image、interior-render、视频生成、数据分析大文件路径（to_md / upload_data）。
- 产出物：判型清单核对表（工具/技能 -> 判型 -> 依据一列），登记入设计文档附录，供 Phase 6 校准与 Phase 4 门禁引用。

### 1.2 agent2 重负载内存三档实测

- 三档：空载 / 混合忙时 / 双重 Excel 满载；基线参考 agent3 实测（执行进程空载 ~87MiB、忙时 ~296MiB、监督 ~45MiB、重负载病理钉 1GiB cgroup）。
- 决策点：实测超预期则下调生产容器数（如 16 容器 = 10 轻 + 6 通用），回写设计 §4.5 后再进 Phase 3/5。

> **实现状态（2026-11-11）**：1.1 已完成（提交 1558a91a）——heavy_tools 9 项 / heavy_skills 8 项写入 config.yaml，全量核对表登记入设计 §4.2；1.2 为真机缺口，待 agent2 部署后实测。

## Phase 2：轻重分离核心实现

> **实现状态（2026-11-11，提交 db5c222c）**：agent_runners 增加 load_class 列（db_update.yaml 增量 + init-postgres.sql 同步，默认 'light' 含 CHECK）；入队判型 `load_class.py::classify_profile`（装配白名单 ∩ heavy 清单，main profile 判 light、异常 fail-open 到 light）；运行时重判 `state_uses_heavy` 信号 + `save_checkpoint` 同事务 `CASE WHEN` 提升；领取角色过滤 `AGENT_RUNNER_ROLE`（light|general，非法值归 general）覆盖 acquire/acquire_priority/timer/continuous/resume 五处扫描，light 角色过滤条件 `(%s <> 'light' OR r.load_class='light')`；`load_class` 不进 public_runner wire 投影。新增 test_load_class.py 25 用例，agent_runner 目录 458 passed（8 个失败为既有 apscheduler 环境缺口）。

### 2.1 数据迁移

- `agent_runners` 新增 `load_class` 列（TEXT，`'light'` | `'heavy'`，默认 `'light'`）。
- 双文件同步：`deploy/db_update.yaml` 新增幂等块（datetime 取当前时刻、严格递增）+ `deploy/init-postgres.sql` 全量 DDL。
- `load_class` 为服务端内部字段，不进 Device API / CLI wire 契约（Phase 3 验收确认项）。

### 2.2 入队判型

- runner 入队时：子智能体装配的工具/技能集 ∩ heavy 清单非空 -> `load_class='heavy'`，否则默认 `'light'`。
- 实现前提核对（设计 §4.2 已列）：入队路径须可取 subagent/profile 标识与技能 `requires_tools` 装配结果；开发首日先核实，不可取则在本阶段补通路，不得跳过判型直接入队。

### 2.3 运行时重判

- 执行中调用 heavy 工具/技能 -> 该次 `save_checkpoint`（`src/services/agent_runner/execution_repository.py:185`）事务内同步更新 `load_class='heavy'`；当下执行继续，中断恢复/下次领取按重处理。
- 覆盖两类场景：轻画像会话触发重工具；技能脚本重负载。

### 2.4 领取角色过滤

- worker 进程读环境变量 `AGENT_RUNNER_ROLE`（`light` | `general`，缺省 `general`）。
- claim SQL 改造点：`execution_repository.py:88` `acquire()` 的 queued 分支与 `acquire_priority()`，light 角色追加 `AND load_class='light'`；沿用 `FOR UPDATE SKIP LOCKED`，多容器重复领取防护不变。
- **零改动承诺**：监督进程、slot_state、两分钟收尾回收、租约/恢复/计费/Trace 主路径不动（相对备选 B 最大的改造量优势，验收中核对 diff 范围）。

### 2.5 可观测性

- `load_class` 入 Trace/监控/告警维度（设计 §5.3 第 3 条），支撑轻池排队/通用池空转两个校准信号的观察。
- **实现状态（2026-11-12，提交 10c6ac28）**：① Trace 维度——worker 创建 TraceCollector 时写 `trace.metadata['load_class']`（随 obs_traces 落库）；② 告警维度——长任务告警参数模型加 `load_class` 字段、外发文本渲染「负载池：轻专用池/通用池」标签、告警详情视图透出（same_attempt 条件下）；③ 池校准信号观测口径 = 领取/入队 tlog（`log/temp/runner轻重分离.log`）的 load_class + Trace metadata 过滤，未新增独立监控端点（agent_runner_alerts 表不加列，零迁移）。

### 2.6 测试

- 单测：入队判型（清单命中/未命中/空装配默认 light）；运行时重判（同事务更新、恢复后按 heavy 领取）；claim 过滤（light 角色不领 heavy、general 无过滤、heavy 误判 light 时可在轻槽跑完不崩溃）。
- 集成：恢复/计费/租约语义回归（重判与领取改造不破坏 finalize、两分钟回收、续租）。

## Phase 3：Compose 双服务与 agent2 全链路验证

> **实现状态（2026-11-11，提交 595527d4）**：3.1 已完成——`runner-worker` 拆为 `runner-worker-light`（默认 replicas 12，AGENT_RUNNER_ROLE=light）+ `runner-worker-general`（默认 replicas 8），共享基座收敛到顶层 anchor `x-runner-worker`；cgroup 统一 1GiB（mem_limit 默认 1g）；每副本 `DB_POOL_MAX` 默认 3（`AGENT_RUNNER_DB_POOL_MAX` 可覆盖）；PG max_connections 80->120 同步入 `deploy/docker-compose.postgres.yml`（生产窗口随 compose 重建生效）。agent2 经 .env 覆盖 4 轻 + 2 通用。3.2/3.3 为真机缺口，待 agent2 部署后执行。

### 3.1 Compose 改造

- 拆两个服务：`runner-worker-light`（replicas 12，env `AGENT_RUNNER_ROLE=light`）+ `runner-worker-general`（replicas 8，缺省角色），复用 Phase 2 已验证的 `deploy.replicas` 参数化机制（`deploy/docker-compose.agent-runner.sim.yml:108` 模式）。
- cgroup 初期统一 1GiB/容器（分离只保容量不省内存）；轻容器收窄须与误判 OOM 风险联动评估，判型准确率有数据后另行决策（设计 §4.4）。

### 3.2 agent2 部署（4 轻 + 2 通用，生产 30% 预设）

- agent2/agent3 同宿主机（8GiB）：6 容器空载 ~0.8GiB 可承受；连接 6×(3+2) + 非 Runner ~20 ≈ 50 ≤ 80，**无需扩 PG**。
- 每容器 `DB_POOL_MAX=3` 在 agent2 先行配置，为生产连接预算口径提前验证。
- 池比例观察信号：轻任务排队时间（轻池不够用）vs 通用池空转率（重任务不足）。用户直觉 2 轻 + 3 通用可能更贴合测试环境流量，跑一段时间后按信号校准（设计 §8 开放问题）。

### 3.3 验收矩阵（设计 §5，在执行槽隔离计划验收矩阵之上追加）

1. P0① 挂死回归：取消后槽位进程正确回收、无人工重启（含 NFS stale handle 用例）。
2. P0② 心跳 LockNotAvailable 回归（含监督补租扫描路径）。
3. 轻重分离专项：
   - 通用池满载重任务时，轻任务全部由轻专用池领取并完成，排队秒级；
   - 重任务不落轻专用执行体（claim SQL 过滤验证 + 监控可见性）；
   - 运行时重判：轻画像会话调用重工具后中断，恢复领取按 heavy；
   - 误判降级：heavy 误判 light -> 在轻槽跑完不崩溃，期间其他轻任务仍可被领取。
4. DB 连接峰值实测（agent2 目标 ≤50；为生产 ≤100 提供口径）。
5. 内存三档实测 + cgroup 拦截验证。
6. 旧客户端（已交付 Runtime/CLI）组合回归：wire 契约、恢复、计费不变；确认 `load_class` 不出现在 Device API/CLI wire。
7. agent2 混合负载排队 p95 ≤ 30s（沿用既有目标）。

## Phase 4：流程规范落地

> **实现状态（2026-11-12，已完成）**：architecture.md「添加工具」「添加技能」节均已补负载等级强制声明（判型标准、登记位置 config.yaml heavy_tools/heavy_skills、误判代价不对称说明）；skill_integration.md §6 首次移植清单补负载等级声明核对项。

- `.claude/rules/architecture.md`「添加工具」「添加技能」节补充**负载等级强制声明**：新增工具/技能必须声明 light/heavy，作为流程门禁（设计 §4.2 第 2 条、§6 第 5 步）。
- 与 [skill_integration.md](../../.claude/rules/skill_integration.md) 衔接：外部技能包整合核对清单中补负载等级声明核对项。

## Phase 5：生产部署窗口

**同批上线**：本方案 + P0③（upload_data_tool 假异步已在 master，部署即生效）。Excel 修复（TPG80509）已先行部署生产（2026-11-11 核对 243 四容器均含修复代码）。

1. PG `max_connections` 80 -> 120（PG 多耗 ~300MiB）。
2. 每 worker 容器 `DB_POOL_MAX=3`（默认 15 的理论上限 20×17=340 是隐患）；峰值 = 20×(3+2) ≈ 100 ≤ 120，非 Runner ~20 已含。
3. 12 轻专用 + 8 通用部署。
4. 部署前合并验收：Phase 0 两项回归 + Phase 3 矩阵在生产口径重跑关键项（DB 连接峰值 ≤100、内存三档、cgroup 拦截、旧客户端组合）。
5. 部署顺序注意：`db_update.yaml` 增量块须先于/随代码发布应用（参考 runner 更新迁移竞态先例，确认迁移水位后启动 worker）。

## Phase 6：上线后复测、校准与文档同步

1. `execution_started_at` 复测真实并发与判型准确率（设计 §2 口径限制：14 天窗口可能未含大促/月底峰值，多槽上线后复测一轮）。
2. 池比例校准：12/8 与判型清单按实测调整；轻容器 cgroup 收窄时机评估（须与误判 OOM 风险联动）。
3. 备选 B 触发评估：若实测证明需 32+ 执行体（届时 32 容器纯拓扑的监督连接 ~160 与内存 ~4.2GiB 空载超过 B 的改造成本），启动 B（8 容器 × 4 进程，先完成 B 的监督多进程生命周期改造与验证）。
4. 文档同步：架构设计 §10 补充角色分离说明（「Worker 内根 Runner 并发数固定为 1」表述维持不变）；执行槽隔离计划追加本计划 Phase 引用；设计文档状态更新。
5. `docs/ideas.md` 条目状态与一句话说明更新。

## 风险与开放问题（承设计 §8）

| 项 | 处置 |
|---|---|
| 池比例（12/8）与判型清单准确率 | agent2 实测校准（Phase 3.2 / Phase 6.2） |
| agent2 比例（4轻+2通用 vs 直觉 2轻+3通用） | 按轻池排队/通用池空转两信号校准 |
| 轻容器 cgroup 收窄 | 判型误判率有数据后评估，与 OOM 风险联动 |
| 执行进程数动态伸缩 | 按容器重启生效，暂不做 |
| waiting 存量 15 条 runner 清理 | 与本方案无关，顺带登记待清理 |
| 4 逻辑核对 5 个 CPU 密集重任务超订 | 与拓扑无关，重任务排队可容忍（20 分钟 vs 40 分钟感知差异小） |

## 资源预算摘要（设计 §2.1/§4.5）

- 生产 20 容器稳态（5 重 + 15 轻）：执行面 7.8~10.3GiB + 同机 ~5.2GiB = 13~15.5GiB，上限口径贴边；TPG80509 已部署生产（2026-11-11 核对），重负载按修复后典型值预估。
- 连接：峰值 ~100（PG 120 上限）；agent2 ~50（PG 80 不扩）。
- 监督开销相对 B 方案多 ~0.7GiB，换来零新架构代码与最细爆炸半径（1 执行体 = 5%）。
