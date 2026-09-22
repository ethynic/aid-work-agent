# Agent Desktop P1 并行技术实现与开发执行计划

## 开发进度

| 阶段 | 内容 | P0 门禁 | 状态 | 完成记录 |
|------|------|---------|------|---------|
| 已有基础 | 独立 Desktop Shell、登录、安全 Bridge、Windows 包与 macOS 基础行为 | 无 | ✅ 完成（2026-08-14，DC0 复核） | 继承现有 A～C 交付记录，不代表旧 D1/Coordinator 继续有效 |
| DC0 | 新旧架构重基线、依赖审计与删除清单 | 可与 P0 Phase 0 并行 | 📋 待开发 | — |
| DC1 | AgentClientPort、RunProjection、Fake Cloud 与桌面 Run UI | P0 Gate G0 前可先做内部端口，G0 后接正式类型 | 📋 待开发 | — |
| DC2 | Main/Preload Agent Bridge、命令 journal、Runtime Bridge 与 Fake Device | P0 Gate G0 | 📋 待开发 | — |
| DC3 | 真实 Agent API、断线恢复、等待/通知联调 | P0 Gate G1/G2/G3 | 📋 待开发 | — |
| DC4 | Device API、本地只读/写工具纵向链与 unknown reconciliation | P0 Gate G2 + Device Gate | 📋 待开发 | — |
| DC5 | 双平台安全、安装包、故障注入、RC 与首发 | Desktop Core Gate | 📋 待开发 | — |

> 状态：待开发。本文同时承担桌面 P1 的技术实现说明和开发执行计划，不再另建一份覆盖相同内容的“纯技术实现文档”。
>
> 设计权威：[桌面客户端 P1 架构与约束基线](./plan-desktop-client-p1.md)。上位契约：[统一 Agent Run P0 设计基线](./plan-unified-agent-run-lifecycle.md)与[P0 开发执行计划](./plan-unified-agent-run-lifecycle-implementation.md)。
>
> 日期：2026-09-22

## 1. 核心结论

桌面客户端可以与 P0 并行，但只能按门禁并行：P0 决定云端 Run、事件、等待、授权和 Device API 语义；Desktop 可以提前完成壳、内部端口、纯投影、Fake Cloud、UI 和 Runtime 生命周期框架，不能提前发明另一套 wire protocol、状态机或本地 Agent loop。

本计划以以下决策替代旧桌面路线：

1. 云端 `RunService` 是业务 Run 的唯一权威；Desktop 不创建 `DEVICE_OWNED` 业务会话，不运行模型—工具决策循环。
2. Desktop Renderer/Main 通过 Agent API 提交命令、查询与订阅；受信 Runtime 独立通过 Device API claim/执行/回传 Invocation。
3. `clients/shared/agent-coordinator-core` 与旧 `contracts/desktop-agent` D1 在 DC0 起冻结，不继续增加 outcome/`agent/next` 能力。复用内容经过审计后迁到新 AgentClient/DeviceClient 边界，其余删除。
4. 已完成的 Electron Shell、safeStorage、窄 IPC、更新器、安全 scheme、窗口状态、独立 renderer 和打包门禁保留并回归验证。
5. `clients/agent-tool-runtime`、Provider、journal、资源锁、result outbox 是可复用设备执行基础，但必须适配 Device API；它们不能拥有 Run 状态或决定下一个模型步骤。
6. Browser Runtime 不进入桌面首发关键路径。
7. Desktop 技术首发不依赖 P0 Phase 6 渠道全部迁移；它依赖的是本文定义的 Desktop Core Gate。P0 仍拥有项目资源优先级，Desktop 不得阻塞 P0 Web/微信客服交付。

## 2. 当前代码资产与处置

| 现有资产 | 处置 | 原因 |
|----------|------|------|
| `frontend/desktop/` Shell、登录、启动状态 | 保留并扩展 | 已满足独立 renderer 与依赖边界 |
| `frontend/shared/auth`、`platform/contracts.ts` | 保留，按需提取 Agent 纯端口 | 已有测试与 Web/Desktop 边界 |
| `clients/agent-desktop/electron/` | 保留，增加 Agent Bridge、通知和 Runtime 管理 | Main 是系统能力和安全凭据边界 |
| `clients/agent-desktop` 构建/安全/更新/包验证 | 保留并回归，不在本 P1 重写更新系统 | 已有可执行和供应链门禁 |
| `clients/agent-tool-runtime` | 保留设备执行内核，替换旧云协议适配层 | Provider/journal/lock/outbox 可复用 |
| `clients/shared/local-tool-host-core` | 保留并逐步承接共用 Provider Host 逻辑 | Desktop managed Runtime 与 headless Runtime 共用 |
| `clients/shared/agent-coordinator-core` | 冻结；DC0 审计后删除或仅迁出纯 reducer | “本地业务 Coordinator”与新所有权冲突 |
| `contracts/desktop-agent` 旧 D1/outcome | 冻结，不再生成新正式协议；新契约可用后删除 | 未发布协议，无兼容义务且语义已过时 |
| `src/desktop_agent`、旧 Gateway 路由 | 只做调用方审计和删除/迁移 | 不作为 Desktop 首发兼容层 |
| Browser legacy/Hub/owner runtime | 不接入 | P0 首期明确 `not_migrated` |

