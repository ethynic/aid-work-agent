# 统一 Agent Run P0 开发执行计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 事实盘点、契约冻结、DDL 评审与测试骨架 | 📋 待开发 | — |
| Phase 1 | AgentApplication 抽取与 inline 单执行链 | 📋 待开发 | — |
| Phase 2 | Run/Command/Event 持久化、队列、等待与终态事务 | 📋 待开发 | — |
| Phase 3 | 独立 agent-runner、租约恢复、deadline 与运维能力 | 📋 待开发 | — |
| Phase 4 | Web submit/subscribe、前端恢复、灰度切换与旧链退场 | 📋 待开发 | — |
| Phase 5 | 持久通知、全局订阅与浏览器通知 | 📋 待开发 | — |
| Phase 6 | Channel Gateway、微信客服首接与全部生产渠道迁移 | 📋 待开发 | — |
| P0 收口 | 压测、故障演练、生产灰度、旧路径删除与验收归档 | 📋 待开发 | — |

> 状态：待开发。本文只负责任务拆解、依赖、交付批次、测试门禁和上线顺序；业务语义与协议以
> [P0 设计基线](./plan-unified-agent-run-lifecycle.md)为唯一权威。两处冲突时先修订设计基线，再修改实现，禁止在代码或本文中另造语义。
>
> 优先级：P0。微信客服 `wecom_kf` 与 Phase 0 盘点出的全部已售、生产启用渠道完成迁移，才算 P0 完成。
>
> 日期：2026-09-22

## 1. 文档分工与技术实现文档结论

现有 P0 设计基线已经明确了对象、状态机、命令、事件、数据责任、目录结构、执行进程、超时、灰度、回滚和验收，不再新建一份覆盖全系统的“纯技术实现文档”。再复制一遍会造成状态、字段和切换规则在两处漂移。

三类文档的职责固定如下：

| 文档 | 负责什么 | 不负责什么 |
|------|----------|------------|
| P0 设计基线 | 为什么改、规范语义、所有权、契约、状态机、数据与切换规则 | 开发排期、领取任务、逐批进度 |
| 本开发执行计划 | 做事顺序、代码落点、批次边界、测试门禁、灰度和完成定义 | 重新定义状态、协议或数据库语义 |
| 局部实现附件 | 仅在需要时记录经代码/实测才能冻结的窄主题 | 复制整份架构设计 |

Phase 0 只有在以下情况出现时，才补局部附件或 ADR：

- DDL 字段、索引或数据回填方案超过主设计可清楚表达的范围；
- 某个渠道的平台顺序、咨询 epoch、重试回执需要独立协议表；
- Web 或渠道生产切换需要操作级 runbook；
- agent-runner/channel-worker 的容量压测形成独立基线；
- `/api/chat` 调用方审计产生退场或兼容迁移清单。

这些附件必须反向链接 P0 设计基线和本文，只能细化，不得改变已冻结语义。

## 2. 开发原则与总顺序

### 2.1 不可破坏的实现边界

1. `src/core/agent.py` 继续负责模型—工具循环；新代码把生命周期、传输、持久化和 UI 责任从它周围剥离，不复制 Agent loop。
2. 新平台通用代码进入 `src/services/agent_run/`；现有渠道协议代码保留在 `src/channels/<channel>/`，通过 Connector 端口接入。
3. `AgentApplication` 是 submit、补充、澄清、等待、取消、结果接纳和查询的唯一应用层入口；HTTP、渠道和未来桌面只做认证与 envelope 转换。
4. PostgreSQL 是 Run、命令、事件、通知和渠道投递事实权威；Redis 只做唤醒和加速。
5. Web 与渠道物理消息表继续分开：`chat_*` 只服务 Web，`channel_*` 只服务渠道；统一的是接口和生命周期，不是强行合表。
6. 所有入库、查询和控制命令先做可信 tenant/user/session 归属校验；客户端提交的 tenant 或授权声明不可信。
7. Browser 首期 fail-closed，不得为兼容其进程内 owner/Hub 破坏新架构。
8. 不使用外键、触发器、存储过程或复杂视图；跨表完整性由稳定引用、唯一索引和 Python 事务校验保证。

### 2.2 依赖关系

```text
Phase 0 契约与事实盘点
        ↓
Phase 1 单进程 AgentApplication
        ↓
Phase 2 持久状态/事件/终态事务
        ├────────→ Phase 3 agent-runner
        ├────────→ Phase 5 通知基础
        └────────→ Phase 6 渠道基础设施
                         ↓
Phase 3 + Phase 4 Web 完成生产切换门槛
                         ↓
                 微信客服生产切换
                         ↓
                  其余生产渠道迁移
                         ↓
                      P0 收口
```

Phase 6 的数据库、Gateway 和 worker 基建在 Phase 2 schema 稳定后即可与 Phase 3～5 并行；微信客服真正切到 `run_service` 依赖 Phase 4 的统一 Run 生产链路，不依赖 Phase 5 浏览器通知。

### 2.3 高风险开发流程

本改造同时触及并发、鉴权/租户隔离、数据库迁移、计费、启动链路和核心执行，全部代码批次按高风险流程执行：

- 开发者完成实现和定向自测；
- 独立测试者根据行为重新选择测试，不只复述开发者结果；
- 独立 Code Review 检查并发、事务、权限、异步资源、配置和测试有效性；
- P0/P1 问题修复后重跑相关测试并复核；
- 每批只更新本计划的真实进度，不在 `docs/ideas.md` 写开发流水账；
- 未获得用户明确授权，不提交或推送 Git。

## 3. Phase 0：开发前冻结包

目标是把所有会影响表结构、公共接口和切换安全的事实一次冻结。Phase 0 允许建立契约模型和测试骨架，但不改变生产执行路径。

### WP0.0 生产观测证据的责任与交付方式

开发者默认不持有、也不应自行索取生产写权限或长期生产凭据。B00 的生产证据采用“开发者定义只读查询包，运维/设计 owner 执行并交付脱敏结果”的方式：

