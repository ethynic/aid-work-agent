# 统一 Agent Run 应用服务、持久执行与完成通知设计基线（P0）

> 状态：设计基线已完成多轮评审；尚未开始编码。实施任务、测试门禁与进度只在
> [P0 开发执行计划](./plan-unified-agent-run-lifecycle-implementation.md)登记，本文继续作为架构与契约权威。
>
> 优先级：P0。本文 Phase 0～6 是 P0 内部实施顺序，不是 P0～P6 优先级。微信客服
> `wecom_kf` 及 Phase 0 盘点出的其他已售、生产启用渠道必须完成 Phase 6，才能宣告本轮
> P0 完成；未启用的新渠道、Task 全面接入及明确标注的后续能力不属于本轮完成门槛。
> 发布方式：Phase 0～6 只在功能分支与隔离环境迭代；Web、微信客服、全部生产渠道、通知及后台进程组成一个完整候选版，在 agent3 联合验收通过后合并 master，并按一次生产发布切换。不做租户/账号灰度，不把任何中间 Phase 当作生产版本。
>
> 日期：2026-09-22
>
> 关联现状：[多会话后台流式设计](../system/multi-session-background-streaming-design.md)、[企业 Agent 平台总体架构](../system/enterprise-agent-platform/enterprise-agent-platform-integration-design.md)、[Enterprise Execution Fabric](../system/enterprise-agent-platform/enterprise-execution-fabric-design.md)、[P0 开发执行计划](./plan-unified-agent-run-lifecycle-implementation.md)、[桌面客户端 P1 规划](./plan-desktop-client-p1.md)、[其他架构演进 P2 规划](./plan-agent-architecture-p2.md)。桌面客户端属于独立 P1 规划，本文件只定义其必须遵守的 Agent API / Device API 与状态所有权约束。

## 1. 决策摘要

将“统一执行对象、状态所有权与生命周期”“从 Web 请求中抽取统一 Agent 应用服务”“Web 长任务脱离 SSE”“任务完成通知”合并为同一条实施主线，不分别建设四套机制。

核心决策：

1. `Run` 是一次已接纳 Agent 执行的唯一顶层生命周期对象，从输入开始，跨多次模型调用、工具调用、等待和恢复，直到终态。
2. `Session` 继续表示用户可理解的对话/上下文容器；一个 Session 包含多个 Run，但 Session 不拥有 Run 的执行状态。
3. `Task` 只表示长期业务目标。现有 `session_tasks` 可以触发多个 Run，不把每次聊天强行升级为 Task，也不建立统一 Task 作为所有能力的前置条件。
4. `ToolInvocation`、`ModelAttempt`、`Artifact` 是 Run 的下级或关联对象，不再用其中任何一个代替 Run。
5. 云端 `RunService` 是当前产品业务 Run 接纳、状态推进、等待、取消请求和终态的唯一权威。Web、未来 Desktop、渠道和通知层只能提交命令、订阅投影，不能独立推进同一个 Run。
6. Phase 1 先抽取真实应用用例，不先重写 `agent.py`，也不先统一所有传输协议；生产旧版保持不变直到完整 P0 候选版一次上线。新 Web 仍可使用 SSE 传输；未发布桌面的旧 D1 不作为产品兼容对象，清点真实依赖后由桌面 P1 直接替换或删除。
7. Agent 执行从 SSE 请求生命周期中剥离后，由专用、可横向扩展的 `agent-runner` 进程承载；它复用现有 background 容器的初始化能力，但不把可扩展 Run worker 混入承载单例 scheduler 的 `background_runner`。采用 PostgreSQL 领取/租约/恢复，不使用易丢任务的进程内 `create_task` 或 `Redis LPOP` 作为唯一权威。
8. 通知以数据库记录为权威，Redis/SSE/Web Push 只是投递通道。Run 完成与通知创建在同一事务或可靠 outbox 边界内完成。
9. 本文件是统一 Run 改造的 P0 唯一架构与契约基线；具体开发批次、测试门禁和进度由配套开发执行计划维护。桌面进程结构、UI、Runtime 生命周期和首发安排不得回写为本计划的实施阶段。
10. 微信客服不是远期“渠道适配”示例，而是已售生产主入口。P0 必须把其入站去重、2 秒合并、
    Run 接纳、过程提示/澄清、最终回复与发送重试完整迁入统一用例；不能只让 Web 获得可靠执行。
11. 企业微信客服/应用/个人账号 RPA、钉钉、飞书等第三方渠道共用一条 Channel Gateway
    生命周期；渠道差异只通过认证、入站、身份解析、能力描述和发送 adapter 表达，禁止每个
    渠道复制一套 Run、等待、历史、重试和 outbox。

## 2. 目标与非目标

### 2.1 目标

- 刷新页面、关闭标签页、SSE 断开后，已接纳的 CLOUD_OWNED Run 继续执行。
- Gunicorn worker 重启或发布不再终止 Run；后台 worker 崩溃后可根据租约恢复或明确收敛状态。
- 生产 Web 通过同一应用服务完成接纳、执行、工具续接、历史持久化、结果生成和取消；未来入口只能复用该用例，不能复制生命周期。
- 微信客服及 Phase 0 盘点出的已售、生产启用渠道通过同一应用服务执行；平台回调超时、重复回调、发送失败不会重复运行 Agent 或丢失最终回复。
- 同一个 `command_id` 重试只产生一个 Run，并返回 `accepted/already_accepted`。
- 最终回答、工具消息、Run 终态和完成通知不再由多个入口分别拼装和写入。
- 支持可游标重连的事件订阅，以及离开当前会话页、页面隐藏后的持久通知和全局提示。
- 外部副作用结果未知时进入 `reconcile_required`，不自动当作普通失败重试。
- 用户可显式补充正在运行的 Run；补充内容持久化、可审计，并在安全检查点进入后续模型调用，不能靠自由文本猜测“打断还是新任务”。

### 2.2 P0 能力—设计—验收闭环

下列不是理论收益，而是本计划完成后必须存在的产品能力。任何一行缺少实现或验收，均不能
以“统一 Run 已完成”结项。

| 用户能力 | P0 中的实现约束 | 验收位置 |
|----------|----------------|----------|
| Web 离开会话、刷新或关闭标签后继续执行 | 持久 Run、独立 runner、lease/fence、可恢复事件 | Phase 2～4、§10.2 |
| 回到页面能发现原任务和当前进度 | `list_session_runs`、snapshot + 单一 `after_seq` | Phase 4、§10.2 |
| 明确停止而不是“断线即取消” | cancel command、安全点、取消终态分离 | Phase 2/4、§10.1/10.2 |
| 新消息可靠排队，澄清回复恢复正确任务 | Session 队列锁、WaitDescriptor、版本校验 | Phase 2/4、§10.1/10.2 |
| 显式补充当前任务 | `append_input` 命令、版本校验、持久事件、安全检查点 | §5.2.1、Phase 4、§10.1 |
| 过程提示、工具进度、澄清可跨断线恢复 | 规范事件、持久 wait、渠道/Web 投影 | §5.5、§6.2、§10.2 |
| 完成后在其他页面收到通知 | 持久通知、全局 SSE、浏览器 Notification API | Phase 5、§10.4 |
| 服务恢复后微信客服已接纳消息可靠、不重跑 | durable inbound receipt、平台消息去重、2 秒持久合并；不覆盖停机发版窗口 | Phase 6、§10.6 |
| 微信客服最终回复发送失败可重试 | channel delivery outbox；重投发送而不重跑 Agent | Phase 6、§10.6 |
| 微信客服等待提示不挤占最终回复额度 | 渠道进度投影，复用现有总额度 5、保留 final 1 的预算 | Phase 6、§10.6 |
| 客服渠道任务可从 Web 查看、审批和继续 | 渠道 `session_ref/run_id` 统一查询、等待与授权入口 | Phase 6、§10.3/10.6 |
| 所有现有生产第三方渠道使用同一可靠链路 | Channel Gateway + 统一 receipt/batch/wait/delivery；差异只在 adapter/capability profile | Phase 6、§10.6 |
| 计费、最终消息和终态不会各成功一半 | 幂等 finalization 事务边界 | Phase 2、§10.5 |

### 2.3 非目标

- 不在本计划中把 `agent.py` 一次性重写为理想化 Kernel。
- 不一次性迁移全部 `chat_sessions/chat_messages/channel_messages` 历史数据。
- 不立即重命名所有既有状态枚举，也不在本计划中设计桌面客户端协议迁移。
- 不把 `session_tasks`、定时任务、recap、browser run 全部合并成一张通用任务表。
- 不把 RunService 扩展为第二套 ToolInvocation 调度中心；工具执行继续复用现有 Server ToolExecutor、`local_tools` 与 Execution Fabric 方向。
- 不让 Web Push、Redis Pub/Sub 或前端 store 成为 Run 或通知的事实来源。

## 3. 统一语义与现有对象映射

### 3.1 规范对象

| 对象 | 规范语义 | 现有项目映射 | 本计划动作 |
|------|----------|--------------|------------|
| Task | 持续存在的业务目标，可触发多个 Run | `session_tasks`、部分 scheduled/marketing 业务任务 | 保留场景模型；只增加可选 Run 关联，不改造成通用聊天任务 |
| Session | 对话与上下文容器 | `chat_sessions.session_id`、渠道 session ref | 继续复用；通过 `session_ref` 关联 Run |
| Run | 一次已接纳的端到端 Agent 执行 | Web SSE 请求协程；旧 D1 实验回合 | 建立统一语义和最小持久化权威；旧 D1/browser continuation 只做依赖审计，不作为兼容前提 |
| ToolInvocation | 一次有身份、授权、目标和结果的工具执行 | Agent tool call、`local_tool_invocations` | 增加 `run_id`/稳定关联；不另建平行调度器；browser invocation 后续重构接入 |
| ModelAttempt | 一次真实模型请求尝试 | LLM call log、Trace/usage 明细 | 先统一引用和查询接口，不在第一阶段迁表 |
| Artifact | 文件、图片等可授权引用的产物 | file_id、image asset、downloadableFiles、本地 artifact ref | 先统一结果引用；沿用现有存储和授权边界 |

### 3.2 旧 Desktop D1 与新桌面的边界

桌面尚未发布，旧 D1 `agent/next`、outcome 和客户端代推进服务端工具的路线不承担产品兼容责任。本 P0 只做以下工作：

- 清点 `src/desktop_agent`、Gateway、Runtime、CLI 与测试夹具的真实调用方，不能按目录名盲删共用能力。
- 停止在统一 Run 主线继续扩展旧 D1；生产和共享依赖确认后，旧桌面专属入口由桌面 P1 直接替换或删除。
- 可复用的授权票据、幂等、Invocation、证据和结果接纳能力迁入 AgentApplication、Device API 或 Execution Fabric，不保留第二套 Run 生命周期。
- 新桌面未来直接使用统一 Agent API；受信 Runtime 使用独立 Device API。普通 UI 身份不能提交工具结果。
- “旧 D1 无兼容义务”只适用于未发布的 `/api/desktop/v1` 与 Desktop Coordinator 协议，不适用于
  已存在的 `agent-tool-runtime`、`/api/local-tools/runtime/*`、Provider、result outbox 和
  `session_tasks`。这些能力在 Phase 0/D00 按“可能已生产使用”审计，未取得反证前不得破坏性删除。
- P0 B01 负责冻结 Device API 的身份、Invocation、claim/fence、result/evidence/ACK 与版本协商
  语义，但 P0 Web/渠道主线不负责交付完整**新版** Device API 服务端；版本化演进由 Desktop P1
  的 Device Backend 子轨交付。**现有生产 Runtime + Provider 的兼容执行链仍是 P0 一次上线门槛**：
  新 Run 经受信设备边界使用现有 `/api/local-tools/runtime/*` 时，Invocation、结果和 Run 必须稳定关联，
  不能因桌面 P1 尚未完成而断开已售客户的本地工具能力。
- P0 B01/G0 同时冻结 Web/Desktop 共用的 typed SessionRef、Session summary、canonical Conversation
  cursor/page、Artifact upload/metadata/ref、command receipt 和受权 Device directory 基础 DTO；Phase 4
  B11 负责实现这些表面。Notification DTO 与实现都留到 B13/G3，不计入 G0。