旧路线中的“Local Agent Coordinator”“DEVICE_OWNED 会话本地权威”“客户端调用
`agent/next` 推进 outcome”不再是实施要求；旧文件已经删除，只能通过 Git 历史用于删除审计。

### 2.1 已完成工程基线

以下事实由旧 v2.4 计划合并而来，是 DC0 的复核输入，不代表旧 D1/Coordinator 继续有效：

| 日期 | 已完成内容 | 继承方式 |
|------|------------|----------|
| 2026-08-12 | `frontend/src` 原样迁移到 `frontend/web`；`@/` 继续指向 Web；Web/Desktop 构建和结构门禁建立 | 保留目录基线，禁止为 Desktop 批量改写稳定 Web import |
| 2026-08-12 | `frontend/desktop`、`frontend/shared`、独立 alias/tsconfig/Vitest、依赖边界和 contract harness 建立 | 继续扩展；把旧 D1 protocol 检查迁到新 Agent/Device 协议 |
| 2026-08-12 | 独立 Desktop renderer/router/Shell/登录、启动 reducer、离线/update/fatal UI、Shared auth/platform contract 完成 | 作为 DC1 起点，不重建第二套 Shell |
| 2026-08-12 | production artifact 对 Web/Portal 引用已有硬门禁，Electron Bridge v3、macOS driver 基础 contract 已建立 | DC0 复跑并修正为新 Agent Bridge/Runtime 边界 |
| 2026-08-14 | Windows Electron 43.1.0 可执行 smoke；macOS arm64 完成默认/720×500/150%/200%、Dock 恢复、`Command+Q` 和窗口拖动真机验收 | 作为历史证据保留；DC5 必须在最终产物和当前锁定版本上重新验收 |

旧记录中的测试计数只证明当时 commit，不作为当前分支绿色结论。D00 必须记录当前 HEAD、实际脚本、
可复现结果和已知债务；如果代码事实与表中记录不一致，以当前代码和复测证据为准。

## 3. P0/P1 并行门禁

### 3.1 门禁定义

| 门禁 | P0 必须交付 | Desktop 解锁能力 |
|------|-------------|------------------|
| G0 契约门禁 | P0 Phase 0 冻结 typed SessionRef、Run status/event、RunResult、等待、命令幂等、reset、授权与 Device API 边界 | 生成正式 TS 类型；AgentClient/RunProjection/DeviceClient 从 Fake 协议转正式 adapter |
| G1 持久 Run 门禁 | P0 Phase 2 提供 submit/get/list/subscribe、command persistence、finalization 和测试环境 | 连接真实测试租户，验证刷新、排队、补充、取消、澄清和 Artifact |
| G2 后台执行门禁 | P0 Phase 3 runner、lease/fence/deadline/recovery 可用 | 验证桌面关闭/断网、waiting_device、Runtime 重启、迟到结果和 unknown effect |
| G3 通知门禁 | P0 Phase 5 notification API/outbox/global subscription 可用 | 接入持久通知、系统通知与离页完成提醒 |
| Device Gate | Device API 端点、Runtime 身份、Invocation claim/fence、result/evidence/ACK 在测试环境可用 | 接真实 Runtime；此前只允许 Fake Device API |
| Desktop Core Gate | G0～G3 与 Device Gate 通过；P0 Phase 4 Agent API 已有稳定测试租户，开放 P0/P1 缺陷为 0 | 允许 Desktop RC/首发；P0 Phase 6 渠道迁移不是技术阻塞项 |

P0 发生兼容性变更时，由 P0 contract owner 先更新协议源、兼容样例和 changelog；Desktop 只消费生成类型，不在客户端先改 wire shape。没有共同协议版本时 fail-loud，不以 `any` 或忽略字段继续运行。

### 3.2 可立即并行与必须等待

可立即开始：

- DC0 的旧 D1/Coordinator/Runtime 依赖审计；
- Desktop Shell、窗口、菜单、托盘策略、设置与系统通知展示壳；
- `AgentClientPort`、`RunProjection`、`DeviceStatusPort` 等内部端口和 Fake 实现；
- Run 卡片、Session Rail、消息列表、三动作 Composer、等待/错误/离线 UI；
- Fake Cloud/Fake Device、契约测试 harness、golden event fixtures；
- Main/Preload 窄 Bridge、命令重试 journal、Runtime managed/external 生命周期框架；
- Windows/macOS 构建、安全和视觉回归。

必须等待门禁：