1. 开发者先完成代码/配置侧盘点，并提交只读 evidence query pack，写清数据源、查询窗口、SQL/日志筛选、字段含义和期望输出；默认观察最近 90 天或系统实际可提供的最长窗口，必须记录实际起止时间与覆盖缺口。
2. 数据库类统计优先在有明确快照时间的生产仿真副本执行；仿真数据过旧、字段缺失或不能代表当前账号时，由运维在生产只读副本/受控只读会话执行同一查询。`sim.sh` 的存在不等于数据足够新，必须记录快照时间。
3. `/api/chat` access log、渠道回调/发送结果等不在数据库中的事实，由运维按查询包导出聚合日志结果；不把生产日志凭据交给开发代码。
4. 运维/设计 owner 对执行时间、数据范围和查询结果签注；开发者负责把脱敏聚合结论写入 B00 交付物。原始客户消息、附件、密钥、token、完整手机号/外部联系人标识不得进入仓库，必要的租户/账号明细保存在批准的安全位置，版本库只记录受控引用和结论。
5. 只有用户或运维明确授权且已有最小权限只读机制时，开发者才可直接查询生产；该授权不扩展为写权限，也不允许临时复制生产凭据到配置或文档。

责任分工：开发者拥有查询定义、代码事实和结论分析；运维/设计 owner 拥有生产查询执行、数据脱敏与覆盖证明。B00 可以先开展代码侧工作，但以下证据未交付前不得标记完成，也不得进入对应生产切换：browser 真实用量、生产渠道账号清单、`/api/chat` 调用量、Run/模型/工具耗时分布。

责任人与时限不再使用无归属的“等待运维”表述：本计划发起人/生产环境负责人是 accountable owner，也是开发者提交 query pack 的默认接收人；B00 启动记录必须填写实际执行人姓名、备份人姓名和安全回传位置。个人姓名不能由设计文档猜测，必须由 accountable owner 在 B00 kickoff 时确认。query pack 提交后默认 2 个工作日内回传结果或书面说明数据缺口；逾期即执行下述保守策略，不无限阻塞开发。仿真快照必须同时回传 `source_snapshot_at`、`restore_completed_at` 和覆盖的数据源；任一时间未知时不得把该快照当成“当前生产”证据。

若某项生产证据在计划窗口内仍不可获得，采用保守结论而不是猜测：相关工具保持 background deny；可能使用 browser 的租户保持 legacy；`/api/chat` 按“存在调用方”实现 durable compatibility adapter、不得 410；未核实渠道账号保持 legacy 且仍在 P0 清单；deadline 只允许内部小流量验证，不得扩大生产灰度。

### WP0.1 真实调用方与对象映射

负责人需从代码、配置和生产观测形成一份带证据的清单：

- Web：`POST /api/chat/stream`、已认证 `POST /api/chat`、匿名/演示入口、cancel、history、附件接纳、SSE 断连行为；
- 渠道：`wecom_kf`、企业微信应用、企业微信个人账号 RPA、钉钉、飞书以及 Phase 0 发现的其他生产账号；
- 旧 Desktop D1、Gateway、Runtime、CLI 的真实调用方与删除/迁移结论；
- browser 工具真实调用租户、次数、成功率、最近使用时间；
- `session_tasks`、`local_tool_invocations`、Trace、usage、`chat_records`、Artifact 的 Run 关联方式；
- 所有 server tool 的 effect、幂等性、可取消性、timeout、结果未知与恢复策略。

交付物：字段级映射表、生产渠道清单、工具准入矩阵、`/api/chat` 二选一决议、browser allowlist/替代方案。所有无法确认的工具默认不进入 background Run。

### WP0.2 公共契约与状态测试

在 `src/services/agent_run/` 建立不含基础设施副作用的模型与状态骨架：

- `commands.py`：Submit、AppendInput、ReplyToClarification、Cancel、Approval、ToolResult；
- `models.py`：typed `SessionRef`、RunSnapshot、RunResult、RunEvent、WaitDescriptor、error class；
- `states.py`：合法迁移、终态、内部 phase 到外部 status 映射；
- fake model、fake tool、memory repository、fake clock；
- repository contract suite 必须与实现无关：同一组状态、幂等、版本、队列和 finalization 场景从 B01 起对 memory repository 运行，B05 起以参数化 fixture/共享 contract mixin 对 PostgreSQL repository 原样复跑；PG 独有的事务隔离、行锁、唯一约束冲突和 `SKIP LOCKED` 另加集成测试，不能用 memory 绿替代；
- 对 command 幂等、版本 CAS、迟到澄清、append 终态竞态、取消请求与取消完成分离建立红灯测试。

三个澄清表面必须在测试里证明都转换到同一个 `ReplyToClarificationCommand`，不得分别实现状态推进。

### WP0.3 DDL 与事务评审

冻结最小表/扩列、索引、租户查询路径、保留期限和清理 owner。至少覆盖：

- Run、command、event、tenant Web route、legacy Web claim；
- `agent_run_commands.input_snapshot`（或 DDL 评审确认的等价 JSONB 字段）保存有序输入块和可信 artifact 快照：稳定 `artifact_id/storage_ref`、mime、size、digest、retention；不保存临时下载 URL、凭据或裸路径。P0 默认不为此另建全局 Artifact 表，除非现有产物存储无法提供稳定引用或所需保留期；
- notification/outbox；
- 渠道 receipt、batch、wait binding、processing claim、projector cursor、delivery outbox、reply budget 与 route epoch；
- ToolInvocation/ModelAttempt/Artifact 的稳定 Run 引用；
- finalization 如何复用现有 `ChatRecordDB.create` 的事务连接；
- `chat_sessions` 与 `channel_sessions` 两类 Session 锁锚点；
- 单 Run event seq 的并发分配和 ephemeral/durable 清理索引。

DDL 评审通过后，实施时同步维护：