- `execution_id`、窗口 ID、连接 ID 均不得成为 Run 身份；Run、Session、Invocation、Device 和 Runtime epoch 必须分离。

旧 D1 的 ID 生成和行为只作为删除/迁移依赖审计，不再成为 P0 的新协议设计任务或验收对象。

### 3.3 与企业平台长期架构的边界

本计划补充“Agent Run 应用层”，不推翻现有平台架构中的原则：

- 不要求所有业务能力先创建 Task；普通聊天可以只有 Session + Run。
- 当前产品的业务 Run 统一由云端 RunService 权威推进。
- 本地 Runtime 只拥有设备能力、资源占用、动作证据和待补传结果，不拥有平行的业务 Run 或模型循环。
- RunService 负责 Agent 用例编排；Execution Fabric 负责工具在哪里执行；Policy 负责能否执行；Evidence 负责副作用证据。四者不互相复制职责。

### 3.4 Browser 工具迁移决议

当前产品决策是**统一 Run 首期不交付 browser 能力**；这不是“生产从未使用”的事实断言。
`BrowserAutomationTool` 等工具仍可能通过全局 registry 被 Web Agent 技术性发现，其
`BrowserResumeWorker`、进程内 owner runtime、`BrowserViewHub` 和 HTTP worker lifespan
属于旧执行架构。**新 Run 架构不为兼容这些约束作妥协，但切换前必须用生产历史核验
实际调用量和真实使用租户。**

- Phase 1–4 的统一 Run 首期能力矩阵明确排除 browser human-assistance/直播。
- 启用 `run_service/background` 模式时，browser 工具必须从该模式的可用工具集中移除，
  或返回明确的 `CAPABILITY_NOT_MIGRATED`，不得暗中回落到 HTTP worker。
- 生产旧版在正式切换前仍可使用 browser；切换后的新 Run 不跨进程续接旧 browser。
- Phase 0 从工具调用记录、Trace 和消息/执行明细核验 browser 工具实际调用次数、租户、
  成功率与最近使用时间；存在真实使用时必须在整体发布前完成能力替代或取得明确的客户停用/迁移决议及提示，不能靠让该租户长期留在旧架构绕过门槛，
  也不以“无价值”的产品判断直接切断。
- 后续独立重构时，browser execution 作为 Execution Fabric 的一种受信 Executor：owner、
  claim、lease、fence 和 resume job 持久化；画面流经跨进程 relay/object channel 提供；
  UI Hub 只做订阅投影。
- `bs_browser_resume_jobs` 是否并入通用 ToolInvocation attempt，在 browser 重构专题中决定；
  不阻塞 Phase 0，也不允许现在建立第二套 Run 调度中心。

### 3.5 第三方渠道统一边界

第三方渠道接入拆为“共同生命周期 + 渠道端口”，而不是按渠道复制业务流程：

```text
平台事件
  → ChannelIngressAdapter（验签/认证、解密、平台 ACK）
  → Channel Gateway（receipt 去重、排序、合并、身份与 session_ref 解析）
  → AgentApplication.submit / clarification resume / append_input
  → Run event projector（按渠道能力生成有限进度/澄清）
  → channel_delivery_outbox
  → ChannelDeliveryAdapter（格式降级、额度、发送、平台回执）
```

统一端口至少包含：

```text
authenticate(raw_request, channel_config) -> TrustedChannelPrincipal
normalize_inbound(raw_event)              -> CanonicalChannelMessage[]
acknowledge(receipt)                      -> PlatformAck
resolve_session(principal, message)       -> session_ref
capabilities(channel_config)              -> ChannelCapabilityProfile
deliver(delivery, credential_ref)         -> DeliveryReceipt | DeliveryUnknown
```

`ChannelCapabilityProfile` 描述 ACK 时限、是否支持主动消息/等待回复/状态提示、单次与总回复
额度、文本/图片/文件上限、引用回复、平台幂等键和结果查询能力。它只影响输入输出投影，不得
改变 Run 状态机。认证材料和平台 secret 由 channel config/credential reference 获取，不进入
Run input 或事件明文。个人账号 RPA/轮询型渠道即使没有 HTTP 回调，也必须先形成同样的
durable receipt，再进入统一 Gateway；差异是 ingress transport，不是另一套执行生命周期。
Connector 还必须声明消息排序依据和允许的 reorder grace。平台有可靠 sequence 时优先使用；
否则使用文档化的稳定比较器（例如 occurred_at + external_message_id），最后才使用数据库
receipt_sequence，并标记 `deterministic_best_effort`。不得把到达顺序或同时间戳任意顺序宣称为
平台原始顺序；重试的同一 external_message_id 必须得到同一 ordering token。

对 AgentApplication 暴露的唯一渠道接入面如下；任何生产渠道都不能绕过它直接调用 Agent：

```typescript
interface CanonicalChannelEnvelope {
  readonly ingress_id: string
  readonly channel: string
  readonly channel_account_ref: string
  readonly external_message_id: string
  readonly external_conversation_ref: string
  readonly external_sender_ref: string
  readonly occurred_at: string
  readonly received_at: string
  readonly platform_sequence?: string
  readonly ordering_token: string
  readonly ordering_basis: 'platform_sequence' | 'timestamp_message_id' | 'receipt_sequence'
  readonly ordering_confidence: 'authoritative' | 'deterministic_best_effort'
  readonly input: SubmitRunCommand['input']
  readonly reply_to_external_message_id?: string
  readonly raw_payload_ref: string
}

interface ChannelGateway {
  accept(
    envelope: CanonicalChannelEnvelope,
    principal: TrustedChannelPrincipal,
  ): Promise<ChannelAccepted>

  acceptClarificationReply(
    envelope: CanonicalChannelEnvelope,
    binding: ChannelClarificationBinding,
    principal: TrustedChannelPrincipal,
  ): Promise<CommandAccepted>

  enqueueDelivery(delivery: CanonicalChannelDelivery): Promise<DeliveryAccepted>
}
```

`channel` 只用于选择 Connector、观测和审计，不允许在 RunService/Agent/ConversationRepository
中出现按渠道分支的业务状态机。Connector 必须先完成平台验签/认证并构造不可由消息正文伪造的
`TrustedChannelPrincipal`；Gateway 随后统一处理去重、合并、Session 映射、Run 接纳、等待回复、
历史和 delivery outbox。出站 worker 读取统一 `CanonicalChannelDelivery`，再调用对应 Connector
做格式转换和发送。新增渠道的标准工作量应是“实现 Connector + capability profile + 契约测试”，
而不是修改 AgentApplication 或复制一套消息处理服务。

#### 3.5.1 全部生产渠道的一刀切发布

P0 不引入按 channel account 的新旧路由、双锁、发布代次、旧任务排空或发布窗口入站缓冲。
agent3 用完整候选版和隔离账号验收全部 Connector；生产发版时 Web 与全部渠道暂停服务，
直接停掉旧版本并部署新版本。停机期间回调可能失败，平台是否重试按其自身规则处理；
**不承诺这些消息被接纳或自动补发**，用户/渠道运营人员可在服务恢复后重新发送。

新版本恢复服务后，所有新到达的渠道消息一律经 Channel Gateway → AgentApplication → Run；
旧版已 ACK 但尚未完成的进程内 task/发送可能失败，不迁入 Run，也不自动重跑。若旧动作的
外部结果不确定，应在用户再次执行有副作用操作前提示核验风险；这不构成必须排空旧 task
才能发版的门槛。正常运行期的新消息仍必须先 durable receipt 再 ACK、去重并可靠投递，
不能把“发版期间允许失败”扩展为新架构的日常丢消息策略。

#### 3.5.2 渠道后台角色与回复预算

新增独立、可多副本部署的 `aid-channel-worker`，不把以下角色塞进 API、`agent-runner` 或仅
承载单例 maintenance 的 `background_runner`：

- batch sealer：按 `seal_at` 使用 `SKIP LOCKED` 领取并幂等封口持久 batch；
- Run event projector：以持久 cursor 消费 Run 事件，按 capability 降级并产生待发送 delivery；
- delivery worker：领取 outbox、调用 Connector 并记录平台回执。

三者都必须有 owner/lease/fence、幂等键、失败退避和积压指标。projector 使用
`(run_id, projector, event_seq)` 唯一键防重复。delivery 只在短事务内锁 channel Session 行：
确认自己是该会话最早可发送项、校验预算/执行 fence、把 outbox 从 pending CAS 为
`sending(owner, fence, lease)` 后立即提交并释放行锁；平台 HTTP 调用必须在锁外执行。回写回执
时再开短事务校验 fence。后续同会话 delivery 在前项 `sending/unknown` 收敛前不可领取，但新
消息接纳、Run finalization 和 outbox 入队不被秒级网络 IO 持锁阻塞。发送前 Connector 还要按
稳定 delivery/request id 重查可发送状态；lease 到期且结果未知时先查询平台或进入人工核验，
不能直接发送下一项。不同会话可并发。没有明确 owner、恢复和清理策略的角色不得上线。
projector 只负责非终态 progress/clarification 等投影；最终回复 outbox 只能由 Run finalization
创建，二者使用不同 delivery kind/唯一键，禁止同时消费 `run.completed` 生成两份 final。
如果 projector cursor 落后到 ephemeral progress 已被清理，先读取 Run snapshot：Run 已终结时
直接将 cursor 跳到 terminal snapshot/event，不补发残留 progress，也不创建 final；Run 未终结
时按 `reset_required + snapshot_seq` 重建投影并仅处理仍存在的新事件。progress 是 best-effort，
不得为了补齐它阻塞最终回复或重新执行 Run。

微信客服回复预算改为持久 `channel_reply_budgets`（或复用现有表实现等价语义），按 tenant、
channel account、external consultation/session epoch 唯一。预算在创建 delivery outbox 时同事务
原子预占，而不是发送时才检查：progress 只有在 `reserved_or_sent < total - final_reserve` 时
能够预占；final 原子消费预留的 1 次。相同 delivery id 重试不重复计数；发送结果 unknown
不得释放预算，只有平台明确未发送且策略允许时才能补偿释放。这样多副本下仍保证微信客服
总额度 5、final 保留 1。
`consultation/session epoch` 必须由 Connector 提供可审计的 `consultation_ref + epoch_basis`：
优先采用平台明确的咨询开始/结束标识；若平台没有可靠字段，使用持久状态机按已冻结的事件和
超时规则生成，不能依赖进程内计数或 worker 启动时间。epoch 判定变化要版本化，旧预算账本
不能被新算法重新归组。

#### 3.5.3 代码落位

稳定 Gateway/port 类型位于 `src/services/agent_run/adapters/channel.py` 与
`channel_ports.py`；具体实现继续放在现有 `src/channels/<channel>/`，由 composition root 注册，
Agent Run 包不得反向 import 任一具体渠道。`channel_messages` 是否扩列或使用关联表由 Phase 0
字段映射决定，但授权、幂等、队列和运维查询所需引用不能只放 metadata。

### 3.6 Web 的一刀切发布

发版窗口 Web 不可用，旧 SSE 连接与旧版在途执行可以被中断并失败；不建立旧执行 claim、
等待排空或把旧轮转换为 Run。页面应明确提示“服务升级中，本次问题可能未完成，请恢复后
重新发送”，不能将旧版失败伪装成新 Run 的 queued/running。新版本恢复后，所有新提交的
用户消息——包括已有 Session 里的新消息——都创建新 Run；旧 Session 历史仍按
ConversationRepository 的兼容读取规则展示。旧版结果不确定的写操作不得被系统自动重试，
用户手动重试前应能看到可能重复执行的风险提示。
旧版 `session_queue` 的 responding/pending 标记、browser suspension 和进程内任务不得自动映射成
新 Run 的 active/waiting，也不得阻塞恢复后的新消息；旧 Session 的历史数据可以继续读取。

## 4. 状态所有权与状态机

### 4.1 所有权矩阵

