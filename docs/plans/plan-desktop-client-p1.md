# AID Work Agent 桌面客户端 P1 架构与约束基线

> 日期：2026-09-22
>
> 版本：v1.5
>
> 优先级：P1；可按门禁与整体架构 P0 并行，P0 仍拥有资源优先级
>
> 状态：架构与约束基线；实施批次和进度只在[桌面 P1 并行开发执行计划](./plan-desktop-client-p1-implementation.md)登记
>
> 上位约束：[统一 Agent Run P0 设计基线](./plan-unified-agent-run-lifecycle.md)与[P0 开发执行计划](./plan-unified-agent-run-lifecycle-implementation.md)

## 1. 文档定位

本文只规划桌面客户端，不重新定义平台 Run、计费、授权或消息持久化。平台对象和状态所有权由总体架构决定；桌面负责接入并遵守。

旧 Desktop v2.3 设计与 v2.4 开发计划中仍有效的 Shell、Shared、Electron 安全、构建、发布
约束和完成证据已经分别合并到本文与配套执行计划；旧文件已删除，避免与统一 Run 架构形成
双重权威。Local Agent Coordinator、`DEVICE_OWNED` 本地业务权威、旧 D1
`agent/next`/outcome 路线明确废弃，只能通过 Git 历史用于 DC0 删除审计，禁止继续扩展。

桌面目前没有正式使用方，因此：

- 不为旧 D1、旧 outcome 或桌面业务 Coordinator 建兼容层；
- 可以删除、改名或重写未发布的桌面协议和接口壳；
- 不能因为目录名称含 desktop 就删除被生产 Web、CLI 或独立 Runtime 使用的真实能力；
- 首次正式发布后再建立客户端版本兼容和升级责任。

这里的“没有正式使用方”只指未发布的 Desktop D1/Coordinator 产品，不适用于已经独立分发的
`agent-tool-runtime`、`/api/local-tools/runtime/*`、Provider、result outbox 和 `session_tasks`。
这些能力在生产使用盘点完成前承担兼容责任，新 Device API 必须版本化演进而非破坏性替换。

## 2. 必须继承的平台约束

桌面实现不得改变以下事实：

1. 业务 Run 由云端 RunService 唯一推进，Desktop 不拥有第二套 Agent loop。
2. Desktop UI 使用 Agent API；本地 Runtime 使用受限 Device API，两类身份和权限不能混用。
3. Renderer、Main、页面 store 和本地缓存都不是 Run、消息、审批、计费的权威来源。
4. 关闭窗口、刷新 UI、网络断开不等于取消 Run；取消必须提交显式命令。
5. 本地真实动作只能由受信 Runtime/Provider 执行，UI 不能伪造 `tool.completed`。
6. Run、Session、Invocation、Device、Runtime epoch、窗口和网络连接是不同身份。
7. 文件跨端使用受权 ArtifactRef；服务器路径、本地绝对路径和永久下载 URL 不进入公共协议。
8. 设备离线、结果未知或版本不支持时 fail-closed，不静默切换设备或执行路径。
9. 桌面不兼容旧 D1，但必须兼容首发时正式声明的 Agent/Device 协议版本和能力范围。
10. 当前 browser legacy 不纳入桌面首发依赖；未来按 Execution Fabric 独立重构。
11. Agent API 使用 typed `SessionRef(kind,id)`；Desktop 不得根据字符串格式猜测 Web/渠道
    Session，也不能通过修改 kind 获得另一类 Session 权限。
12. “停止”“补充/纠正当前 Run”“发起下一 Run”是三个不同命令；Desktop 必须使用
    `append_input(mode=supplement|correction)`，不能靠普通文本猜测打断意图。
13. clarification 回复与 approval 决定是不同权限：普通外部渠道身份只能回复 clarification；
    Desktop 仅在当前登录主体具备审批权限时显示并提交 `resolve_approval`，Runtime 无审批权。
14. `after_seq` 落入 ephemeral 清理缺口时，RunProjection 按 `reset_required + snapshot_seq`
    替换快照并丢弃不可恢复的旧 progress；不得把 progress 缺口当作 Run 失败或重新提交任务。
15. Channel Gateway/Connector 属云端渠道入口，不进入 Desktop Main/Renderer/Runtime。Desktop
    如获授权查看渠道 Session，也只通过 Agent API 读取 typed Session/Run 投影。