- G0 前不得固化 JSON/SSE 字段、错误码和状态枚举；
- G1 前不得声称真实 Run 恢复、取消或补充已联调；
- G2 前不得声称关闭桌面后长任务和租约恢复成立；
- Device Gate 前不得让 Runtime 向生产提交工具结果；
- G3 前不得把本地 toast 当作可靠通知；
- Desktop Core Gate 前不得对外发布正式客户端。

### 3.3 并行分支与 Worktree 纪律

P0 与桌面 P1 必须使用两个相互独立的 Git worktree，不能在同一个工作目录中交叉开发：

| 工作流 | 分支 | Worktree | Desktop 开发者权限 |
|--------|------|----------|--------------------|
| 统一 Agent Run P0 | `feature/unified-agent-run-p0` | 当前为 `/Users/ethynic/repos/aid-work-agent-run-p0`；路径变化时以 `git worktree list --porcelain` 为准 | 只读查看进度、已提交代码、计划和测试证据，不在此目录编辑、暂存、提交或执行会改写文件的命令 |
| Desktop P1 | 默认 `feature/desktop-client-p1` | 必须单独创建 Desktop 专用 worktree，实际路径在 D00 证据中登记 | 桌面开发、测试、提交和 CR 的唯一工作目录 |

并行协作规则：

1. Desktop 开工前先创建并确认自己的分支和 worktree；不得直接在主工作区或 P0 worktree 开发。
2. 需要了解 P0 进展时，可在 P0 worktree 查看本计划所依赖的 Phase 状态、`git log`、契约、测试和 evidence；不得把“目录里存在但尚未提交”的文件视为稳定接口。
3. P0/P1 之间只通过已提交 commit、正式生成协议、测试环境 API 和明确的门禁结论交付依赖；禁止跨 worktree 相对路径引用、手工复制生成文件，或直接修改对方未提交内容。
4. G0 等门禁到达后，Desktop 按团队约定把对应的已提交 P0 基线合入 Desktop 分支，再更新正式 adapter；不能长期从 P0 worktree 动态读取源码进行构建。
5. 每次 D 批次记录都要写明 Desktop worktree 路径、分支、HEAD，以及当时参考的 P0 HEAD/Gate；发现 P0 worktree 有未提交变化时，只记录风险并联系 P0 owner，不代为整理或提交。
6. 两个 worktree 分别运行自己的格式化、构建和测试。任何会批量改写仓库文件的命令只能在任务所属 worktree 中运行。

## 4. 目标进程与依赖结构

```text
Desktop Renderer
  DesktopSessionRail / Conversation / RunCard / Composer / NotificationCenter
  RunProjection（纯投影）
          │  frozen typed bridge
          ▼
Preload
  agentApi / platform / runtime / notifications
          │  schema validation + sender validation
          ▼
Electron Main
  AgentApiTransport + SubscriptionManager + PendingCommandJournal
  CredentialStore + SystemNotification + RuntimeProcessManager
          │                              │
          │ HTTPS/SSE                    │ authenticated local bridge
          ▼                              ▼
Cloud Agent API                    Managed/External Runtime
  RunService                         DeviceClient
                                     Provider Host / journal / lock / outbox
                                              │
                                              ▼
                                           Providers
```

依赖方向固定：

```text
frontend/desktop ─────> frontend/shared/agent-run
frontend/shared/agent-run -X-> Electron/Node/Web 页面
clients/agent-desktop ─> 生成协议类型 + local-tool-host-core
clients/agent-tool-runtime ─> Device API 类型 + local-tool-host-core
Renderer -X-> Provider / DeviceClient / 任意 Node API
Runtime -X-> AgentApplication 状态推进 / approval 决策
```

## 5. 建议代码落点

```text
frontend/shared/agent-run/
  ports.ts                    # AgentClientPort、DeviceStatusPort；非 wire source
  projection.ts               # RunProjection 纯 reducer
  presentation.ts             # 状态/等待/错误展示模型
  command.ts                  # command id 与本地 pending 状态纯逻辑
  fake.ts                     # Fake AgentClient/事件脚本
  __tests__/

frontend/desktop/features/agent/
  DesktopAgentPage.vue
  DesktopSessionRail.vue
  DesktopConversationPane.vue
  DesktopComposer.vue
  DesktopRunCard.vue
  DesktopWaitCard.vue
  DesktopNotificationCenter.vue
  useDesktopAgent.ts

clients/agent-desktop/electron/
  agentApiTransport.ts        # 精确 API origin、认证请求、错误映射
  subscriptionManager.ts      # authenticated fetch stream、重连、after_seq
  pendingCommandJournal.ts    # 原子、加密、有界重试 journal
  runtimeProcessManager.ts    # managed child / external attachment
  systemNotifications.ts
  preload.cts                 # 只暴露冻结的 typed bridge

clients/agent-tool-runtime/src/
  deviceApiClient.ts          # register/heartbeat/claim/result/evidence/ack
  invocationRunner.ts         # 保留执行职责，不推进 Run
  resultOutbox.ts             # ACK 前持久保存，重复回传幂等

contracts/
  agent-run/                  # 建议：P0 拥有的 Agent API/event 协议源
  device-runtime/             # 建议：Runtime 专用 Device API 协议源
  desktop-agent/              # 旧 D1，冻结，迁移完成后删除
```