- `deploy/init-postgres.sql`；
- `deploy/db_update.yaml`，每个逻辑批次使用唯一递增时间；
- 对应 Python 初始化链；
- `docs/system/database_system_table.md`；
- migration/upgrade 单元测试。

### WP0.4 deadline、容量与平台事实校准

- 用真实耗时分布校准 lifecycle 72h、active execution 30m、model attempt 5m、tool attempt 10m 的初值；样本必须覆盖不同 Agent/工具、单轮/多轮链、P50/P95/P99、最长合法样本和异常 hang。
- 特别判断合法多轮工具任务是否会超过 30 分钟 active budget；如需放宽，使用服务端分级 policy 与硬上限，不移除默认 deadline。
- 冻结 supervisor-bound 活性判断、15s 检查/60s lease 初值、reclaim 3 次与 5/30/120s+jitter 退避。
- 按渠道核实原始顺序字段、重试语义、msgid 冲突策略和 reorder grace；微信客服必须冻结可靠 consultation epoch 来源或版本化持久状态机。
- 压测 delta 200ms/2KB 初值、事件写入量、每 runner 内存/并发和数据库 claim 开销。

### WP0.5 前端、配置与运维清单

输出文件/组件级估时，至少点名：

- `frontend/web/composables/useAgent.ts` 的 submit/subscribe、断网重试、stable command_id 和 RunProjection；
- `ChatContainer.vue`、`ChatInput.vue` 的停止/补充/新任务三动作；
- 会话列表的 active/queued/blocked/completed unread 投影；
- 刷新后的 `list_session_runs` 恢复、`reset_required`、draining 和 cancel requested 展示；
- `src/config/settings.py` 与 `configs/config.yaml` 的 flag/default；
- `docker-compose.prod.yml` 中 `aid-agent-runner`、`aid-channel-worker`、healthcheck、优雅 drain 和副本配置；
- Web 与渠道 shadow/draining/run_service 的上线 SOP 和监控面板字段。

### Phase 0 退出门禁

- [ ] P0 设计基线中所有 Phase 0 待实测项都有结论或明确 deny；
- [ ] 公共 contract/state 测试骨架通过，且没有连接真实外部服务；
- [ ] DDL 经多租户、索引、升级/回滚和数据保留评审；
- [ ] `/api/chat`、browser、旧 D1、生产渠道清单均已拍板；
- [ ] 生产 evidence query pack 已由运维/设计 owner 执行并记录窗口、快照时间和覆盖缺口；无法取得的数据已按保守策略处理；
- [ ] deadline 覆盖 P95/P99 和合法长任务，不以猜测定值；
- [ ] Web/渠道/worker 上线与回滚负责人、指标和阈值已写入 SOP；
- [ ] 独立测试与独立 CR 完成，无未关闭 P0/P1 问题。

## 4. Phase 1：统一应用服务，仍保持 inline

目标是在不引入后台 worker 的情况下，让生产 Web 新路径能通过一个应用服务完成一次真实运行。此阶段禁止双执行和双主写。

### WP1.1 AgentApplication 与执行适配器

代码落点：

```text
src/services/agent_run/
  application.py
  commands.py
  models.py
  states.py
  executor.py
  conversation.py
  adapters/web.py
```

工作项：

- `AgentApplication` 只编排用例，不包含 SSE 文本、Vue 状态或渠道格式；
- executor 把现有 `Agent.process_message` 事件映射为规范事件/RunResult，不复制模型—工具循环；
- ConversationRepository 按 typed SessionRef 分派 Web/渠道双表，冻结消息配对和幂等键；
- 附件在接纳时解析为可信 artifact snapshot，不依赖 Redis 24h metadata；
- `trusted_context` 只由认证边界构造，服务端重算 tenant、user、授权和计费主体；
- browser 在新模式中返回 `CAPABILITY_NOT_MIGRATED` 或不暴露；
- 匿名入口迁到受限 `transient_inline`，不创建 durable Run、不执行写工具；
- 按 Phase 0 决议，把 `/api/chat` 做 deprecated/410 准备或 durable transient adapter。

### WP1.2 Web shadow 转换器与对账准备

- legacy 仍是唯一执行者和消息主写者；
- 本阶段只完成“legacy 事件→规范 Run 事件/快照”的转换器、测试 sink 和对账规则，不声称已持久接纳 Run；
- Phase 2 仓储和 DDL 上线后，才把转换结果持久化为 `run_origin=shadow_projection` 快照并启用生产 shadow；
- shadow Run 无 owner/lease，不占 Session active 槽，不进入 runner/reaper；
- 对比最终文本、状态、消息引用、usage 和错误分类；发现差异只记录，不让 shadow 改业务事实。

### WP1.3 Phase 1 临时生产灰度

Phase 1 的 inline 新路径也必须灰度，不能因为它仍在 HTTP 请求内执行就直接替换全部 Web：

- 默认所有租户保持 legacy；使用服务端临时配置 `agent_runs.phase1_inline_tenant_allowlist=[]`，客户端无权选择；
- 先只加入内部测试租户，验证 Agent 构造、消息主写、usage/计费、取消、附件和 browser fail-closed，再按 Phase 0 冻结的指标扩到少量明确批准租户；
- 每次扩量前等待上一批观察窗口结束；任何重复消息、漏计费、权限错误或行为差异立即移出 allowlist，当前请求自然收敛，不在中途交给另一条路径；
- Phase 2 DDL 部署前停止扩量；部署时先等 Phase 1 inline 在途请求结束，再把普通生产租户收回 legacy/shadow，对新的持久 Run 以 shadow 对账为主，仅内部/测试租户保留 inline 验证；
- `tenant_agent_run_routes` 上线并通过 Phase 2/3 门禁后，删除临时 allowlist，后续只使用持久 route mode/epoch，不能让两套租户路由长期并存。

### Phase 1 退出门禁