16. Web 与 Desktop 是同一云端 Agent 的两个交互入口：两者都把命令提交给云端 RunService；
    即使 Runtime 与 Desktop 安装在同一台电脑，Desktop UI 也不得绕过云端直接调用 Provider。
17. Desktop 正式安装包必须内置与独立 `agent-tool-runtime` 共用核心的 Runtime Host；同一安装包
    既可运行完整桌面界面，也可在不使用会话 UI 时作为受信执行节点运行。两种形态共用 Device API、
    设备身份、journal、资源锁和 result outbox，不形成两套 Runtime。
18. BOSS CLI、weixin CLI 等第一方 Provider 不永久塞入主安装包；P1 必须提供受控 catalog、签名包、
    按需安装、版本固定、回滚与隔离能力。模型和 Invocation 都不能提供任意下载 URL 或可执行路径。

## 3. 桌面目标结构

```text
Desktop Renderer
  会话、Run 状态、进度、审批、通知、草稿
        │
        ▼
Preload / Narrow IPC
  参数校验、最小平台能力暴露
        │
        ▼
Desktop Main
  窗口、系统通知、文件对话框、凭据访问、Runtime 进程管理
        │
        ├──────── AgentClient ────────> Cloud Agent API
        │                                      │
        │                                      ▼
        │                                  Cloud RunService
        │                                      │
        │                               authorized Invocation
        │                                      ▼
        └─ Runtime Control Bridge ───> Embedded Runtime <──── Cloud Device API
                                          │
                                          ├─ Provider Package Manager <── signed first-party catalog
                                          └─ Provider Host ─────────────> BOSS/weixin/... Provider
```

逻辑职责可以部署在较少进程中，但依赖方向和权限边界不能合并。`Runtime Control Bridge` 只管理
配对、启停、暂停、升级和诊断；业务工具调用仍由云端 RunService 产生 Invocation，再由 Runtime
通过 Device API 领取。Web 发起与 Desktop 发起最终进入的是同一条执行链。

### 3.1 Renderer

允许：

- 展示云端 Session、Run snapshot、事件投影和持久通知；
- 管理草稿、布局、当前选中项和乐观的“提交中”状态；
- 提交 Run、显式补充/纠正、取消、经授权审批、澄清回复和设备选择意图；
- 通过受限平台端口选择文件、保存产物和打开外链。

禁止：

- 运行模型循环或决定 Agent 下一步；
- 直接调用 Provider、数据库、任意本地命令或任意路径；
- 将 loading、已点击按钮或本地 store 当作业务完成事实；
- 持有设备长期私钥或直接提交工具成功结果。

### 3.2 Desktop Main

允许管理窗口、托盘、系统通知、安全凭据、文件对话框和 Runtime 进程生命周期。

Main 不保存客户任务下一步、不维护权威 Run 状态、不实现工具快捷执行旁路。所有 IPC 都按不可信 Renderer 输入校验；不得暴露万能 `executeNative(method,args)`。

长期 bearer token、会话校验/滑动续期、401/撤权处理和 Artifact 上传下载授权全部留在 Main；Renderer 永远只获得
脱敏 AuthSessionSummary，不接触 token，也不能向下载 IPC 提供 URL、Authorization 或 tenantId。
P1 只支持单 active tenant，不支持平台管理员代租户；切换身份必须先 drain 旧命令/Runtime。

### 3.3 内置 Runtime 与产品形态

Runtime 只负责授权范围内的设备执行事实：

- 设备身份、配对、能力 manifest 和 runtime epoch；
- invocation 接纳、参数摘要、资源锁和本地 journal；
- Provider 生命周期与真实动作；
- 关键证据、结果 outbox、ACK 和断网补传；
- unknown effect 的保守阻断和人工核验入口。

Runtime 不建立业务 Run、不调用云端模型决定下一步、不覆盖服务端计费、不把本地 UI 选择当作最终授权。
本地 Runtime Provider 与云端 Channel Connector 是两类不同扩展：前者执行受信设备 Invocation，
后者转换第三方消息入口/出口；不得为了复用插件框架让 Provider 绕过 Device API，或让 Connector
进入本地工具执行路径。