| 事实 | 权威所有者 | 其他组件允许行为 | 禁止行为 |
|------|------------|------------------|----------|
| Run 是否已接纳、当前状态、版本和终态 | 云端 RunService | 查询、订阅、提交命令 | Web SSE、任何客户端 adapter、前端 store 分别改终态 |
| Run 的执行租约和当前 worker | RunRepository/后台执行器事务 | worker 续租、控制器回收 | 进程内集合被视为运行权威 |
| 工具实际执行、effect 与证据 | 对应受信执行路径；云端接纳投影 | RunService 根据结果继续 | 超时直接等价为未执行，或未知写动作自动重试 |
| 审批决定 | 云端授权记录 | 有权限入口提交决定 | 接受客户端裸 `approved=true` |
| 模型用量和计费 | 云端计量记录 | RunResult 引用摘要 | Desktop/Web 自算并覆盖 |
| 会话消息主写 | ConversationRepository | 各入口读取统一视图 | Web、Desktop、渠道各写一套不同终态规则 |
| 页面 loading、草稿、当前选中项 | 客户端 | 本地管理 | 作为 Run 是否运行或完成的依据 |
| 完成通知是否存在/已读 | NotificationRepository | SSE/Push 投递，客户端确认已读 | 仅靠 Toast 或内存红点 |

### 4.2 Run 状态

```text
queued → running → completed
              ├→ queued                # lease 丢失且可证明安全恢复
              ├→ waiting_approval → running
              ├→ waiting_device   → running
              ├→ waiting_tool     → running
              ├→ waiting_clarification → running
              ├→ reconcile_required → running / failed / cancelled
              ├→ failed
              └→ cancelled
```

迁移期允许内部复用既有枚举并通过映射输出规范状态。每个 `waiting_*` 必须同时记录：

- `wait_kind`
- `wait_ref` 或等待对象
- `resume_condition`
- `deadline_at`
- `timeout_policy`
- 创建等待时的 Run version/fence

`reconcile_required` 适用于外部动作结果不确定，例如进程或网络在写动作完成边界中断。它不是可直接重试的 `failed`。

`interrupted` **不是对外 Run status**。worker 丢失且无法继续时使用
`failed(error_class=worker_interrupted)`；可证明没有副作用或可安全恢复时，记录
`recovery_reason=worker_interrupted` 后回到 `queued`；效果未知时进入
`reconcile_required`。

自动 `running → queued` reclaim 只用于可证明安全恢复的 worker/进程中断，不与模型重试、
工具 attempt 重试共用计数。Run 保存 `automatic_reclaim_count`、`last_recovery_reason` 和
`next_eligible_at`：初始默认最多自动 reclaim 3 次，使用 5 秒、30 秒、120 秒并带 jitter
的退避；每次回迁在校验旧 fence 后原子递增计数。第 4 次需要 reclaim 时收敛为
`failed(error_class=worker_interrupted, retry_exhausted=true)`，不得继续 queued↔running
乒乓。人工确认重试创建引用原 Run 的新 Run，不清零旧 Run 计数或覆盖旧执行证据。

### 4.2.1 内部执行 phase 与对外状态映射

| 内部 phase | 对外 Run status | 约束 |
|------------|-----------------|------|
| accepted / claimable / recovering | queued | recovering 只能在安全恢复判定后使用 |
| executing_model / executing_tool | running | 必须持有有效 owner/lease/fence |
| finalizing | running | 最终消息、计费、terminal snapshot 与 notification outbox 未全部提交前不得公开 completed |
| waiting_approval / waiting_device / waiting_tool / waiting_clarification | 同名 waiting 状态 | 必须有 WaitDescriptor、版本和恢复条件 |

内部 phase 可以扩展诊断细节，但不得绕过规范状态机或被客户端当成新的终态。

### 4.2.2 Run deadline、Attempt timeout 与执行活性

lease 只证明 worker 暂时拥有执行权，不能证明模型或工具仍会返回。首期冻结三层上限：

| 上限 | 初始默认 | 计时范围 | 超时动作 |
|------|----------|----------|----------|
| Run lifecycle deadline | accepted 后 72 小时 | 包含 queued/waiting，作为异常兜底 | 按当前 phase/effect 收敛，不再继续新步骤 |
| Active execution budget | 累计 30 分钟 | 只累计 running/executing，不含 queued/waiting | 安全点 failed；未知副作用则 reconcile_required |
| ModelAttempt timeout | 每次 5 分钟 | 单次模型请求 | 记录用量未知/timeout，按有界策略重试或 failed |
| ToolInvocation timeout | 默认每次 10 分钟 | 单次工具执行 | 依据 effect/dispatch evidence failed 或 reconcile_required |

工具可以在注册清单中声明更短或经评审的更长 timeout，但必须同时声明 effect、是否支持取消、
幂等键和 timeout policy；不得使用“无 timeout”。租户/Agent 可以在服务端策略允许范围内调整，
客户端不能任意延长。Phase 0 用现有耗时分布校准数值，但上线前必须存在非空默认值和硬上限。
`waiting_*` 仍使用各自 deadline；进入 waiting 后停止 active budget，绝不能靠续租维持等待。
finalization 使用独立持久化重试/告警阈值，超时只能重试结算，不能重跑模型或工具。

Run/Attempt 至少记录 `lifecycle_deadline_at`、`active_execution_ms`、`current_step_started_at`、
`current_attempt_deadline_at`、`progress_heartbeat_at` 和 `last_progress_kind`。worker 的 supervisor
初始建议每 15 秒检查并续 60 秒 lease，但只有同时满足以下条件才续租：执行 task/event loop
仍可被 supervisor 观测、当前 attempt 尚未超过 deadline、fence 仍有效。禁止独立定时器在不检查
attempt 状态的情况下无限续租。模型/HTTP 请求“仍未返回”只允许续到该 attempt deadline；到期
后请求协作取消、停止续租并持久记录 timeout。同步阻塞导致 supervisor 无法运行时，heartbeat
自然停止，由 lease reaper 接管。

超时收敛规则：尚未派发外部写动作，或只读/明确幂等动作可证明未生效时，按有界 attempt
策略重试，耗尽后 `failed(error_class=model_timeout|tool_timeout|run_deadline_exceeded)`；写动作已
派发、取消结果未知、线程/进程可能仍在执行或 effect 未声明时进入 `reconcile_required`。旧 fence
返回的迟到结果只能作为核验证据接纳，不能直接把 Run 推进为 completed。

进入任一 `waiting_*` 前必须提交执行检查点并释放 owner/lease，旧 fence 随即失效，等待期间
不占 worker。`continue_tool`、`resolve_approval` 或澄清回复必须在 Session 队列锁内校验
`wait_ref + expected_run_version + deadline_at`，成功后把 Run 变为 claimable/queued；background
模式由 runner 重新 claim 并取得新 fence，inline 测试模式也必须取得新的临时 claim/fence，
不得沿用等待前的执行身份。正常等待恢复不增加 `automatic_reclaim_count`。

首期默认等待策略由服务端配置、客户端无权延长：clarification 24 小时后
`failed(clarification_timeout)`；approval 48 小时后 `failed(approval_timeout)`；device 24 小时后
`failed(device_timeout)`；tool 30 分钟后，若写动作可能已发出或 effect 未知则进入
`reconcile_required`，否则 `failed(tool_timeout)`。具体租户策略可缩短或经受信管理端覆盖。
等待已经超时并终结后到达的回复不得复活旧 Run：系统将它接纳为引用原 Run 的新 submit，
按 Session 队列规则执行并向用户说明原等待已过期。

### 4.3 取消语义

- `POST cancel` 成功只表示取消命令已接纳，写入 `cancel_requested_at/requested_by/reason`。
- queued 且没有副作用的 Run 可直接收敛为 cancelled。
- running 由 worker 在安全点检查取消，并请求当前工具协作停止。
- 已执行或结果未知的副作用不能伪装为未执行；根据 effect/evidence 进入 completed、cancelled-with-effects 或 reconcile_required 的规范映射。
- 撤销权限后立即禁止新副作用，但保留已发生动作的记录和证据。

## 5. 目标应用服务

建议形成以下语义接口；可以拆为 application service、repository 和 executor，不强制一个巨型类：

```python
submit(command, trusted_context)       -> RunAccepted
append_input(command, trusted_context) -> CommandAccepted
get_run(run_id, trusted_context)       -> RunSnapshot
list_session_runs(session_ref, active_only, cursor, context) -> RunPage
subscribe(run_id, after_seq, context)  -> AsyncIterator[RunEvent]
continue_tool(result, device_context)  -> CommandAccepted
reply_to_clarification(command, trusted_context) -> CommandAccepted
resolve_approval(decision, context)    -> CommandAccepted
cancel(command, trusted_context)       -> CommandAccepted
```

内部另设仅供后台执行器调用的边界：

```python
claim(worker_id, lease_seconds)        -> ClaimedRun | None
execute(claimed_run)                   -> None
renew_lease(run_id, fence)             -> bool
finish(run_id, fence, result)          -> bool
```

### 5.1 推荐目录结构

按项目“新增平台通用模块放入 `src/services/<name>/`”的约定，推荐使用
`src/services/agent_run/`，而不是带连字符、不能作为 Python package 导入的
`agent-run`，也不继续把新生命周期代码堆入 `src/main.py` 或 `src/core/agent.py`。

```text
src/services/agent_run/
  __init__.py             # 只导出稳定接口，不做重初始化
  application.py          # submit/get/subscribe/cancel/continue/resolve 用例
  commands.py             # 规范 command 与 trusted context
  models.py               # RunSnapshot/RunResult/RunEvent/WaitDescriptor
  states.py               # 状态、合法迁移与终态判断
  repository.py           # Run/command/event 的 PostgreSQL 仓储接口
  executor.py             # 调用现有 Agent 核心并产出规范事件/结果
  conversation.py         # ConversationRepository 适配接口
  event_store.py          # seq 分配、补拉、短期流事件
  worker.py               # claim/lease/renew/execute/recover
  worker_main.py          # 专用 agent-runner 进程入口，可多副本部署
  notifications.py        # terminal/waiting-user → 持久通知/outbox
  adapters/
    web.py                # ChatRequest/SSE 与规范协议互转
    device.py             # 受信 Runtime 的 Invocation 结果接纳边界
    channel.py            # Channel Gateway：统一 receipt/batch/wait/delivery 用例
    channel_ports.py      # ingress/auth/identity/capability/delivery 端口
```

数据库 DDL 初始化仍按项目既有 `src/db/database.py` 初始化链登记；不能在 package
import 时自动建表。HTTP 路由可继续留在现有入口，或后续增加薄路由模块，但只能调用
application service，不能重新实现状态推进和消息主写。

现有核心边界保持：

```text
AgentApplication / RunService    负责用例、状态、持久化和恢复
          ↓
Agent executor adapter           把现有 Agent 事件规范化
          ↓
src/core/agent.py                负责模型-工具循环本身
          ↓
ToolExecutor / Execution Fabric  负责具体工具执行
```

这里的“剥离 Agent”是把**应用生命周期和传输/UI 责任**从 Agent 核心中剥离，不是
第一阶段重写或复制 `src/core/agent.py`。核心 Agent 不应知道 SSE 文本、Vue 路由、
Toast、桌面 UI 状态、通知投递或 Run 表；RunService 也不应复制模型循环和工具调度。

### 5.2 Submit 契约

```typescript
interface SessionRef {
  readonly kind: 'web' | 'channel'
  readonly id: string
}

interface SubmitRunCommand {
  readonly command_id: string
  readonly session_ref: SessionRef
  readonly input: ReadonlyArray<
    | { readonly type: 'text'; readonly text: string }
    | { readonly type: 'artifact'; readonly artifact_id: string }
  >
  readonly requested_agent?: string
  readonly requested_device_id?: string
}

interface RunAccepted {
  readonly command_id: string
  readonly run_id: string
  readonly status: 'accepted' | 'already_accepted'
  readonly snapshot_seq: number
}
```

`trusted_context` 必须从认证边界构造，至少包含可信 tenant/user、授权结果、计费主体和 surface。客户端提交的 tenant、user、工具全集或最终计费账号不能进入可信快照。
入口 adapter 必须根据已认证资源重新构造或核验 typed `session_ref`；客户端仅把 `kind` 改成
`channel` 不能取得渠道 Session 权限，也不能改变锁锚点。

`accepted` 的前提是接纳记录与幂等键已可靠提交到 PostgreSQL。仅加入内存 map、`asyncio.create_task` 或 Redis Pub/Sub 不算接纳成功。