- [ ] 假模型+假工具可完成 submit→tool→final→query→cancel；
- [ ] 原 Web 行为回归通过，且 shadow 转换器不产生第二次模型/工具执行或第二次消息主写；
- [ ] Web/渠道 SessionRef 无法混用，越权查询 fail-closed；
- [ ] 匿名路径无持久 Run、无写工具，断线可终止；
- [ ] `/api/chat` 分支有对应计量、队列和退场测试；
- [ ] Phase 1 allowlist 已完成内部租户→批准小批租户灰度，扩量/回退指标有记录，进入 Phase 2 前在途 inline 已排空；
- [ ] 对账指标达到 Phase 0 门槛后才进入 Phase 2。

## 5. Phase 2：持久权威、事件和终态事务

目标是让 `accepted` 真正代表可靠接纳，并让等待、取消、排队、事件和结算全部可以跨进程恢复。

### WP2.1 数据库与 Repository

- 实施 Phase 0 通过的 DDL 和升级记录；
- `repository.py` 提供 Run/Command/claim/wait/cancel/queue/finalization 的事务方法；
- `event_store.py` 在 PostgreSQL 内按 Run 分配唯一单调 seq；
- 接入 Phase 1 shadow 转换器，持久化 `run_origin=shadow_projection` 对账快照；该投影无 owner/lease、不占队列槽；
- delta 按冻结阈值合并写入，Redis Pub/Sub 只发布“有新 seq”；
- subscriber 游标落入清理缺口时返回 `reset_required + snapshot_seq`；
- 所有 tenant 查询经 typed SessionRef 和对应 session 归属中转。

### WP2.2 Session 队列与恢复命令

- submit、queued cancel、finalization 晋升和 clarification resume 都取得同一种 Session 队列锁；
- Web 锁 `chat_sessions`，渠道锁 `channel_sessions`；
- 单 Session 一个 active Run，后续 Run 以 `accepted_at, run_id` 排队；
- finalization 同事务晋升最早 live queued Run，取消也修复阻塞链；
- maintenance job 只兜底有界批次未收敛队列，不成为第二调度中心；
- waiting 状态释放 lease；恢复命令验证 wait_ref+version 后重新 claim，不增加 reclaim_count；
- append 在安全检查点消费时才写 user message；终态竞态只转一个新 queued Run；
- 迟到澄清转引用旧 Run 的新 submit，不复活旧 Run。

### WP2.3 幂等 finalization

同一事务或可靠事务边界完成：

- 最终 assistant/tool 消息；
- Run terminal snapshot 和 durable terminal event；
- usage/chat_record/积分结算；
- notification outbox（Phase 2～4 写入开关默认关闭）；
- 队列晋升；
- finalization idempotency key。

结算或消息持久化失败只重试 finalization，不允许重跑模型或工具。外部副作用结果未知进入 `reconcile_required`。

### WP2.4 清理与 reaper 基础

- 单例 `background_runner` 增加事件清理、队列修复和失效 wait 扫描；
- ephemeral delta 只在终态可靠落库后满 15 分钟清理，关键事件初始保留 30 天；
- projector 落后时按 snapshot 跳到 terminal 或 reset，不为补 progress 阻塞 final；
- reaper 只处理有 owner/lease 的执行 Run，排除 shadow projection；
- inline orphan 有可观测、可重排或明确收敛策略。

### Phase 2 退出门禁

- [ ] 相同 command_id 并发重试只产生一个 Run；
- [ ] B01 的 repository contract suite 已对 memory 与 PostgreSQL 两种实现运行；PG 事务、行锁、唯一约束和 `SKIP LOCKED` 附加用例通过；
- [ ] 多副本并发写事件仍保持每 Run seq 唯一、连续或明确 reset；
- [ ] Session 队列、取消、终态和晋升竞态测试通过；
- [ ] finalization 任一步故障恢复后不重复消息、不重复扣费、不重跑工具；
- [ ] migration 新装与存量升级测试均通过；
- [ ] maintenance 有 owner、批次、索引、失败重试和监控。

## 6. Phase 3：独立 agent-runner

目标是让 CLOUD_OWNED Run 脱离 HTTP/SSE 生命周期，并能在 worker 崩溃、重启和发布后恢复。

### WP3.1 claim/lease/fence 执行器

代码落点：`worker.py`、`worker_main.py`、`docker-compose.prod.yml`。

- PostgreSQL `FOR UPDATE SKIP LOCKED` claim；
- 每次 claim 生成新 fence，所有续租、事件和 finish 都校验 fence；
- lease 续租由监督器绑定真实 attempt 活性，不能由失控工具自己“证明还活着”；
- waiting 立即释放 lease，恢复后是新 claim/new fence；
- 自动 reclaim 使用冻结次数/退避，耗尽后 `failed(error_class=worker_interrupted)`；
- 不可安全重试或副作用未知的 attempt 进入 `reconcile_required`；
- `agent_runs.runner_claim_enabled` 只控制新领取，不篡改已接纳事实。

### WP3.2 timeout 与取消

- model/tool attempt 统一受 supervisor timeout 包裹；不允许某个 server tool 无 timeout；
- active execution 累计只计算真实执行时间，waiting 不消耗 active budget；
- lifecycle deadline 和 wait deadline 分别处理；
- cancel_requested 在安全点协作停止，HTTP 只返回“命令已接纳”；
- 已发生副作用不可因 cancelled 被抹去，结果未知必须核验。

### WP3.3 进程与运维

- 新增可多副本 `aid-agent-runner`，不混入单例 scheduler 的 `background_runner`；
- healthcheck 同时体现进程存活、数据库可达、claim loop 活性和优雅 drain 状态；
- SIGTERM 先停止 claim，再等待有界安全点，超时由 lease/reaper 接管；
- runbook 包含扩缩容、暂停领取、发布顺序、积压观测、卡 Run 诊断和回滚；
- 指标至少包含 queued age、claim latency、running age、lease renew failure、reclaim/reconcile、deadline timeout、finalization retry。

### Phase 3 退出门禁