Desktop 安装包不是“UI 外加一个可选的另一产品”，而是同一产品中的交互面与执行面：

| 运行形态 | 交互界面 | 本机 Runtime | 典型用途 |
| --- | --- | --- | --- |
| 完整桌面模式 | 有 | 用户启用后运行 | 在 Desktop 对话，也允许这台电脑执行 BOSS/微信等本地工具 |
| 仅交互模式 | 有 | 关闭 | 只把 Desktop 当云端 Agent 入口，本地工具可由其他已授权电脑执行 |
| 执行节点模式 | 仅托盘/状态与设置，不要求打开会话 UI | 有 | 同一安装包充当 Runtime 客户端，供 Web、Desktop 或渠道发起的 Run 使用 |
| 独立 headless Runtime | 无 | 独立包 | 兼容既有生产客户、服务器/VM 和无 GUI 设备 |

完整桌面模式与执行节点模式使用同一内置 Runtime 二进制和数据目录约束；独立 headless Runtime
继续作为受兼容保护的部署形态。关闭会话窗口不等于停止执行节点，停止执行节点也不等于取消云端 Run。

### 3.4 产品壳、UI 与依赖边界

- `frontend/desktop` 是独立 Desktop renderer，不加载 `frontend/web` 的 App、路由、页面外壳或
  Portal；Web 与 Desktop 只通过经过验证的 `frontend/shared` 纯类型、客户端、投影和展示组件复用。
- `clients/agent-desktop` 长期作为 Electron Main/Preload、系统能力、Runtime 管理、打包、签名和
  更新边界存在，不能在 UI 完成后删除；Vue 页面不得放入该目录。
- 正式客户端只加载安装包内静态资源并连接配置的 HTTPS/SSE 服务，不加载远程页面后赋予
  Electron 权限。低频、未桌面化的管理页只能通过 HTTPS/host allowlist 在系统浏览器打开。
- Desktop 自己定义 Session、Run、Device、Settings 等路由和导航；`/portal/**` 永不进入安装包。
- 720×500 是安全最小窗口，推荐默认 1200×800；100%～200% 缩放、键盘路径、焦点态、
  可访问名称和错误恢复都属于首发门禁。窄窗口使用桌面紧凑布局，不模拟移动端抽屉。
- Windows/macOS 共用同一 renderer；菜单、快捷键、窗口、权限、更新和进程管理差异封装在
  Platform Driver，不复制两套业务 UI，也不在业务组件中散落 `process.platform` 判断。

## 4. 远端契约与第一方 Provider 分发

### 4.1 Agent API

供 Web、Desktop 等交互客户端使用；云端 Channel Gateway 在服务端内部复用同一
AgentApplication 语义，但具体 Connector 不作为 Agent API 客户端直连 RunService：

```text
create_session / list_sessions / get_session
list_messages(after_cursor)       # canonical Conversation
submit(typed_session_ref)
append_input(run_id, expected_version, mode)
get_run / list_session_runs(typed_session_ref)
subscribe(after_seq)
get_command_receipt(command_id, request_digest)
cancel
resolve_approval                 # 仅授权主体
reply_to_clarification(wait_ref, expected_version)
upload_artifact(typed_session_ref, metadata, content_stream)
get_artifact_metadata / authorize_artifact_download
list_devices / get_device / select_device_intent
list_notifications / mark_notification_read
```

G0/B01 冻结除 Notification 外的上述基础 DTO、cursor、权限摘要和错误语义；G1/B11 才表示真实接口
已经实现并可联调。Notification DTO 与服务端表面到 G3/B13 才成为正式契约，G0 前后的通知 UI
只能使用 Desktop 私有 ViewModel/Fake。

Artifact 上传和下载都由 Main 执行：文件选择器只向 Renderer 返回短期 opaque local handle 与脱敏
文件名/大小/MIME，不返回绝对路径；用户确认目标 typed SessionRef 后，Main 重新校验 handle、当前身份、
大小、MIME 和权限并流式上传。Renderer 不能传 Authorization、tenantId、任意上传/下载 URL 或路径。