`contracts/agent-run` 与 `contracts/device-runtime` 的最终路径由 P0 G0 冻结；在此之前 Desktop 只实现内部 Port/Fake，不创建临时 wire schema。正式类型由协议源生成，生成物禁止手改。

## 6. AgentClient 技术实现

### 6.1 内部端口

内部 Port 表达客户端需要什么，不定义服务端 JSON：

```typescript
interface AgentClientPort {
  submit(command: SubmitRunCommand): Promise<RunAccepted>
  appendInput(command: AppendRunInputCommand): Promise<CommandAccepted>
  replyToClarification(command: ReplyToClarificationCommand): Promise<CommandAccepted>
  cancel(command: CancelRunCommand): Promise<CommandAccepted>
  resolveApproval(command: ResolveApprovalCommand): Promise<CommandAccepted>
  getRun(runId: string): Promise<RunSnapshot>
  listSessionRuns(ref: SessionRef, options: RunListOptions): Promise<RunPage>
  subscribe(runId: string, afterSeq: number, signal: AbortSignal): AsyncIterable<RunStreamItem>
}
```

约束：

- Desktop 新建普通会话使用授权的 Web Session 命名空间，不新增 `session_kind=desktop`；查看渠道会话时仍使用 `kind=channel` 且服务端重新授权。
- submit/cancel/append/clarification/approval 使用稳定 `command_id`；查询可重试，命令不得因重连生成新 id。
- Main 持有持久凭据与真实网络 transport；Renderer 只通过窄 Bridge 收到已校验的 DTO 和事件。
- 现有 `credentials.hydrate()` 会把完整 token 带入 Renderer，只作为待迁移基线；D04 将登录/恢复后的 token 留在 Main，Renderer 只获得用户/tenant/session 摘要和 Agent Bridge，不得把旧行为固化为正式接口。
- SSE 使用支持 Authorization header 的 authenticated fetch stream，不依赖不能带 header 的原生 EventSource。
- 每个订阅以 `run_id + after_seq` 恢复；连接 ID、窗口 ID 和本地 task ID 都不能替代 Run identity。
- HTTP/SSE 解析先做 schema validation，再送给 Renderer；未知 major 版本进入 update-required，不把未知状态映射为 running。

### 6.2 PendingCommandJournal

为解决“请求已发出但 Renderer/Main 在 accepted 前崩溃”的不确定性，Main 维护独立 journal：

- 写网络前原子保存 command_id、command kind、session/run ref、canonical request digest、加密 payload、created_at 和 retry state；
- 只使用 safeStorage 包装的专用 journal key，文件权限最小化；不复用 credential key-value 文件，也不写明文客户内容；
- 收到 accepted/already_accepted 后保存 run_id 并删除敏感 payload；终态或明确拒绝后清理记录；
- 重启后只重放仍在期限内且 digest 一致的命令；服务端幂等负责返回原 Run；
- journal TTL、容量、损坏 safe mode 和退出 drain 在 DC1 冻结；损坏时不自动重发写命令。

Renderer 的“提交中”只是本地状态。只有服务端 accepted/snapshot 才能建立 Run 卡片。

## 7. RunProjection 技术实现

RunProjection 是纯 reducer，可被 Desktop/Web 共用，不发网络、不读 Electron、不持有凭据：

```text
输入：RunSnapshot | RunEvent | reset_required | local_pending
输出：RunViewState
```

必须满足：

1. 以 `run_id`、snapshot version 和 event seq 去重；旧 snapshot/旧事件不能回滚较新状态。
2. seq 连续则应用；发现缺口先补拉；服务端返回 reset 时清除旧 ephemeral progress，以新 snapshot 为基线。
3. terminal snapshot 优先于残留 progress；completed/failed/cancelled 不因迟到事件回到 running。
4. `cancel_requested` 与 `cancelled` 分开显示。
5. `waiting_clarification/approval/device/tool` 保留 wait_ref、deadline、权限和可执行动作；UI 不靠文案猜 wait kind。
6. `reconcile_required` 是独立阻断状态，不能显示成普通“重试”按钮。
7. queued/blocked_by 与 active Run 分开；Session Rail 能同时展示当前 active 和后续队列。
8. `assistant.response_delta`、verbose 和 tool progress 属 ephemeral；最终消息来自服务端 Conversation/RunResult，不由 Desktop 拼接落库。
9. local_pending、离线、重连中、草稿和当前选中项在单独 UI 层，不写回 Run status。

## 8. Runtime Bridge 与 DeviceClient

### 8.1 ownership