- [ ] 关闭浏览器、断开 SSE 不影响 background Run；
- [ ] kill -9 runner 后 Run 被唯一新 owner 恢复，无双执行/双终态；
- [ ] hang model/tool 在 deadline 后可预测收敛；
- [ ] waiting 不持 lease，回复后新 claim 且 reclaim_count 不变；
- [ ] 停 claim、优雅发布、横向扩缩容演练通过；
- [ ] browser 与未准入工具 fail-closed。

## 7. Phase 4：Web 产品链切换

目标是把 Web 从“一次 SSE 请求拥有执行”改为“submit 接纳 + Run 订阅”，同时交付用户看得见的恢复、排队、补充和停止能力。

### WP4.1 薄 API

建议路由保持现有入口可渐进改造，但业务只调用 AgentApplication：

- submit：返回 RunAccepted；
- get/list：按 session_ref 发现 active/最近 Run；
- subscribe：snapshot + `after_seq`，支持 reset；
- append_input、reply_to_clarification、cancel；
- approval 由有权限管理入口处理；
- 旧 cancel 补认证/归属并映射 active Run；
- `/api/chat` 按 Phase 0 决议正式退场或启用兼容 adapter。

### WP4.2 前端状态投影

主要代码：`frontend/web/composables/useAgent.ts`、`frontend/web/components/ChatContainer.vue`、`ChatInput.vue`、会话列表和新增 API client。

- stable command_id 在断网重试时复用，收到 accepted 才进入订阅；
- RunProjection 以 snapshot/version/seq 为准，不把本地 loading 当事实；
- 刷新后先 list_session_runs，再按 snapshot_seq 订阅；
- 停止、补充/纠正、下一任务三个明确动作；
- 展示 queued、blocked_by、waiting_*、cancel_requested、reconcile_required、draining；
- response delta 可丢后由 snapshot/最终消息恢复；
- 终态后刷新会话历史，避免 UI 自己拼最终 assistant 消息；
- 发布说明明确“关页面不等于停止，停止按钮才是取消请求”。

### WP4.3 Web shadow/draining/run_service 切换

- 全局 `agent_runs.web_mode_default` 只提供默认；`tenant_agent_run_routes` 的 mode/epoch 是租户权威；
- `agent_runs.enabled` 只作为新接纳紧急门禁；
- shadow 建立 durable legacy execution claim；
- draining 期间新请求可靠 queued，旧 claim 清空后再领取；
- unknown effect 不强切，先 reconcile；
- 切换按单租户低风险顺序推进，不能把已 accepted Run 回退到 legacy；
- 回滚只停止新 claim/新路由，已有 Run 继续由新链收敛。

### Phase 4 退出门禁

- [ ] 页面刷新、切会话、关闭标签、API worker 重启后 Run 继续且可找回；
- [ ] 网络重试不重复 Run，seq 缺口能 reset；
- [ ] 三动作和四种 waiting 的 UI/权限/竞态测试通过；
- [ ] 旧新链切换期间同一 Session 无双执行者；
- [ ] 新 final 消息只由 ConversationRepository 主写一次；
- [ ] 至少一个内部租户完成 shadow→draining→run_service 和回滚演练。

## 8. Phase 5：持久通知

目标是用户只要浏览器开着，即使离开会话页也能知道 Run 完成或需要操作。

### 工作项

- 实现 `user_notifications` repository、未读查询、已读/归档和租户授权；
- finalization 在同一可靠边界创建 notification outbox；
- `write_outbox` 与 `consume` 同批开启，Phase 2～4 不自动积压历史通知；
- 全局 SSE/事件连接与当前会话订阅分离；
- 浏览器 Notification API 仅在用户授权后使用，站内通知始终可用；
- terminal、clarification、approval、device、reconcile 使用不同类型和跳转目标；
- 与 `session_task_notifications` 先并存，不在本 P0 强制迁旧表；
- 通知投递幂等，漏实时广播可由未读列表补回。

### Phase 5 退出门禁

- [ ] 用户在其他页面能收到站内完成提示并跳回正确 Run；
- [ ] 浏览器后台且已授权时能收到系统通知；
- [ ] 重连补未读不重复，越权用户看不到通知；
- [ ] Run completed 与通知不存在永久“只成功一边”；
- [ ] 开关关闭时无消费者误处理，历史 backfill 只能显式执行。

## 9. Phase 6：统一 Channel Gateway 与生产渠道迁移

目标是所有渠道只在认证、身份、能力和发送协议上不同，Run、等待、重试、历史与 outbox 只有一套实现。

### WP6.1 Channel Gateway 与 Connector 端口

在 `src/services/agent_run/adapters/channel.py` 和 `channel_ports.py` 实现：

- ingress/auth/decrypt/ACK；
- durable receipt 去重、可信身份和 typed channel SessionRef；
- 按平台 ordering token + reorder grace 的持久 batch；
- 普通 submit、active clarification binding、显式 append 的确定性路由；
- approval 永远不接受外部客户文本，只走受信管理入口；
- capability profile 把规范事件降级成渠道可发送内容；
- delivery outbox 与发送回执；发送重试不重跑 Agent。

已有 `src/channels/<channel>/` adapter 改为端口实现，禁止复制 Run 状态推进。

### WP6.2 aid-channel-worker

独立多副本进程承载：

1. batch sealer：API 重启后仍能封口半批；
2. event projector：Run event→渠道投影→delivery outbox；
3. delivery worker：按 conversation 顺序发送。

每个角色有独立 claim flag：

- `agent_channels.batch_claim_enabled`；
- `agent_channels.projector_claim_enabled`；
- `agent_channels.delivery_claim_enabled`。

Session 行锁内只做头项顺序、预算、epoch 和 sending CAS；平台 HTTP 在锁外执行。头项为 sending/unknown 时后项不得越过。projector 落后于 ephemeral 清理边界时按 Run snapshot 跳到 terminal/reset，绝不重跑 Run 或为 progress 阻塞 final。

### WP6.3 微信客服首接

微信客服是首个生产切换渠道，必须交付：