Desktop 直接使用统一新契约，不使用旧 D1 `agent/next` 或 outcome 循环。
该方法是 P0 `ReplyToClarificationCommand` 的客户端表面，必须携带稳定 command_id、run_id、
wait_ref、expected_run_version 和 input；Desktop 不实现自己的 wait-resume 状态机。
即使 Desktop 展示渠道会话，也不能调用 ChannelGateway、持有渠道 secret 或伪造
`TrustedChannelPrincipal`；它提交的是当前登录用户在 Agent API 上被授权的管理命令。

### 4.2 Device API

只供已配对 Runtime 使用：

```text
register / heartbeat / capabilities
claim_invocation / accept / reject
progress / result / evidence
durable result receipt          # 服务端持久 2xx 即 outbox ACK
reconcile_report
```

工具结果必须绑定 tenant、device、runtime epoch、invocation、run fence、参数摘要和结果摘要。知道 run_id 不足以提交结果。

Device API 以现有 `/api/local-tools/runtime/*` 为兼容起点，而不是另建第二套设备队列。P0 B01
拥有规范协议；Desktop P1 Device Backend 子轨负责现有服务端 endpoint、表族、旧/新 Runtime
兼容和测试环境。`operation-result` 的持久 2xx 即 result outbox ACK；旧稳定 Runtime 至少跨两个
发布窗口受支持，停止旧版新 claim 后仍允许原 invocation 身份受限补交历史结果。

### 4.3 第一方 Provider Package API

P1 为 BOSS CLI、weixin CLI 等第一方 Provider 提供最小可生产的受控分发面：

```text
# 仅 release CI/受权运营主体
create_release_draft(envelope, immutable_object_ref)
approve_release(provider_release_id, review_evidence)
publish_release(provider_release_id, rollout_policy)
revoke_release(provider_release_id, reason, effective_at)

# 已配对 Runtime
resolve_release(provider_id, platform, arch, runtime_version, policy_ref)
issue_download_ticket(provider_release_id, device_id)
download_signed_package(ticket)
report_installation(provider_release_id, manifest_digest, status)
```

release 管理面和 Runtime 下载面使用不同 principal/capability；Desktop 用户、Renderer、模型和普通
Runtime 都不能创建、审核、发布或撤回 release。

- Device Invocation 只引用服务端已批准的 `provider_id + provider_release_id + manifest_digest`，
  不携带任意 URL、命令、环境变量或可执行路径；
- Runtime 先检查本地版本缓存。缺失或版本不匹配时，按租户策略从第一方 catalog 获取短期下载票据，
  校验发布者签名、SHA-256、平台/架构、manifest、最低 Runtime 版本和撤回状态后原子安装；
- 安装到版本化目录，Invocation 固定到具体 release。在途调用完成前保留旧版本；失败版本进入隔离，
  不覆盖可用版本，也不偷偷改用另一版本；
- 安装期间云端 Run 显示明确的 `waiting_tool`/工具准备进度；下载或校验失败以稳定错误码收敛，
  写工具不得因安装失败绕路执行；
- heartbeat/capabilities 上报 `available/installing/quarantined/update_required` 等 Provider 状态，
  但客户端上报的 schema 不能直接进入模型工具清单；
- P1 自动按需安装仅限平台签名的第一方 Provider。第三方市场、开放上传和社区信任治理仍属 P2。
- 可信 release 只能经“可复现构建/测试/SBOM → immutable package+envelope → Ed25519 签名 →
  审核 → 发布/灰度 → 可审计撤回”进入 catalog；上传不自动发布，同一 `provider_release_id` 永不
  覆盖内容。D00 必须冻结 catalog/对象存储 owner、trust root/密钥轮换/紧急撤回和管理权限。

## 5. 共享客户端边界

桌面可以与 Web 共享：

- `AgentClient`：命令、查询、订阅、版本协商；
- `RunProjection`：事件去重、顺序归并、快照替换和缺口检测；
- 展示模型：Run 状态、等待对象、工具卡片、Artifact 与通知；
- Agent 协议生成类型和行为样例。

不共享：

- Electron、浏览器 DOM 或系统 API 具体实现；
- Runtime/Provider 执行代码；
- 页面级导航和布局状态；
- 模型循环、工具策略和服务端权限判断。

AgentClient 的网络重试必须遵守命令幂等语义。查询可以重试；提交、取消和审批使用稳定 command_id；不能用“重连”重新发送原始用户消息。
RunProjection 遇到 `reset_required` 时以服务端 snapshot 为基线清除旧 ephemeral progress，再从
`snapshot_seq` 继续；terminal snapshot 优先于残留 progress，不能在桌面端自行补造事件。