- 开发阶段先支持 external attachment，便于复用现有 `agent-tool-runtime`；
- Desktop 首发时，当前电脑执行使用 Main 管理的 managed child；远端/无 GUI 节点继续使用独立 headless Runtime；
- managed child 由 Main 启动、停止和 drain，使用一次性 bootstrap secret 建立受认证本地通道；禁止无认证 localhost 控制端口；
- ownership record 包含 runtime instance、PID、epoch、启动者和能力版本。Main 退出时只停止自己拥有的 child；external attachment 不随窗口关闭而结束。

### 8.2 DeviceClient

Runtime—not Renderer—实现：

```text
register / heartbeat / capabilities
claim_invocation / accept / reject
progress / result / evidence
result_outbox_ack / reconcile_report
```

每次结果绑定 tenant、device、runtime epoch、invocation、run fence、参数摘要、结果摘要和 provider release。Runtime 只执行服务端已授权 Invocation：

- 参数/版本/能力不匹配时 reject，不做猜测性兼容；
- effect 前持久 journal，effect 后先存结果/outbox 再回传；
- ACK 前可以重发同一结果，但不能再次执行；
- 写动作取消仅在安全点生效；结果未知进入 local blocked，并向云端报告 reconciliation；
- Renderer 只能查看状态和发出“暂停接新任务/请求退出”等管理意图，不能提交 `tool.completed`。

## 9. Desktop UI 纵向功能

首个 Fake Cloud 纵向切片必须同时具备：

- Session Rail：Web Session、授权渠道 Session、active/queued/unread；
- Conversation：canonical user/tool/assistant 消息、Artifact 卡片、verbose/progress；
- RunCard：状态、当前阶段、等待对象、deadline、错误分类；
- Composer 三动作：下一任务、补充/纠正当前 Run、回复 clarification；
- Approval：只有 capability/服务端授权允许时显示；
- Cancel：先显示 cancel requested，再以服务端 snapshot 收敛；
- Refresh/Restart：Main 重建 subscription，Renderer 从 list+snapshot 恢复；
- Notification Center：持久通知与系统通知跳转到正确 Session/Run；
- Device Panel：managed/external、online/capability、paused/draining/update-required；
- 明确提示匿名/transient 场景不支持后台继续，不把它包装成 durable Run。

## 10. 分阶段实施

### DC0：重基线与删除清单（可立即并行）

工作项：

1. 固定当前 commit，盘点 `contracts/desktop-agent`、`src/desktop_agent`、Gateway、Coordinator core、Runtime、CLI 的真实调用方。
2. 将旧 D1/Coordinator 标记 frozen，禁止继续扩展；形成删除、迁移、保留三类清单。
3. 复核现有 A～C 完成证据：Desktop/Web build、Windows smoke、macOS 行为、安全与 artifact 门禁。
4. 冻结 Renderer/Main/Runtime threat model、token 所在进程、managed/external ownership 和日志保留。
5. 与 P0 B01 对齐正式协议源 owner、生成命令、版本规则和 golden examples。

退出门禁：没有生产依赖会被误删；开发者明确哪些旧代码只能读不能扩；P0/P1 只有一套目标协议。

### DC1：共享客户端、Fake Cloud 与桌面 UI（主体可立即并行）

工作项：

1. 建立 `AgentClientPort`、RunProjection、展示模型、Fake AgentClient 和事件脚本。
2. 实现 Session Rail、Conversation、RunCard、WaitCard、三动作 Composer、Artifact 和通知壳。
3. 建立 Main Agent Bridge、SubscriptionManager、PendingCommandJournal 的 Fake transport。
4. 把 Desktop auth 网络与 token 持久化迁到 Main：preload 不再暴露完整 credential hydrate，Renderer 只读取脱敏 session summary。
5. 以录制/构造事件覆盖乱序、重复、seq gap、reset、terminal late progress、等待和队列。
6. G0 后把正式生成类型接到 adapter；内部 Port 不直接暴露 wire DTO。

退出门禁：不启动后端/Runtime，Fake Cloud 可演示 submit→progress→wait→resume→terminal、刷新恢复和三个不同动作；Renderer 无 Node/Provider/凭据直连。

### DC2：Runtime Bridge 与 Fake Device（框架可立即并行，正式类型等 G0）

工作项：

1. Main 实现 RuntimeProcessManager 和受认证本地 Bridge；先 external，后 managed child。
2. 抽取/复用 local-tool-host-core；旧 Runtime 的 journal、lock、outbox 通过 characterization tests 固定行为。
3. Fake Device API 完成 register→claim→fake provider→result→ACK。
4. 验证重复 result、断网补传、runtime restart、epoch 变化和 unknown effect。
5. G0/Device Gate 后接正式生成类型和测试服务。

退出门禁：Fake Server 下 Runtime 崩溃/重启不重复 effect；Main/Renderer 不能伪造执行结果；两个启动者不会拥有同一 managed child。

### DC3：真实 Agent API 与通知联调（依赖 G1～G3）

工作项：

