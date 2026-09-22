# Agent Desktop P1 并行技术实现与开发执行计划

## 开发进度

| 阶段 | 内容 | P0 门禁 | 状态 | 完成记录 |
|------|------|---------|------|---------|
| 历史基础 | 独立 Desktop Shell、登录、安全 Bridge、Windows 包与 macOS 基础行为 | 无 | 🧾 历史证据（待 DC0 复核） | 继承 A～C 记录，不代表当前 HEAD、旧 D1 或 Coordinator 已验收 |
| DC0 | 新旧架构重基线、依赖审计与删除清单 | 可与 P0 Phase 0 并行 | 📋 待开发 | — |
| DC1a | Desktop 私有 ViewModel、场景 Fake 与 UI 骨架 | 无；禁止形成正式 wire/status 契约 | 📋 待开发 | — |
| DC1b | 正式 AgentClient、RunProjection 与 golden fixtures | G0 | 📋 待开发 | — |
| DC2 | Main/Preload Agent Bridge、命令 journal、内置 Runtime/执行节点模式与 Fake Device | P0 Gate G0 | 📋 待开发 | — |
| DC3 | 真实 Agent API、断线恢复、等待/通知联调 | P0 Gate G1/G2/G3 | 📋 待开发 | — |
| DC4 | Device Backend、Runtime 适配、第一方 Provider 按需分发与本地三链路 | G0；DDL 等 P0 B04/B05；真实链等 G2 与 Device/Provider Gate | 📋 待开发 | — |
| DC5 | 双平台安全、安装包、故障注入、RC candidate 与 Release Gate 收口 | Desktop Core Gate 解锁；发布须 Release Gate | 📋 待开发 | — |

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
7. Desktop 技术首发不依赖 P0 Phase 6 渠道全部迁移；D11 由 Desktop Core Gate 解锁，正式 RC/GA
   必须通过 Desktop Release Gate。P0 仍拥有项目资源优先级，Desktop 不得阻塞 P0 Web/微信客服交付。
8. Web 与 Desktop 只是同一云端 Agent 的不同交互入口。Desktop 安装包同时内置 Runtime 执行面，
   可运行完整桌面、仅交互或执行节点模式；任何模式下本地工具都必须由云端 Invocation 驱动，
   Desktop UI 不直接调用 Provider。
9. BOSS CLI、weixin CLI 等第一方 Provider 作为独立签名包按需安装。P1 交付最小第一方 catalog、
   下载票据、Runtime Package Manager、版本固定/回滚/隔离；第三方市场与开放上传仍留在 P2。

## 2. 当前代码资产与处置

| 现有资产 | 处置 | 原因 |
|----------|------|------|
| `frontend/desktop/` Shell、登录、启动状态 | 保留并扩展 | 已满足独立 renderer 与依赖边界 |
| `frontend/shared/auth`、`platform/contracts.ts` | 保留，按需提取 Agent 纯端口 | 已有测试与 Web/Desktop 边界 |
| `clients/agent-desktop/electron/` | 保留，增加 Agent Bridge、通知和 Runtime 管理 | Main 是系统能力和安全凭据边界 |
| `clients/agent-desktop` 构建/安全/更新/包验证 | 保留并回归，不在本 P1 重写更新系统 | 已有可执行和供应链门禁 |
| `clients/agent-tool-runtime` | 保留设备执行内核，与 Desktop 同包交付但仍可独立 headless 运行，替换旧云协议适配层 | Provider/journal/lock/outbox 可复用；Web/Desktop 发起共用同一执行面 |
| `clients/shared/local-tool-host-core` | 保留并逐步承接共用 Provider Host 逻辑 | Desktop managed Runtime 与 headless Runtime 共用 |
| 第一方 BOSS/weixin Provider 发布包 | 保留独立二进制与 manifest；纳入签名 catalog 和按需安装 | 不把业务源码永久打入 Desktop 主包，不改变标准 MCP 接口 |
| `clients/shared/agent-coordinator-core` | 冻结；DC0 审计后删除或仅迁出纯 reducer | “本地业务 Coordinator”与新所有权冲突 |
| `contracts/desktop-agent` 旧 D1/outcome | 冻结，不再生成新正式协议；新契约可用后删除 | 未发布协议，无兼容义务且语义已过时 |
| `src/desktop_agent`、旧 Gateway 路由 | 只做调用方审计和删除/迁移 | 不作为 Desktop 首发兼容层 |
| Browser legacy/Hub/owner runtime | 不接入 | P0 首期明确 `not_migrated` |

旧路线中的“Local Agent Coordinator”“DEVICE_OWNED 会话本地权威”“客户端调用
`agent/next` 推进 outcome”不再是实施要求。两份旧设计/计划文档已经删除；但
`src/desktop_agent`、`contracts/desktop-agent`、`deploy/desktop_agent_d1.sql`、旧生成类型、测试和
按开关注册的 `/api/desktop/v1` 实现仍保留并冻结，D00 负责调用方审计，D-final 才删除正式构建
与生产引用。不得把“文档已删除”误读为“旧实现已经不存在”。

### 2.1 已完成工程基线

以下事实由旧 v2.4 计划合并而来，是 DC0 的复核输入，不代表旧 D1/Coordinator 继续有效：

| 日期 | 已完成内容 | 继承方式 |
|------|------------|----------|
| 2026-08-12 | `frontend/src` 原样迁移到 `frontend/web`；`@/` 继续指向 Web；Web/Desktop 构建和结构门禁建立 | 保留目录基线，禁止为 Desktop 批量改写稳定 Web import |
| 2026-08-12 | `frontend/desktop`、`frontend/shared`、独立 alias/tsconfig/Vitest、依赖边界和 contract harness 建立 | 继续扩展；把旧 D1 protocol 检查迁到新 Agent/Device 协议 |
| 2026-08-12 | 独立 Desktop renderer/router/Shell/登录、启动 reducer、离线/update/fatal UI、Shared auth/platform contract 完成 | 作为 DC1a 起点，不重建第二套 Shell |
| 2026-08-12 | production artifact 对 Web/Portal 引用已有硬门禁，Electron Bridge v3、macOS driver 基础 contract 已建立 | DC0 复跑并修正为新 Agent Bridge/Runtime 边界 |
| 2026-08-14 | Windows Electron 43.1.0 可执行 smoke；macOS arm64 完成默认/720×500/150%/200%、Dock 恢复、`Command+Q` 和窗口拖动真机验收 | 作为历史证据保留；DC5 必须在最终产物和当前锁定版本上重新验收 |

旧记录中的测试计数只证明当时 commit，不作为当前分支绿色结论。D00 必须记录当前 HEAD、实际脚本、
可复现结果和已知债务；如果代码事实与表中记录不一致，以当前代码和复测证据为准。

### 2.2 已有 Runtime 的生产兼容责任

“Desktop 尚未发布、旧 D1 无兼容义务”不适用于已经独立版本化和分发的
`clients/agent-tool-runtime` 及其 `/api/local-tools/runtime/*`、Provider、result outbox、
`local_tool_invocations` 和 `session_tasks` 能力。D00/B00 未取得生产未使用的证据前，一律按生产能力
保护：

1. D00 输出已部署 Runtime 版本、设备/租户、最近心跳、Provider、标准 invocation 与
   `session_tasks` 使用量；同时盘点现有 BOSS/weixin Provider 的包格式、安装目录、版本来源、升级/
   回滚方法和签名现状。原始客户数据不入库内文档。
2. 新 Device API 是现有 Runtime API 的版本化演进，不平行建设第二套设备调度中心。服务端优先在
   `src/local_tools/`、现有设备/Invocation/permit/result 表族上增加正式版本协商、Run/Invocation
   稳定引用和缺失语义；`session_tasks` 作为独立生产 lane 保留，不能被 Desktop Run 改造顺手删除。
3. 目标语义与现有端点映射如下；URL 是否增加版本前缀由 D00 ADR 决定，但不得在没有迁移期时
   原地改变旧 payload：

| Device 语义 | 现有/目标服务端表面 | 迁移要求 |
|-------------|--------------------|----------|
| user device directory | `GET /api/local-tools/devices`、`POST .../devices/{id}/select`、`DELETE .../devices/{id}` | 保留用户鉴权；Desktop `DeviceDirectoryPort` 只走该受权表面，不调用 Runtime token API |
| pair/register | `POST /api/local-tools/runtime/pair` | 保留现有配对；响应增加协商后的 protocol/capability/min-version |
| heartbeat/capabilities | `POST /api/local-tools/runtime/heartbeat` | 保留；绑定 installation/device/runtime epoch 和能力摘要 |
| claim | `POST /api/local-tools/runtime/claim` | 保留；正式返回 invocation identity、claim/fence/epoch、deadline 和 payload ref |
| accept | `POST .../invocations/{id}/started` | 保留并冻结 started 幂等/CAS 语义 |
| reject | 新增版本化 `POST .../invocations/{id}/reject`，或经 D00 证明等价的无 effect 终态入口 | 只允许 effect=none；能力/版本/参数不匹配不得伪装工具失败 |
| progress | `POST .../invocations/{id}/progress` | best-effort、有界、不能决定终态 |
| result/evidence | 优先统一到 `POST .../invocations/{id}/operation-result` | 复用 v2 effect/phase/permit；绑定 run/invocation/fence/digest/evidence refs |
| durable ACK | `operation-result` 的持久 2xx receipt | ACK 是服务端已持久接纳，不另要求 Runtime 再回 ACK；ACK 前 outbox 可重传同一结果 |
| reconcile | 新增版本化 `POST .../invocations/{id}/reconcile` | 只补交核验事实，不触发第二次 effect |