Shared 采用单模块迁移，不做一次性搬家：先建立无平台依赖的真实实现，Web 原路径保留短期
re-export，Fake/真实适配器复跑同一 contract tests；Web typecheck、测试、production build、
路由/认证和 bundle 边界通过后 Desktop 才能消费。`shared` 禁止依赖 Web/Desktop 页面、Electron、
Node 或 `window.agentDesktop`，Desktop 失败也不能让 Web 部署依赖 Desktop 构建产物。

## 6. 桌面生命周期

| 行为 | Desktop / Runtime | 云端 Run |
| --- | --- | --- |
| 关闭会话窗口 | 隐藏或关闭 UI；已启用的执行节点按设置继续 | 不取消，稍后由 snapshot 恢复 |
| Renderer 崩溃或刷新 | 重建 UI 和 RunProjection | 不受影响 |
| 临时断网 | 保留投影；Runtime 保存待回传结果 | 云端等待设备或继续纯云端步骤 |
| 暂停本机自动执行 | 默认停止全部新 Invocation；当前动作在安全边界收敛 | 依赖设备的 Run 进入明确等待 |
| 退出会话界面但保留执行节点 | 关闭窗口，托盘 Runtime 继续 heartbeat/claim | 设备保持在线；不影响由 Web/渠道发起的 Run |
| 停止执行节点并退出 | drain、保存 outbox、停止接单后退出 | 设备下线，不等于取消业务 Run |
| 显式退出但 Runtime 是外部实例 | UI 断开，不终止其他启动者管理的 Runtime | Runtime 可在授权范围内继续 |
| Runtime 崩溃/电脑重启 | journal 恢复；unknown 动作先核验 | 不自动改派另一设备重发 |

桌面首版不做系统级 service/daemon 或多用户后台服务；可以提供用户明确启用的托盘运行和当前用户
登录后启动执行节点，且必须可随时关闭。用户显式启用本地工具后，当前电脑优先使用 managed child；
已经运行的同 installation/device external Runtime 通过受认证 attach 复用，不能 attach 时拒绝再启动
一个 Host。登出/撤权停止新 claim但允许受限补交旧 outbox；正常退出/更新先 drain，effect unknown
或 outbox 无法持久化时默认阻止退出。

## 7. 安全与本地资源约束

- 优先使用权限受限的本地 IPC；如使用 localhost 端口，必须有会话认证，不能信任“来自本机”。
- Electron 固定 `nodeIntegration=false`、`contextIsolation=true`、`sandbox=true`、
  `webSecurity=true`；自定义 scheme 为 standard/secure 且不绕过 CSP。
- 所有 IPC 校验 sender、main frame、方法白名单、参数 schema、大小、版本和 capability；禁止
  暴露原始 `ipcRenderer`、任意 URL、任意路径、命令、脚本或更新地址。
- Renderer 不读取设备长期秘密；密钥存储由 Main 或 Runtime 的受限能力负责。
- 同一交互桌面、应用实例、账号和目标窗口使用明确资源锁；定位目标、输入和提交属于同一个受保护动作窗口。
- Desktop 与独立 CLI 同时运行时，必须连接同一执行宿主或拒绝冲突，不能产生两个互不知情的控制器。
- Local Host 位于隔离 utility/child process，Provider 再作为 Host 子进程；Provider 崩溃、超时、
  输出过大或取消只能终止当前调用，不能拖垮 Electron Main 或云端聊天主链。
- Windows 使用 kill-on-job-close 并检查 breakaway；macOS 使用 process group/PID+create-time
  ownership，禁止 Provider daemonize，也禁止按进程名全局清理。
- 本地日志和截图遵循最小化、ACL、保留时间和容量限制；磁盘写入失败时保守阻断新副作用。
- 下载只允许已配置 API origin，校验 redirect、文件名和大小，以临时文件 + 原子替换落盘；
  日志、崩溃和诊断包默认排除 token、验证码、消息正文、文件内容和本地绝对路径。
- Provider 包只允许来自服务端批准的第一方 catalog 和短期签名票据；安装前校验发布签名、digest、
  manifest、平台/架构和 Runtime 兼容性，安装后重新执行只读 `version --json`/`doctor` 验证。