- 回调快速 ACK 后 durable receipt；
- 平台消息去重与可靠 consultation epoch；
- 2 秒持久合并，API/worker 重启不丢半批；
- persistent reply budget：每 consultation total=5、final reserve=1，outbox 入队原子预占，unknown 不释放；
- progress/clarification/final 的能力降级与额度策略；
- final 发送失败只重试 delivery；
- Web 管理端可查询渠道 Run、处理 approval/reconcile；
- route_epoch + receipt claim 的 shadow→draining→run_service；
- draining 低峰、单账号、分钟级静默窗口 SOP 与运营面板。

### WP6.4 其余生产渠道

按 Phase 0 清单逐一接入企业微信应用、企业微信个人账号 RPA、钉钉、飞书及其他生产渠道。每个 Connector 都要提供：

- 验签/认证和账号归属；
- 入站唯一键、顺序依据、重试语义；
- 身份/session 映射；
- 消息/附件归一化；
- capability profile；
- delivery idempotency/回执；
- shadow 对账、draining SOP、回滚和真机验收记录。

不能可靠接入的已售生产渠道必须取得业务批准的阻断方案，不得静默从 P0 完成范围剔除。

### Phase 6 退出门禁

- [ ] duplicate/out-of-order/retry callback 不产生重复 Run 或乱序上下文；
- [ ] API/channel worker/runner 任一重启不丢 receipt、batch 或 final delivery；
- [ ] clarification binding 优先且一次消费，approval 越权被拒绝；
- [ ] 微信客服预算并发测试不超过 5 且始终保留 final；
- [ ] delivery unknown 不释放预算、不越序、不重跑 Agent；
- [ ] 微信客服先完成单账号生产灰度和回滚演练；
- [ ] Phase 0 盘点的所有生产渠道均有迁移或批准的阻断结论。

## 10. 交付批次与提交边界

每一批必须可独立审查、可运行定向测试，禁止把 schema、全渠道迁移、历史大迁移和 UI 重写塞进同一批。

| 批次 | 内容 | 前置 | 禁止混入 |
|------|------|------|----------|
| B00 | 事实盘点、生产查询包、工具矩阵、协议/DDL、压测与 E2E 方案 | 无 | 生产行为修改 |
| B01 | commands/models/states/fakes/contract tests | B00 | 数据库、HTTP 切换 |
| B02 | Agent executor + ConversationRepository 接口 | B01 | 后台 runner |
| B03 | Web inline adapter + shadow 转换器 + allowlist 灰度 | B02 | 双执行、双主写 |
| B04 | Run/Command/Event DDL 与 migration tests | B00/B01 | 渠道真机切换 |
| B05 | repository/event store/subscribe/reset + 双实现 contract suite | B04 | 前端大改 |
| B06 | Session queue/wait/append/cancel | B05 | 通知 UI |
| B07 | finalization/usage/billing/queue promotion | B05/B06 | 模型或工具重跑逻辑 |
| B08 | maintenance/reaper/event retention | B05/B07 | runner 扩容 |
| B09 | agent-runner/lease/fence/reclaim/deadline | B07 | Web route 切换 |
| B10 | runner compose/health/runbook/metrics + 隔离环境压测 | B09 | 渠道 worker |
| B11 | Web API + frontend RunProjection/三动作 | B06/B09 | 旧链删除 |
| B12 | Web shadow/draining/run_service 灰度 | B11 | 全租户一次切换 |
| B13 | notification outbox + global subscription/UI | B07 | 旧通知表迁移 |
| B14 | channel receipt/batch/ports/schema | B04/B06 | 具体平台发送改写 |
| B15 | channel worker/projector/delivery/budget | B14/B07 | 所有 Connector 同批迁移 |
| B16 | wecom_kf Connector + 真机灰度 | B12/B15 | 其余渠道顺手改造 |
| B17+ | 每个生产 Connector 独立批次 | B15/B16 | 未核实平台事实的假设 |
| B-final | 压测、故障演练、legacy 删除、文档归档 | 全部 | 新功能扩项 |

推荐每批提交正文关联：

```text
文档: docs/plans/plan-unified-agent-run-lifecycle.md, docs/plans/plan-unified-agent-run-lifecycle-implementation.md
```

## 11. 测试与验证计划

### 11.1 测试目录

```text
tests/unit/services/agent_run/
  test_states.py
  test_commands.py
  test_application.py
  test_event_store.py
  test_queue.py
  test_waits.py
  test_finalization.py
  test_worker.py
  test_channel_gateway.py

tests/integration/agent_run/
  test_postgres_repository.py
  test_run_api.py
  test_runner_recovery.py
  test_web_cutover.py
  test_channel_pipeline.py
  test_billing_finalization.py

frontend/web/__tests__/
  composables/useAgentRun*.test.ts
  components/*Run*.test.ts

frontend/e2e/specs/
  agent-run-lifecycle.spec.ts
```

测试文件名可按现有目录调整，但必须维持 unit/integration/e2e 分层。

### 11.2 后端必测矩阵

| 类别 | 必测故障/竞态 |
|------|---------------|
| 幂等 | submit、append、clarification、cancel、finish、delivery 重放 |
| 租户安全 | 跨 tenant run/session/message/notification/tool result 全部拒绝 |
| 并发 | 双 claim、lease 过期、stale fence、submit 与 finalization、cancel 与晋升 |
| 恢复 | API 重启、runner kill -9、channel worker 重启、Redis 丢广播、PG 暂时失败 |
| 等待 | 四类 waiting、deadline、迟到回复、重复绑定、waiting 不持 lease |
| 事件 | seq 并发、delta 合并、清理缺口、reset、projector 落后 |
| 终态 | 消息/计费/通知/队列任一步失败后的幂等重试 |
| 工具 | timeout、取消、安全重试、unknown effect→reconcile、browser deny |
| 渠道 | duplicate、out-of-order、半批封口、预算并发、HTTP unknown、严格发送顺序 |

### 11.3 执行命令

后端统一经项目脚本执行，示例：