规范状态映射：`queued → claimed → running → succeeded|failed|cancelled|unknown`；取消请求是控制事实，
不等于已取消。claim 到期且确认未 started/effect 时，服务端可在 attempt 上限内回 queued；running
租约丢失或 effect 不明进入 unknown/reconcile，不能直接重领重做。reject 由服务端根据原因决定：
永久 capability/version/参数错误收敛为 `failed(effect=none, phase=prepared)`；暂时资源不可用只有在
同一固定 target、未执行且 attempt/退避允许时才能回 queued。Runtime 只报告 reject reason，不能
自行换设备或决定重试。

4. 服务端兼容实现由 Desktop P1 的 `D09S-A/B` Device Backend 子轨负责，部署在现有 API 服务，数据库
   归现有 `local_tools` 表族并按 P0 稳定引用扩列；不是另建微服务，也不纳入 P0 Web/渠道完成门槛。
5. Runtime 迁移至少保留“旧稳定版 + 新版”两个发布窗口。先上线向后兼容服务端，再灰度新版
   Runtime；观测无旧 claim/outbox 后才提升最低版本。旧版被撤权时停止新 claim，但持原
   invocation/claim 身份的历史 result/evidence 仍可在受限窗口补交。
6. 回滚只回滚新 claim 路由和最低版本，不删除新字段/表；在途新旧 Invocation 按原协议收敛，
   禁止把失败任务改投另一设备重做。
7. 当前电脑已存在同 installation/device 的全局 external Runtime 时，Desktop 优先通过受认证
   attach 连接同一实例；不支持 attach 或身份不一致时拒绝启动 managed child，并引导用户选择
   “使用现有 Runtime”或“停止现有 Runtime”。绝不让两个 Host 同时领取同一设备任务。

## 3. P0/P1 并行门禁

### 3.1 门禁定义

| 门禁 | 可核验交付物 | 签字角色 | Desktop 解锁能力 |
|------|--------------|----------|------------------|
| G0 契约门禁 | P0 `B01` 已合入双方共同基线；除 Notification 外，Run/Command/Wait、typed SessionRef、Session summary、Conversation cursor/page、Artifact upload/metadata/ref、command receipt、Device directory 与 Device Invocation 协议源、生成命令、golden examples、版本和 owner 明确 | P0 contract owner + Desktop consumer owner | 生成上述正式 TS 类型；DC1b、正式 RunProjection/DeviceClient adapter 开工；Notification 仍等 G3 |
| G1 Agent API 测试门禁 | P0 `B11` 完成并部署隔离测试环境；submit/get/list/subscribe、session/conversation/artifact、command receipt、等待/取消/审批和终态刷新可用；其依赖的 B05/B06/B07/B09 已通过 | Agent API owner + 隔离环境测试 owner | Desktop 连接真实测试租户，验证刷新、排队、补充、取消、澄清与 Artifact |
| G2 后台执行门禁 | P0 `B09+B10` 的 runner、lease/fence/reclaim/deadline/health/drain 在隔离环境通过故障演练；客户端访问仍经 G1 API | runner owner + 运维 owner | 验证桌面关闭/断网、长任务、等待设备、runner 重启和迟到结果 |
| G3 通知门禁 | P0 `B13` 的 notification outbox、查询、已读、全局订阅和补拉部署到隔离环境 | notification owner + Desktop consumer owner | 接入持久通知、系统通知与离页完成提醒 |
| Device Gate | `D09S-A` 完成兼容 API/版本协商；P0 B04/B05 后 `D09S-B` 完成 Run/Invocation 稳定引用、独立 migration、repository 联调和隔离部署；`D09R` Runtime adapter contract tests 及 G2 故障链通过 | P1 Device Backend owner + P0 schema owner + Runtime owner + 安全测试 owner | 接真实 Runtime 和真 Provider；此前只允许 Fake Device API |
| Provider Gate | D00 package/signing ADR 已批准；`D09P` 完成 immutable release 构建/签名/上传/审核/发布/撤回管理链、第一方 catalog、短期下载票据、Runtime 安装/校验/原子切换/回滚/隔离和供应链测试；BOSS/weixin 参考包通过 | Provider release owner + Runtime owner + 安全/供应链 owner | 自动按需安装第一方 Provider；此前只允许预装或人工批准的兼容安装 |
| Desktop Core Gate | G0～G3、Device Gate、Provider Gate 和 D00～D10 对应门禁通过；启用范围无开放 Sev-0/Sev-1 | P0/P1 integration owners | 解锁 D11 和 RC candidate 构建；不等于允许 RC/GA |
| Desktop Release Gate | D11 与 D-final 完成，§15 完成定义、独立测试/CR、签名/notarization、回滚和 §14 Sev 门禁全部通过 | Desktop release owner + 安全/测试 owners | 允许批准范围的 RC/GA；P0 Phase 6 全渠道迁移不是技术阻塞项 |

P0 发生兼容性变更时，由 P0 contract owner 先更新协议源、兼容样例和 changelog；Desktop 只消费生成类型，不在客户端先改 wire shape。没有共同协议版本时 fail-loud，不以 `any` 或忽略字段继续运行。

### 3.2 可立即并行与必须等待

可立即开始：

- DC0 的旧 D1/Coordinator/Runtime 依赖审计；
- Desktop Shell、窗口、菜单、托盘策略、设置与系统通知展示壳；
- DC1a 的 Desktop 私有 ViewModel、场景状态和 Fake；这些类型放在 Desktop prototype/test
  命名空间，不导出到 `frontend/shared`，也不称为 golden/contract；
- Run 卡片、Session Rail、消息列表、三动作 Composer、等待/错误/离线 UI 骨架；
- Fake Cloud/Fake Device 和测试 harness；正式 golden event fixtures 必须等待 G0；
- Main/Preload 窄 Bridge、命令重试 journal、Runtime managed/external 生命周期框架；
- 内置 Runtime 的完整桌面/仅交互/执行节点模式壳，以及第一方 Provider Package Manager 的纯本地
  状态机与 Fake catalog；正式 Device/Provider wire 类型、签名根和发布源仍须等待相应门禁；
- Windows/macOS 构建、安全和视觉回归。

必须等待门禁：

- G0 前不得固化 JSON/SSE 字段、错误码和状态枚举；
- G0 前不得把 prototype/Fake 类型移动到 shared public API，也不得用其生成生产 adapter；
- G1 前不得声称真实 Run 恢复、取消或补充已联调；
- G2 前不得声称关闭桌面后长任务和租约恢复成立；
- Device Gate 前不得让 Runtime 向生产提交工具结果；
- Provider Gate 前不得按云端请求自动下载或运行 Provider 包；
- G3 前不得把本地 toast 当作可靠通知；
- Desktop Core Gate 前不得进入 D11/RC candidate；Desktop Release Gate 前不得向批准用户发布 RC/GA。

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

### 3.4 D00 kickoff 必填决议

D00 开始记录必须由项目负责人填写，不能由开发者猜测：

- Desktop worktree 的基线 commit、实际分支/路径，以及吸收 P0 已提交变更采用 merge、rebase 还是
  定期集成分支；不得直接依赖 P0 worktree 的未提交内容；
- G0/G1/G2/G3 和 Device Gate 的具名 owner、证据文件及签署方式；未有证据一律视为未通过；
- 当前允许范围。默认在任何 P0 Gate 均未签署时，只能做 D00、DC1a、D04 的 Main Auth 内部迁移、
  D05 的 Fake/ownership 框架和不固化协议的 UI；不能连接真实 Agent/Device 测试服务；
- Windows Authenticode、Apple Developer/notarization、Windows 10/11 与 macOS arm64/x64 真机或
  runner、生产更新源和 RC/GA release owner；缺失项写成外部阻塞，不以 unsigned 包替代正式门禁；
- 视觉回归工具、基线视口/主题/DPI、截图存储位置和批准人。工具未定前以人工清单留证，不能把
  “肉眼看过”记为自动化门禁；
- production Runtime/version/session_tasks 证据的查询 owner 和回传期限，沿用 P0 B00 的脱敏与
  保守结论规则。

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
          │                              │ management only
          │ HTTPS/SSE                    ▼
          ▼                         Embedded/External Runtime
Cloud Agent API                       DeviceClient + journal/lock/outbox
  RunService ──authorized Invocation──> Cloud Device API <──claim/result──┘
                                           │
                                  Provider Package Manager
                                  signed first-party catalog
                                           │
                                      Provider Host
                                           │
                                  BOSS/weixin/... Providers