1. G1：接 submit/get/list/subscribe/cancel/append/clarification/approval 与 Artifact。
2. G2：验证桌面关闭、Renderer 崩溃、断网、API/runner 重启后的 Run 恢复。
3. G3：接持久通知、全局订阅和 Electron 系统通知。
4. Web 发起/Desktop 订阅、Desktop 发起/Web 查看使用同一 Run，不产生第二执行者。
5. 移除 Agent API Fake 的生产入口；Fake 只留测试构建。

退出门禁：测试租户完成纯云端长任务全链，重连不重复命令/消息，跨租户与修改 Session kind 均 fail-closed。

### DC4：真实 Device API 与三条工具链（依赖 G2 + Device Gate）

分别完成：

1. 纯云端工具：Desktop/Runtime 离线，Run 仍完成；
2. 本地只读工具：waiting_device→claim→result→Run 继续；
3. 本地写工具：审批→绑定参数/目标→persist-before-effect→证据→ACK；
4. 写动作结果未知：不自动重试，进入 reconcile_required；
5. 解绑/撤权：阻止新副作用，允许受限补交旧结果。

退出门禁：重复 claim/result、Runtime 重启、迟到结果、stale fence 和断网恢复均不重复副作用。

### DC5：首发收口（依赖 Desktop Core Gate）

- Windows 10/11 x64：user-scope NSIS、Authenticode、publisher/appId、全新安装、覆盖/跳版本升级、
  错误签名与损坏包拒绝；
- macOS 13+ arm64/x64：DMG + updater ZIP、Developer ID、Hardened Runtime、nested signing、
  notarization/stapling/Gatekeeper；arm64 真机必测，x64 全量发布前补真机或受控 runner；
- CSP、导航、IPC sender、token、journal、文件下载与 Artifact 授权安全检查；
- 窗口关闭、退出、暂停设备、取消 Run 四种行为验收；
- sleep/wake、网络切换、Renderer crash、Main crash、Runtime crash 故障注入；
- 旧 D1/outcome/Coordinator 从生产 artifact 和正式协议检查中删除；
- 协议最低版本、update-required、降级/回滚和用户说明；
- 复用现有更新器并做回归；更新安装前执行 Run/Runtime/Artifact outbox quiesce，metadata 与产物
  同批原子发布，坏版本只用更高 SemVer 修复；本 P1 不重写更新服务或插件市场；
- release manifest、SBOM、license、audit、ASAR 解包扫描、产物 SHA-256 和签名/notarization
  记录齐全，任何正式门禁失败都不得产生可误认的 release。

退出门禁：§15 完成定义全部通过，无开放 P0/P1 缺陷，才允许 RC/首发。

## 11. 交付批次

| 批次 | 内容 | P0 门禁 | 允许并行 | 禁止混入 |
|------|------|---------|----------|----------|
| D00 | 旧 D1/Coordinator/Runtime 调用方与删除清单 | 无 | P0 B00 | 行为修改 |
| D01 | 内部 Ports、RunProjection、golden fixtures | 无；正式 wire 等 G0 | P0 B01 | 手写正式 wire DTO |
| D02 | Fake AgentClient/Fake Cloud/contract harness | 无 | D01、P0 Phase 0 | 真实生产路由 |
| D03 | Desktop Agent UI 纵向切片 | D01 | D02/DC2 | Web 页面大迁移 |
| D04 | Main Agent Bridge + subscription + command journal + token 迁移 | D01 | D03 | 任意 IPC/明文 journal/Renderer 长期 token |
| D05 | RuntimeProcessManager + Fake Device | G0 前只用内部端口 | D03/D04 | Provider 业务扩项 |
| D06 | 正式 Agent API 生成类型与 adapter | G0 | P0 Phase 1/2 | 修改 P0 wire 语义 |
| D07 | 真实 Agent API/Run 恢复联调 | G1/G2 | P0 Phase 3/4 | Device 写工具 |
| D08 | 通知中心和系统通知 | G3 | D07 | 新通知权威 |
| D09 | Device API client + Runtime 正式适配 | Device Gate/G2 | D07/D08 | 本地业务 Run 状态机 |
| D10 | 纯云端/本地只读/本地写三链路 | D09 | 发布准备 | Browser Runtime |
| D11 | 双平台安全、故障注入、package/RC | Desktop Core Gate | 无 | 新功能扩项 |
| D-final | 旧协议删除、文档与首发验收归档 | 全部 | 无 | 未经评审的兼容层 |

共享文件所有权：P0 修改正式协议源/服务端；P1 修改 Desktop 消费者、Main、Runtime adapter。需要同时修改生成器时，先由 P0 合入协议源和生成物，P1 再消费，避免两个分支同时手改生成文件。

## 12. 测试与验证

本计划涉及凭据、IPC、本地副作用、协议、进程生命周期和跨端一致性，所有非纯 UI 批次按高风险流程执行：开发者自测后，由独立测试者和独立 Code Review 分别验证；P0/P1 问题修复后重跑相关门禁。纯展示组件仍需独立验证，但不要求为形式凑无关全量测试。未获得用户明确授权，不提交或推送 Git。