```bash
./scripts/dev_test.sh tests/unit/services/agent_run -p no:cacheprovider -q
./scripts/dev_test.sh tests/integration/agent_run -p no:cacheprovider -q
./scripts/dev_test.sh tests/unit/channels tests/integration -m channels -p no:cacheprovider -q
```

前端：

```bash
cd frontend
npm run test -- web/__tests__/composables web/__tests__/components
npm run build
```

生产渠道 E2E 只在隔离账号/测试租户运行；微信客服上线前必须补真机回调、合并、进度、澄清、final、重试和预算验收。不得用 mock 平台测试冒充真机验收。

### 11.4 Web 浏览器级 E2E

项目已经有 Playwright，不引入第二套浏览器 E2E 框架。Phase 4 在
`frontend/e2e/specs/agent-run-lifecycle.spec.ts` 增加以下场景：

- submit 后切换会话、刷新页面、关闭并重新打开页面，能够通过 list+snapshot+seq 恢复；
- 停止、补充/纠正、下一任务分别发出不同命令，终态竞态和断网重试不重复；
- queued/blocked、四类 waiting、cancel_requested、reconcile_required、draining 展示正确；
- 丢失 Pub/Sub 唤醒或清理 delta 后，客户端按 `reset_required` 恢复最终结果；
- route 灰度期间 legacy/shadow/run_service 页面投影与回退提示正确。

E2E 使用 `docker-compose.test.yml`、测试租户和仅测试环境启用的确定性 fake model/tool，禁止调用真实写工具或依赖不可复现的外部 LLM。执行命令：

```bash
cd frontend
node e2e/scripts/refresh-auth.mjs
npx playwright test e2e/specs/agent-run-lifecycle.spec.ts --project=tenant-admin
```

Playwright 报告、失败截图和 trace 作为 B11/B12 批次证据。操作系统级 Notification 权限弹窗、真实标签页后台通知以及生产 route 切换仍补一份人工验收清单；人工记录必须包含环境、tenant、route epoch、时间、预期/实际和截图引用，不能用“肉眼通过”一句代替。

### 11.5 压测环境与负载工具

不在生产环境直接造压。B00 先根据生产证据冻结 workload profile；B09/B10 在扩展后的
`docker-compose.test.yml` 隔离栈执行，使用专用测试数据库/Redis、至少 2 个 runner 副本和确定性 fake model/tool。需要数据库级隔离时复用 `./scripts/dev_test.sh --isolated-db` 的建库方式，不能连接开发者共享库、仿真业务库或生产库。

新增仓库内可复现脚本 `scripts/benchmark_agent_run.py`，使用项目已有 Python asyncio/HTTP/数据库依赖，不为本次压测额外引入 k6/Locust。脚本至少支持：

- 直接 repository/event-store 模式：测 seq 分配、delta 合并、批量事件写入和 Session 锁；
- worker 模式：并发 queued Run、`SKIP LOCKED` claim、lease renew、finish/reclaim；
- HTTP 模式：submit/get/subscribe/reconnect 的端到端延迟；
- 固定 seed、并发数、Run 数、每 Run 事件数、payload 大小和 worker 副本数；
- 输出吞吐、P50/P95/P99、错误率、重复领取/seq 冲突、queued age、数据库连接占用和容器峰值内存。

运行参数由 B00 workload profile 给出，不在计划中凭空写死容量目标。原始结果写入未提交的 `tmp/agent-run-bench/`；命令、代码版本、compose 配置、机器规格和脱敏摘要写入 B10 批次证据。资源数据优先取应用指标、数据库查询耗时与 `docker stats --no-stream`；若引入新的观测依赖，必须单独评审。B-final 在候选发布版本上复跑同一 profile，未达到 Phase 0 冻结阈值不得扩大灰度。

### 11.6 独立验证证据

每批在本计划“开发进度”或批次记录中保留：

- 最终改动范围和 commit（仅实际提交后记录）；
- 开发者自测命令与结果；
- 独立测试命令、通过/失败/跳过；
- 独立 CR 的 P0/P1/P2 结论；
- 未验证项和生产前置；
- 若有回滚演练，记录 route epoch、积压和恢复结果，不记录凭据。

## 12. 灰度、观测与回滚

### 12.1 开关初始值

新版本首次部署时，新领取/消费开关全部关闭：

```text
agent_runs.enabled=false
agent_runs.web_mode_default=legacy
agent_runs.phase1_inline_tenant_allowlist=[]  # Phase 1 临时配置，Phase 2 正式路由稳定后删除
agent_runs.runner_claim_enabled=false
agent_channels.batch_claim_enabled=false
agent_channels.projector_claim_enabled=false
agent_channels.delivery_claim_enabled=false
agent_notifications.write_outbox=false
agent_notifications.consume=false
channel_accounts.run_mode=legacy       # PostgreSQL 每账号默认值
channel_accounts.route_epoch=0         # PostgreSQL 初始路由代次
```

精确配置层级和命名以 P0 Phase 0 冻结结果为准；`web_mode_default=legacy` 只在租户没有持久 route 行时兜底，租户 Web route 与渠道账号 route/epoch 必须持久化，不能只放环境变量。

### 12.2 推荐启用顺序

Phase 1 前置灰度先按 WP1.3 执行：全局默认 legacy，只给内部租户开启临时 inline allowlist，
再扩少量批准租户；进入下列 Phase 2+ 顺序前先停止扩量、等待在途 inline 请求结束，并将普通
生产租户收回 legacy/shadow。临时 allowlist 与正式 route 表不得同时承担长期路由权威。

1. 部署 schema 和只读/shadow 代码，所有 claim/consume 关闭；
2. 启动 agent-runner/channel-worker，验证健康但不领取；
3. 开启内部租户 Web shadow，对账状态、结果、usage；
4. 内部租户 draining 后切 Web run_service；
5. 扩到小批低风险 Web 租户；
6. 微信客服单测试账号 shadow→draining→run_service；
7. 微信客服单生产账号低峰灰度，观察一个完整业务周期；
8. 分账号扩微信客服，再逐一迁其余生产渠道；
9. 通知 write/consume 同批开启；
10. 达到稳定窗口后再删除 legacy，不以“已部署新代码”视为完成。