### 5.2.1 运行中补充输入契约

“补充当前任务”是显式命令，不从普通聊天文本猜测：

```typescript
interface AppendRunInputCommand {
  readonly command_id: string
  readonly run_id: string
  readonly expected_run_version: number
  readonly mode: 'supplement' | 'correction'
  readonly input: SubmitRunCommand['input']
}
```

服务端校验租户、Session 归属、Run 非终态和版本后，持久化输入及 `run.input_appended` 事件。
`supplement` 表示增加材料，`correction` 表示纠正后续方向；二者都只影响尚未开始的后续步骤，
并在事件中保留 mode。runner 只在安全检查点消费：下一次模型请求前，或当前工具结果已接纳后再进入下一次模型请求；
它不能改变已发出的模型请求、撤回已派发工具或覆盖已发生的副作用。每个补充命令独立幂等，
保存“已接纳/已消费”的顺序和输入引用。若 Run 在补充被消费前已经终结，服务端自动创建
引用原 Run 的 queued 新 Run，不能静默丢弃。Web 提供明确的“补充当前任务”动作；微信客服
只通过确定性的回复绑定、结构化入口或约定指令触发，其他普通消息仍作为新 Run 排队。
渠道回调进入 Run 前的 2 秒多消息合并属于入站聚合，不等同于运行中补充。
UI 必须把“停止”“补充/纠正当前任务”“发起下一任务”做成三个不同动作，不能让一个输入框
隐式承担三种生命周期语义。

append 接纳时只保存 command/input 审计记录，不立即把它当作已进入对话上下文的 user message。
runner 在安全检查点消费时，以 `append command_id` 为幂等键，在同一事务追加规范 user message、
标记 command consumed 并写 `run.input_consumed`；这样工具/user/assistant 配对与真实模型上下文一致。
如果终态竞态转为新 Run，该输入只按新 Run 的普通 submit 规则写一次，不得同时出现在旧、新会话回合。

### 5.2.2 澄清回复的唯一应用用例

澄清恢复只有一个应用层命令和实现：

```typescript
interface ReplyToClarificationCommand {
  readonly command_id: string
  readonly run_id: string
  readonly wait_ref: string
  readonly expected_run_version: number
  readonly input: SubmitRunCommand['input']
}
```

`AgentApplication.reply_to_clarification(command, trusted_context)` 在 typed Session 锁内完成授权、
wait kind、deadline、版本和 CAS 消费校验。Web 传输层可暂时接收
`in_reply_to_wait_ref + expected_run_version` 字段，但必须转换为该命令，不能进入普通 submit
实现；Channel Gateway 的 `acceptClarificationReply` 与 Desktop AgentClient 的
`reply_to_clarification` 也只做 envelope/身份转换后调用同一用例。三个表面共用 command id
幂等、迟到回复转新 submit、消息落库和事件规则，禁止各自实现 wait-resume 状态推进。

### 5.3 RunResult

```text
RunResult
  run_id
  status
  content_blocks
  artifacts
  pending_action
  usage_reference
  error_code
  error_class
  retryable
```

`content_blocks` 允许 text/image/file 等结构化结果。Web 可展示丰富卡片，渠道可降级纯文本，未来 Desktop 可生成自己的展示投影；所有端共享同一结果来源。

### 5.4 ConversationRepository

Phase 1 只统一接口和主写责任，不立即合并物理表：

```python
load_context(session_ref, principal, policy) -> list[CanonicalMessage]
append_run_input(run, input_blocks)           -> MessageRefs
commit_run_result(run, result, tool_messages) -> MessageRefs
```

必须冻结：

- user/tool/assistant 消息配对规则；
- `tool_call_id` 的稳定引用；
- 附件和 Artifact 的授权解析；
- Run 终态与最终 assistant 消息的原子关系；
- 重试和恢复时不重复写消息的幂等键。
- `session_ref` 必须携带不可混淆的 `session_kind=web|channel`（或等价命名空间），不能靠
  session id 字符串格式猜测来源。ConversationRepository 按类型分别适配
  `chat_sessions/chat_messages` 与 `channel_sessions/channel_messages`；物理双表迁移期保留，
  但加载上下文、输入/工具/最终消息配对和 Run 引用遵守同一接口与幂等规则。
- `run_id`、`delivery_id` 等授权查询、幂等和运维检索所需引用必须使用可索引稳定列或关联表，
  不得只塞入不可可靠查询的 metadata 文本；source message ids 优先关联 durable receipt。

### 5.4.1 匿名执行与 `/api/chat` transient 语义

匿名/演示入口不再长期保留 legacy Agent loop。它迁到 AgentApplication 的
`execution_policy=transient_inline`：只复用规范化输入、Agent executor、RunResult 和安全工具
清单，不创建 durable `agent_runs`，不占持久 Session 队列，不支持后台继续、subscribe/list、
持久 cancel、通知或等待续接；SSE 断开可以终止该 transient execution。它使用无副作用/显式
匿名 allowlist，不能执行写工具。UI 必须明确“离开页面不会继续”。SSE 编码传输可以保留，
但 legacy 生命周期、消息主写和 Agent 构造代码必须删除。

已认证非流式 `POST /api/chat` 在 Phase 0 真实调用方盘点后只允许二选一：

1. 无生产调用方：Phase 1 标记 deprecated 和观测告警，Phase 4 返回 410 并删除入口；
2. 确需保留：作为同步兼容 adapter 调用普通 durable `submit`，使用
   `conversation_policy=transient`（不写 `chat_messages`）并等待终态。它仍写 Run、Trace、
   `chat_records(source_type=api_chat_transient)` 和实际用量/扣费；若关联持久 Web Session，必须
   占用同一 Session active/queue 槽位。它可按 run_id cancel/subscribe/get，默认 session 列表不
   展示已经终结的 transient 历史，只有 `include_transient=true` 或直接 run_id 查询可见；但
   active/queued transient 必须出现在 `active_only`/队列投影中并标记 `visibility=transient`，避免
   形成看不见的 Session 阻塞者。HTTP 等待超时返回 202 + run_id，
   不取消后台 Run。

现状 `/api/chat` 未写 `chat_records` 属计量缺口，保留入口时从完整新版本一次上线日起补齐，
并同步计费说明；历史请求缺少可靠依据，不自动追溯扣费。禁止出现“不写会话消息”等同于
“不计量”或“不进入 Session 并发控制”的解释。

### 5.5 过程提示、澄清与等待成为一等事件

统一 Run 后，过程信息不再只是当前 SSE 回调，而是带 Run identity、顺序和恢复语义的
规范事件。建议最小事件集合：

```text
run.accepted
run.started
run.status_changed
assistant.verbose            # 面向用户的简短过程提示，可覆盖/限流
assistant.response_delta     # 流式文本增量，可按保留策略截断
tool.started
tool.progress
tool.completed
clarification.required       # 需要用户补充信息
approval.required            # 需要授权主体决定
device.required              # 需要设备上线、选择或恢复
reconcile.required           # 外部效果不确定，需要核验
run.completed
run.failed
run.cancel_requested
run.cancelled
run.input_appended          # 显式补充已可靠接纳
run.input_consumed          # runner 已在安全检查点吸收补充
```

每个事件至少携带：

```text
run_id, seq, type, occurred_at, audience, visibility,
payload, persistence_class, correlation_refs
```

- `audience/visibility` 区分用户可见提示、调试进度和敏感内部事件，避免把工具参数或
  推理内容直接推到 UI。
- `assistant.verbose` 复用现有 verbose 的确定性文案和限流，但由 Run event store
  分配 seq，因此切页和重连后仍可恢复当前提示。
- `clarification.required` 不只是显示一行问题，同时把 Run 置为带
  `WaitDescriptor` 的等待状态；用户回复必须引用 run/version/wait_ref，避免回复到
  已过期等待。
- `approval.required` 与普通 clarification 分开，因为它需要权限校验、决定记录和
  可能的失效期限。
- 渠道、Web 和 Desktop 消费同一事件，由 adapter 决定显示为卡片、纯文本、Toast
  或等待回复；它们不再各自猜测 Agent 当前阶段。
- 最终状态始终以 Run snapshot 为准，事件消费者不能因为漏到一条 `completed`
  广播就自行改状态。

## 6. 数据与事件策略

### 6.1 先映射，后决定最小新表

Phase 0 完成字段级映射后再冻结 DDL。预期最小缺口通常包括：

- `agent_runs`：Run 权威快照、状态、版本、`run_origin`、租约、自动 reclaim 计数/下次可领取时间、等待、取消请求、排队阻塞和结果引用。
  同时保存 lifecycle deadline、累计 active execution、当前 step/attempt deadline 和 progress heartbeat。
- `agent_run_commands`：`command_id` 幂等接纳和请求摘要。默认由结构化
  `input_snapshot`（JSONB 或 DDL 评审确认的等价字段）保存有序输入块；artifact 块保存稳定
  `artifact_id/storage_ref`、mime、size、digest、retention，不保存临时下载 URL、凭据或裸路径。
  P0 不因附件快照另建一套全局 Artifact 表，除非现有产物存储无法提供稳定引用或所需保留期。
- `agent_run_events`：每 Run 单调 `seq`、可重放的规范事件或关键状态事件。
- `user_notifications`：平台级持久通知。
- 发布不增加旧 Web/渠道在途执行 claim、入站缓冲表或按租户新旧路由表；新 Run 正常运行所需的
  claim/lease/fence 仍由 `agent_runs` 等权威记录持久化。
- 生产渠道可靠性缺口：优先复用现有去重/消息/outbox 表；若现有表无法表达，则最小增加或扩展
  `channel_inbound_receipts`、`channel_ingress_batches`、`channel_wait_bindings`、
  `channel_delivery_outbox`、`channel_processing_claims/projector_cursors`、
  `channel_reply_budgets`。所有对象通过稳定
  `run_id/session_ref/receipt_id/delivery_id` 关联。Phase 0 字段映射后决定复用还是新建，但
  durable receipt、batch、projector cursor、预算和 delivery outbox 语义不得省略。

ToolInvocation、ModelAttempt、Artifact 第一阶段优先给现有表增加稳定 Run 引用或建立关联表，不同时另建四套替代表。

数据库遵循项目既有约束：不使用外键、触发器、存储过程或复杂视图。跨表完整性使用稳定
引用、复合唯一索引和 Python 事务校验；`chat_messages` 不含 `tenant_id`，任何 Run→消息
读取必须按 typed session_ref 经对应 `chat_sessions` 或 `channel_sessions` 归属校验，不允许只按
message/session id 直接返回。

### 6.2 事件分层与唯一顺序

| 层级 | 内容 | 权威与保留 |
|------|------|------------|
| Run snapshot event | accepted/status/wait/terminal/cancel requested | `agent_run_events`，`persistence_class=durable` |
| 用户可见流事件 | verbose/progress/response delta/tool card | 同一 `agent_run_events`；关键事件 durable，delta 可标为 ephemeral |
| 最终结果 | content_blocks/artifacts/pending_action/error | PostgreSQL/ConversationRepository 权威 |
| 实时广播 | “有新 seq”轻通知 | Redis Pub/Sub，仅延迟优化，不承担恢复 |

采用单一 PostgreSQL 事件拓扑：一个 Run 的所有规范事件，包括 response delta，都只由
`agent_run_events` 的同一个分配器产生单调 `seq`。Redis Stream 不作为第二份规范事件源；
Redis Pub/Sub 只发“有新 seq”的唤醒。delta 不按 token 逐行写，必须合并成有界片段，初始建议
按 200ms 或 2KB 任一阈值 flush，最终阈值由 Phase 0 压测冻结。订阅端收到唤醒后按
`after_seq` 拉取；丢广播、跨 worker 或重连不会拆裂 seq。ephemeral delta 在 Run 终态可靠
落库 15 分钟后允许清理，用户关键事件初始保留 30 天；游标落入已清理缺口时明确返回
`reset_required + snapshot_seq`，客户端按 snapshot/最终消息恢复，不伪装成连续重放。

事件保留清理由现有单例 `background_runner` 注册有界批次 maintenance job 负责，不放入可多副本
`agent-runner`，避免每个执行副本同时扫描。Phase 2 DDL 评审必须同时冻结清理索引、批次、
失败重试、合法 hold/审计保留例外和监控；没有清理 owner 的事件表不得上线自动增长。

