# AID Work Agent 桌面客户端 P1 架构与约束基线

> 日期：2026-09-22
>
> 版本：v1.2
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
        │
        └──────── Runtime Bridge ─────> Local Runtime
                                           │
                                           ▼
                                        Providers
```

逻辑职责可以部署在较少进程中，但依赖方向和权限边界不能合并。

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

### 3.3 Local Runtime

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

## 4. 两份远端契约

### 4.1 Agent API

供 Web、Desktop 等交互客户端使用；云端 Channel Gateway 在服务端内部复用同一
AgentApplication 语义，但具体 Connector 不作为 Agent API 客户端直连 RunService：

```text
submit(typed_session_ref)
append_input(run_id, expected_version, mode)
get_run / list_session_runs(typed_session_ref)
subscribe(after_seq)
cancel
resolve_approval                 # 仅授权主体
reply_to_clarification(wait_ref, expected_version)
list_notifications / mark_notification_read
```

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
result_outbox_ack
reconcile_report
```

工具结果必须绑定 tenant、device、runtime epoch、invocation、run fence、参数摘要和结果摘要。知道 run_id 不足以提交结果。

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
| 关闭普通窗口 | 隐藏或关闭 UI；按产品配置保留 Main/Runtime | 不取消，稍后由 snapshot 恢复 |
| Renderer 崩溃或刷新 | 重建 UI 和 RunProjection | 不受影响 |
| 临时断网 | 保留投影；Runtime 保存待回传结果 | 云端等待设备或继续纯云端步骤 |
| 暂停本机自动执行 | Runtime 停止接纳新写动作，在安全边界暂停 | 依赖设备的 Run 进入明确等待 |
| 显式退出且 Runtime 由应用管理 | drain、保存 outbox、停止接单后退出 | 设备下线，不等于取消业务 Run |
| 显式退出但 Runtime 是外部实例 | UI 断开，不终止其他启动者管理的 Runtime | Runtime 可在授权范围内继续 |
| Runtime 崩溃/电脑重启 | journal 恢复；unknown 动作先核验 | 不自动改派另一设备重发 |

桌面首版可以不做系统服务或开机启动，但必须明确 Runtime 是 managed child 还是 external attachment。

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
- 解绑或撤权后允许受限补交历史结果，但不得借补传通道接纳新动作。

## 8. 实施阶段

### DC0：基线与删除清单

- 固定仓库 commit、现有桌面/D1/Runtime/CLI 依赖图；
- 标记可直接删除的未发布桌面模块和必须迁移的共用能力；
- 形成 Agent API、Device API、PlatformPorts、Runtime ownership 四份 ADR；
- 停止继续开发旧 D1 和桌面业务 Coordinator。

退出条件：没有未知生产调用方会因清理被误删；桌面团队只有一套目标协议。

### DC1：共享客户端与桌面壳

- 接入统一契约生成物；
- 实现 AgentClient 和纯 RunProjection；
- 支持 typed SessionRef、append_input、clarification/approval 分权和 reset_required 快照替换；
- 建立窄 Preload IPC、凭据边界和 PlatformPorts；
- 使用假服务完成会话、Run、等待、审批、Artifact 和通知界面。

退出条件：不启动 Runtime 也能用录制事件验证两端投影一致；Renderer 无 Node/Provider 直连。

### DC2：Runtime Bridge 与设备契约

- 复用现有 Runtime 的调用接纳、journal、resource lock、result outbox；
- 实现设备配对、版本/能力握手、managed/external ownership；
- 删除 Main/Renderer 的工具快捷旁路；
- 不支持的能力和版本明确拒绝。

退出条件：独立启动 Runtime 时仍可登记、接单、执行假 Provider 并补传结果；多启动者不会争抢同一资源。

### DC3：三条纵向链路

分别验证：

1. 纯云端工具：Desktop 离线也能完成；
2. 本地只读工具：Run 等待设备、Runtime 执行、结果回传后继续；
3. 本地写工具：审批、参数绑定、资源锁、结果证据、unknown reconciliation 全链路。

退出条件：断网、重复提交、Runtime 重启和迟到结果不会产生重复副作用。

### DC4：首发收口

- 完成窗口、退出、暂停设备和系统通知体验；
- 建立协议支持窗口、最低 Runtime 版本和能力拒绝规则；
- 删除发布产物中的旧 D1 入口、类型和业务主持循环；
- 完成 Windows/macOS 安装包、签名/notarization、更新、供应链、安全检查、故障注入和回滚说明。

## 9. 与总体架构的协作门禁

| 桌面工作 | 前置条件 |
| --- | --- |
| UI 原型与假数据页面 | 可立即进行，不形成正式协议 |
| AgentClient / RunProjection | P0 Phase 0 协议、事件、等待与权限契约冻结 |
| 连接真实 Run | P0 Phase 1 AgentApplication 与 Phase 2 Run/Event repository 可用 |
| 连接真实 Runtime | P0 Phase 0 Device API、ToolInvocation effect 和授权边界冻结 |
| 写工具端到端 | P0 Phase 2 finalization/outbox 与 Phase 3 lease/fence 可用 |
| 桌面正式首发 | P0 Phase 0/2/3/4 与相关 Phase 5 Agent/通知门禁通过、Device API 测试环境可用，桌面 DC0～DC4 通过；不以 P0 Phase 6 全渠道迁移作为技术阻塞项 |

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

## 11. 本规划不包含

- 平台 Run 表结构、runner 部署和 Web 迁移实现；
- browser executor 重构；
- 某个具体 Provider 的业务流程和 UI；
- 重写已经存在的桌面更新体系、插件市场和复杂离线 Agent；现有 updater 的安全收口、签名包联调
  和活跃任务 quiesce 属于 DC4/DC5 首发工作；
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