### 12.3 回滚原则

- 关闭新 claim 只阻止新领取，不把 running/accepted Run 改成不存在；
- 已 accepted Run 由新链完成、等待或明确收敛，不能交给 legacy 再跑一次；
- Web/channel route epoch 只单调推进，stale receipt/claim 不得越代接管；
- draining 超时保持缓冲并报警，不强切；
- 发送失败只回滚投递，不重跑 Agent；
- 数据库变更优先向前兼容，回滚应用版本前确认旧版本不会误读新状态；
- reconcile_required 由人工/受信流程核验，不用批量 retry 掩盖未知副作用。

### 12.4 上线阻断指标

Phase 0 用基线数据给出数值阈值；以下任一越过阈值即停止扩大灰度：

- duplicate Run/tool/delivery；
- terminal 与 final message/usage/billing 不一致；
- stale fence 写成功或同 Session 双 active；
- queued age、claim latency、reclaim、reconcile、finalization retry 异常；
- channel receipt 未处理、batch 最老等待、delivery unknown/failed、预算泄漏；
- tenant 越权、未授权 approval 或敏感事件外泄；
- P95/P99 active execution/attempt timeout 明显误杀合法任务。

## 13. P0 完成定义

只有同时满足以下条件，才将本计划标记完成并把 `docs/ideas.md` 条目迁入 `ideas_finished.md`：

- [ ] Web 和所有已售生产渠道使用同一 AgentApplication/Run 生命周期；
- [ ] 微信客服完成生产灰度，回调、合并、等待、进度、final、重试和预算均真机验收；
- [ ] 浏览器关闭、API/runner/channel worker 重启不会丢已接纳 Run 或最终回复；
- [ ] cancel、append、clarification、approval、device、reconcile 均有持久语义和权限测试；
- [ ] 消息、终态、usage/扣费、通知和队列晋升的幂等边界通过故障注入；
- [ ] 多租户隔离、stale fence、双 claim、乱序渠道消息和发送 unknown 测试通过；
- [ ] Web 三动作、刷新恢复、全局通知和用户行为变更说明上线；
- [ ] runner/channel worker 的 compose、健康检查、claim 开关、优雅 drain 和 runbook 完成；
- [ ] legacy Web/匿名 loop 和已迁渠道的独立生命周期代码删除，或有明确时限与 owner；
- [ ] 所有 P0 代码批次经过独立测试和独立 CR，无开放 P0/P1 问题；
- [ ] P1 桌面和 P2 演进文档仍遵守本次冻结的 Agent API、Device API、typed SessionRef 和状态所有权，没有复制 Run 状态机。

## 14. 开发者开始 Phase 0 时的首批动作

1. 读取 P0 设计基线、本计划及项目 backend/frontend/database/testing/dev-workflow 规则。
2. 建立 B00 事实盘点，不先写表、不先改 `src/main.py`。
3. 把 `/api/chat`、旧 D1、browser、生产渠道、工具 effect 和 deadline 样本列成可核验清单。
4. 起草 B01 contract/state 红灯测试，由独立测试者先审测试语义。
5. 完成 DDL 草案与 finalization 事务草图，独立 CR 后再进入 Phase 1/2 实现。
6. 在 B00 kickoff 完成、实际开始第一项盘点/查询包工作时，把本文 Phase 0 状态改为“🔧 进行中”，并把 `docs/ideas.md` 条目改为“🔧 部分完成”；仅有开工意向时不要提前更新。

## 15. 批次记录

本节是批次级证据的唯一索引，Phase 顶部表只表示阶段状态。状态更新规则：某 Phase 的首个批次实际开始时改为“🔧 进行中”；该 Phase 全部必需批次完成、独立测试/CR 关闭 P0/P1 且退出门禁通过后，才改为“✅ 完成（日期）”。仅写完代码、仅测试通过或仅部署均不能提前标记完成。

每个批次开始时在 `docs/plans/evidence/agent-run/<batch>.md` 建立一份简短证据附件并从下表链接。附件记录改动范围、命令与退出状态、独立测试、CR、未验证项、灰度/回滚结果；只保存脱敏摘要，不提交原始生产数据、凭据、完整日志、Playwright storageState 或大体积压测输出。纯调研的 B00 同样使用该格式。目录和附件在批次真正开始时创建，不预建空文件。

| 批次 | 所属阶段 | 状态 | 开始/完成 | 证据附件 | 阻塞/遗留 |
|------|----------|------|-----------|----------|-----------|
| B00 | Phase 0 | 📋 待开发 | — | — | kickoff 时填写生产查询实际执行人/备份人 |
| B01 | Phase 0 | 📋 待开发 | — | — | — |
| B02 | Phase 1 | 📋 待开发 | — | — | — |
| B03 | Phase 1 | 📋 待开发 | — | — | — |
| B04 | Phase 2 | 📋 待开发 | — | — | — |
| B05 | Phase 2 | 📋 待开发 | — | — | — |
| B06 | Phase 2 | 📋 待开发 | — | — | — |
| B07 | Phase 2 | 📋 待开发 | — | — | — |
| B08 | Phase 2 | 📋 待开发 | — | — | — |
| B09 | Phase 3 | 📋 待开发 | — | — | — |
| B10 | Phase 3 | 📋 待开发 | — | — | — |
| B11 | Phase 4 | 📋 待开发 | — | — | — |
| B12 | Phase 4 | 📋 待开发 | — | — | — |
| B13 | Phase 5 | 📋 待开发 | — | — | — |
| B14 | Phase 6 | 📋 待开发 | — | — | — |
| B15 | Phase 6 | 📋 待开发 | — | — | — |
| B16 | Phase 6 | 📋 待开发 | — | — | — |
| B17+ | Phase 6 | 📋 待开发 | — | — | 每个生产 Connector 拆独立附件 |
| B-final | P0 收口 | 📋 待开发 | — | — | — |