## 7. 分阶段实施

### Phase 0：语义冻结与契约测试骨架

交付：

1. 输出当前 Web、旧 D1、渠道、browser continuation、`session_tasks`、`local_tools` 的字段级对象与真实调用方映射；旧 D1 只做删除/迁移审计。
2. 冻结 Run status、event envelope、RunResult、error class、等待描述和 command 契约；明确
   Web 字段、Channel Gateway 与 Desktop 方法都映射到唯一 `reply_to_clarification` 应用用例。
   同时冻结跨 Python/TypeScript 的正式 Agent/Device wire 协议源、生成命令、兼容样例和 owner；
   建议目标目录为 `contracts/agent-run/` 与 `contracts/device-runtime/`，最终路径经 Phase 0 评审
   确认。旧 `contracts/desktop-agent` D1 保持 frozen，只作删除审计，Desktop 只能消费生成类型。
3. 冻结云端 RunService 与 Device Runtime 的所有权边界；停止旧 D1 协议扩展，不改线上 Web 执行路径。
4. 建立 fake model、fake tool、内存 repository 的应用服务契约测试骨架，用于可重复的异常、竞态
   和恢复测试；同时以现有本地容器的真实模型、真实工具、测试数据库与隔离账号做代表性端到端
   联调，不能用 fake 通过代替真实链路验收。测试必须通过参数化
   fixture 或共享 contract mixin 与仓储实现解耦。Phase 2 PostgreSQL repository 完成后原样复跑
   同一套契约，并另测事务隔离、行锁、唯一约束冲突和 `SKIP LOCKED`，不能用内存实现通过
   代替数据库实现验证。
5. 决定最小 DDL；评审多租户复合约束、索引、保留期限和敏感字段。
   同时冻结 `run_origin = run_service_inline | run_service_background`；
   配置只使用 `agent_runs.runner_mode = inline | background`，禁止再使用同名不同义的
   `execution_mode`。生产最终只启用 background；冻结新版本的接纳/暂停开关和审计，
   不创建租户/账号级新旧执行路由或发布专用切换代次。
6. 完成所有可进入后台 Run 的 server tool effect、timeout、取消和恢复清单；没有明确
   effect/幂等/timeout/恢复策略的工具不得进入 background Run。冻结 §4.2.2 的默认值、硬上限、
   supervisor heartbeat/lease 条件和各 timeout 收敛结果。校准必须覆盖不同 Agent/工具类型、
   单轮与多轮工具链的 active execution P50/P95/P99、最长合法样本和超时异常样本；尤其验证
   30 分钟初始 active budget 不会误杀合法长任务。需要放宽时使用服务端分级 policy/硬上限，
   不能删除默认 deadline 或允许客户端无限延长。
7. 冻结 browser 工具为 `not_migrated`，不把其进程内 owner/Hub 约束带入新架构。
8. 冻结附件接纳规则：submit 时由服务端解析并校验 file_id，默认在
   `agent_run_commands.input_snapshot`（或 DDL 评审确认的等价结构化字段）保存有序输入块及可信
   artifact/storage 引用、mime、size、digest/retention；不保存临时下载 URL、凭据或裸路径，
   后台执行不依赖 24 小时 Redis 元数据。
9. 冻结匿名策略：durable Run 只接受可信 tenant/user；匿名/演示入口使用 §5.4.1
   `transient_inline`，不写 tenant_id 空值 Run、不执行写工具，也不保留 legacy Agent loop。
   完成 `/api/chat` 调用方和现状漏计量审计，冻结“退场”或“durable transient adapter”二选一。
10. 完成 browser 真实调用量审计；根据结果冻结用户提示、替代路径或客户停用决议和整体发布门槛，不以 registry 是否暴露推断实际无人使用。
11. 选择单一 PostgreSQL 事件拓扑，冻结 per-Run seq 分配、delta 合并阈值、清理缺口和 reset 契约。
12. 冻结四类 WaitDescriptor 的默认期限/超时结果、释放 lease 与新 claim/fence 的恢复规则。
13. 盘点全部已售和生产启用渠道、租户与适配器；微信客服列为 P0 必选，每个生产渠道必须在整体发布前迁入统一链路；确实无法迁入的，只有先完成经业务批准的客户迁移/停用及影响告知，并更新生产范围清单，才可继续发布。
    对每个 Connector 逐一核实平台消息 sequence/msgid/时间戳与重试语义，冻结 ordering token、
    comparator、reorder grace 和 confidence；单独核实 wecom_kf 咨询开始/结束的可靠平台字段，
    没有可靠字段时冻结持久 epoch 状态机、版本和预算归组迁移规则。
14. 冻结 typed `session_ref`、Web/渠道 Session 锁锚点、ConversationRepository 双表分派、
    Connector 代码落位以及 `channel_messages`/关联表的稳定 Run/Delivery/Receipt 引用。
15. 冻结 clarification binding 与 approval 授权边界、三个 channel worker 角色及微信客服持久
    回复预算；逐平台记录停机期间回调失败/平台重试的用户预期，但不把可靠重试/补拉作为发版前置。
16. 冻结 projector 落后于 ephemeral 清理边界时的 snapshot/reset/skip 语义，并形成低峰、
    全部生产渠道同窗停机、发布失败处理与用户自行重试 SOP；同时冻结 §3.6 Web
    旧请求失败提示、恢复后新 Run 路由和已有 Session 历史兼容读取。
17. 输出 Phase 4 前端页面/组件/API client 级工作清单、依赖和估时，至少覆盖 submit→subscribe、
    RunProjection、三动作输入、等待/审批、刷新发现、队列/blocked、维护提示、通知入口、稳定
    command_id 的本地保存与断网重试；不得用一句“前端适配”代替排期。
18. 冻结生产观测交付协议：开发者提交只读 evidence query pack；运维/设计 owner 优先在注明
    快照时间的生产仿真副本执行，数据过旧/缺失时在生产只读副本或受控只读会话执行，并交付
    脱敏聚合结果和覆盖证明。默认观察最近 90 天或最大可用窗口；原始客户内容、附件、凭据和
    完整个人标识不得进入仓库。开发者默认不持有生产凭据。本计划发起人/生产环境负责人是
    accountable owner，B00 kickoff 时必须登记实际执行人和备份人；query pack 默认 2 个工作日
    回传结果或缺口说明，并同时提供 `source_snapshot_at/restore_completed_at`。证据缺失时采用保守策略：工具
    background deny、未知生产渠道阻断整体发布、`/api/chat` 按存在调用方保留兼容适配，不能
    用“未查到”替代“没有使用”。
19. 冻结压测与 Web 浏览器级验收方案：压测使用扩展后的 `docker-compose.test.yml` 隔离栈、
    专用数据库/Redis、至少 2 个 runner 和确定性 fake model/tool，由仓库内可复现脚本产生负载；
    workload 取自生产基线，不直接在生产造压。Web 复用现有 Playwright，覆盖刷新恢复、三动作、
    排队/等待/维护提示/reset；操作系统通知和 agent3 停机/恢复另留人工验收记录。

退出条件：团队能对任一现有对象回答“它是不是 Run、谁能改状态、如何关联工具/消息/产物”。

### Phase 1：抽取统一 AgentApplication，仍保持同步执行

交付：

1. 从 `src/main.py` 提取输入规范化、Agent 解析、核心执行、事件收集、RunResult 构建与历史持久化。
2. `src/main.py` 只保留鉴权、ChatRequest 转换和 SSE 编码。
3. 按 §5.4.1 处理非流式 `/api/chat`：无调用方则开始退场；保留时通过 durable submit +
   transient conversation policy 执行，并补齐 chat_records/计量、Session 队列和 202+run_id。
   匿名入口改用受限 `transient_inline`，不再调用 legacy Agent 生命周期。
4. browser continuation 标记未迁移；生产旧版保持原行为直到整体切换，新应用服务不为 browser 复制进程内兼容逻辑。
5. 保留请求内执行只用于本地/隔离环境的应用服务验证；用固定输入、录制回归和假工具对照旧行为。
   Phase 1 代码只合入 P0 功能分支，不做生产租户 allowlist、shadow 投影或按租户回退开关。
6. 旧 D1 仅完成依赖审计和冻结，不在本阶段建设 outcome 兼容映射；桌面接入由独立 P1 规划负责。

退出条件：不用浏览器/Electron，用假模型和假工具验证提交、工具调用、最终结果、持久化查询和取消；Web 新 adapter 在隔离环境进入统一应用服务，生产旧版不变。

### Phase 2：Run 持久化、事件游标与取消请求

交付：

1. 落地最小 Run/command/event DDL、repository 和 tenant/user 授权查询。
2. `submit` 在返回 accepted 前可靠写入；同 command digest 返回 already_accepted，不同 digest 返回冲突。
3. 规范事件分配单调 seq；增加 `get_run`、`subscribe(after_seq)`。
4. 取消改为 command + `cancel_requested_at`，前端断开不再调用取消。
5. `chat_records`/用量与扣费、最终消息、Artifact 引用、Run terminal snapshot 和 notification outbox 在同一可幂等 finalization 边界收敛；持久化失败只重试结算，不重跑模型或工具。内部 `finalizing` 对外仍不是 completed。Phase 2–4 只落地 outbox schema/写入能力，`agent_notifications.write_outbox` 默认关闭；Phase 5 消费者就绪后与写入开关同批启用，只通知启用后的新状态变化。历史回放必须使用显式 backfill，不把无消费者积压当作默认迁移方案。
6. Phase 2 在隔离环境验证 inline Run，不作为生产版本。可执行 Run 写 `run_origin=run_service_inline|run_service_background` 及 owner/lease/fence；reaper 只扫描具有执行 owner 且 lease 过期的可执行 Run。可安全恢复者按 §4.2 的计数、上限与退避回到 queued；不可继续或 reclaim 耗尽者收敛为 `failed(error_class=worker_interrupted)` 或 `reconcile_required`，绝不遗留永久 running，也不在证据不足时重跑工具。不建设 legacy shadow projection。
7. 旧 `/api/chat/{session_id}/cancel` 增加认证、租户和会话归属校验，并映射到明确的 active run；无法唯一定位时返回冲突。新 cancel 只接受可信 context。
8. Session submit、active Run finalization 和队列晋升共用同一个 typed Session 队列锁：
   `session_kind=web` 锁对应 `chat_sessions` 归属行，`session_kind=channel` 锁对应
   `channel_sessions` 归属行，两种 id 命名空间严禁混用；持久渠道消息进入 Gateway 前必须先
   解析或创建授权的 `channel_sessions` 行。只有匿名 `transient_inline` 等确实没有持久 Session
   的执行不进入 durable 队列；已认证 `/api/chat` 的 durable transient Run 只要关联
   `chat_sessions`，就必须进入同一队列。active Run 成功、失败或取消的 finalization 事务中，按 `accepted_at, run_id` 选择
   最早的 live queued Run，清除其 `blocked_by_run_id` 并写 `run.queue_promoted` 关键事件；
   同一事务把更晚 queued Run 重新绑定到新 active Run。已带取消请求或已终结的候选在有界
   批次内跳过，直到晋升一个可执行 Run 或队列为空；超过批次由幂等 queue-maintenance job
   继续。finalization 未提交时不得提前领取下一个 Run。

退出条件：断开订阅后重连能够补齐状态；重复提交/终态回调不会重复执行或重复写消息。

### Phase 3：专用 agent-runner 持久执行

交付：

1. 增加 `src.services.agent_run.worker_main` 和独立 `aid-agent-runner` 部署服务；现有 `background_runner` 继续承载 scheduler/recap/poller 等既有任务。
2. PostgreSQL `FOR UPDATE SKIP LOCKED` 领取，写 worker、fence、lease；由执行 supervisor 按
   §4.2.2 在 task 可观测且 attempt 未超时时续租，禁止无条件后台续租。