- Provider 包使用版本化目录、原子切换和最小权限运行；下载、解压、安装、回滚和删除均不得接受
  Renderer 或模型提供的任意路径。被撤回或隔离版本不能承接新 Invocation。
- Provider archive 解包拒绝路径穿越、绝对路径、symlink/hardlink、设备文件、权限提升位、重复路径、
  文件数/单文件/展开总量/压缩比超限；完整验签、digest 与 manifest 校验前不得执行 `doctor/version`。
- 解绑或撤权后允许受限补交历史结果，但不得借补传通道接纳新动作。

## 8. 实施阶段

### DC0：基线与删除清单

- 固定仓库 commit、现有桌面/D1/Runtime/CLI 依赖图；
- 标记可直接删除的未发布桌面模块和必须迁移的共用能力；
- 形成 Agent API、Device API、PlatformPorts、Runtime ownership、Provider package/catalog/signing 五份 ADR；
- 停止继续开发旧 D1 和桌面业务 Coordinator。

退出条件：没有未知生产调用方会因清理被误删；桌面团队只有一套目标协议。

### DC1a：Desktop 私有模型、Fake 场景与 UI 骨架

- 在 Desktop prototype/test 命名空间建立 ViewModel、场景 Fake 和页面状态矩阵；
- 完成 Session Rail、Conversation、Run/Wait 卡、三动作 Composer、Artifact/通知壳；
- 不定义正式 status/event/wait/error，不导出 shared public contract，不称为 golden fixtures；
- 把认证网络/token 迁入 Main，Renderer 改用脱敏 Auth Bridge。

退出条件：无需 P0 正式协议即可验证交互，但生产 adapter 不依赖 Prototype 类型。

### DC1b：正式共享客户端与投影（依赖 G0）

- 接入统一契约生成物；
- 实现 Session/Conversation/Agent/Artifact/Device ports 和纯 RunProjection；Notification 只保留内部
  展示端口，正式 Notification DTO/adapter 等 G3；
- 支持 typed SessionRef、append_input、clarification/approval 分权和 reset_required 快照替换；
- 建立窄 Preload IPC、凭据边界和 PlatformPorts；
- Fake/真实 adapter 复跑同一 contract，正式 fixtures 只来自 P0 golden examples。

退出条件：reducer 只返回 gap/reset/terminal-refresh effects，不发网络；Renderer 无 Node/Provider/token 直连。

### DC2：Runtime Bridge 与设备契约

- 复用现有 Runtime 的调用接纳、journal、resource lock、result outbox；
- 将同一 Runtime core 打入 Desktop 安装包，实现完整桌面/仅交互/执行节点三种模式；
- 实现设备配对、版本/能力握手、managed/external ownership、托盘状态和显式启停；
- 删除 Main/Renderer 的工具快捷旁路；
- 不支持的能力和版本明确拒绝。

退出条件：内置与独立 Runtime 使用同一 Device contract；Desktop 不打开会话 UI 时仍可作为执行节点；
独立启动 Runtime 时仍可登记、接单、执行假 Provider 并补传结果；多启动者不会争抢同一资源。

### DC3：真实 Agent API、恢复与通知

- G1 后接入真实 Session/Conversation/Run/Artifact 和 command receipt；
- G2 后验证关闭、断网和 runner 故障恢复；
- G3 后接入持久通知、已读同步和系统通知隐私策略。

退出条件：真实测试租户的纯云端长任务、刷新恢复、等待与通知闭环通过。

### DC4：Device Backend、Runtime 迁移与三条纵向链路

- G0 后先在现有 `src/local_tools` 上实现不碰 Run DDL 的版本化兼容 API、旧 Runtime contract 和缺失
  reject/reconcile 语义；P0 B04/B05 合入后，才以排序在后的独立 migration 增加 local-tool 表到
  Run/Invocation/fence/evidence 的稳定引用和 repository 联调，禁止同时编辑 P0 migration；
- 新 Runtime adapter 与旧稳定版运行兼容矩阵，形成 Device Gate；
- 实现第一方 Provider catalog、签名包发布、短期下载票据和 Runtime Package Manager，形成
  Provider Gate；BOSS CLI 与 weixin CLI 至少各完成一次缺失→按需安装→调用→升级/回滚演练；