```

依赖方向固定：

```text
frontend/desktop ─────> frontend/shared/agent-run
frontend/shared/agent-run -X-> Electron/Node/Web 页面
clients/agent-desktop ─> 生成协议类型 + local-tool-host-core
clients/agent-tool-runtime ─> Device API 类型 + local-tool-host-core
Renderer -X-> Provider / DeviceClient / 任意 Node API
Desktop Main -X-> 直接发起 Provider 业务调用
Runtime -X-> AgentApplication 状态推进 / approval 决策
```

Web、Desktop 和 Channel 只改变 Run 的接纳入口，不改变本地工具链：RunService 选择已授权设备并
创建 Invocation，Runtime 经 Device API 领取后才可调用 Provider。Runtime 与 Desktop 同机时也不
建立“本地快速旁路”，否则会重新产生第二套授权、审计和状态所有权。

## 5. 建议代码落点

```text
frontend/shared/agent-run/
  ports.ts                    # G0 后：Agent/Session/Artifact/Device ports；Notification 正式类型等 G3
  projection.ts               # RunProjection 纯 reducer
  presentation.ts             # 状态/等待/错误展示模型
  command.ts                  # command id 与本地 pending 状态纯逻辑
  fake.ts                     # G0 后按正式类型实现的 Fake adapter
  __tests__/

frontend/desktop/features/agent/
  prototype/                  # G0 前私有 Prototype* ViewModel/场景；不得成为 wire/shared API
  DesktopAgentPage.vue
  DesktopSessionRail.vue
  DesktopConversationPane.vue
  DesktopComposer.vue
  DesktopRunCard.vue
  DesktopWaitCard.vue
  DesktopNotificationCenter.vue
  useDesktopAgent.ts

clients/agent-desktop/electron/
  authBridge.ts               # captcha/login/restore/validate/logout；token 只在 Main
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
  providerPackageManager.ts   # 第一方 release 解析、下载、验证、原子安装、回滚和隔离
  providerCatalogClient.ts    # 只接受受控 catalog/短期票据，不接受任意 URL
  providerInventory.ts        # installed/available/installing/quarantined 能力投影

src/local_tools/              # D09S-A/B：现有生产 Device Backend 原地兼容演进
  api.py                      # pair/heartbeat/claim/started/progress/operation-result
  device_api.py               # 版本协商、reject/reconcile 等正式应用边界（名称可由 D00 ADR 调整）
  provider_catalog.py         # D09P：Runtime resolve、策略、撤回状态与下载票据边界
  provider_release_admin.py   # D09P：仅 CI/受权运营的 draft/review/publish/revoke 应用边界

contracts/
  agent-run/                  # 建议：P0 拥有的 Agent API/event 协议源
  device-runtime/             # 建议：Runtime 专用 Device API 协议源
  provider-package/           # P1 拥有的第一方 package manifest/catalog/install result 协议源
  desktop-agent/              # 旧 D1，冻结，迁移完成后删除
```

`contracts/agent-run` 与 `contracts/device-runtime` 的最终路径由 P0 G0 冻结；在此之前 Desktop 只实现内部 Port/Fake，不创建临时 wire schema。正式类型由协议源生成，生成物禁止手改。

## 6. AgentClient 技术实现

### 6.1 内部端口

内部 Port 表达客户端需要什么，不定义服务端 JSON。Run 命令、会话/消息、Artifact、通知和设备
目录分开，避免一个万能 client 模糊权限：

```typescript
interface AgentClientPort {
  submit(command: SubmitRunCommand): Promise<RunAccepted>
  appendInput(command: AppendRunInputCommand): Promise<CommandAccepted>
  replyToClarification(command: ReplyToClarificationCommand): Promise<CommandAccepted>
  cancel(command: CancelRunCommand): Promise<CommandAccepted>
  resolveApproval(command: ResolveApprovalCommand): Promise<CommandAccepted>
  getCommandReceipt(commandId: string, requestDigest: string): Promise<CommandReceipt | null>
  getRun(runId: string): Promise<RunSnapshot>
  listSessionRuns(ref: SessionRef, options: RunListOptions): Promise<RunPage>
  subscribe(runId: string, afterSeq: number, signal: AbortSignal): AsyncIterable<RunStreamItem>
}

interface SessionClientPort {
  createSession(command: CreateSessionCommand): Promise<SessionSummary>
  listSessions(query: SessionQuery): Promise<SessionPage>
  getSession(ref: SessionRef): Promise<SessionSummary>
  listMessages(ref: SessionRef, after: MessageCursor | null): Promise<MessagePage>
}

interface ArtifactClientPort {
  chooseLocalArtifact(options: ArtifactPickerOptions): Promise<LocalArtifactSelection | null>
  upload(input: {
    localHandle: OpaqueLocalFileHandle
    sessionRef: SessionRef
    purpose: ArtifactPurpose
  }): Promise<ArtifactRef>
  getMetadata(ref: ArtifactRef): Promise<ArtifactMetadata>
  // 真实上传、下载和落盘都在 Main；Renderer 只提交 opaque handle/ref
  saveArtifact(ref: ArtifactRef, suggestedName?: string): Promise<SaveArtifactResult>
}

interface NotificationClientPort {
  listNotifications(query: NotificationQuery): Promise<NotificationPage>
  markRead(notificationIds: readonly string[]): Promise<void>
  subscribe(after: NotificationCursor | null, signal: AbortSignal): AsyncIterable<NotificationStreamItem>
}

interface DeviceDirectoryPort {
  listDevices(query: DeviceQuery): Promise<DevicePage>
  getDevice(deviceId: string): Promise<DeviceSummary>
  selectDevice(command: SelectDeviceIntent): Promise<DeviceSelectionReceipt>
}
```

约束：

- Desktop 新建普通会话使用授权的 Web Session 命名空间，不新增 `session_kind=desktop`；查看渠道会话时仍使用 `kind=channel` 且服务端重新授权。
- `SessionSummary/RunSnapshot` 携带服务端计算的 capability 摘要，例如
  `can_submit/can_append/can_cancel/can_approve/can_select_device/can_view_channel`；UI 不从角色名猜权限。
- P1 对 `reconcile_required` 只展示服务端 evidence/原因摘要和受控“在 Web 管理端核验”入口；
  没有正式、可审计的 reconcile command 前，Desktop 不提供“标记成功/重试”按钮。
- submit/cancel/append/clarification/approval 使用稳定 `command_id`；查询可重试，命令不得因重连生成新 id。
- Main 持有持久凭据与真实网络 transport；Renderer 只通过窄 Bridge 收到已校验的 DTO 和事件。
- `chooseLocalArtifact` 的系统文件选择器运行在 Main，只返回绑定 window+account+tenant、短 TTL、
  不可猜测的 local handle 和脱敏 displayName/size/MIME；绝对路径和文件描述符不进入 Renderer。
  `upload` 时 Main 重新校验 handle/SessionRef/权限、文件未被替换、大小/MIME/允许类型，计算 digest
  并流式上传；handle 使用后或过期即释放。Renderer 不提供 Authorization、tenantId、上传 URL 或路径。
- 现有 `credentials.hydrate()` 会把完整 token 带入 Renderer，只作为待迁移基线；D04 将登录/恢复后的 token 留在 Main，Renderer 只获得用户/tenant/session 摘要和 Agent Bridge，不得把旧行为固化为正式接口。
- SSE 使用支持 Authorization header 的 authenticated fetch stream，不依赖不能带 header 的原生 EventSource。
- 每个订阅以 `run_id + after_seq` 恢复；连接 ID、窗口 ID 和本地 task ID 都不能替代 Run identity。
- HTTP/SSE 解析先做 schema validation，再送给 Renderer；未知 major 版本进入 update-required，不把未知状态映射为 running。

### 6.2 Main Auth Bridge

P1 沿用当前后端“同一 token 滑动过期”的认证语义，不在客户端虚构 refresh token。认证、bearer
token、`/api/auth/me` 校验和滑动续期全部迁到 Main。Renderer 永远不得接触 bearer token，Preload 只暴露
版本化窄接口：

```typescript
interface DesktopAuthBridge {
  getCaptcha(): Promise<CaptchaChallenge>
  login(input: LoginInput): Promise<AuthSessionSummary>
  restore(): Promise<AuthSessionSummary | null>
  validate(): Promise<AuthSessionSummary>
  logout(options?: { keepUnresolvedForSameAccount?: boolean }): Promise<void>
  getSessionSummary(): Promise<AuthSessionSummary | null>
  onAuthStateChanged(listener: (state: AuthState) => void): Unsubscribe
}