3. API 容器只接纳和订阅，不再承载 Agent loop。
4. worker 崩溃后：无外部副作用或可证明未开始者按策略重新领取；结果未知者进入 reconcile_required。
5. 停机时停止领取、短暂 drain；未完成 Run 靠租约恢复，不靠进程内 task 集合。
6. 附件路径、tenant context、agent release/配置快照足以在后台进程重建执行上下文。
7. worker 从第一天支持多副本安全领取和 fence；staging 先用 1 副本压测，生产副本数按内存/吞吐压测决定，目标允许 ≥2，不把单容器写入协议假设。每个副本设置显式 Run 并发上限和优雅 drain。
8. browser 工具不迁入 runner；相关工具在能力矩阵中 fail closed，未来按 §3.4 独立重构。
9. 所有模型和 server tool 调用都经过统一 timeout wrapper；阻塞调用隔离到可监督线程/进程，
   超时后停止接受旧 fence 结果，并按 effect 进入 failed 或 reconcile_required。
10. 增加 `aid-agent-runner` compose/生产编排、liveness/readiness、依赖检查、并发/内存配置、
    日志指标、优雅停止和扩缩容 runbook。`agent_runs.runner_claim_enabled=false` 只停止领取新
    queued Run，已领取任务按 drain deadline 收敛；不得靠直接 kill 容器作为正常停机流程。

退出条件：刷新/关页不取消；重启 API 不影响；杀死 `agent-runner` 后 Run 可恢复或明确收敛，不静默消失。

### Phase 4：Web 订阅、等待续接与历史主写收敛

交付：

1. Web 变为 `submit → subscribe`；会话切换继续沿用现有 per-session UI，但运行状态改读 Run snapshot。
2. 新增 `GET run`、带 `after_seq` 的事件订阅，以及按已授权 `session_ref` 查询 active/最近 Run 的分页接口（例如 `GET /runs?session_ref=&active=`）。页面刷新只有 session_id 时，先发现 Run，再按 snapshot_seq 订阅；查询必须经 Session 归属校验。
   实现 B01 已冻结的 Session create/list/get、canonical Conversation cursor、Artifact upload/metadata/
   download authorization、Device directory 和按 `command_id + request_digest` 查询 command receipt
   的受权客户端表面；这些接口供 Web/Desktop 共用，不要求把现有物理表合并，也不得在 B11
   重新发明不兼容 DTO。
3. `waiting_tool/waiting_approval/waiting_device/waiting_clarification` 使用统一等待对象和恢复命令；
   只有有效 `wait_ref + expected_run_version` 且调用者通过对应 wait kind 授权才能恢复原 Run。
   外部渠道用户仅有 clarification 回复权，不因持有 wait_ref 获得 approval/tool/device 权限。
4. 增加显式 `append_input`；Web 以明确动作补充当前 Run，普通消息不猜测为打断。补充在安全检查点生效，终态竞态按 §5.2.1 自动转为新 queued Run。
5. ConversationRepository 成为最终消息唯一主写入口；移除 `main.py` 和已迁移 continuation 的重复拼装逻辑。
6. 同一 Session 单 active Run；后续普通 submit 在 Session 队列锁内可靠排队并记录 `blocked_by_run_id`，不由文本猜测是续接还是新任务。active Run 的任一终态（包括 cancelled）按 Phase 2.8 原子晋升恰好一个后继；重复 finalization 或 maintenance 不得重复晋升。
7. 配套更新停止按钮、运行中提示和发布说明，明确“离开/关闭页面不会取消；必须显式停止；停止只在安全边界生效”。该用户可感知变更不得只写在后端发布记录中。
8. 按 §3.6 在隔离环境演练停机发布：旧 SSE 轮允许失败，不生成新 Run；新版本恢复后已有
   Session 的新消息也只能进入 Run。Web 可展示维护提示，服务完全不可达时也应有清晰的失败/
   重新发送体验，不要求旧轮排空。
9. Phase 4 的完整候选版删除认证/匿名入口对 legacy Agent 执行循环的调用；保留的 SSE 只做
   AgentApplication 事件编码。若 `/api/chat` 决议为退场，本阶段返回 410 并移除内部调用方。
10. 前端按 Phase 0.17 清单交付：API client 在一次用户意图生成并持久保存 command_id，收到
    accepted/already_accepted 前所有网络重试复用同一 id；RunProjection 支持 snapshot/after_seq/
    reset；会话页展示 queued、blocked_by、维护提示、cancel requested 与 terminal；输入区明确
    “停止”“补充/纠正”“新任务”，刷新后从 list_session_runs 恢复而非依赖内存 loading。

退出条件：隔离集成环境中 Web 的流式与非流式入口产生一致 RunResult 和终态；传输层不再拥有生命周期代码；多标签重复提交不会生成第二个 Run。agent3 联合验收是全部 Phase 收口后的发布门禁。

### Phase 5：持久通知与浏览器通知

交付按三层递进：

1. **站内持久通知**：Run 进入 terminal/waiting-user 时创建 `user_notifications`，支持列表、未读数、已读和 deep link。
2. **全局实时通知**：App 根层建立用户级 SSE；Redis 只发布 notification id/new seq，前端再拉持久记录。离开聊天页仍显示 Toast。
3. **页面隐藏系统通知**：本站标签仍存活且获得权限时，使用 Notification API；前台可见时只显示 Toast，避免重复。
4. **Web Push（P2 可选）**：如后续要求本站标签全部关闭、只要浏览器运行仍能通知，再独立增加 Service Worker、Push Subscription、VAPID 和订阅失效回收；不作为本轮 P0 完成门槛。

通知正文默认不包含敏感任务结果，只含“已完成/需要处理”和授权 deep link。

`session_task_notifications` 与平台 `user_notifications` 在迁移期长期并存：前者仍是
session_tasks 的场景内记录，后者服务 Run。前端通知中心先由聚合 service 读取两者，
Phase 6 再评估是否把旧表通过 adapter 投影到平台通知；不在 Phase 5 迁表。

退出条件：完成通知 exactly-once 创建；SSE 断开后可补拉；多标签页不重复弹；未授权通知时站内中心仍可用。

### Phase 6：生产第三方渠道通过统一 Channel Gateway 接入 Run

本阶段是依赖编号，不要求等 Phase 5 全部完成才开工：Phase 0 冻结渠道契约，Phase 2 的
Run/事件/Session 锁 DDL 稳定后即可与 Phase 3～5 并行开发 Gateway、receipt、batch、worker、
outbox 和 Connector；微信客服可随 Phase 4 在隔离环境联调，但生产切换必须等完整 P0 候选版
的 Web、全部生产渠道与 Phase 5 站内通知一并验收通过。

交付：

1. 先实现单一 `ChannelGateway`、规范 envelope/delivery、Connector 端口和参数化契约测试；
   Phase 0 清单中的企业微信客服/应用/个人账号 RPA、钉钉、飞书等所有已售、生产启用渠道
   均接入这个接口，不为任何渠道建立旁路。`wecom_kf` 是首个强制真机验收样板。
2. 每个 Connector 只处理本渠道的验签/认证、解密、原始消息转换、ACK 和实际发送；完成后
   输出同一种 `CanonicalChannelEnvelope` 和 `TrustedChannelPrincipal`。公共 Gateway 及其后续
   组件不再感知平台协议差异。
3. Connector/Gateway 先将入站消息写入 durable receipt，再在平台要求的时限内 ACK。receipt 使用
   `(tenant_id, channel, channel_account/config_id, platform_message_id)` 唯一键去重；平台重试
   只能返回已有接纳结果，不得产生第二个 Run。
4. 合并窗口和 reorder grace 由 capability/profile 配置；微信客服保留现有 2 秒多消息合并体验，但从进程内等待
   改为持久 `channel_ingress_batch`：按
   Connector 已冻结的 ordering token 排序封口，记录 basis/confidence 和全部 source message ids，
   只由封口批次提交一个 Run。平台未提供权威 sequence 时不得标记为“原始平台顺序”。批次中断
   可恢复，不能因 API 进程重启漏掉半批消息。
5. Gateway 统一完成可信身份到 `session_ref` 的映射并调用 AgentApplication；所有 Connector
   均不得直接构造 Agent、推进 Run、拼装终态或形成第二套历史主写。
6. Run event projector 根据 capability profile 做统一降级。过程提示复用现有
   `send_status_message` 语义及各渠道预算；微信客服遵守总回复额度 5、始终
   为 final 保留 1 次的既有规则；高频 Run 事件需合并/限流，不能把每个 progress 都发给用户。
7. `channel_wait_binding` 只允许绑定 `waiting_clarification`：外部终端用户的下一条有效消息
   在同一 channel Session 锁内优先 CAS 消费 active binding，并以 `run_id + wait_ref +
   expected_run_version` 恢复 Run；binding 被消费、过期或不存在后，消息才按 §9.1 作为普通
   submit 排队。`waiting_approval` 绝不绑定外部客户消息，只能由 Web 管理端或其他具备明确
   权限的受信入口调用 `resolve_approval`；裸文本“同意”没有审批效力。
8. Run finalization 原子创建渠道无关的 `channel_delivery_outbox`。独立 delivery worker 以稳定
   `delivery_id/request_id` 发送最终回复；超时和平台 5xx 只重试投递，不重跑 Agent、工具、
   计费或消息主写。永久失败进入可查询/告警/人工重投状态，不能把 Run 改回 running。
9. 最终渠道消息由 ConversationRepository 单一主写，并保存 run_id、source message ids、
   delivery id 和平台回执；迁移期禁止新旧路径同时发送或同时写同一 assistant 消息。
10. Web 管理端能按已授权渠道 Session 查看 active/最近 Run、状态、最终结果和投递状态，
   并处理 approval/clarification；离开客服会话不会令 Run 或等待事实消失。
11. 开发批次先在隔离环境对全部生产 Connector 的测试账号回放重复回调、乱序、进程重启、
    平台超时、额度耗尽和发送失败；完整候选版在 agent3 联合复验，不建设 tenant/channel account allowlist 灰度或 shadow 投影。
12. 按 §3.5.1 在隔离环境演练全部生产渠道停机发布：旧 task 允许失败，不转换为新 Run；
    恢复后新到达消息由 Gateway 接纳。平台可能在服务恢复后重试停机期间的回调，按新架构
    去重处理；不以平台必须重试或额外入站缓冲作为发版门槛。
13. 部署 §3.5.2 的 `aid-channel-worker`，分别验证 batch sealer、event projector 和 delivery
    worker 的多副本领取、会话内顺序、崩溃恢复、积压告警和优雅 drain。
14. 微信客服持久回复预算在 outbox 入队事务原子预占；进度、澄清和 final 全部使用同一预算
    账本，进程重启和多副本并发不能突破 total=5/final reserve=1。
15. 增加 `aid-channel-worker` compose/生产编排和 runbook：batch/projector/delivery 分角色健康
    检查、claim 开关、并发、依赖、扩缩容、优雅 drain、积压告警与回滚顺序。正常停机先关闭
    对应 claim 开关、等待 sending/lease 收敛，再停止容器。

`session_tasks`、recap、定时任务和营销自动化是否触发 Run 留在 P2：它们不阻塞已售生产
渠道迁移，也不能借 Phase 6 再复制一套 Agent 生命周期。

退出条件：公共 Gateway 契约测试只编写一套并对全部生产 Connector 参数化运行；所有生产
渠道在隔离环境完成“接纳 → 去重/可选合并 → Run 执行/等待 → finalization → 最终回复 outbox → 平台
回执”联调，完整候选版在 agent3 联合验收。微信客服完成真机基准验收；重复入站不重复运行，发送失败不重跑 Agent，Web
可查同一 Run。新增渠道无需修改 AgentApplication 即可接入。

## 8. 关键技术选择

### 8.1 PostgreSQL 是 Run 权威，Redis 是加速层

- PostgreSQL 保存接纳、状态版本、租约、等待、取消、终态和结果引用。
- Redis Pub/Sub 负责低延迟唤醒，不提供可恢复保证。
- 所有规范事件统一写 PostgreSQL `agent_run_events` 并使用每 Run 单一 seq；Redis 不分配第二套游标。
- 不复用 recap 当前 `LPOP → create_task` 作为 Run 队列，因为消费后进程崩溃会丢任务。

事件保留初始建议：Run snapshot/最终结果按会话与企业数据策略保留；用户可见关键事件
默认 30 天；response delta 只保留到最终结果可靠落库后的短窗口（建议 15 分钟）；现有
Trace/模型诊断继续沿用其 90 天基线。正式期限在 Phase 0 数据评审中按敏感等级冻结。