分别验证：

1. 纯云端工具：Desktop 离线也能完成；
2. 本地只读工具：Run 等待设备、Runtime 执行、结果回传后继续；
3. 本地写工具：审批、参数绑定、资源锁、结果证据、unknown reconciliation 全链路。

退出条件：断网、重复提交、Runtime 重启和迟到结果不会产生重复副作用；缺失/损坏/过旧 Provider
不会执行，按需安装失败可观察、可恢复且不能下载任意代码。

### DC5：首发收口

- 完成窗口、退出、暂停设备和系统通知体验；
- 建立协议支持窗口、最低 Runtime 版本和能力拒绝规则；
- 删除发布产物中的旧 D1 入口、类型和业务主持循环；
- 完成 Windows/macOS 安装包、签名/notarization、更新、供应链、安全检查、故障注入和回滚说明。

退出条件：D11 与 D-final、本文首发验收、独立测试/CR 和 Sev 门禁全部完成并签署 Desktop Release
Gate 后，才允许面向批准用户发布 RC/GA；Desktop Core Gate 只允许开始本阶段和构建候选包。

## 9. 与总体架构的协作门禁

| 桌面工作 | 前置条件 |
| --- | --- |
| DC1a UI 原型与假数据页面 | 可立即进行，不形成正式协议 |
| DC1b AgentClient / RunProjection | G0：P0 B01 已合入并由 contract/consumer owner 签署 |
| 连接真实 Run | G1：P0 B11 及依赖完成并部署隔离测试环境 |
| 后台恢复 | G2：P0 B09+B10 故障演练通过；访问仍依赖 G1 |
| 通知 | G3：P0 B13 查询/已读/订阅部署隔离环境 |
| 连接真实 Runtime | Device Gate：兼容 API 已通过；P0 B04/B05 后的稳定引用/migration 已合入；Runtime compatibility/adapter 与 G2 故障链通过 |
| 按需安装第一方 Provider | Provider Gate：D00 签名 ADR、immutable release 构建/上传/审核/发布/撤回链、catalog/下载票据、Runtime 安装器与供应链测试通过 |
| 构建 RC candidate | Desktop Core Gate：G0～G3、Device Gate、Provider Gate、D00～D10 通过且无开放 Sev-0/Sev-1 |
| 正式 RC/首发 | Desktop Release Gate：D11、D-final、完成定义和 Sev 门禁全部通过；不以 P0 Phase 6 全渠道迁移作为技术阻塞项 |

桌面不能为了赶进度临时定义第二套状态或续接协议。如果平台契约尚未准备好，使用 fake adapter 继续界面开发，而不是让 UI 成为事实来源。

## 10. 首发验收

至少通过以下场景：

1. Web 发起、Desktop 订阅同一 Run，不产生第二次执行。
2. Desktop 发起后关闭窗口，重新打开能从 snapshot 恢复。
3. 同一命令重复提交返回原 Run；同键不同参数被拒绝。
4. Desktop 离线不阻断纯云端工具。
5. 设备离线进入 waiting_device，不暗中换设备。
6. Renderer 伪造设备结果、跨租户 Run 或 Artifact 访问均被拒绝。
7. Desktop 与 CLI 同时启动时不存在两个执行宿主争抢桌面。
8. Runtime 成功执行但回传断网时只补传结果，不再次操作。
9. 动作可能已经发生但证据不足时进入 reconciliation，不自动重试。
10. 关闭窗口、退出应用、暂停设备、取消 Run 四种行为不混同。
11. 设备解绑或权限撤回后阻断新副作用，同时允许受限补交旧事实。
12. 发布包不包含旧 D1 Agent 回合入口或桌面业务 Coordinator。
13. Web/渠道 Session id 即使值相同也不会串会话；Desktop 修改 Session kind 或跨租户查询被拒绝。
14. supplement/correction 在安全检查点生效；普通新消息仍创建/排队新 Run，三个 UI 动作不混同。
15. 无审批权限的 Desktop 用户和 Runtime 都不能解决 waiting_approval；有权限用户的决定有审计记录。
16. after_seq 落入已清理 delta 时，Desktop 通过 reset_required/snapshot 恢复；不显示永久 loading、
    不重复提交任务，也不重放已经过时的 progress。