### 12.1 自动化层级

| 层级 | 位置 | 重点 |
|------|------|------|
| 纯领域单测 | `frontend/shared/agent-run/__tests__` | projection、seq/reset、命令 id、本地展示映射 |
| Desktop 组件 | `frontend/desktop/__tests__` | 三动作、等待权限、队列、离线/恢复、通知跳转 |
| Main/Preload | `clients/agent-desktop/tests` | IPC sender、schema、token、journal、订阅和进程 ownership |
| Runtime | `clients/agent-tool-runtime/tests` | claim/fence、journal、lock、outbox、ACK、unknown effect |
| Contract | 正式协议 golden examples + Fake/真实 adapter 共用 suite | TS/Python/服务端/客户端一致性、版本兼容 |
| 集成 | 测试后端 + Desktop Main/Renderer + Runtime | Run 与 Device 两条真实链路 |
| E2E/真机 | Electron smoke、Windows/macOS 安装包 | 关闭/重开、通知、下载、更新、安全和系统行为 |

同一 AgentClient contract suite 必须对 Fake adapter 与真实 adapter 复跑；Fake 通过不能替代测试环境真实 API。RunProjection 事件 fixtures 由 P0 规范事件样例生成或校验，禁止 Desktop 独立维护同名不同义枚举。

### 12.2 常用命令

```bash
cd frontend
npm run typecheck:desktop
npm run test -- desktop shared
npm run check:boundaries
npm run check:protocol
npm run build
npm run build:desktop

cd ../clients/agent-desktop
npm run typecheck
npm test
npm run smoke

cd ../agent-tool-runtime
npm run typecheck
npm test

cd ../shared
npm run typecheck
npm test
```

DC0/D06 必须把现有 `npm run check:protocol` 从“只验证 frozen 旧 D1”迁到正式 Agent/Device
协议源，并增加生产 artifact 不引用旧 D1 generated types 的反向门禁；迁移完成前该命令通过
不代表新协议已经验收。

真实后端联调按 P0 测试计划使用隔离测试租户和专用环境；不得把生产租户、真实写 Provider 或长期凭据用于自动化测试。

### 12.3 必测故障矩阵

- accepted 响应前 Main/Renderer crash，重启以同 command_id 收敛；
- subscribe 断开、重复事件、乱序事件、seq gap、reset_required；
- terminal 后迟到 progress、wait 超时后迟到回复、append 与 terminal 竞态；
- window close、app quit、pause runtime、cancel Run；
- Runtime 断网、crash、重启、stale epoch/fence、result ACK 丢失；
- Device 写工具执行后回传失败，不能重新执行；
- token/journal 损坏、磁盘满、safeStorage 不可用；
- 跨 tenant、修改 Session kind、伪造 approval/tool result/Artifact ref；
- 协议 major 不兼容、最低版本不足、服务端滚动升级；
- Windows/macOS sleep/wake、多显示器、缩放和网络切换。

## 13. 安全与数据规则

- Renderer 永远按不可信输入处理；preload 只暴露冻结方法，不暴露 `ipcRenderer`、任意 URL、任意文件或命令。
- API base URL 必须是配置的精确 HTTPS origin；开发 HTTP 只允许 localhost/loopback。
- 长期 token 只持久化于 Main safeStorage；Renderer 迁移后只接收非敏感会话摘要和短期内存态，不在 localStorage 保存 token。
- PendingCommandJournal、Runtime journal 和结果 outbox 分开；前者不成为消息历史，后两者不成为 Run 权威。
- Artifact 下载由 Main 校验 origin、tenant、display name 和授权，再写临时文件并原子 rename。
- 本地日志默认不记录消息正文、工具参数、token、证据原文和绝对路径；诊断包需显式同意和脱敏。
- 未授权 capability、版本不匹配、设备离线、磁盘写失败、结果未知全部 fail-closed。
- Desktop/Runtime 不能持有 Channel Connector secret，也不能调用内部 Channel Gateway principal。

## 14. 灰度与发布

| 阶段 | 用户范围 | 后端条件 | 能力 |
|------|----------|----------|------|
| Internal UI Alpha | 开发/测试人员 | 无真实 P0；Fake Cloud/Device | Shell、Run UI、投影、三动作演示 |
| Contract Alpha | 内部租户 | G0 | 正式生成类型，仍以 Fake/录制事件为主 |
| Cloud Beta | 内部测试租户 | G1/G2 | 真实云端 Run、断线恢复、无本地写工具 |
| Device Beta | 指定测试设备 | Device Gate/G2 | 本地只读，写工具仅批准的测试 Provider |
| RC | 小范围内部用户 | Desktop Core Gate | 通知、三链路、双平台包、回滚 |
| 首发 | 批准用户 | RC 无 P0/P1 缺陷 | 正式支持矩阵；Browser 不承诺 |