interface AuthSessionSummary {
  authState: 'authenticated' | 'validating' | 'expired' | 'revoked'
  userId: string
  displayName: string
  tenantId: string
  tenantCode: string
  capabilities: readonly string[]
  expiresAt: string | null
}
```

规则：

- captcha/password 只通过一次 IPC 送到 Main 网络层并立即释放，不写 credential store、日志或 journal；
- P1 一个窗口上下文只允许一个 active tenant。普通用户切租户必须完成旧身份 drain/logout 后重新
  登录；P1 不支持平台管理员“代租户/冒充”模式；
- restore、定时校验或临近 `expiresAt` 时，Main 对仍持有的 token 以 single-flight 调用
  `/api/auth/me`；成功即按服务端现有语义刷新滑动期限并恢复订阅。当前后端没有 refresh token/
  refresh endpoint，因此任何业务请求或 `/me` 收到 401 后都不得拿同一 token 重试，直接进入
  expired/revoked、停止新命令和 Runtime 新 claim，并要求重新登录；
- Runtime 的 device token 与 Desktop 用户 token 分离。登录过期时，Runtime 只允许在有限期限内
  补交已执行结果/outbox，不得领取新 Invocation；
- `upload(localHandle, sessionRef)` 与 `saveArtifact(artifactRef)` 都由 Main 使用当前可信身份解析授权、
  上传/下载和落盘；删除 Renderer 传入 URL、Authorization、tenantId 或绝对路径的正式接口；
- pending command 的处置遵守下一节，不因会话校验或重新登录自动生成新 command_id；只有恢复到
  同一账号/tenant 且 receipt-first 规则允许时才继续。

### 6.3 PendingCommandJournal

为解决“请求已发出但 Renderer/Main 在 accepted 前崩溃”的不确定性，Main 维护独立 journal：

- 写网络前原子保存 command_id、command kind、session/run ref、canonical request digest、加密 payload、created_at 和 retry state；
- 只使用 safeStorage 包装的专用 journal key，文件权限最小化；不复用 credential key-value 文件，也不写明文客户内容；
- 收到 accepted/already_accepted 后保存 run_id 并删除敏感 payload；终态或明确拒绝后清理记录；
- 重启恢复先调用 `getCommandReceipt(command_id,digest)`；只有服务端明确“未接纳”、当前身份/版本/
  target 仍匹配且下表允许时，才重放原请求。查询不可达时保持 pending，不盲发；
- canonical digest 使用 RFC 8785 JSON Canonicalization + SHA-256，包含 command kind、协议语义版本、
  typed SessionRef、run/wait/version、保持顺序的 input blocks 以及 artifact id+digest；排除 token、
  临时 URL、UI 文案和本地路径；
- journal 上限 100 条或加密后 4 MiB（先到者为准），单条 payload 使用协议输入上限；重试最多
  5 次，退避 1/5/30/120/600 秒+jitter，达到上限后转人工处理，不丢弃；
- command 的恢复策略固定如下：

| command | 自动恢复条件 | TTL/过期收敛 |
|---------|--------------|--------------|
| submit | 同 user/tenant/session、协议兼容；receipt 未找到后可重放 | 24h；过期后保留摘要并要求用户重新确认新任务 |
| append supplement/correction | Run 仍非终态、expected version/目标仍匹配 | 30min；否则转“未发送的补充”草稿，不创建新 Run |
| clarification reply | wait_ref 仍 active 且未过 deadline | 取 wait deadline，最长 24h；过期后按服务端规则提示作为新任务提交 |
| approval | 只查询 receipt；跨重启绝不自动重放未接纳的批准/拒绝 | deadline 到期或状态变化后作废，要求重新查看当前审批 |
| cancel | Run 仍可取消且 receipt 未找到 | 24h；终态视为已收敛，不把 cancelled 当作一定未执行 |

- logout/切租户先 reconcile。已 accepted 只保留非敏感 receipt；未确认 payload 按原 subject 加密隔离，
  不在另一账号下重放，默认 24h 后清理；显式撤权立即停止重放；
- major 协议升级后旧记录进入只读 safe mode。用户可以查看脱敏摘要、丢弃单条、确认清空全部或
  导出不含 payload/token 的诊断；journal 损坏时同样不自动重发，并保留原文件供受控诊断。

Renderer 的“提交中”只是本地状态。只有服务端 accepted/snapshot 才能建立 Run 卡片。

## 7. RunProjection 技术实现

RunProjection 是纯 reducer，可被 Desktop/Web 共用，不发网络、不读 Electron、不持有凭据：

```text
输入：RunSnapshot | RunEvent | reset_required
输出：{ state: RunViewState, effects: ProjectionEffect[] }
```

必须满足：

1. reducer 发现 `event.seq > last_seq + 1` 时不改变权威状态，只返回
   `gap_detected(expected,actual)`；SubscriptionManager 负责补拉。服务端返回 `reset_required` 后，
   orchestrator 拉 snapshot，再将 snapshot 作为新输入；reducer 自己不发网络。
2. RunSnapshot 最少携带 `run_id/session_ref/status/phase/run_version/snapshot_seq/updated_at`，以及
   当前 `wait_descriptor/blocked_by/result_ref/error/capabilities`；终态还必须有
   `terminal_at/terminal_version`。缺少必填字段视为协议错误，不用默认值猜测。
3. event `seq <= last_seq` 幂等忽略；`seq == last_seq + 1` 且 event run_version 不旧于当前可接受
   版本时应用；seq 连续但语义版本倒退时记录 protocol violation 并请求 snapshot。
4. snapshot 仅在 `run_version` 更高，或相同 version 且 `snapshot_seq` 更高时替换；完全相同的
   version/seq/digest 幂等忽略，同 identity 不同 digest 视为协议违例。同一 Run 的旧 snapshot
   不能覆盖新状态。
5. terminal snapshot/event 建立单向闩锁；后续非终态 snapshot、delta 或 progress 都不能使
   completed/failed/cancelled 回到 running。更高 terminal_version 的服务端纠正只能更新终态摘要，
   不能重启执行。
6. 未识别的 optional/minor progress event 记录 telemetry 后忽略并继续；未知 status、wait_kind、
   command result 或 protocol major fail-loud，返回 `update_required`，不能映射为 running。
7. `reset_required` 清除旧 ephemeral delta/progress，以新 snapshot_seq 为基线；
   `assistant.response_delta`、verbose 和 tool progress 永不成为 canonical 消息。
8. `cancel_requested` 与 `cancelled` 分开；四类 waiting 保留 wait_ref、deadline、权限和动作；
   `reconcile_required` 独立阻断；queued/blocked_by 与 active Run 分开。
9. terminal 首次生效时 reducer 返回 `conversation_refresh_required(session_ref,result_ref)` effect；
   orchestrator 通过 `SessionClientPort.listMessages(after_cursor)` 拉 canonical Conversation。刷新失败
   只显示“结果同步中”，不把本地 delta 拼成最终消息。
10. `local_pending`、离线、重连、草稿、选中项和未发送补充属于独立 `DesktopUiState`，不是
    RunProjection 输入，也不写回 Run status。

## 8. Runtime Bridge 与 DeviceClient

### 8.1 ownership

- 开发阶段先支持 external attachment，便于复用现有 `agent-tool-runtime`；
- Desktop 首发时，同一安装包内置与 headless Runtime 共用核心的 managed child。产品支持：
  `interactive+runtime`、`interactive-only`、`runtime-node` 三种模式；远端/无 GUI 节点继续使用独立
  headless Runtime。模式只改变 UI 与进程生命周期，不改变 Device API 或执行语义；
- managed child 不安装为系统服务，不在首次登录后静默开启写工具；用户启用“此电脑可执行本地工具”
  后才配对并启动。P1 可以提供用户明确启用的托盘常驻和当前用户登录后启动，但系统级 daemon、
  多用户服务与无人值守系统启动仍属 P2；
- 产品模式由 Main 的受保护设置和启动参数共同决定：正常启动进入完整桌面或仅交互模式；受控的
  `runtime-node` 启动只显示托盘/设备/Provider/诊断面，不创建会话窗口。模式切换不得改变
  installation/device identity，也不得绕过登录、租户绑定或 Runtime 配对；
- managed child 由 Main 启动、停止和 drain，使用一次性 bootstrap secret 建立受认证本地通道；禁止无认证 localhost 控制端口；
- ownership record 包含 runtime instance、PID、epoch、启动者、产品模式和能力版本。关闭会话窗口不
  停止已启用的 runtime-node；“退出界面”与“停止执行节点并退出”是两个不同动作。Main 只停止自己
  拥有的 child；external attachment 不随窗口关闭而结束；
- installation identity 代表这份安装，device registration 绑定 tenant+user+installation。切账号/租户
  必须先停止旧身份新 claim、受限补交旧 outbox、注销 attach，再为新身份配对；设备 token 不能复用；
- external Runtime 不做局域网广播发现。用户显式选择后，Main 通过受认证本地 socket/loopback
  handshake 校验 installation/device/tenant/protocol/owner；不匹配则拒绝 attachment；
- managed Runtime 二进制与本地 schema 跟随 Desktop release；Provider 独立版本化并由第一方 catalog
  管理。Runtime 升级先 drain，创建迁移备份/marker，健康检查失败回滚 binary 或进入只读 safe mode，
  不直接降级已升级 schema；Provider 升级按 §8.3 独立原子切换。

生命周期与用户行为冻结如下：

| 触发 | Runtime 行为 | UI 文案/操作 | 云端影响 |
|------|--------------|-------------|----------|
| 首次登录 | 不自动执行；提示用户选择是否启用此电脑，启用后 pair/start | “启用此电脑执行本地工具” | 未启用时不注册可执行 capability |
| 普通关窗/退出会话界面 | 已启用 runtime-node 时转托盘继续；未启用时只关闭 UI | “云端任务继续；此电脑执行节点仍在运行/未启用” | 不取消 Run；已启用节点保持在线 |
| 停止执行节点并退出 | 停止新 claim，当前动作到安全点，fsync journal/outbox 后限时退出 | 有未 ACK 写结果时提示“正在保存执行结果”；允许等待或紧急退出 | 设备离线；已 accepted Run 继续/进入 waiting_device |
| 暂停设备 | 默认停止所有新 Invocation；当前只读可完成，当前写动作只在安全点停 | “暂停接收新任务”；另设“紧急停止当前动作” | 不等同取消 Run，也不自动换设备 |
| 系统睡眠 | 不承诺继续；恢复后新 epoch/heartbeat，先补交 outbox 再 claim | “设备已离线/正在恢复” | 依赖设备的 Run 明确等待 |
| 登出/撤权 | 立即停止新 claim；仅用原 invocation/result 身份在受限期限补交旧事实，然后清除用户 token/attach | “已停止接收新任务；仍有 N 条执行结果待确认” | 禁止新副作用，历史事实不能伪装未执行 |
| 外部 Runtime 已运行 | 验证后 attach 同一实例；不能 attach 时拒绝启动 managed child | 选择“使用现有 Runtime”或“停止现有 Runtime 后重试” | 保持同设备单执行宿主 |
| 更新/回滚 | 有 active effect 或未 ACK outbox 时先 quiesce；超时默认延后更新 | “本地任务尚未安全收敛，更新已延后” | 不重跑 Invocation |

正常退出/更新的 drain 默认最多等待 30 秒，可配置但有硬上限；超时且 outbox 已持久化时允许退出并
明确提示稍后补传。journal/outbox 无法持久化或 effect 未知时默认阻止普通退出/更新；只有用户二次
确认的紧急退出可以继续，并必须留下本地 reconciliation marker。

### 8.2 DeviceClient

Runtime—not Renderer—实现：

```text
register / heartbeat / capabilities
claim_invocation / accept / reject
progress / result / evidence
durable_result_receipt / reconcile_report
```

每次结果绑定 tenant、device、runtime epoch、invocation、run fence、参数摘要、结果摘要和 provider release。Runtime 只执行服务端已授权 Invocation：

- 参数/版本/能力不匹配时 reject，不做猜测性兼容；
- effect 前持久 journal，effect 后先存结果/outbox 再回传；
- ACK 前可以重发同一结果，但不能再次执行；
- 写动作取消仅在安全点生效；结果未知进入 local blocked，并向云端报告 reconciliation；
- Renderer 只能查看状态和发出“暂停接新任务/请求退出”等管理意图，不能提交 `tool.completed`。

### 8.3 第一方 Provider 按需安装

Runtime—not Renderer—实现 `ProviderPackageManager`。标准流程为：

```text
Invocation(provider_id, release_id, manifest_digest)
  → 检查本地 inventory 与租户/设备策略
  → 缺失时向受控 catalog resolve 并领取短期 download ticket
  → 下载到 staging
  → 校验 TLS/redirect、publisher signature、SHA-256、manifest、platform/arch、min_runtime
  → 解压到版本化目录并执行只读 version/doctor
  → 原子登记 installed；Invocation 固定该 release 后启动 Provider Host