### 8.2 一个业务状态机，一个设备执行状态机

- RunService 决定 Run 业务状态和下一步是否可继续。
- 本地 Runtime/Execution Fabric 决定设备 invocation 的 claim/running/result/unknown。
- 本地回传受信结果，云端据此推进 Run；本地 session store 不独立决定另一个业务后续动作。

### 8.3 先统一应用用例，不先统一所有物理数据

- Phase 1 允许现有入口继续读取各自兼容视图，但只有 ConversationRepository 可以成为新主写边界。
- 新的 ConversationRepository 先收敛主写接口。
- 历史表迁移、消息格式升级和 Run 后台化分别提交，避免不可审查的大爆炸变更。

## 9. 一次发布、故障收敛与观测

P0 只形成一个完整生产候选版。Phase 0～6 在功能分支和隔离环境实施，agent3 全链路验收通过后
合并 master，再执行一次停机发布。发版期间所有入口不可用，旧版在途任务允许失败；恢复后
所有新消息统一进入 Agent Run。没有发布专用的旧任务排空、入站缓冲、切换代次、
`tenant_agent_run_routes`、`channel_accounts.run_mode` 或生产 `shadow`。

建议全局配置/持久控制项（精确命名在 Phase 0 冻结）：

```text
agent_runs.admission_enabled=false
agent_runs.runner_mode=background       # inline 仅作隔离测试
agent_runs.runner_claim_enabled=false
agent_channels.batch_claim_enabled=false
agent_channels.projector_claim_enabled=false
agent_channels.delivery_claim_enabled=false
agent_notifications.write_outbox=false
agent_notifications.consume=false
```

这些开关只服务**新版本正常运维**，不是新旧架构灰度/迁移协议。停机部署完成且健康检查通过后
启用新接纳与领取；关闭 `admission_enabled` 只影响其后新请求，不取消已经 accepted 的新 Run/
receipt，也不隐式回退旧代码。claim 开关只阻止新领取，readiness 反映暂停状态，liveness 仍
反映进程健康。完整发版步骤见开发执行计划 §12。

关键指标：

- submit acceptance latency、queue wait、run duration；
- running lease expiry、reclaim、reconcile_required 比率；
- lifecycle deadline、active execution budget、model/tool timeout、progress heartbeat age 与无条件续租违规；
- automatic reclaim 次数、耗尽量、backoff 等待时间和毒 Run 比率；
- 重复 command、重复终态、事件 seq 缺口；
- SSE disconnect 后完成率；
- cancel acceptance/cancel terminal latency；
- notification creation/delivery/read latency；
- channel inbound ACK latency、dedup 命中、batch seal 延迟、Run 接纳率；
- channel delivery outbox 延迟、重试、永久失败、回复预算和重复发送量；
- 计划停机时长、服务恢复时间、恢复后各渠道新消息接纳/投递失败量；
- batch sealer/projector/delivery worker 的 lease expiry、积压、重复抑制与会话顺序冲突；
- 回复预算预占冲突、final reserve 使用、unknown 占用和人工补偿量；
- 隔离环境旧行为对照差异、旧版在途失败量和用户手动重试量；
- token/credit 与 Run/ModelAttempt 关联完整率。

故障原则：旧版停机中断的任务不自动重跑，用户可在恢复后自行重新提交；历史写操作可能
已有外部效果，重试前应提示核验。新版本一旦 accepted Run/receipt，仍按新架构的持久与
幂等语义处理；若新版本发布后故障，停止新接纳并修复兼容版本，不把新 Run 交给旧版重跑。

### 9.1 会话并发策略

- 首期每个 Session 同时最多一个 active Run，但第二次普通 submit 不返回 409，而是可靠
  接纳为新的 queued Run，并以 `blocked_by_run_id` 串行等待；不同 Session 可并发。
- 如果 active Run 正在 `waiting_approval/waiting_tool/waiting_device/waiting_clarification`，普通新消息仍排队。
- Web 只有带有效 `in_reply_to_wait_ref + expected_run_version` 的传输输入才能被 adapter 转换为
  §5.2.2 `ReplyToClarificationCommand`；clarification 回复与创建新 Run 的意图不能靠文本猜测。
  外部渠道普通用户只允许通过 active clarification binding 恢复 clarification；approval 必须
  由受信管理入口调用 `resolve_approval`，即使携带或猜中 wait_ref 也不得越权。
- 多标签重复提交由 command idempotency 去重。
- submit 与 finalization 都必须先取得同一 Session 队列锁；终态提交原子晋升最早 live queued Run。
  active Run 被取消也继续队列；连续取消/终结候选由有界循环跳过，queue-maintenance job
  幂等兜底，不能留下有任务但无 active Run 的静默卡死队列。
- 取消 blocked/queued Run 必须取得同一个 Session 队列锁，在事务内标记 cancelled、修复
  `blocked_by_run_id` 链并在需要时晋升后继，避免与 finalization 晋升竞态。
- 已超时等待的迟到回复按新的 submit 接纳并引用原 Run；不得报错后丢弃，也不得恢复旧 fence。

## 10. 验收矩阵

### 10.1 无 UI 核心验收

- 假模型 + 假工具完成 `submit → tool → final → persist → get_run`。
- 同 command 重试返回同一 run；请求摘要不同返回冲突。
- 工具等待后 `continue_tool` 恢复同一 Run，不创建第二 Run。
- Web `in_reply_to_wait_ref`、Channel `acceptClarificationReply`、Desktop
  `reply_to_clarification` 对同一 command 重放时只消费一次 binding、只恢复一个 Run，并产生
  等价事件/消息；代码路径最终调用同一应用服务方法。
- waiting 状态不持有 lease；恢复取得新 claim/fence 且不增加 automatic reclaim 计数。
- `append_input` 重试不重复吸收输入；在模型/工具进行中只于安全检查点生效；终态竞态自动转为新 Run。
- 四类 wait 超时均按默认策略收敛；迟到回复成为引用旧 Run 的新 submit，不丢消息、不复活旧 Run。
- cancel 请求与 cancelled 终态分离；不可中断工具保留实际 effect。
- `reconcile_required` 不自动重试写动作。

### 10.2 生命周期验收

- 关闭 Web SSE、刷新页面、切换会话均不改变 Run 状态。
- API worker 重启后订阅可恢复。
- agent3 演练停机发版：旧 SSE 轮允许失败，停机期间请求不被接纳；恢复后新 Session 和已有
  Session 的新消息只产生新 Run，不误标旧请求为新 Run，也不自动重跑旧任务。
- 旧 Session 遗留的 responding/pending/browse suspension 状态不会被当作新 Run 的 active/waiting，
  不阻塞恢复后新消息；旧历史仍可授权读取。
- 对旧版中断且外部效果未知的写操作，系统不自动重试；用户可在确认风险后自行重新提交。
- 页面刷新后仅凭已授权 session_id 能发现 active/最近 Run，并从 snapshot_seq 恢复卡片。
- `agent-runner` 在模型调用前/工具执行中/结果落库前被杀死，各阶段按证据安全收敛。
- 真实模型和现有 Runtime/Provider 在测试账号上完成短、长 Run 的本地工具链；设备结果断网后
  只补传结果、不重复执行，Run/Invocation 关联可查询。P0 不依赖桌面客户端先发布。
- fake model、只读工具和未知 effect 写工具分别永久 hang：attempt deadline 到期后不再续租；
  前两者按有界重试/failed 收敛，未知写动作进入 reconcile_required，没有 Run 永久 running。
- active execution 累计超过 30 分钟以及 lifecycle 超过 72 小时均按策略终结；queued/waiting 不
  误计 active budget，waiting 仍受自身 deadline 约束。
- 连续制造 4 次可安全恢复的 worker 中断时，前三次按退避 reclaim，第 4 次收敛为 `failed(worker_interrupted, retry_exhausted)`；不会无限占用 worker。
- Run completed 与最终 assistant 消息不存在“一个成功、一个缺失”的不一致。
- active Run completed/failed/cancelled 后原子晋升恰好一个 queued Run；重复 finalization、连续取消和 maintenance 重放都不重复晋升、不留下断链。

### 10.3 权限与受信结果验收

- 生产 Web 的流式与非流式入口经过同一 AgentApplication，并产生等价规范结果。
- 保留的认证 `/api/chat` 占持久 Session 队列、写 `api_chat_transient` 计量且 HTTP 超时返回
  202/run_id；terminal 历史默认隐藏但 active/queued 投影必须可见，直接授权查询/取消/订阅可用。
  退场分支则无生产调用并返回 410。
- 匿名 transient_inline 断线可终止、不产生 durable Run/通知/等待、不执行写工具；代码中不再
  存在匿名专用 legacy Agent loop。
- `trusted_context` 中 tenant/user 不接受客户端覆盖。
- 跨租户 run_id、event、artifact、notification 查询全部 fail-closed。
- `continue_tool` 校验 device identity、invocation、run version/fence 和结果摘要。
- Web Session 事务只锁授权 `chat_sessions` 行，渠道 Session 事务只锁授权 `channel_sessions`
  行；同值 id 不能跨命名空间查询、排队或取消。
- 外部渠道用户即使发送“同意/批准”也不能解决 `waiting_approval`；只有具备授权的受信管理
  入口能调用 `resolve_approval`，审计记录包含决定人和策略版本。

### 10.4 通知验收

- 用户在其他会话、其他站内页面、页面隐藏时分别收到正确层级通知。
- 多标签页只显示一次系统提示，通知中心记录不丢。
- Phase 2–4 写入开关关闭时不产生无消费者 outbox 积压；Phase 5 同批开启写入和消费后只通知新变化，显式 backfill 才允许历史回放。
- 浏览器 Notification 权限未授予或被撤销时，站内通知仍可补拉；通知正文不泄露敏感结果。Web Push 验收留到 P2 专题。

### 10.5 计费与终态验收

- Run 接纳时沿用余额硬阻断，但它不替代实际用量结算。
- `chat_records`、用量/扣费、最终消息、Run terminal snapshot 和 notification outbox
  必须进入一个可幂等重试的 finalization 边界；现有 `ChatRecordDB.create` 的原子扣费
  语义必须保留，不能先公开 completed 再补计费。
- finalization 遇到基础设施错误时只重试持久化/扣费事务，不重新调用模型或工具；Run
  保持内部 finalizing phase（对外仍 running），超过运维阈值告警并人工处理。
- 任何永久业务异常都必须保留已产生的 usage/result/effect 记录，不能通过把 Run 标成
  cancelled 来抹掉费用或外部副作用。

### 10.6 统一 Channel Gateway 与生产渠道验收

- 同一微信客服 platform message 重放 10 次，只形成一个 receipt、一个 sealed batch 和一个 Run。
- 2 秒合并窗口内多条消息按冻结的 ordering token 进入同一 Run；有权威 platform sequence 时
  保持平台顺序，没有时明确标记 best-effort；API 进程在窗口内重启后仍能确定性封口执行。
- 每个生产 Connector 用重复 msgid、乱序到达、相同 timestamp 和缺失 sequence 样例验证冻结的
  comparator；结果确定且可审计，best-effort 情况明确标记，不随 worker/进程变化。
- wecom_kf 新咨询、继续咨询、结束后重开和边界事件缺失分别产生预期 consultation epoch；预算
  不因进程重启重置，也不因算法版本升级重组旧账本。
- 回调在平台时限内 ACK；Agent 耗时不占用回调连接，用户离开微信会话不影响执行。
- waiting clarification 的回复恢复正确 Run；过期回复产生新 Run；相邻普通消息可靠排队。
- verbose/progress 合并限流，微信客服总回复额度不超过 5 且 final 始终保留 1 次。
- final 已落库但发送接口超时/5xx 时，只重试同一 delivery；模型、工具、扣费、最终消息均不重复。
- 平台返回不确定结果时以稳定 request/delivery id 查询或人工核验，不盲发第二条回复。
- 新版本正常运行中，每条已接纳渠道消息只有一个执行者和一个发送者；不存在生产 shadow 发送路径。
- Web 管理端按租户授权发现同一渠道 Session 的 Run、等待、结果和 delivery 状态；跨租户查询失败。
- Phase 0 清单中每个生产启用渠道通过同等契约测试；专属额度/格式限制由 adapter 附加测试覆盖。
- 同一组 Gateway contract tests 对每个 Connector 参数化运行；公共测试不得复制成渠道专属版本。
- 新增 fake channel 只实现 Connector/profile 即可完成 submit、wait reply、final delivery，全程不修改 AgentApplication。
- RunService、Agent 和 ConversationRepository 中不存在以渠道名推进生命周期的条件分支。
- active clarification binding 在 channel Session 锁内优先且只能消费一次；approval/device/tool
  wait 均不能被普通外部消息消费，binding 过期后的消息作为新 submit。