17. Desktop 查看渠道 Run 时只使用 Agent API；安装包不包含生产 Channel Connector 或渠道 secret。
18. Renderer 无法取得 bearer/device token，也不能向下载接口注入 URL、Authorization 或 tenantId；
    401/撤权后停止新命令/claim但不丢失已执行结果。
19. terminal 后由 canonical Conversation cursor 刷新最终消息；RunProjection 不发网络、不把 delta
    或 local pending 拼成服务端事实。
20. 旧稳定 `agent-tool-runtime` 与新版服务端兼容矩阵通过；提升最低版本前已完成灰度、旧版 drain、
    outbox 补交和回滚演练。
21. 系统通知默认不显示客户正文、工具参数或文件名；前台抑制、去重、已读同步、权限拒绝降级通过。
22. Web 发起的 Run 与 Desktop 发起的 Run 都可路由到同一个内置/独立 Runtime，且不存在
    Desktop UI 直调 Provider 的旁路。
23. 关闭会话窗口后，用户已启用的执行节点仍能接收由 Web/渠道发起的 Invocation；“退出界面”与
    “停止执行节点”有不同的明确操作和状态展示。
24. 本地缺少 BOSS CLI 或 weixin CLI 时，Runtime 只从签名第一方 catalog 按需安装被固定的 release；
    任意 URL、digest 不符、签名错误、平台不符、版本撤回或 Runtime 不兼容都 fail-closed。
25. Provider 升级不影响固定到旧 release 的在途 Invocation；安装失败保留旧可用版本，损坏版本隔离，
    result/outbox 不因 Provider 安装或回滚而丢失。

## 11. 本规划不包含

- 平台 Run 表结构、runner 部署和 Web 迁移实现；
- browser executor 重构；
- 某个具体 Provider 的业务流程和专属业务 UI；第一方 Provider 的通用 catalog、按需安装、状态和
  版本管理属于 P1；
- 重写已经存在的桌面更新体系、第三方插件市场和复杂离线 Agent；现有 updater 的安全收口、签名包联调
  和活跃任务 quiesce 属于 DC5 首发工作；
- 本地自行选择模型供应商或绕过云端计费；
- 为尚未发布的 D1 构建兼容层。

这些内容需要各自专题，但都必须遵守总体架构固定的执行所有权、安全和事实一致性约束。

## 12. 双平台发布与供应链基线

旧设计中仍有效的交付约束在此收敛，后续不得再引用旧阶段编号：

| 平台 | 首发范围 | 正式交付门禁 |
|------|----------|--------------|
| Windows | Windows 10/11 x64、user-scope NSIS | Authenticode 有效；publisher/appId/更新源一致；安装、覆盖升级、跳版本、损坏包与错误签名拒绝通过 |
| macOS | macOS 13+ arm64/x64，DMG + updater ZIP | Developer ID、Hardened Runtime、notarization、stapling、Gatekeeper 通过；arm64 真机必测，x64 在全量发布前有真机或受控 runner 证据 |

- `electron-updater` 只使用包内只读 HTTPS 更新源；Renderer 不能提供更新 URL。
- `autoDownload=false`，下载完成后只有在 Run/Runtime/Artifact outbox 已 quiesce 或用户明确确认的
  安全点才能重启安装；强制安全更新也不能丢失本地执行事实。
- Windows/macOS 分平台、分架构发布，metadata、安装包、blockmap/ZIP 必须来自同一次构建并原子发布。
- release manifest 记录 commit、dirty 状态、Node/Electron、平台/架构、输入哈希、产物 SHA-256、
  签名/notarization；同时生成 SBOM、许可证清单并执行解包/ASAR、安全和 smoke 门禁。
- 撤回坏版本使用更高 SemVer 修复版，不能覆盖同版本文件；不兼容本地 schema 时禁止直接降级，
  迁移失败进入旧 binary 或只读 safe mode。

平台约束以实现期锁定版本的官方文档复核：

- [Electron Security](https://www.electronjs.org/docs/latest/tutorial/security)
- [Electron Code Signing](https://www.electronjs.org/docs/latest/tutorial/code-signing)
- [electron-builder macOS](https://www.electron.build/docs/mac/)
- [electron-builder Auto Update](https://www.electron.build/docs/features/auto-update/)