回滚时停止 Desktop 新版本分发和 Device 新 claim；已 accepted 云端 Run 继续由 P0 收敛，Runtime 已执行结果继续受限补传。不得把 Desktop 请求回退到旧 D1 或本地 Coordinator。客户端版本不兼容时进入 update-required/只读诊断，而不是静默改协议。

## 15. 完成定义

- [ ] 旧 D1、outcome 和本地业务 Coordinator 不在生产 Desktop 主链；旧生成协议不再被正式构建引用；
- [ ] Desktop 与 Web 使用同一 Agent API/Run identity，Web/Desktop 同时订阅不会双执行；
- [ ] Renderer/Main/Runtime 权限边界和窄 IPC 通过独立安全审查；
- [ ] 长期 token 只由 Main safeStorage 持久化，Renderer/preload 不再暴露完整 credential hydrate；
- [ ] submit、append、clarification、approval、cancel、list/get/subscribe 全部使用正式生成类型；
- [ ] RunProjection 通过重复、乱序、缺口、reset、terminal 迟到事件和四种 waiting 测试；
- [ ] 关闭窗口、Renderer crash、断网和应用重启不取消或复制云端 Run；
- [ ] PendingCommandJournal 在 accepted 不确定时只重放同一 command，不泄露明文内容；
- [ ] managed/external Runtime ownership 明确，两个启动者不会争抢同一执行宿主；
- [ ] 三条工具链通过，写动作 unknown 不自动重试；
- [ ] 跨租户、伪造 Session kind、approval、tool result 和 Artifact 访问全部拒绝；
- [ ] 站内/系统通知能够跳转正确 Session/Run，漏实时通知可补拉；
- [ ] Windows/macOS 支持矩阵、安装包、安全、故障注入和回滚记录完成；
- [ ] Web build、边界与关键回归无退化；
- [ ] 所有批次完成独立测试和独立 CR，无开放 P0/P1 问题；
- [ ] P0 Phase 6 未完成不会让 Desktop 引入渠道 secret/Connector 或第二套渠道生命周期。

## 16. 批次记录

状态规则：某阶段首批实际启动时将顶部状态改为“🔧 进行中”；所属批次全部完成、门禁通过且独立测试/CR 关闭 P0/P1 后，才改为“✅ 完成（日期）”。每批开始时在 `docs/plans/evidence/desktop-p1/<batch>.md` 建立脱敏证据附件；不提交凭据、storageState、原始客户内容、完整日志或大体积安装包。

| 批次 | 阶段 | 状态 | P0 门禁 | 证据附件 | 阻塞/遗留 |
|------|------|------|---------|----------|-----------|
| D00 | DC0 | 📋 待开发 | 无 | — | — |
| D01 | DC1 | 📋 待开发 | 正式 wire 等 G0 | — | — |
| D02 | DC1 | 📋 待开发 | 无 | — | — |
| D03 | DC1 | 📋 待开发 | D01 | — | — |
| D04 | DC1 | 📋 待开发 | D01 | — | — |
| D05 | DC2 | 📋 待开发 | 正式 Device 类型等 G0 | — | — |
| D06 | DC2/DC3 | 📋 待开发 | G0 | — | — |
| D07 | DC3 | 📋 待开发 | G1/G2 | — | — |
| D08 | DC3 | 📋 待开发 | G3 | — | — |
| D09 | DC4 | 📋 待开发 | Device Gate/G2 | — | — |
| D10 | DC4 | 📋 待开发 | D09 | — | — |
| D11 | DC5 | 📋 待开发 | Desktop Core Gate | — | — |
| D-final | DC5 | 📋 待开发 | 全部 | — | — |

## 17. 开工顺序

收到开发指令后按以下顺序启动，不等待全部 P0 完成：

1. 创建或确认 `feature/desktop-client-p1` 的独立 Desktop worktree，记录其路径、分支和 HEAD；同时用 `git worktree list --porcelain` 定位只读的 P0 worktree。
2. D00：输出旧 D1/Coordinator/Runtime 依赖与删除清单，同时确认 P0 G0 contract owner，并记录参考的 P0 HEAD/Gate。
3. D01/D02：建立内部 Port、RunProjection、Fake Cloud 和 golden fixtures；不得创建临时 wire 协议。
4. D03/D04：Desktop UI 与 Main Bridge 并行，但共享类型由 D01 单一维护。
5. D05：Runtime ownership/Fake Device；不接生产 Device API。
6. G0 到达后，将已提交的 P0 契约基线按团队合并策略纳入 Desktop 分支并执行 D06；G1/G2/G3 到达后依次 D07/D08。
7. Device Gate 到达后执行 D09/D10；Desktop Core Gate 后执行 D11/D-final。
8. D00 实际启动时，把本文 DC0 和 `docs/ideas.md` 桌面条目标记“🔧 部分完成”；仅有开工意向时不更新。