- agent3 停机发布演练覆盖全部生产渠道：停机期间回调可失败，恢复后重新发送或平台重试
  的消息按新 Gateway 去重；旧版未完成 task 不被自动迁入或重新执行。
- 分别杀死 batch sealer、event projector、delivery worker 后，lease 到期可恢复；batch/event/
  delivery 不重复，同一 channel Session 的发送顺序不乱，不同 Session 可并发。
- 模拟平台 HTTP 调用阻塞 10 秒时，channel Session 数据库行锁已释放；同会话 submit、Run
  finalization 和 outbox 入队可完成，但下一 delivery 不越过 sending/unknown 头项发送。
- 两个 delivery worker 并发争抢微信客服最后一个 progress 额度时最多一个成功；始终保留 final
  额度。相同 delivery 重试不重复扣预算，unknown 不释放，进程重启后计数不回退。
- projector 停摆超过 ephemeral 保留窗后恢复：已终结 Run 不补发旧 progress、不重复 final，
  cursor 跳到终态；未终结 Run 按 reset_required/snapshot 恢复后只处理现存新事件。
- 停机发布 SOP 覆盖全部生产账号；明确告知停机期间消息可能失败、平台重试不保证、恢复后
  需要用户/运营重新发送。不能将发版容许的旧消息失败解释成新版本日常 receipt 可丢失。
- append_input 接纳时只存在审计 command；安全检查点消费时才幂等追加一次 user message；终态
  竞态转新 Run 时旧 Run 不出现重复 user message。

## 11. 建议的提交边界

每个边界独立评审、测试和回滚，不混合实施：

1. 契约、fake repository 与 contract tests。
2. ConversationRepository + RunResult 提取，Web 新 adapter 在隔离环境接入。
3. Run DDL/repository/tenant tests。
4. Channel Gateway/envelope/delivery 契约、正常运行期的 durable receipt/admission 骨架。
5. 可游标事件和显式取消命令。
6. 幂等 finalization：计费、最终消息、Run terminal、notification/channel outbox。
7. `agent-runner` claim/lease/fence/recovery；并行建设 `aid-channel-worker` 三类角色。
8. Web submit/subscribe 切换和刷新恢复。
9. approval/clarification/device waiting 状态统一；browser continuation 留待独立重构。
10. 微信客服 Connector、持久预算、停机/恢复演练和 Web 查询；先隔离账号真机联调，最终随全部渠道在 agent3 联合验收。
11. 站内通知、全局 SSE 与 Notification API；它可与步骤 7～10 并行，Web Push 留到 P2。
12. 其余生产 Connector 参数化接入，收口各自 legacy 执行/发送路径。

## 12. 2026-09-22 开发前评审决议

| 议题 | 决议 |
|------|------|
| 首期入口 | 生产 Web + 已售生产渠道；微信客服 `wecom_kf` 是 P0 必选。匿名入口不进入 durable Run；旧 D1 只做依赖审计，桌面另行 P1 规划。 |
| 事件拓扑 | 采用方案 A：全部规范事件统一写 `agent_run_events` 并分配每 Run 单一 seq；delta 合并写入并标记 ephemeral，Redis 只唤醒。 |
| Waiting 与 lease | waiting 不占 lease，恢复命令在 Session 锁内校验 wait/version/deadline，重新 claim 新 fence；正常恢复不计 reclaim。默认 clarification 24h、approval 48h、device 24h、tool 30m。 |
| Browser | 标记旧架构不兼容且首期不迁移；不为进程内 owner/Hub 妥协。整体切换前审计生产真实调用量，存在使用时先完成替代或客户停用/迁移决议与提示；后续按 Execution Fabric 独立重构。 |
| Desktop 边界 | 旧 D1 不承担产品兼容；新 Desktop 使用 Agent API，Runtime 使用 Device API，二者都不能拥有第二套 Run 状态机。 |
| 状态映射 | `waiting_clarification` 是规范等待态；`interrupted` 只是 recovery reason/error_class；`finalizing` 是对外 running 的内部 phase。 |
| 对照验证 | 固定输入/录制回归在隔离环境比较旧版与新应用服务，不建设生产 shadow Run、双执行或双消息主写。 |
| 自动 reclaim | 仅安全恢复使用独立计数；默认最多 3 次，5s/30s/120s+jitter；耗尽后 failed(worker_interrupted)，人工重试创建新 Run。 |
| Phase 2 孤儿 | Phase 2 不作为生产默认；inline 也必须有 lease/reaper，过期安全收敛，不等待 Phase 3。 |
| Runner | 新建可多副本 `aid-agent-runner`，不横向扩容混有单例任务的现有 background_runner。 |
| Deadline/活性 | 默认 lifecycle 72h、active execution 30m、model attempt 5m、tool attempt 10m；续租必须绑定 supervisor 活性和 attempt deadline，未知写效果超时进入 reconcile_required。 |
| 同 Session 并发 | 单 active Run，后续 submit 可靠排队；wait 回复通过 wait_ref/version 显式恢复。 |
| Session 锁锚点 | typed session_ref；Web 锁 `chat_sessions`，渠道锁 `channel_sessions`，禁止两种 session id 混用；ConversationRepository 保留双表 adapter。 |
| 队列晋升 | Session 队列锁下由 active Run finalization 原子晋升最早 live queued Run；取消同样晋升，连续无效候选由有界循环和 maintenance job 收敛。 |
| 排队取消 | 取消 blocked/queued Run 也必须取得 Session 队列锁，并在事务内修复阻塞链/晋升。 |
| 迟到回复 | wait 已超时终结后，回复作为引用旧 Run 的新 submit，不丢弃、不复活旧 Run。 |
| 运行中补充 | `append_input` 是显式、幂等、带版本命令；安全检查点吸收，终态竞态自动转新 queued Run。 |
| 生产渠道 | 所有渠道共用唯一 Channel Gateway/envelope/delivery、durable receipt、可配置 batch、wait binding 与 delivery outbox；差异封装在 Connector/profile，发送重试永不重跑 Agent。 |
| 渠道等待授权 | 外部消息只能消费 active clarification binding；approval 仅允许受信管理入口解决，客户文本无审批效力。 |
| 渠道切换 | 全部生产渠道随 Web 停机发版；旧 task 和停机期间回调允许失败，不做排空/缓冲/自动重跑；恢复后所有新消息进入 Gateway/Run。 |
| 渠道后台 | 独立多副本 `aid-channel-worker` 承载 batch sealer、event projector、delivery worker；均有 lease/fence/幂等。Session 行锁只做短事务顺序/CAS，平台 HTTP 锁外执行，后项不越过 sending/unknown 头项。 |
| 微信回复预算 | 数据库按咨询会话持久记账，outbox 入队时原子预占；total=5、final reserve=1，unknown 不释放。 |
| Append 消息 | 接纳时只写审计 command；安全检查点消费时才幂等追加 user message；转新 Run 时只写新 Run。 |
| 渠道排期 | Phase 2 DDL 稳定后与 Phase 3～5 并行建设；Web、微信客服、全部生产渠道和通知在 agent3 联合验收后才整体上线。 |
| 渠道切换 SOP | 低峰、全部生产账号同窗停机；预告不可用和旧任务失败风险，恢复后用户/运营可重新发送。 |
| Projector 清理缺口 | 落后于 ephemeral 清理时读取 snapshot；terminal 直接跳过旧 progress，非终态按 reset_required 恢复，绝不重跑 Run。 |
| Web 切换 | 停掉旧版并部署完整新版本；旧 SSE 轮可失败，恢复后新旧 Session 的新消息都进入 Run。 |
| 命名 | 配置使用 `agent_runs.runner_mode`；Run 行使用 `run_origin`，禁止复用 `execution_mode` 表达不同枚举。 |
| 发布控制 | 新版本 `admission_enabled`/claim 开关是正常运维控制，不需要发布专用切换代次或租户/账号级新旧执行路由。 |
| 澄清命令 | Web 字段、Channel `acceptClarificationReply`、Desktop `reply_to_clarification` 全部映射唯一 `ReplyToClarificationCommand`/应用服务实现。 |
| Delivery 锁 | Session 行锁内只做头项顺序、预算和 sending CAS；平台 HTTP 锁外执行，后项不越过 sending/unknown。 |
| 渠道平台事实 | Phase 0 逐 Connector 核实排序字段/重试语义；wecom_kf 咨询 epoch 必须来自可靠字段或版本化持久状态机。 |
| 前端交付 | Phase 0 输出页面/组件/API client 清单与估时；Phase 4 验收 command_id、三动作、恢复、队列、blocked 与维护提示。 |
| Worker 运维 | agent/channel worker 均需 compose、健康检查、claim 开关、优雅 drain、扩缩容/回滚 runbook；停容器不是正常暂停机制。 |
| 匿名入口 | 迁入受限 `transient_inline` AgentApplication；非 durable、断线可终止、无写工具，不长期保留 legacy loop。 |
| `/api/chat` | Phase 0 调用方审计后二选一：无生产调用则 Phase 4 退场；保留则 durable submit + transient messages，进入 Session 队列并写 `api_chat_transient` 计量，HTTP 超时返回 202/run_id。 |
| 数据关联 | 无外键/触发器；稳定引用、复合唯一索引、Python 事务与经 session 的租户校验。 |
| 附件 | submit 时解析为可信 artifact 快照并保证保留，不依赖 Redis 24h metadata。 |
| Cancel | 旧端点必须补鉴权/归属并映射 active Run；HTTP 成功只表示命令接纳。 |
| Run 发现 | 页面刷新只有 session_id 时，通过已授权 session_ref 查询 active/最近 Run，再按 snapshot_seq 恢复订阅。 |
| 通知 | `user_notifications` 与 `session_task_notifications` 先并存；Phase 2–4 写入门控关闭，Phase 5 写入与消费同批启用，历史回放只走显式 backfill。 |
| 计费 | 纳入幂等 finalization；失败只重试结算持久化，不重跑 Agent/工具。 |
| 事件保留 | delta 短期；关键用户事件初始 30 天；Trace 沿用 90 天。清理由单例 background_runner maintenance job 负责，Phase 2 同时冻结索引、批次和监控。 |
| 工具恢复 | Phase 0 完成 effect 清单；未知 effect 默认不可自动重试。 |
| 生产观测 | 开发者提交只读 evidence query pack；运维/设计 owner 在注明快照时间的仿真副本或生产只读边界执行并交付脱敏聚合结果。无证据时按 deny/兼容保留/整体发布阻断处理，不得推断“无人使用”。 |
| 发布策略 | Phase 1～6 只在功能分支/隔离环境实施；完整候选版在 agent3 联合验收，通过后合并 master 并一次生产发布。无 Phase 1 inline 生产灰度。 |
| 仓储契约测试 | 同一 repository contract suite 对 memory 与 PostgreSQL 实现复跑；PG 另测事务、锁、唯一冲突和 `SKIP LOCKED`。 |
| 压测与 Web E2E | 压测在 `docker-compose.test.yml` 隔离栈由仓库脚本造负载；Web 复用 Playwright 覆盖刷新、三动作、队列/等待/维护提示/reset，系统通知与 agent3 停机/恢复补人工记录。 |

### 12.1 仍需 Phase 0 以代码/实测定案的非阻塞细节

1. 旧 D1、Gateway、Runtime 和 CLI 的真实调用方清单，以及哪些能力迁入 Device API、哪些可删除。
2. agent-runner 单副本的真实内存/并发基线、生产副本数和每副本并发上限。
3. 现有 server tools 的 effect/幂等/可恢复清单和默认 deny 范围。
4. finalization 如何最小改造现有 `ChatRecordDB.create` 以共享事务连接。
5. 用户可见关键事件的敏感等级与最终保留期限。