```

实现规则：

- catalog 由服务端持有可信 release 元数据和撤回状态；Runtime 只接受已批准的第一方 publisher。
  Invocation、模型、Renderer 均不能传 URL、entrypoint、环境变量或任意安装路径；
- 每个下载使用短期、单设备、单 release 票据，限制大小和重定向 origin。包签名与 digest 双校验，
  staging 失败完整清理，正式目录只以原子 rename/switch 暴露；
- 版本化目录允许新旧版本并存。在途 Invocation 固定 release，完成后才可回收旧版本；撤回版本停止
  新调用但不抹掉已发生事实，紧急安全撤回按服务端策略拒绝启动并要求人工收敛；
- 安装状态持久化为 `resolving/downloading/verifying/installing/ready/failed/quarantined`，有稳定错误码、
  重试上限和退避。安装/更新不是业务 effect，失败不得触发 Provider 工具重试或换设备；
- Provider 运行仍遵守 invocation journal、资源锁、超时、取消、result outbox 和 unknown effect；包管理
  不能删除未 ACK 结果或正在使用的 release；
- heartbeat/capabilities 上报 inventory 摘要和状态，服务端据此决定等待或拒绝；云端只使用批准 catalog
  的 schema，不能信任 Runtime 动态上传 schema；
- 迁移期保留既有人工安装 Provider 的兼容读取。自动按需安装只在 Provider Gate 后开启；D00 必须
  盘点当前 BOSS Runtime 客户的包格式、版本、升级方式和回滚要求，不能为新下载链破坏现网。

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
- Device Panel：完整桌面/仅交互/执行节点模式，managed/external、online/capability、paused/
  draining/update-required，以及第一方 Provider 的版本、安装/校验/隔离状态和修复入口；
- 明确提示匿名/transient 场景不支持后台继续，不把它包装成 durable Run。

### 9.1 首屏与选中规则

登录后默认进入 `/agent`：

```text
┌ Session Rail ─────┬ Conversation / Artifact ─────────────┬ Run / Device ┐
│ 搜索、新会话       │ canonical messages                  │ active/queue │
│ active / unread   │ progress cards（可折叠）             │ wait/deadline│
│ recent sessions   │                                      │ device state │
├───────────────────┴──────────────────────────────────────┴──────────────┤
│ Composer：新任务 | 补充/纠正当前 Run | 回复澄清（按状态只显示可用动作） │
└────────────────────────────────────────────────────────────────────────┘
```

选中优先级固定为：受权 deep link 的 run/session → 本机上次选中且仍有权限的 Session → 最近
active/waiting/unread Session → 最近 Session → 空状态。Run deep link 先校验 Session 归属，不允许仅凭
run_id 打开。一个 Session 内默认选 active Run；无 active 时选最近终态，同时明确展示 queued 列表。

720×500 时 Rail 收成图标+状态点，Run/Device 面板变为右侧抽屉，Composer 保持可见；不得隐藏
waiting deadline、取消请求或权限错误。恢复窗口/刷新后焦点回到原选中消息或 Composer，不跳到页面顶部。

### 9.2 页面状态和三动作

| 状态 | 主界面 | Composer/动作 |
|------|--------|---------------|
| 首次/空会话 | 产品说明、创建会话 | 只显示“发起新任务” |
| loading/reconnecting | 保留最后 snapshot，顶部“正在重新连接” | 新写命令暂存但不宣称已发送；危险动作禁用 |
| queued/blocked | 显示前序 Run 和预计顺序，不伪装 running | 可取消本 queued Run；新任务继续排队 |
| running | 显示阶段、progress、设备和 cancel-request 状态 | “补充”“纠正”“停止”分开；“新任务”明确会排队 |
| waiting_clarification | WaitCard、问题、deadline | 主动作“回复澄清”；普通新任务是独立入口 |
| waiting_approval | 展示动作摘要、风险、deadline 和审批主体 | 无 `can_approve` 时只读；批准/拒绝必须二次确认 |
| waiting_device/tool | 展示目标设备/工具、恢复条件 | 允许选择设备仅提交 intent，不承诺立即切换 |
| reconcile_required | 展示“结果尚待核验”和 evidence 摘要 | 不提供普通重试；打开受控 Web 核验入口 |
| terminal | canonical final、Artifact、错误/取消事实 | Composer 回到“发起新任务”；迟到补充转草稿并提示 |
| update_required/revoked | 全屏阻断或只读诊断 | 禁止所有写命令，提供更新/重新登录 |

三动作不能只靠输入框文案猜测：顶部/Composer 明确选择“新任务”“补充当前执行”“纠正当前执行”。
append 在点击时绑定 run_id + expected version；若 terminal 竞态发生，客户端保留文字并让用户确认
“作为新任务发送”，不能静默转换。clarification 只在 active wait_ref 下出现，回复过期后同样不丢文本。

Artifact 上传由 Main 选择文件并向 Renderer 返回 opaque handle + 脱敏元数据；UI 先显示文件名、大小、
目标 Session 和数据将发送到云端，确认后让 Main 流式上传。下载/保存显示服务端元数据和本地目标，
调用 `saveArtifact(ref)`。设备切换显示当前/目标设备、能力和“不会自动重做已执行动作”。

键盘基线：`Ctrl/Cmd+K` 搜索 Session，`Ctrl/Cmd+N` 新任务，`Ctrl/Cmd+Enter` 发送当前已选动作，
`Esc` 关闭抽屉/弹窗但不取消 Run。所有状态变化通过 aria-live 适度播报；审批/错误先聚焦标题，
消息和 Composer 的屏幕阅读器顺序与视觉顺序一致。

### 9.3 通知策略

- 默认隐私模式：OS 通知标题只显示“AID Work Agent”，正文使用“任务已完成/需要补充信息/需要审批/
  设备需要处理/结果需要核验”等类别文案，不展示客户正文、工具参数、文件名或渠道联系人；
- App 在前台且目标 Run 当前可见时抑制 OS 通知，只更新站内卡片和未读状态；窗口隐藏、其他 Session
  或应用不在前台时才尝试系统通知；
- 去重键使用服务端 `notification_id`，本机只记录“已展示”投影；已读通过 Notification API 同步。
  多设备可能各展示一次系统通知，不承诺 OS 层 exactly-once，但点击后都跳到同一 typed SessionRef/run_id；
- 优先级：approval/reconcile 为高，clarification/device 为中，terminal 为普通。过期通知点击后先拉
  当前 snapshot；不得提交旧 wait/approval，也不得因 progress 缺失重跑 Run；
- OS 权限被拒绝时，站内 Notification Center、未读 badge 和前台 banner 必须完整可用，并在设置页
  提供打开系统设置的受控操作；
- P1 不提供把正文显示到系统通知的开关；后续若增加，必须由用户显式 opt-in 且受企业策略限制。

## 10. 分阶段实施

### DC0：重基线与删除清单（可立即并行）

工作项：

1. 固定当前 commit，盘点 `contracts/desktop-agent`、`src/desktop_agent`、Gateway、Coordinator core、Runtime、CLI 的真实调用方。
2. 将旧 D1/Coordinator 标记 frozen，禁止继续扩展；形成删除、迁移、保留三类清单。
3. 复核现有 A～C 完成证据：Desktop/Web build、Windows smoke、macOS 行为、安全与 artifact 门禁。
4. 冻结 Renderer/Main/Runtime threat model、token 所在进程、完整桌面/仅交互/执行节点模式、
   managed/external ownership、退出/托盘/登录启动语义和日志保留。
5. 与 P0 B01 对齐正式协议源 owner、生成命令、版本规则和 golden examples。
6. 盘点 BOSS/weixin Provider 的既有部署与客户升级路径，形成 package/catalog/signing/compatibility
   ADR；没有签名与回滚证据前，不启用自动按需下载。ADR 必须冻结：
   - reproducible build、测试/SBOM、签名、immutable object 上传、review、publish、灰度和 revoke 的
     最小管理用例与角色，上传不自动发布；
   - catalog record/config 的权威 owner、数据库/对象存储位置、审计/备份/恢复和撤回 SLA；
   - Ed25519 package 签名、Runtime 内置 trust root、KMS/HSM/离线私钥边界、key id、双钥轮换窗口和
     紧急 denylist；操作系统代码签名只是附加门禁；
   - package-neutral runtime manifest 与 immutable package envelope 的字段边界；envelope 至少包含
     provider_release_id、provider/version、platform/arch、format/size、package/manifest digest、
     min/max Runtime、publisher key、算法、签名和 created_at；同 release id 禁止覆盖内容；
   - archive 解包防护：绝对路径/`..`、symlink/hardlink、设备文件、权限提升位、重复路径、文件数、
     单文件/展开总量/压缩比和磁盘配额限制；完整验签前禁止执行 `doctor/version`；
   - 现有人工安装 Provider 的兼容识别、迁移、失败回退和客户回滚路径。

退出门禁：没有生产依赖会被误删；开发者明确哪些旧代码只能读不能扩；P0/P1 只有一套目标协议。

### DC1a：Desktop 私有模型、Fake 场景与 UI 骨架（可立即并行）

工作项：

1. 在 `frontend/desktop/features/agent/prototype` 或测试目录建立私有 ViewModel、场景 Fake 和事件脚本；
   名称使用 `Prototype*`，不导出为 shared/wire 类型。
2. 按 §9 实现 Session Rail、Conversation、RunCard、WaitCard、三动作 Composer、Artifact 和通知壳。
3. 建立 Main Agent Bridge、SubscriptionManager、PendingCommandJournal 的接口和 Fake transport，但不
   固化 P0 status/event/wait/error 字段。
4. 把 Desktop auth 网络与 token 持久化迁到 Main：preload 不再暴露完整 credential hydrate，Renderer 只读取脱敏 session summary。
5. 用产品场景覆盖空状态、运行、排队、四类等待、reconcile、离线和 terminal；这些是 UI 测试数据，
   不能标记为协议 golden fixtures。

退出门禁：不启动后端/Runtime，Prototype Fake 可演示 §9 页面状态与三个动作；Renderer 无
Node/Provider/凭据直连；生产 bundle 不把 Prototype 类型作为远端协议 adapter。

### DC1b：正式 AgentClient/RunProjection（依赖 G0）

工作项：

1. 从 P0 协议源生成 TS 类型，建立 §6 的正式 Ports 与 wire adapters；内部 Port 不直接暴露原始 DTO。
2. 实现 §7 纯 RunProjection 和 Subscription orchestration，补拉、reset、terminal conversation refresh
   留在 orchestrator。
3. 由 P0 golden examples 生成/校验正式 fixtures，覆盖重复、乱序、gap、reset、unknown minor、
   unknown status、terminal late progress 和 snapshot 竞态。
4. 将 DC1a UI 映射到正式 presentation model，删除/隔离 Prototype→生产 adapter。

退出门禁：同一 contract suite 对 Fake 与正式 adapter 运行；G0 类型不被客户端手改；未知关键语义
fail-loud；RunProjection 不发网络、不包含 DesktopUiState。

### DC2：Runtime Bridge 与 Fake Device（框架可立即并行，正式类型等 G0）

工作项：

1. Main 实现 RuntimeProcessManager 和受认证本地 Bridge；先 external，后 managed child；完整桌面、
   仅交互和执行节点模式使用同一 Runtime core，关闭会话 UI 不终止已启用的执行节点。
2. 抽取/复用 local-tool-host-core；旧 Runtime 的 journal、lock、outbox 通过 characterization tests 固定行为。
3. Fake Device API 完成 register→claim→fake provider→result→ACK。
4. 建立 ProviderPackageManager 状态机与 Fake catalog/package；此阶段不得连接生产下载源。
5. 验证重复 result、断网补传、runtime restart、epoch 变化和 unknown effect。
6. G0/Device Gate 后接正式生成类型和测试服务。

退出门禁：Fake Server 下 Runtime 崩溃/重启不重复 effect；不打开会话 UI 时执行节点仍可工作；
Main/Renderer 不能伪造执行结果或任意 Provider 包；两个启动者不会拥有同一 managed child。

### DC3：真实 Agent API 与通知联调（依赖 G1～G3）

工作项：

1. G1：接 Session/Conversation、submit/get/list/subscribe、command receipt、cancel/append/
   clarification/approval 与 Artifact。
2. G2：验证桌面关闭、Renderer 崩溃、断网、API/runner 重启后的 Run 恢复。
3. G3：接持久通知、全局订阅和 Electron 系统通知。
4. Web 发起/Desktop 订阅、Desktop 发起/Web 查看使用同一 Run，不产生第二执行者。
5. 移除 Agent API Fake 的生产入口；Fake 只留测试构建。

退出门禁：测试租户完成纯云端长任务全链，重连不重复命令/消息，跨租户与修改 Session kind 均 fail-closed。

### DC4：Device Backend、Runtime/Provider 迁移与三条工具链（分层依赖 G0、B04/B05、G2）

先完成三个独立子批次：

1. `D09S-A`（G0 后）：只在 `src/local_tools/` 演进兼容 API、版本协商、reject/reconcile 契约和旧
   Runtime compatibility suite；不得提前创建/修改 P0 Run 表或猜测 repository schema；
2. `D09S-B`（P0 B04/B05 后）：在已合入的 Run/Command/Event schema 与 repository 基线上增加
   local-tool 表到 Run/Invocation/fence/evidence 的稳定引用、独立 migration、repository/隔离环境联调。
   项目禁外键时仍用 Python 层 fail-closed 校验和索引，不以物理 FK 越过数据库规范；
3. `D09R`（Runtime/Desktop）：新 DeviceClient adapter、managed/external attach、最低版本和 result outbox
   迁移；旧稳定 Runtime 与新版复跑同一服务端兼容矩阵。
4. `D09P`（Provider distribution，硬前置为 D00 package/signing ADR 已批准）：实现 draft→reviewed→
   active→revoked 的受权 release 管理用例、immutable object/catalog 存储、Ed25519 签名验证与撤回、
   短期下载票据、Runtime Package Manager、inventory/capability 上报、archive 安全解包、原子升级/
   回滚/隔离和供应链 contract；BOSS CLI、weixin CLI 作为首批参考 Provider，业务 operation/schema
   本身不在此批重写。任何生产下载前必须证明同 release id 不能覆盖对象或元数据。

migration 合入顺序固定为：P0 schema owner 先将 B04 migration 与 B05 repository 基线合入共同集成
基线；Desktop worktree 再按团队策略合入该提交；D09S-B 只能新增排序在后的 migration，并只修改
`local_tools` 所属表/映射和稳定引用，不编辑 B04 已发布 migration。若必须改共享模型，由 P0 schema
owner 先合入，P1 消费；两个 worktree 不同时改同一 migration 文件。

`D09S-A+D09S-B+D09R` 且 G2 真故障链通过后签署 Device Gate，`D09P` 通过签署 Provider Gate。
此后分别完成：

1. 纯云端工具：Desktop/Runtime 离线，Run 仍完成；
2. 本地只读工具：waiting_device→claim→result→Run 继续；
3. 本地写工具：审批→绑定参数/目标→persist-before-effect→证据→ACK；
4. 写动作结果未知：不自动重试，进入 reconcile_required；
5. 解绑/撤权：阻止新副作用，允许受限补交旧结果。

退出门禁：重复 claim/result、Runtime 重启、迟到结果、stale fence 和断网恢复均不重复副作用；
Web/Desktop 发起均可进入同一 Runtime；Provider 缺失时能安全按需安装，签名/摘要/平台/版本/撤回
任一不满足均不执行，升级/回滚不破坏在途 Invocation 或 result outbox。

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
  同批原子发布，坏版本只用更高 SemVer 修复；本 P1 不重写 Desktop 更新服务，也不建设第三方
  插件市场，但必须交付 D09P 的第一方 Provider 受控分发；
- Desktop release manifest 明确记录内置 Runtime 版本与兼容 Provider protocol 范围；Provider release
  独立记录 publisher、digest、min/max Runtime、平台/架构、撤回和回滚目标，不能靠 Desktop 版本猜测；
- release manifest、SBOM、license、audit、ASAR 解包扫描、产物 SHA-256 和签名/notarization
  记录齐全，任何正式门禁失败都不得产生可误认的 release。

退出门禁：D11 完成后只得到 RC candidate；D-final、§15 完成定义、独立测试/CR 和 §14 Sev 门禁
全部通过并由 release owner 签署 Desktop Release Gate 后，才允许 RC/首发。

## 11. 交付批次

| 批次 | 内容 | P0 门禁 | 允许并行 | 禁止混入 |
|------|------|---------|----------|----------|
| D00 | 旧 D1/Coordinator、生产 Runtime/session_tasks 调用方、当前基线与删除/兼容清单 | 无 | P0 B00 | 行为修改 |
| D01 | DC1a 私有 Prototype ViewModel/场景 Fake | 无 | P0 B01 | shared public/wire status |
| D02 | DC1a Fake Cloud/UI harness | 无 | D01、P0 Phase 0 | golden 命名、真实生产路由 |
| D03 | Desktop Agent UI 纵向切片与状态矩阵 | D01 | D02/DC2 | Web 页面大迁移 |
| D04 | Main Auth/Agent Bridge + subscription + command journal + token 迁移 | D01 | D03 | 任意 IPC/明文 journal/Renderer 长期 token |
| D05 | RuntimeProcessManager、执行节点模式 + Fake Device/Provider catalog | G0 前只用内部端口 | D03/D04 | Provider 业务流程、生产下载源 |
| D06 | DC1b 正式 Agent API 生成类型、RunProjection 与 adapter | G0 | P0 Phase 1/2 | 修改 P0 wire 语义 |
| D07 | 真实 Agent API/Run/Conversation/Artifact 恢复联调 | G1/G2 | P0 Phase 3/4 | Device 写工具 |
| D08 | 通知中心和系统通知 | G3 | D07 | 新通知权威 |
| D09S-A | Device Backend 兼容 API、版本协商、旧/新 Runtime contract | G0 | D07/D08、P0 B04/B05 | Run DDL、第二设备调度中心、破坏旧 Runtime |
| D09S-B | local-tool→Run/Invocation 稳定引用、后续 migration、repository/隔离部署 | P0 B04+B05 已合入 Desktop 基线；真实链还需 G2 | D09R/D09P | 编辑 P0 已发布 migration、物理 FK 违规 |
| D09R | DeviceClient + managed/external Runtime 正式适配 | G0；adapter 可依赖 D09S-A 测试表面，Device Gate 还需 D09S-B+G2 | D07/D08 | 本地业务 Run 状态机 |
| D09P | 第一方 Provider release 管理链、catalog、签名包、按需安装/校验/回滚/隔离 | D00 package/signing ADR + G0；可与 D09S-A/B/R 并行，生产启用须 Device Gate | D07/D08 | 第三方市场、任意 URL/代码下载、可覆盖 release |
| D10 | 纯云端/本地只读/本地写三链路，BOSS/weixin 按需安装 | Device Gate（D09S-A+D09S-B+D09R+G2）+ Provider Gate（D09P） | 发布准备 | Browser Runtime |
| D11 | 双平台安全、故障注入、package/RC candidate | Desktop Core Gate | 无 | 新功能扩项、对外发布 |
| D-final | 旧协议删除、文档与首发验收归档，签署 Desktop Release Gate | D11 + 其余全部 | 无 | 未经评审的兼容层 |

共享文件所有权：P0 修改正式 Agent/Device 规范协议源与云端 Run；P1 修改 Desktop 消费者、Main、
Runtime adapter，并在 D09S-A/B 修改现有 `src/local_tools` Device Backend、在 D09P 实现第一方 Provider
package/catalog 表面。D09S-A/B/D09P 不得绕过 P0 contract
owner 改写 Device 语义；需要修改生成器时，先由 P0 合入协议源/生成物，P1 再消费。

## 12. 测试与验证

本计划涉及凭据、IPC、本地副作用、协议、进程生命周期和跨端一致性，所有非纯 UI 批次按高风险流程执行：开发者自测后，由独立测试者和独立 Code Review 分别验证；Sev-0/Sev-1 问题修复后重跑相关门禁。纯展示组件仍需独立验证，但不要求为形式凑无关全量测试。未获得用户明确授权，不提交或推送 Git。

### 12.1 自动化层级

| 层级 | 位置 | 重点 |
|------|------|------|
| 纯领域单测 | `frontend/shared/agent-run/__tests__` | projection、seq/reset、命令 id、本地展示映射 |
| Desktop 组件 | `frontend/desktop/__tests__` | 三动作、等待权限、队列、离线/恢复、通知跳转 |
| Main/Preload | `clients/agent-desktop/tests` | IPC sender、schema、token、journal、订阅和进程 ownership |
| Runtime | `clients/agent-tool-runtime/tests` | claim/fence、journal、lock、outbox、ACK、unknown effect |
| Provider package | Runtime + catalog contract/供应链测试 | ticket、签名/digest、platform/arch、原子安装、版本固定、撤回、回滚、隔离 |
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
- login captcha/restore/single-flight `/me` validation、401 不重试、撤权、logout/切租户；测试证明 bearer token 和
  Artifact Authorization 不进入 Renderer；
- Artifact local handle 伪造、过期、跨 window/account/tenant、选择后文件替换、size/MIME 变化、上传
  中断和跨 Session 权限；Renderer 始终拿不到绝对路径、Authorization 或任意 upload URL；
- 五类 command 的 receipt-first 恢复、TTL、过期、major upgrade、跨账号隔离和 journal safe mode；
- 跨 tenant、修改 Session kind、伪造 approval/tool result/Artifact ref；
- 旧稳定 Runtime 与新版分别执行 pair/heartbeat/claim/started/reject/progress/operation-result/
  reconcile；`session_tasks` lane 不退化；
- external Runtime 已占用时 managed child 不启动；attach 身份不匹配、双启动、暂停、睡眠、登出和
  未 ACK 写结果下退出/更新；
- 完整桌面/仅交互/执行节点模式切换、普通关窗、退出界面、停止节点和当前用户登录后启动；
- Provider 缺失、并发安装、断点/损坏下载、任意 redirect、错误签名/digest、平台/架构或最低 Runtime
  不匹配、release 撤回、磁盘满、安装中 crash、旧版在途、新版回滚和隔离后修复；
- 未授权主体 create/review/publish/revoke、上传未发布、同 release id 覆盖、key 轮换重叠/过期、紧急
  denylist、zip slip、symlink/hardlink、压缩炸弹、文件数/权限/展开大小超限；验签前不得启动任何包进程；
- Web 与 Desktop 分别发起同一种本地工具 Run，均只能经云端 Invocation 到达同一 Runtime；验证
  Renderer/Main 不能构造本地 Provider 业务调用或指定下载 URL；
- 协议 major 不兼容、最低版本不足、服务端滚动升级；
- Windows/macOS sleep/wake、多显示器、缩放和网络切换。

## 13. 安全与数据规则

- Renderer 永远按不可信输入处理；preload 只暴露冻结方法，不暴露 `ipcRenderer`、任意 URL、任意文件或命令。
- API base URL 必须是配置的精确 HTTPS origin；开发 HTTP 只允许 localhost/loopback。
- 长期 token 只持久化于 Main safeStorage；Renderer 迁移后只接收非敏感会话摘要和短期内存态，不在 localStorage 保存 token。
- PendingCommandJournal、Runtime journal 和结果 outbox 分开；前者不成为消息历史，后两者不成为 Run 权威。
- Artifact 上传由 Main 校验 opaque handle、session/tenant、size/MIME/digest 和授权后流式发送；下载由
  Main 校验 origin、tenant、display name 和授权，再写临时文件并原子 rename。Renderer 不见绝对路径。
- 本地日志默认不记录消息正文、工具参数、token、证据原文和绝对路径；诊断包需显式同意和脱敏。
- 未授权 capability、版本不匹配、设备离线、磁盘写失败、结果未知全部 fail-closed。
- 第一方 Provider 自动下载仅允许受控 catalog、短期单 release ticket 和固定 publisher 信任根；
  模型、Renderer、Invocation 参数不得指定 URL、entrypoint、env、命令或安装目录。
- Desktop/Runtime 不能持有 Channel Connector secret，也不能调用内部 Channel Gateway principal。

## 14. 灰度与发布

缺陷严重度与项目优先级分开：

- `Sev-0`：跨租户/凭据泄露、不可恢复数据或外部副作用损坏、大面积不可用、更新供应链失陷；
- `Sev-1`：核心提交/恢复/审批/设备执行错误，可能重复副作用，主流程无可接受规避方案；
- `Sev-2`：重要功能受损但有明确安全规避或可关闭能力；必须有 owner、修复日期、用户影响和批准记录；
- `Sev-3`：低影响体验/文案/非关键诊断问题。

发布门禁：Internal UI Alpha 的启用范围不得有 Sev-0，Sev-1 相关真实能力必须关闭；Contract Alpha、
Cloud Beta 和 Device Beta 的启用范围不得有开放 Sev-0/Sev-1；RC/首发全产品不得有开放 Sev-0/
Sev-1。Sev-2 只有具备安全规避、owner、期限和 release owner 书面接受才可进入 RC；涉及租户隔离、
认证、审批、重复副作用、结果未知、升级/回滚的 Sev-2 一律阻断 RC。

| 阶段 | 用户范围 | 后端条件 | 能力 |
|------|----------|----------|------|
| Internal UI Alpha | 开发/测试人员 | 无真实 P0；Fake Cloud/Device | Shell、Run UI、投影、三动作演示 |
| Contract Alpha | 内部租户 | G0 | 正式生成类型，仍以 Fake/录制事件为主 |
| Cloud Beta | 内部测试租户 | G1/G2 | 真实云端 Run、断线恢复、无本地写工具 |
| Device Beta | 指定测试设备 | Device Gate/G2 | 本地只读，写工具仅批准的预装测试 Provider |
| Provider Beta | 指定测试设备和第一方 catalog | Device Gate + Provider Gate | BOSS/weixin 按需安装、升级/回滚；先只读再受控写工具 |
| RC candidate | 不分发；release owner/测试环境 | Desktop Core Gate | D11 构建、通知、三链路、双平台包、回滚演练 |
| RC | 小范围批准用户 | Desktop Release Gate | 已签名候选、正式支持矩阵、回滚 |
| 首发 | 批准用户 | Desktop Release Gate 持续有效，RC 无 Sev-0/Sev-1，允许项满足上述 Sev-2 规则 | 正式支持矩阵；Browser 不承诺 |

回滚时停止 Desktop 新版本分发和 Device 新 claim；已 accepted 云端 Run 继续由 P0 收敛，Runtime 已执行结果继续受限补传。不得把 Desktop 请求回退到旧 D1 或本地 Coordinator。客户端版本不兼容时进入 update-required/只读诊断，而不是静默改协议。

## 15. 完成定义

- [ ] 旧 D1、outcome 和本地业务 Coordinator 不在生产 Desktop 主链；旧生成协议不再被正式构建引用；
- [ ] Desktop 与 Web 使用同一 Agent API/Run identity，Web/Desktop 同时订阅不会双执行；
- [ ] Renderer/Main/Runtime 权限边界和窄 IPC 通过独立安全审查；
- [ ] 长期 token 只由 Main safeStorage 持久化，Renderer/preload 不再暴露完整 credential hydrate；
- [ ] 当前滑动 token 只由 Main 在 token 尚有效时以 single-flight `/api/auth/me` 校验/续期；401 不用
  同一 token 重试，也没有客户端私造的 refresh credential/endpoint；
- [ ] submit、append、clarification、approval、cancel、command receipt、Session/Conversation、Artifact、
  Notification、Device directory 和 list/get/subscribe 全部使用正式生成类型/受权 adapter；
- [ ] Artifact 选择/上传/下载均由 Main 执行，opaque handle 的 TTL、主体绑定、文件替换检测、流式上传
  和跨租户拒绝测试通过；Renderer 不见绝对路径、token 或任意上传 URL；
- [ ] RunProjection 通过重复、乱序、缺口、reset、terminal 迟到事件和四种 waiting 测试；
- [ ] 关闭窗口、Renderer crash、断网和应用重启不取消或复制云端 Run；
- [ ] PendingCommandJournal 在 accepted 不确定时只重放同一 command，不泄露明文内容；
- [ ] managed/external Runtime ownership 明确，两个启动者不会争抢同一执行宿主；
- [ ] 同一 Desktop 安装包可运行完整桌面、仅交互和执行节点模式；关闭会话 UI 不终止已启用节点，
  停止节点也不伪装为取消 Run；独立 headless Runtime 继续兼容；
- [ ] 旧稳定 Runtime、`session_tasks` 与新版 Device Backend 的兼容、灰度、最低版本、撤权、补传和
  回滚证据完整；
- [ ] P0 B04/B05 先合入，D09S-B 使用排序在后的独立 migration；两个 worktree 未编辑同一 migration，
  local-tool 稳定引用与 Python fail-closed 归属校验通过；
- [ ] 第一方 Provider catalog、签名包、短期票据、安装状态、原子切换、版本固定、撤回、回滚和隔离
  通过安全/供应链评审；BOSS CLI 与 weixin CLI 完成按需安装实测；
- [ ] Web/Desktop 发起的本地工具都只经 RunService→Device API→Runtime→Provider 执行，不存在
  Desktop UI/Main 直调 Provider 或任意包下载旁路；
- [ ] 三条工具链通过，写动作 unknown 不自动重试；
- [ ] 跨租户、伪造 Session kind、approval、tool result 和 Artifact 访问全部拒绝；
- [ ] 站内/系统通知能够跳转正确 Session/Run，漏实时通知可补拉；
- [ ] 系统通知默认隐私模式、前台抑制、notification_id 去重、已读同步和权限拒绝降级通过；
- [ ] Windows/macOS 支持矩阵、安装包、安全、故障注入和回滚记录完成；
- [ ] Web build、边界与关键回归无退化；
- [ ] 所有批次完成独立测试和独立 CR，无开放 Sev-0/Sev-1；Sev-2 满足 §14 门禁；
- [ ] Desktop Release Gate 已由 release、安全和测试 owner 签署；Core Gate 未被误作发布许可；
- [ ] P0 Phase 6 未完成不会让 Desktop 引入渠道 secret/Connector 或第二套渠道生命周期。

## 16. 批次记录

状态规则：某阶段首批实际启动时将顶部状态改为“🔧 进行中”；所属批次全部完成、门禁通过且独立测试/CR 关闭 Sev-0/Sev-1 后，才改为“✅ 完成（日期）”。每批开始时在 `docs/plans/evidence/desktop-p1/<batch>.md` 建立脱敏证据附件；不提交凭据、storageState、原始客户内容、完整日志或大体积安装包。

| 批次 | 阶段 | 状态 | P0 门禁 | 证据附件 | 阻塞/遗留 |
|------|------|------|---------|----------|-----------|
| D00 | DC0 | 📋 待开发 | 无 | — | — |
| D01 | DC1a | 📋 待开发 | 无；正式 wire 等 G0 | — | Prototype only |
| D02 | DC1a | 📋 待开发 | 无 | — | — |
| D03 | DC1a | 📋 待开发 | D01 | — | — |
| D04 | DC1a | 📋 待开发 | D01 | — | — |
| D05 | DC2 | 📋 待开发 | 正式 Device 类型等 G0 | — | — |
| D06 | DC1b/DC3 | 📋 待开发 | G0 | — | — |
| D07 | DC3 | 📋 待开发 | G1/G2 | — | — |
| D08 | DC3 | 📋 待开发 | G3 | — | — |
| D09S-A | DC4 | 📋 待开发 | G0 | — | 禁止提前做 Run DDL |
| D09S-B | DC4 | 📋 待开发 | P0 B04+B05；真实链需 G2 | — | — |
| D09R | DC4 | 📋 待开发 | G0 + D09S-A 测试表面；门禁需 D09S-B/G2 | — | — |
| D09P | DC4 | 📋 待开发 | D00 package/signing ADR + G0；生产启用须 Device Gate | — | — |
| D10 | DC4 | 📋 待开发 | Device Gate + Provider Gate | — | — |
| D11 | DC5 | 📋 待开发 | Desktop Core Gate | — | — |
| D-final | DC5 | 📋 待开发 | 全部 | — | — |

## 17. 开工顺序

收到开发指令后按以下顺序启动，不等待全部 P0 完成：

1. 创建或确认 `feature/desktop-client-p1` 的独立 Desktop worktree，记录其路径、分支和 HEAD；同时用 `git worktree list --porcelain` 定位只读的 P0 worktree。
2. D00：输出旧 D1/Coordinator、生产 Runtime/session_tasks 依赖与兼容/删除清单，确认 P0 G0
   contract owner，并记录参考的 P0 HEAD/Gate。
3. D01/D02：只建立 Desktop 私有 Prototype ViewModel、场景 Fake 和 UI harness；不得命名为
   RunProjection/golden contract，不得创建临时 wire 协议。
4. D03/D04：Desktop UI 与 Main Auth/Agent Bridge 并行；正式 shared 类型必须等 G0/D06。
5. D05：Runtime ownership/Fake Device；不接生产 Device API。
6. G0 到达后，将已提交的 P0 契约基线按团队合并策略纳入 Desktop 分支并执行 D06；G1/G2/G3 到达后依次 D07/D08。
7. G0 后可启动 D09S-A、D09R 的 adapter/Fake 工作与 D09P ADR/本地实现；P0 B04+B05 合入后才启动
   D09S-B migration/repository 联调，G2 后完成真实故障链。D09S-A+B+D09R+G2 形成 Device Gate，
   D09P 形成 Provider Gate，两者都通过后执行 D10；Desktop Core Gate
   后执行 D11/D-final。
8. D00 实际启动时，把本文 DC0 和 `docs/ideas.md` 桌面条目标记“🔧 部分完成”；仅有开工意向时不更新。
