# 浏览器执行架构、可视化与人工接管设计

> 版本：v2.8
>
> 日期：2026-07-14
>
> 状态：🔧 设计完成，Phase 3 修复待开发
>
> 替代：v1.0《浏览器工具可视化设计文档》（2026-05-21）
>
> 关联：[浏览器自动化工具设计](./browser_automation_design.md) · [开发计划](./browser_execution_dev_plan.md)
>
> v2.4 变更：明确 `auto` 必须服务端 headless 优先；新增 HeadlessFailureDetector、确定性证据规则和 EscalationGate，普通 HTTP/网络/站点故障不得转桌面执行。
>
> v2.5 变更：把人工参与改为可持久化的工具 suspend/resume；增加结构化操作指引、完成条件监测、自动/手工交还和原 Agent 工具调用续跑，禁止依赖用户再次发消息或 LLM 重新调用工具。
>
> v2.6 变更：确立 Agent-first 原则。桌面执行能力改为 [Agent Desktop](../../system/desktop-agent-client-design.md) 的可选 browser runtime。
>
> v2.7 变更：彻底删除独立浏览器客户端产品、工程、安装包、协议 scheme、更新器和发布依赖。桌面执行只作为 Agent Desktop 内置可选 `browser-runtime` 模块存在；`browser/1.0` 仅是主进程内 runtime 与服务端 RemoteExecutor 的隔离协议。
>
> 2026-07-22 v2.8 修订：测试环境真实验收发现 Redis 服务不支持恢复链路所需的 Stream 能力（`XREAD` 实测为 unknown command）、恢复 worker 持续 `ResponseError`，且登录页图形验证码被 LLM `done` 错判为成功。Phase 3 改用 PostgreSQL 租约队列持久化 resume job；Redis 只保留短期状态、锁和 continuation 事件缓存；新增确定性人工需求检测、启动能力探针和跨事件循环测试隔离门禁。

## 1. 结论与范围

本次采用一套确定的混合执行架构，不再把问题限定为“给服务端 headless 浏览器加画面”：

1. **服务端执行器**：默认使用 Playwright Chromium 新 headless 模式；一次 `browser_automation` 任务独占一个浏览器进程，终态必须关闭。
2. **桌面执行器**：作为 Agent Desktop 的可选 browser runtime 交付，首期支持范围、认证、签名、更新和发布节奏全部跟随 Agent Desktop。runtime 使用当前桌面登录态建立短期连接，启动专用、可见、隔离 Profile 浏览器，不连接用户日常 Chrome Profile；不产生第二个客户端产品。
3. **统一编排**：LLM 决策仍在服务端；`PageOps` 改为调用统一 `BrowserExecutor` 契约，不因执行位置复制编排逻辑。
4. **统一视图**：服务端和桌面执行器都向 `BrowserViewHub` 发送 JPEG 帧；Web 与 Agent Desktop renderer 使用同一 `BrowserView` 契约查看。
5. **人工接管**：验证码、扫码登录、MFA、支付确认或自动化低置信度时，Agent 暂停；用户在本机浏览器或网页远程视图中操作，点击“完成并交还”后继续。
6. **生命周期边界**：完成、失败、取消、总超时、Agent Desktop/runtime 断线、租约过期和服务关闭均关闭浏览器。`WAITING_HUMAN` 是唯一非终态例外，最多保留 5 分钟，可续租一次；到期仍关闭。
7. **不绕过风控**：不实现 CAPTCHA 破解、指纹伪装或反检测绕过。桌面可见模式解决的是兼容性、已有登录和人工协作，不保证绕过网站自动化政策。
8. **Agent-first 隔离**：browser runtime 可关闭、可缺失、可延迟加载；初始化失败、Worker 崩溃、协议不兼容和更新失败只影响当前浏览器能力，不得阻止 Agent Desktop 启动、登录、对话、文件或其他工具。

本设计覆盖浏览器生命周期、执行路由、runtime 协议、Agent Desktop 集成、实时画面、人工接管、租户隔离、安全、可观测性、迁移和验收。Agent Desktop 的安装、签名、自动更新、托盘、认证和平台范围由其自身设计负责。本设计不覆盖移动端浏览器、Linux 桌面客户端、Safari/Firefox、云端第三方 Browser-as-a-Service 和验证码代答服务。

## 2. 当前实现审查

### 2.1 合理部分

- `BrowserAutomationTool → BrowserOrchestrator → PageOps → BrowserSession` 分层方向正确。
- 语义快照和 ref 驱动比让 LLM 猜 CSS 选择器稳定。
- 已有 `ask_user` 和 `BrowserTaskContext`，说明业务上已经识别到人机协作需求。
- `requirements.txt` 已要求 Playwright 1.59+，可以使用 `page.screencast`。
- browser runtime 必须是 Agent Desktop 内的独立故障边界，避免浏览器权限、依赖和崩溃影响主窗口、登录、对话、文件和其他工具。

### 2.2 P0/P1 问题

| 严重度 | 现状 | 后果 | 明确修复 |
|---|---|---|---|
| P0 | `configs/config.yaml` 把 `tools.browser.headless` 配为 `false`，覆盖代码默认 `true` | Docker 启动失败或依赖不可用显示环境 | YAML 和 Settings 同时改为 `true`；生产禁止工具参数覆盖为 headed |
| P0 | `close_browser_session()` 用 `asyncio.create_task()` 后立即删除字典并返回 | 实际关闭失败/任务被取消但调用方报告已关闭 | 全链路改为 `async close()` 并 `await`；关闭结果可观测 |
| P0 | `BrowserOrchestrator.execute()` 无 `finally`，完成、失败、超步数均不关闭 | Chromium/Playwright 子进程泄漏 | 终态统一进入 `BrowserRunManager.finalize()` |
| P0 | `BrowserSession.start()` 半途失败没有逆序清理 | Playwright driver 或 browser 泄漏 | 启动事务化；异常调用幂等 `close(reason="start_failed")` |
| P0 | `BrowserSession.close()` 任一步抛错会跳过后续步骤 | page/context/browser/driver 部分残留 | 每层独立 try，逆序关闭，汇总错误，引用置空 |
| P0 | 全局 `_browser_sessions` 跨请求保存状态 | Gunicorn worker 不一致；重启即丢；无法统一回收 | Redis 存运行元数据和租约；浏览器对象仅存在 owner worker 内存 |
| P0 | 默认 `session_id="default"` 且没有 tenant/user 归属校验 | 并发用户串会话、跨租户访问 | 服务端生成不可猜 `run_id`；所有读取校验 tenant_id + user_id |
| P0 | `ask_user` 返回后工具调用已经结束；上下文和浏览器仅在当前进程字典，恢复依赖用户再发消息、LLM 再次调用并带相同 `session_id` | 用户操作完成后无法自动继续；跨 worker、刷新或重启必丢 | Agent Runtime 增加一等 `SUSPENDED_TOOL`；持久化 assistance/continuation，浏览器完成事件幂等唤醒原工具调用 |
| P0 | URL 无 SSRF 校验 | 可访问内网、metadata、localhost | 导航前统一 URL Policy，DNS 解析后拒绝私网/环回/链路本地并复检重定向 |
| P1 | `is_running()` 只判断对象非空 | 浏览器崩溃后仍被当作可用 | 检查 `browser.is_connected`、context/page closed 状态和 owner lease |
| P1 | 无任务总超时、空闲 TTL、并发上限、shutdown hook | 僵尸会话和资源耗尽 | 5 分钟任务超时、60 秒普通空闲、5 分钟人工租约、租户/实例信号量、关机清理 |
| P1 | 用户回复和异常原文进入日志/返回；截图可能含凭据 | 敏感信息泄露 | 不记录用户输入；错误白名单；人工阶段不持久化截图；帧仅内存转发 |
| P1 | 截图写到共享 `storage/screenshots` 且文件名含外部 session_id | 跨租户、路径和保留期问题 | 使用租户存储工具 + UUID 文件名；默认不落盘；调试产物加密且 24h TTL |
| P1 | `--no-sandbox` 无条件开启 | 容器逃逸防护降低 | 默认启用 Chromium sandbox；仅 root 容器显式配置降级并产生告警，部署改非 root |
| P1 | YAML 的 timeout/viewport 未被完整使用，代码有硬编码 | 配置名存实亡 | 所有 executor 参数只从 `BrowserRuntimeConfig` 读取 |

### 2.3 生命周期语义冲突的处理

`browser_open` 返回后若立即关闭，后续 `click/fill/snapshot` 无法工作；但让 LLM 负责最后调用 `browser_close` 又不可靠。因此：

- 对 Agent 只公开一次完整任务边界的 `browser_automation`。
- 旧原子工具不再注册给主 Agent，只保留为内部诊断/兼容 API，并要求显式 `run_id`。
- 完整任务在终态自动关闭；人工等待不是终态，由有上限租约管理。
- 即使调用方忘记关闭，reaper 和应用 shutdown 仍会回收。

## 3. 目标架构

```text
Agent / BrowserAutomationTool
        |
        v
BrowserRunManager -- Redis lease/owner -- bs_browser_runs audit
        |
        v
BrowserOrchestrator -> BrowserExecutor protocol
                         |                    |
                         v                    v
                LocalPlaywrightExecutor   DesktopRuntimeExecutor
                Docker/new headless       Agent Desktop Browser Worker
                         |                    |
                         +---- frames/events-+
                                      |
                               BrowserViewHub
                                      |
                    authenticated WebSocket + SSE status
                                      |
                              BrowserView.vue
                         watch / take over / return
```

### 3.1 核心组件

| 组件 | 唯一职责 |
|---|---|
| `BrowserRunManager` | 创建 run、状态迁移、租约、取消、finalize、reaper、所有权校验 |
| `BrowserExecutor` | 执行确定性浏览器命令并返回标准结果，不调用 LLM |
| `LocalPlaywrightExecutor` | 管理每 run 一个隔离 worker 子进程及其 IPC；Playwright 不在 API worker 内运行 |
| `DesktopRuntimeExecutor` | 通过 `browser/1.0` 通道向在线 Agent Desktop browser runtime 下发命令并等待有序结果 |
| `BrowserRouter` | 强制 server 起步，并按 EscalationGate 最多一次升级 desktop runtime |
| `BrowserViewHub` | 最新帧转发、背压、观察者授权，不持久化业务画面 |
| `HumanControlCoordinator` | Agent/人工互斥锁、接管租约、输入转发、完成 CAS |
| `HumanCompletionMonitor` | 监测白名单页面完成条件，稳定判定后请求恢复，不读取输入正文 |
| `AgentResumeCoordinator` | 从 PostgreSQL 租约队列幂等领取 resume job，续跑原 BrowserOrchestrator 和原 Agent tool_call |
| `DesktopRuntimeRegistry` | 在线 Agent Desktop runtime、能力、当前负载和协议版本信息 |

### 3.2 执行器契约

服务端 Python 定义抽象接口；本地和远程实现必须返回同一 DTO：

```python
class BrowserExecutor(Protocol):
    async def start(self, run: BrowserRunSpec) -> StartResult: ...
    async def navigate(self, command: NavigateCommand) -> CommandResult: ...
    async def snapshot(self, command: SnapshotCommand) -> SnapshotResult: ...
    async def click(self, command: ClickCommand) -> CommandResult: ...
    async def fill(self, command: FillCommand) -> CommandResult: ...
    async def select(self, command: SelectCommand) -> CommandResult: ...
    async def keyboard(self, command: KeyboardCommand) -> CommandResult: ...
    async def pointer(self, command: PointerCommand) -> CommandResult: ...
    async def content(self, command: ContentCommand) -> ContentResult: ...
    async def close(self, reason: str) -> CloseResult: ...
```

每条命令必须包含 `run_id`、单调递增 `seq`、唯一 `command_id`、`deadline_at`。runtime 按 `(run_id, command_id)` 幂等；旧 seq 拒绝；同一 run 严格串行。

## 4. 执行路由

### 4.1 对外参数

`browser_automation` 新增：

- `execution_target`: Agent 可见值仅为 `auto | server`，默认 `auto`。`desktop_runtime` 是服务端升级后的内部目标，不允许 LLM 直接指定。
- 移除对 Agent 可见的 `headless` 参数。headless 是部署策略，不由 LLM 决定。
- `desktop_installation_id` 不进入工具参数；升级目标只能是当前 `tenant_id + user_id` 已登录、在线且声明 browser capability 的 Agent Desktop runtime。

管理员可用受 RBAC 和审计保护的诊断接口强制 desktop runtime，但该接口不注册为 Agent 工具，不参与生产自动路由。

### 4.2 `auto` 的确定性规则

`auto` 的主路径固定为 `server headless -> 失败检测 -> desktop_runtime`，不因“登录、可视化、页面可能有验证码”等推测提前选择桌面执行：

1. 先执行 URL/租户/域名安全策略；`blocked` 直接拒绝。
2. 运行能力预检。只有目标是 RFC1918/企业内网、必须使用客户端证书/系统 WebAuthn、或用户明确要求使用 Agent Desktop 专用 Profile 中已有登录态时，才能判定 `LOCAL_CAPABILITY_REQUIRED`。这是“服务端确定不具备能力”，视为服务端预检失败，可直接进入步骤 5；域名配置不得仅因反爬而标记桌面执行。
3. 其余任务一律创建 server run，以新 headless 模式执行。登录、MFA、扫码、文件选择器和“希望看到过程”本身都不是提前转桌面执行的理由；服务端 BrowserView 已提供画面和人工接管。
4. 每次主文档导航、动作失败、动作无进展和返回终态失败前运行 `HeadlessFailureDetector`。只有分类结果为 `ESCALATE_DESKTOP` 才进入步骤 5；`RETRY_SERVER`、`WAIT_HUMAN_SERVER` 和 `FAIL_SERVER` 均不得切换。
5. `EscalationGate` 检查未发生或无法确认发生不可逆动作、此前未升级过，并确认当前用户存在已登录、在线、版本兼容且启用 browser runtime 的 Agent Desktop。通过后先完整 finalize 并确认 server 浏览器关闭，再向该 runtime 下发任务，新建 desktop runtime run。
6. desktop runtime run 从脱敏检查点和当前顶层 URL 重新开始，不迁移 cookie、storage、请求头、表单值或服务端凭据。桌面执行再失败只能人工接管或终止，禁止回切 server，单任务最多升级一次。

Agent Desktop 未安装时返回 `DESKTOP_INSTALL_REQUIRED`；已安装但 browser runtime 禁用、离线或版本不兼容时分别返回 `DESKTOP_RUNTIME_DISABLED`、`DESKTOP_RUNTIME_OFFLINE`、`DESKTOP_UPDATE_REQUIRED`。Web 只展示 Agent Desktop 的统一安装、打开或更新入口，不展示浏览器独立客户端。路由选择、证据类别和父子 run 关系写审计枚举。

### 4.3 Headless 失败检测器

`HeadlessFailureDetector` 是纯规则组件，输入为 executor 产生的结构化、脱敏观测，不接收完整 DOM，也不依赖 LLM 自由判断：

- 主文档状态码、响应头白名单、Content-Type、重定向次数和 origin 变化；
- Playwright `requestfailed`、导航错误、page error、console 错误类别；
- title/body 的受控短文本特征哈希、验证码/挑战 iframe provider、关键元素是否存在；
- 相同 URL/动作连续无进展、挑战页与目标页之间循环；
- WebAuthn、客户端证书、本地网络、文件选择器等能力请求。

特征库放在版本化配置中，只用于识别和交接，不用于规避检测。证据等级固定为：

- **强信号**：`cf-mitigated: challenge` 等明确挑战响应头；已知 CAPTCHA/challenge iframe；页面明确声明不支持 headless/自动化；浏览器明确请求客户端证书、系统 WebAuthn 或本地地址。任一强信号足以得到高置信“故障分类”，但分类后的处置仍按下表执行；例如 CAPTCHA 只能直接证明需要人工，不能直接证明必须转 desktop runtime。
- **中信号**：403/503；challenge/access-check 标题或短文本；挑战脚本加空业务 DOM；同一导航的 JS 重定向循环；相同安全动作两次无进展。必须在同一次导航出现两个不同来源的中信号才得到高置信分类。
- **弱信号**：空白页、单次超时、慢加载、普通 JS 错误、仅检测到 `navigator.webdriver`。弱信号永不触发升级。

检测器在 `DOMContentLoaded`、其后 2 秒稳定点及动作失败时取样；不会为探测额外重复提交请求。分类矩阵如下：

| 观测/分类 | 默认处置 | 允许转 desktop runtime |
|---|---|---|
| CAPTCHA/MFA/扫码，服务端 BrowserView 可显示并输入 | `WAIT_HUMAN_SERVER` | 否，先 Web 人工接管 |
| 页面明确声明拒绝 headless/自动化，或服务端人工交还后同一挑战仍持续 | `ESCALATE_DESKTOP/HEADLESS_BLOCKED` | 是，且必须过 EscalationGate |
| 明确反爬 challenge header，且稳定点后仍未进入业务页面 | `ESCALATE_DESKTOP/ANTI_BOT_CHALLENGE` | 是，且必须过 EscalationGate |
| 401/407 | `FAIL_SERVER/AUTH_REQUIRED` | 否；仅预检已确认本机认证能力时才是 `LOCAL_CAPABILITY_REQUIRED` |
| 单独 403 或 503 | `FAIL_SERVER/ACCESS_DENIED` 或 `SITE_ERROR` | 否；还需独立挑战证据 |
| 429/`Retry-After` | `RETRY_SERVER/RATE_LIMITED` | 否；遵守等待上限，禁止借桌面执行规避限流 |
| 404/410 | `FAIL_SERVER/NOT_FOUND` | 否 |
| 普通 5xx | `RETRY_SERVER/SITE_ERROR`，预算耗尽后失败 | 否 |
| DNS/TLS/拒绝连接/普通超时 | `RETRY_SERVER/NETWORK_ERROR`，预算耗尽后失败 | 否；只有预检确认目标仅本地网络可达才允许 desktop runtime |
| 信号冲突或只有弱信号 | `FAIL_SERVER/HEADLESS_DETECTION_INCONCLUSIVE` | 否，向用户说明而不冒险切换 |

普通幂等读取最多重试 2 次，指数退避 2 秒、5 秒；429 服从 `Retry-After` 且不超过任务总超时；非幂等动作不自动重试。LLM 可解释分类结果，但不得改变分类或单独产生 `ESCALATE_DESKTOP`。

### 4.4 EscalationGate 与检查点

升级前同时满足：检测结果是高置信 `ESCALATE_DESKTOP` 或确定的 `LOCAL_CAPABILITY_REQUIRED`；`irreversible_action_committed=false`；`irreversible_state_unknown=false`；`escalation_count=0`；租户策略允许 desktop runtime；当前用户的 Agent Desktop/runtime 在线且版本兼容。任一条件不满足即停止并返回明确错误。

server run 必须先进入 `ESCALATING_DESKTOP -> FINALIZING`，收到 worker `closed` 或完成进程组兜底回收后，才能创建 desktop runtime 子 run。检查点仅含 `task_id`、脱敏目标 origin/path、已完成的可安全重放步骤类型、失败分类和 `parent_run_id`；禁止含 query、cookie、storage、Authorization、DOM 正文、表单值、截图和键盘内容。若服务端已经输入敏感字段，桌面浏览器要求用户重新输入。

### 4.5 headless 兼容策略

服务端按顺序使用：

1. Playwright `channel="chromium"` 的新 headless 模式。
2. 标准 Desktop Chrome viewport、locale、timezone；不伪造不一致 UA。
3. 站点自身明确不支持 headless 或出现验证时转人工/Agent Desktop runtime。

不添加 `navigator.webdriver` 篡改、Canvas/WebGL 指纹伪造、验证码识别或代理轮换。这些方案不稳定并引入合规风险。

## 5. 生命周期与资源回收

### 5.1 状态机

```text
CREATED -> ROUTING -> STARTING -> RUNNING_AGENT
                               -> WAITING_HUMAN -> RUNNING_HUMAN -> RESUMING -> RUNNING_AGENT
RUNNING_AGENT -> ESCALATING_DESKTOP -> FINALIZING(server) -> WAITING_DESKTOP -> STARTING(desktop_runtime)

任意非终态 -> CANCELLING -> CANCELLED
任意非终态 -> FINALIZING -> SUCCEEDED | FAILED | TIMED_OUT | EXPIRED
```

只有 `FINALIZING` 能进入终态。`finalize(run_id, terminal_state, reason)` 使用 Redis 锁保证只执行一次。

浏览器 run 状态之外，原 Agent 工具调用有独立状态：

```text
RUNNING_TOOL -> SUSPENDING -> SUSPENDED_TOOL -> RESUME_QUEUED -> RESUMING_TOOL -> RUNNING_TOOL
任意非终态 -> TOOL_FAILED | TOOL_CANCELLED
```

进入 `WAITING_HUMAN` 时必须同步完成 `SUSPENDED_TOOL` 持久化后才能向用户发送“请操作”事件。工具不得先返回普通 success/failure，HTTP/SSE 请求也不需要保持 5 分钟；恢复由后台 `AgentResumeCoordinator` 完成。

`ToolSuspension` 是 Agent Runtime 内部控制结果，不是写给模型的普通 tool result。`src/core/agent.py` 收到后不得追加 `role=tool`、不得把计划任务标记 completed/failed、不得继续调用 LLM；只持久化原 assistant tool call 和 suspension 引用。若模型提供的 tool_call_id 为空，执行前必须生成不可猜稳定 ID 并同时写回 assistant message。恢复后的浏览器工具真正进入终态时，才用该 ID 追加唯一 tool result 并继续 Agent 循环。

### 5.2 关闭顺序

服务端 `LocalPlaywrightExecutor` 使用 `asyncio.create_subprocess_exec(..., start_new_session=True)` 为每个 run 启动 `python -m src.tools.browser.worker_main`。父子进程通过 stdin/stdout 长度前缀二进制帧通信；stderr 只输出脱敏诊断。Playwright、context 和 page 全部只存在于子进程。这样父进程可以可靠拥有一个 Linux process group，避免并发启动时靠扫描系统 Chrome PID 猜归属。

worker 内严格执行并逐项捕获错误：

1. 停止接收新命令，取消运行中的 Playwright await。
2. 停止 screencast，解除 frame callback。
3. 停止 trace/video/HAR 并按策略保存或删除。
4. `page.close()`。
5. `context.close(reason=...)`。
6. `browser.close(reason=...)`。
7. `playwright.stop()`。
8. worker 正常退出；父进程等待最多 5 秒，超时向本 run 的 PGID 发 SIGTERM，再等 2 秒，最后 SIGKILL。绝不按进程名批量杀 Chrome。
9. 引用置空、释放并发配额、删除 lease/owner/view ticket，写关闭指标。

Agent Desktop runtime 执行同一逻辑，并在服务端收到 `closed` ACK 后完成 finalize。ACK 3 秒超时则标记 `CLOSE_UNCONFIRMED`；runtime watchdog 仍必须在命令通道断开 30 秒后关闭浏览器。

### 5.3 超时与回收

| 项目 | 值 |
|---|---:|
| 单命令默认超时 | 30 秒 |
| 自动执行累计超时 | 5 分钟，不计 `WAITING_HUMAN/RUNNING_HUMAN` |
| 单个 browser run 硬墙钟上限 | 15 分钟 |
| RUNNING 空闲超时 | 60 秒 |
| WAITING_HUMAN 初始租约 | 5 分钟 |
| 人工租约续期 | 最多 1 次，每次 5 分钟 |
| Agent Desktop runtime 断线宽限 | 30 秒 |
| reaper 周期 | 15 秒 |
| 应用 shutdown 优雅关闭预算 | 15 秒 |

显式“取消任务”和应用 shutdown 必须向 run cancellation token 传播。工具挂起后原 SSE 正常结束、页面刷新或短暂断线不视为取消；由 Web presence 的 30 秒宽限和人工租约决定是否保留。人工等待期间暂停自动执行预算，但不突破人工租约和 15 分钟硬上限；恢复后从剩余自动执行预算继续。reaper 处理 owner worker 崩溃后的过期租约。

## 6. Agent Desktop browser runtime

### 6.1 产品与工程边界

浏览器桌面执行不形成独立产品、独立工程或独立发布物。技术栈、安装包、认证、更新、托盘和主进程生命周期由 Agent Desktop 设计决定；浏览器方案只在主应用内提供可选 Node.js runtime：

```text
clients/agent-desktop/
├── electron/                       # Agent 主应用，浏览器不得反向依赖 UI
├── browser-runtime/
│   ├── worker/                     # 独立 Node 子进程，Playwright
│   ├── protocol/                   # browser/1.0 DTO 与帧编解码
│   └── process-controller/         # Windows/macOS 进程树托管
└── tests/
```

Agent Desktop main 按需加载 `BrowserRuntimeManager`，后者为每个 run 启动独立 Node Worker。Windows 使用 Job Object（`KILL_ON_JOB_CLOSE`）托管 Worker 与 Chromium 进程树；macOS 使用独立 process group，按 SIGTERM 5 秒、SIGKILL 2 秒回收。Playwright 不进入 Vue renderer，browser runtime 不得成为主窗口创建、登录或普通 Agent API 初始化的依赖。`clients/wecom-personal-rpa/` 不增加项目引用、不接收 Browser 消息、不共享配置目录。

### 6.2 浏览器 Profile

- Profile 根目录使用 Agent Desktop 的 `app.getPath("userData")/browser-runtime/profiles/{installation_id}`。`installation_id` 由 Agent Desktop 管理，是本机随机 UUID，不包含账号或租户信息。
- 浏览器选择顺序固定为：本机稳定版 Google Chrome（Playwright `channel="chrome"`）→ Agent Desktop 安装包携带的匹配版 Chromium；实际选择通过 capability 上报。两者都使用上述专用 Profile。
- 第一次启动显示“这是受 Agent 控制的专用浏览器”，用户可在其中登录。
- 禁止使用默认 Chrome User Data Directory；禁止复制用户日常 cookies。
- runtime 复用 Agent Desktop 当前登录身份建立短期 browser session，不新增浏览器专用长期凭据；服务端永不接收 cookie 或 storage_state。
- 同一 Profile 同时只允许一个 run；每个 Agent Desktop runtime 最多 2 个浏览器 run。
- terminal 时关闭浏览器进程，但保留 Profile 登录态；用户可在设置中清除。

### 6.3 runtime 通道

`browser/1.0` 是 Agent Desktop browser runtime 与服务端 `DesktopRuntimeExecutor` 的窄化隔离协议，不代表独立客户端：

- Desktop presence：Agent Desktop 登录后用现有认证换取短期 browser session，通过 `/api/v1/browser/desktop/ws` 注册 `installation_id + tenant_id + user_id + capabilities`；客户端不能自报或切换租户/用户。
- Web presence：仅当 Agent Web 需要拉起已安装的 Agent Desktop 时使用 `/api/browser/web_presence/ws`；Agent Desktop 自身发起任务不依赖 Web 标签页 presence。
- 画面上行：同一 WebSocket 的二进制帧，头部为固定长度 run_id/seq/timestamp/尺寸；最大单帧 256 KB。
- 前端观看：`/api/browser/runs/{run_id}/view_ws?ticket=...`，服务端只转发最新帧。
- browser runtime 自己实现 WebSocket 分片组装、单消息 1 MB 上限、单 writer 锁、心跳和指数退避；不复用聊天 SSE，也不引用企业微信 RPA 的连接类。

runtime 上线发送 capability：

```json
{
  "type": "capabilities",
  "protocol_version": "browser/1.0",
  "desktop_runtime_session_id": "bdrs_...",
  "installation_id": "desktop_...",
  "features": ["headed", "screencast", "human_control", "persistent_profile"],
  "browser": "chromium",
  "max_runs": 2
}
```

### 6.4 Agent Desktop 启动与临时认证

1. Agent Desktop 登录成功后，main process 使用现有 `CredentialStore` 中的当前身份调用 `POST /api/v1/browser/desktop/session`，换取 60 秒短期 browser session；原始 token 不进入 WebSocket URL。
2. main process 使用短期 session 连接 `/api/v1/browser/desktop/ws`，服务端固定绑定 `tenant_id + user_id + installation_id`，token 每 30 秒通过存活连接轮换且不落盘。
3. Agent Desktop renderer 发起浏览器任务时，main process 直接按需启用 runtime，不经过自定义协议。
4. Agent Web 需要使用本机能力时，调用 `POST /api/browser/desktop/launch_ticket` 获取 60 秒单次 ticket，再打开既有 `aidagent://browser-launch?...`；Agent Desktop 原子交换 ticket 后关联当前登录身份，不新增浏览器专用协议 scheme。
5. Agent Desktop 退出登录、凭据失效、runtime 被禁用或主应用退出时，立即取消关联 run；网络断开给予 30 秒宽限，watchdog 到期关闭 Worker 和浏览器。
6. 用户不填写浏览器专用服务器地址、标识、密钥、设备码或配对码；API 地址、安装来源和更新渠道全部复用 Agent Desktop。

### 6.5 安装与引导边界

执行模式与安装要求固定如下：

| 场景 | 是否安装 Agent Desktop | 执行位置 |
|---|---|---|
| 公网页面读取、搜索、采集 | 不需要 | server headless |
| 在 Agent Web 中观看服务端浏览器 | 不需要 | server + Web BrowserView |
| 在 Agent Web 中远程处理服务端验证码/MFA | 不需要 | server + Web 人工接管 |
| 使用本机 Chrome、本机登录态、本地网络/证书 | 需要 | Agent Desktop browser runtime |
| 任务满足桌面升级条件，用户直接看到本机可见浏览器并操作 | 需要 | Agent Desktop browser runtime |

普通 Agent Web 页面受浏览器同源策略、进程隔离和权限模型限制，不能启动本机 Playwright。因此本机执行需要 Agent Desktop；不会再为浏览器单独安装第二个客户端。

Web 引导流程：

1. `BrowserRouter` 未找到 Agent Desktop 时返回 `DESKTOP_INSTALL_REQUIRED`，前端复用 Agent Desktop 统一安装卡。
2. 已安装但未在线时，用户点击“打开 Agent Desktop”，Web 获取一次性 launch ticket 并打开 `aidagent://browser-launch?...`。
3. runtime 被禁用时返回 `DESKTOP_RUNTIME_DISABLED`，引导用户在 Agent Desktop 设置中启用；版本过低时返回 `DESKTOP_UPDATE_REQUIRED`，交给现有更新 UI。
4. Agent Desktop runtime 上线后发布 `browser_desktop_runtime_online`，原任务自动继续；不要求刷新页面或重新发送消息。
5. 用户拒绝安装、打开、启用或更新时结束升级；不得把已失败任务静默重跑 server，也不得伪装成 server 可完成。

browser runtime 随 Agent Desktop 的签名安装包和统一更新渠道发布，不生成独立安装包、下载页、托盘、设置应用或更新器。

## 7. 可视化与人工接管

### 7.1 画面链路

- 服务端执行器：`page.screencast.start(on_frame=...)`，JPEG quality 60，最大 1280×720，最高 5 FPS。
- Agent Desktop runtime：固定 Node.js Playwright 1.59+，使用其 Screencast API；版本不满足时 capability 注册失败，不运行兼容分支。
- Hub 为每个 run 只保留最新一帧；消费者慢时丢旧帧，不累积队列。
- 无观看者时降为每个动作后一帧；有观看者时开启实时流。
- 帧不写日志、不进 Redis、不进数据库。审计截图必须显式开启并走租户加密临时存储。

### 7.2 接管流程

1. 每次导航后的首个 snapshot、每步执行后的新 snapshot、以及接受 LLM `done` 前，都先经过 `HumanRequirementDetector`。检测器只使用结构化信号：challenge iframe、验证码/校验码控件标签与角色、密码/MFA/扫码表单结构、文件选择器和受阻目标区域；不得读取输入值。命中后直接生成 `CAPTCHA_REQUIRED`、`AUTH_REQUIRED`、`MFA_REQUIRED` 或 `FILE_PICKER_REQUIRED`，LLM 的 `done`、自由文本“请用户登录”或普通成功结果不能覆盖结构化证据。只有页面上存在普通“登录”链接而没有受阻表单/挑战时不得误触发。
2. 确定性检测命中、LLM 明确返回 `ask_user`，或用户点击“接管”时，生成 `HumanAssistanceRequest`。请求必须包含 `assistance_id/run_id/agent_execution_id/tool_call_id/reason_code`、用户可执行指引、操作位置、完成模式、白名单完成条件和到期时间。若原任务的完成条件尚未满足，编排器不得把“页面已打开，请用户处理”记为 `SUCCEEDED`。
3. `HumanControlCoordinator` 先保存浏览器检查点，再把原工具调用持久化为 `SUSPENDED_TOOL`。两项都成功后 CAS 获取控制锁，browser run 变为 `WAITING_HUMAN`；Agent 停止发命令。任何一步失败都不得向用户谎报“可以继续”。
4. Agent Web 必须展示 `HumanAssistanceCard`，明确告诉用户：为什么暂停、在哪个浏览器操作、按顺序做什么、完成后是否会自动继续、无法自动识别时点击哪个按钮、剩余时间。密码、验证码要求用户直接输入浏览器，不在聊天中回复。
5. 用户点击“开始接管”，状态变 `RUNNING_HUMAN`。本机用户操作 Agent Desktop 打开的专用浏览器窗口；网页用户的 pointer/keyboard 经授权 WebSocket 直达 executor，不进入 LLM 上下文、不持久化。
6. executor 在人工控制期间只上报导航、DOM 结构变化和脱敏完成条件，不上报按键、输入值、cookies 或表单正文。`HumanCompletionMonitor` 在条件连续两次、间隔 1 秒成立后发出 `human_completion_detected`。
7. 自动条件成立时，或用户点击“完成并继续”且服务端重新校验通过时，`HumanControlCoordinator` 在同一 PostgreSQL 事务中 CAS assistance 状态并插入唯一 resume job；重复点击、重复事件和 WebSocket 重连只能形成一条待处理记录。
8. CAS 成功后丢弃在途人工输入、释放人工控制锁并执行新 snapshot。`AgentResumeCoordinator` 通过 `FOR UPDATE SKIP LOCKED` 租约领取 job，从原 run、原 page/context、原步骤索引继续 `BrowserOrchestrator`；不是创建新 browser task，也不重新导航起始 URL。
9. 原工具下一次真正完成、失败或再次需要人工时，Agent Runtime 才把对应 tool result 接回原 `tool_call_id` 并继续原 Agent 执行。前端收到 `agent_continuation_started`，无需用户再发消息。
10. 用户选择取消、租约到期或上下文丢失时终止原工具并关闭浏览器。owner/browser 已崩溃时返回 `RESUME_CONTEXT_LOST`，不得假装从原页面继续；只有检查点声明所有步骤可安全重放且未发生不可逆操作时，才可另行让用户确认是否重开。

同一时刻控制者只能是 `agent` 或一个 `user_id`。观察者不能发送输入。涉及提交/支付的最后一步即使人工已完成，系统只描述结果，不重复点击。

`browser_human_required` 的最小前端契约固定如下；`steps` 由 `instruction_code + reason_code` 的服务端模板生成，LLM 可以补充上下文，但不能删除或改写安全提示：

```json
{
  "assistance_id": "bha_...",
  "run_id": "br_...",
  "reason_code": "CAPTCHA_REQUIRED",
  "surface": "server_web",
  "title": "请完成页面验证",
  "steps": [
    "点击“开始接管”",
    "直接在浏览器画面中完成验证码，不要在聊天中发送验证码或密码",
    "验证成功后系统会自动继续；若未自动继续，请点击“完成并继续”"
  ],
  "completion_mode": "auto_or_confirm",
  "expires_at": "2026-07-14T11:00:00+08:00"
}
```

Agent Runtime 收到该事件必须立即输出对应的用户提示并结束当前 SSE 的运行态显示为“等待你的操作”，不能输出“任务已完成”。即使 LLM 文本生成失败，前端仍直接根据结构化事件渲染卡片，保证用户一定知道要做什么。

同一会话存在活动 suspension 时，新的普通聊天消息不得启动第二个 Agent 循环或覆盖原 tool_call。Agent Web 禁用普通发送并保留“完成并继续/取消”；API 收到并发消息返回 `409 TOOL_WAITING_HUMAN` 和 assistance_id。这样恢复前不会形成悬空或乱序 tool message。

### 7.3 完成条件

完成条件不是自由 JavaScript。编排器只能从以下白名单组合中选择，服务端校验后下发：`url_origin_path_matches`、`element_present`、`element_absent`、`challenge_iframe_absent`、`page_classifier_state`。不得把 URL query、DOM 正文、账号或输入值写进条件。

- CAPTCHA：挑战 iframe/容器消失，并且目标业务区域出现，连续两次成立后自动继续。
- 登录/MFA/扫码：登录控件消失且目标页面状态出现；只看结构和 origin/path，不读取凭据。
- 文件选择或无稳定结构的第三方控件：`completion_mode=confirm_only`，用户点击“完成并继续”后取快照；无法验证时明确询问用户确认，不能自动猜完成。
- 提交、支付、发送、删除：`completion_mode=confirm_only`。快照标记 `completed_by_human=true`，恢复后的 Agent 禁止重放该不可逆动作。

自动监测是便利能力，显式“完成并继续”是可靠兜底。按钮校验未通过时，卡片显示仍缺少的结构化条件，保持人工控制，不生成 resume job。

### 7.4 前端 UI

`BrowserView.vue` 必须显示：执行位置、连接状态、当前 URL origin、Agent/人工控制状态、剩余租约、接管/完成并继续/取消按钮。`HumanAssistanceCard.vue` 展示结构化指引和自动检测状态；接管时自动展开。Agent Desktop renderer 同步显示相同指引和完成按钮。禁止把密码、验证码或键盘内容显示在进度消息中。

## 8. 数据、缓存和 API

### 8.1 PostgreSQL

新增业务审计表 `bs_browser_runs`。Agent Desktop browser runtime 使用现有 installation identity 和临时执行会话，不新增长期浏览器设备绑定表；runtime session、capabilities 和在线状态只存 Redis，任务审计仍写入 `bs_browser_runs`：

| 字段 | 说明 |
|---|---|
| `id` | BIGSERIAL |
| `tenant_id` | TEXT，所有查询必带 |
| `user_id` | TEXT，创建者 |
| `run_id` | TEXT UNIQUE，不可猜 UUIDv4 |
| `parent_run_id` | 可空；desktop runtime 升级 run 指向已关闭的 server run |
| `session_id` | TEXT，关联对话，仅用于审计 |
| `execution_target` | server/desktop_runtime |
| `executor_installation_id` | 可空；返回前脱敏 |
| `state` | 状态机枚举 |
| `routing_reason` | 枚举，不存自由文本 |
| `failure_class/evidence_level` | 可空白名单枚举，不存页面证据正文 |
| `escalation_count` | 0 或 1 |
| `started_at/finished_at` | TIMESTAMP |
| `close_reason/error_code` | 白名单枚举 |
| `steps_count` | INTEGER |
| `created_at/updated_at` | TIMESTAMP |

Phase 2 已落地 DDL 中的 `client`、`executor_client_id` 及对应状态名属于旧预留值；Phase 4A 必须通过非破坏迁移改为上表的 desktop runtime 语义，并提供历史数据兼容读取。不得因为本次文档收口直接破坏线上表或已存在 run 审计。

不存完整 URL、DOM、帧、cookies、表单值、用户输入和 LLM prompt。建表同时更新 `deploy/init-postgres.sql` 和数据库迁移记录。

新增 `bs_browser_assistance_requests` 保存可恢复的人工协作事实，不保存浏览器内容：

| 字段 | 说明 |
|---|---|
| `tenant_id/user_id` | 归属，所有查询必带 |
| `assistance_id` | UNIQUE，不可猜 UUIDv4；完成/恢复幂等键 |
| `run_id` | 关联 `bs_browser_runs` |
| `agent_execution_id/tool_call_id` | 恢复原 Agent 工具调用，不返回前端 |
| `state` | pending/controlling/completed/resume_queued/resumed/expired/cancelled/failed |
| `reason_code/instruction_code` | 白名单枚举；指引参数只能是脱敏结构化值 |
| `completion_mode/predicate_type` | auto_or_confirm/confirm_only 与白名单条件类型 |
| `expires_at/completed_at/resumed_at` | 生命周期审计 |
| `error_code` | 白名单恢复错误 |

原 Agent messages 仍使用现有会话/Trace 持久化，不复制到该表。浏览器语义检查点只存 Redis 并设租约；进程或检查点丢失必须失败为 `RESUME_CONTEXT_LOST`，不能用数据库旧数据伪造活页面恢复。

新增 `bs_browser_resume_jobs` 作为 Phase 3 唯一可靠恢复队列，不依赖 Redis Stream：

| 字段 | 说明 |
|---|---|
| `job_id` | TEXT UNIQUE，不可猜 UUIDv4 |
| `tenant_id/assistance_id/run_id` | 归属与恢复关联；`assistance_id` UNIQUE，防止重复入队 |
| `state` | pending/processing/completed/failed |
| `lease_owner/lease_until` | worker 领取身份与短租约；崩溃后可回收 |
| `attempts/available_at` | 有界重试与退避调度 |
| `last_error_code` | 白名单错误码，不保存异常正文 |
| `created_at/updated_at/completed_at` | 生命周期审计 |

入队必须与 `bs_browser_assistance_requests.state -> resume_queued` 在同一 PostgreSQL 事务提交。worker 每 500ms～1s 批量执行以下语义：

```sql
SELECT job_id
FROM bs_browser_resume_jobs
WHERE (state = 'pending' AND available_at <= CURRENT_TIMESTAMP)
   OR (state = 'processing' AND lease_until < CURRENT_TIMESTAMP)
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 10;
```

同一事务将领取记录更新为 `processing`、写入 `lease_owner/lease_until` 并递增 `attempts`，提交后再执行恢复。成功后标记 `completed`；可重试失败按退避重置为 `pending`；超过最大次数或确定性失败标记 `failed` 并收口原 run。传输语义允许 worker 崩溃造成至少一次领取，但通过 `assistance_id` 唯一约束、assistance CAS、原 `tool_call_id` 结果幂等键和 continuation 最终消息去重，实现 exactly-once effect。禁止宣称分布式执行本身 exactly-once。

PostgreSQL 表是恢复任务事实来源；可选 `LISTEN/NOTIFY` 只能用于唤醒降低延迟，丢通知时仍靠轮询恢复，不得作为唯一队列。DDL 同步更新 `deploy/init-postgres.sql`、`deploy/db_update.sql` 和数据库文档。上线顺序为先部署 DDL、再滚动更新 API worker；回滚只回退代码，不删除新表。部署前若存在活动 assistance，必须先完成、取消或明确失败收口。

### 8.2 Redis

在 `CacheKeys` 登记：

- `browser_run:{tenant_id}:{run_id}`：状态摘要，TTL 10 分钟，terminal 主动删除。
- `browser_owner:{tenant_id}:{run_id}`：server worker/desktop runtime owner lease，TTL 30 秒，每 10 秒续租。
- `browser_control:{tenant_id}:{run_id}`：agent/user 控制锁，TTL 随状态。
- `browser_view_ticket:{tenant_id}:{run_id}:{jti}`：一次性观看票据，TTL 60 秒，连接后删除。
- `browser_web_presence:{tenant_id}:{user_id}:{web_instance_id}`：Web 在线租约，TTL 30 秒，每 15 秒续租。
- `browser_launch_ticket:{ticket_hash}`：绑定 tenant/user/web_instance/origin 的单次拉起票据，TTL 60 秒，exchange 时原子删除。
- `browser_desktop_runtime_session:{tenant_id}:{user_id}:{desktop_runtime_session_id}`：Agent Desktop runtime 临时 capability 与连接状态，TTL 60 秒，WSS 存活时续租，断开主动删除。
- `browser_assistance:{tenant_id}:{assistance_id}`：脱敏完成条件、步骤索引和控制状态，TTL 等于人工租约，CAS 更新。
- `agent_tool_suspension:{tenant_id}:{agent_execution_id}:{tool_call_id}`：原工具挂起引用，TTL 为人工租约加 2 分钟收尾时间。
- `agent_continuation_events:{tenant_id}:{continuation_id}`：Redis JSON 事件窗口，CAS 锁保护递增 seq，TTL 15 分钟；前端重连按 last_seq 补取，最终 Agent 消息仍按现有会话消息机制持久化。不得使用当前环境不支持的 Stream 命令。

Redis 不可用时：禁止创建需要跨请求人工接管或 desktop runtime 执行的 run；只允许单请求 server run，且必须在请求 `finally` 中关闭。不能静默降级为跨 worker 内存会话。应用启动时必须探测 Phase 3 实际使用的 Redis 原语（GET/SET NX/EX、DEL、TTL、SCAN、发布通知及现有 Lua/CAS）；缺失时记录单条聚合告警并禁用人工接管，不得启动每秒刷错的后台循环。PostgreSQL resume worker 只依赖数据库连接池，不因 Redis 缺少 `XADD/XREAD` 失败。

### 8.3 API/事件

- `POST /api/browser/runs/{run_id}/take_control`
- `POST /api/browser/runs/{run_id}/assistance/{assistance_id}/complete`：显式“完成并继续”，服务端重新校验完成条件并 CAS。
- `POST /api/browser/runs/{run_id}/assistance/{assistance_id}/extend`：人工租约续期，最多一次。
- `GET /api/agent/continuations/{continuation_id}/events?after_seq=N`：当前用户补取续跑事件；continuation_id 来自绑定当前 Web presence 的通知，不可跨用户读取。
- `POST /api/browser/runs/{run_id}/cancel`
- `POST /api/browser/runs/{run_id}/view_ticket`
- `WS /api/browser/runs/{run_id}/view_ws?ticket=...`
- `WS /api/browser/web_presence/ws`：已登录 Web 页面在线租约。
- `POST /api/browser/web_presence/ticket`：已登录 Web 页面换取 60 秒单次 presence WS ticket。
- `POST /api/browser/desktop/launch_ticket`：Agent Web 获取 60 秒一次性 Agent Desktop 拉起票据。
- `POST /api/v1/browser/desktop/session`：Agent Desktop 当前登录身份换取短期 browser session，或原子交换 Web launch ticket。
- `WS /api/v1/browser/desktop/ws`：Agent Desktop browser runtime 控制与帧通道。
- SSE：`browser_run_ready`、`browser_run_state`、`browser_human_required`、`browser_human_completion_detected`、`browser_resume_queued`、`browser_resume_started`、`agent_continuation_started`、`browser_run_closed`

原请求 SSE 在 `SUSPENDED_TOOL` 后正常结束。恢复时 `AgentResumeCoordinator` 将带 seq 事件写入 continuation stream，同时经当前 Web presence WebSocket 发 `agent_continuation_available` 通知；前端随即拉取并展示续跑事件。刷新或 30 秒内重连后使用 last_seq 补取，不能要求用户重新发送聊天消息来制造新请求。

所有接口使用现有认证和 `X-Tenant-Id`，并校验 `tenant_id + user_id + run_id`。平台管理员只能在明确租户上下文中观看，不得默认跨租户列出。

## 9. 安全与合规

- URL Policy 拒绝 `file:`, `data:`, `javascript:`、localhost、RFC1918、链路本地、云 metadata；每次重定向和 DNS 变化复检。
- 下载默认拒绝可执行文件；文件写入租户 temp，限制 50 MB，任务结束删除或经用户确认注册。
- 填表值默认标记 sensitive，不进入 steps、日志、异常、截图文件名或 LLM 回显。
- 浏览器视图票据一次性、短 TTL；WebSocket Origin 校验；全链路 TLS。
- Agent Desktop 首次启用 Browser capability 需管理员开启、终端用户确认；既有托盘持续显示“浏览器受 Agent 控制”。
- 人工输入只转发到 executor；服务端不得记录按键正文。
- 不暴露 Playwright WS/CDP endpoint 到公网。官方文档明确提示知道 Playwright wsPath 的任意进程或网页可能取得 OS 用户控制权。
- Agent Desktop runtime 不调试默认 Chrome Profile。Chrome 136+ 也要求 remote debugging 使用非默认 `user-data-dir`。

## 10. 可观测性与容量

指标：

- `browser_runs_total{target,outcome,error_code}`
- `browser_active_runs{target}`
- `browser_close_duration_seconds`
- `browser_close_failures_total{stage}`
- `browser_process_orphans_total`
- `browser_command_duration_seconds{action,target}`
- `browser_human_takeovers_total{reason}`
- `browser_human_assistance_total{reason,completion_mode,outcome}`
- `browser_resume_jobs_total{outcome}`
- `browser_resume_latency_seconds`
- `browser_headless_detection_total{classification,evidence_level}`
- `browser_desktop_escalations_total{reason,outcome}`
- `browser_desktop_escalation_rejected_total{reason}`
- `browser_frame_dropped_total{source}`
- `browser_desktop_runtime_online`、`browser_desktop_command_timeout_total`

日志只记录 `run_id`、脱敏 installation_id、动作类型、耗时、状态和白名单错误码。禁止记录 URL query、页面正文、表单值、验证码和截图 base64。

默认容量：每个 API 实例 server run 上限 4；每租户 server run 上限 2；每个 Agent Desktop runtime 上限 2；超限排队最多 30 秒，之后 `CAPACITY_EXCEEDED`。容量值进入配置并同时维护 Settings/YAML。

## 11. 失败语义

错误码固定为：`WEB_PRESENCE_REQUIRED`、`DESKTOP_INSTALL_REQUIRED`、`DESKTOP_UPDATE_REQUIRED`、`DESKTOP_RUNTIME_DISABLED`、`DESKTOP_RUNTIME_OFFLINE`、`DESKTOP_PROTOCOL_UNSUPPORTED`、`HEADLESS_BLOCKED`、`ANTI_BOT_CHALLENGE`、`HEADLESS_DETECTION_INCONCLUSIVE`、`LOCAL_CAPABILITY_REQUIRED`、`DESKTOP_ESCALATION_BLOCKED`、`IRREVERSIBLE_STATE_UNKNOWN`、`AUTH_REQUIRED`、`ACCESS_DENIED`、`RATE_LIMITED`、`SITE_ERROR`、`NOT_FOUND`、`NETWORK_ERROR`、`HUMAN_REQUIRED`、`HUMAN_TIMEOUT`、`HUMAN_COMPLETION_NOT_MET`、`TOOL_SUSPEND_FAILED`、`TOOL_WAITING_HUMAN`、`RESUME_CONTEXT_LOST`、`RESUME_ALREADY_CONSUMED`、`COMMAND_TIMEOUT`、`TASK_TIMEOUT`、`BROWSER_CRASHED`、`NAVIGATION_BLOCKED`、`SSRF_BLOCKED`、`CAPACITY_EXCEEDED`、`CANCELLED`、`CLOSE_UNCONFIRMED`、`INTERNAL_ERROR`。

对用户返回稳定中文说明；debug 字段使用脱敏后的错误类别，不直接返回 `str(e)`。

## 12. Codex 可借鉴边界

Codex 当前公开产品表面区分：应用内 Browser Use、控制用户现有 Chrome 状态的 Chrome 扩展、以及桌面 Computer Use。可借鉴的是“按执行表面选择工具”和“用户能观察/接管”，不是其未公开内部协议。本方案对应为 server browser、Agent Desktop browser runtime、human control 三层；不声称复刻 Codex 内部实现。

## 13. 方案取舍

| 备选 | 结论 | 原因 |
|---|---|---|
| Docker headed + Xvfb + noVNC 作为主方案 | 不采用；仅运维诊断 | 用户仍操作服务器浏览器，不具备本地登录态；VNC 桌面暴露面更大 |
| 只做 server screencast | 不足 | 能看不能稳定处理本地登录、扫码、客户端证书和复杂验证码 |
| 直接连接用户日常 Chrome CDP | 禁止 | 高权限、Profile 风险、Chrome 136 限制、误操作日常标签页 |
| 浏览器扩展首期实现 | 暂不采用 | 需要用 `chrome.debugger` 重建 Playwright 等待/定位能力；Agent Desktop 隔离 Worker 可直接复用 Playwright |
| 第三方 browser cloud | 暂不采用 | 数据出境、租户隔离、成本和供应商锁定；不解决本机登录态 |
| 自动验证码识别/代答 | 禁止 | 合规和可靠性不可接受 |

## 14. 验收标准

1. Docker 默认配置不传参数时以 headless 启动。
2. 正常、导航失败、LLM 异常、命令超时、取消和 shutdown 六条路径后，目标 Chromium/Playwright 进程数回到基线。
3. `WAITING_HUMAN` 5 分钟到期自动关闭；续租上限生效。
4. 两租户同时使用同名 session 不会互见状态、帧或控制权。
5. Gunicorn 两 worker 下任意 API worker 都能读取 run 状态，只有 owner 执行命令。
6. server/desktop runtime 两执行器通过同一套 executor contract tests。
7. 用户能实时观看、接管、输入验证码、交还并继续；输入内容不出现在日志/DB/LLM steps。
8. Agent Desktop runtime 断网 30 秒后 watchdog 关闭浏览器；恢复连接不重放已完成的不可逆命令。
9. SSRF 测试覆盖 DNS 重绑定、重定向到私网、IPv6 loopback 和 metadata 地址。
10. Windows 10/11 x64 与 macOS 13+（Intel、Apple Silicon）均完成安装、Web 拉起、临时认证、可见执行、人工接管、断线回收和卸载验收。
11. 未安装 Agent Desktop 或 browser runtime 被禁用时 server 模式正常使用；只有高置信 headless 升级或确定的本地能力预检失败才出现 Agent Desktop 统一安装/打开/更新入口。
12. Agent Desktop 退出登录、关闭应用或禁用 runtime 后 30 秒内，关联 desktop runtime run 被取消，专用浏览器关闭且连接失效。
13. 前端 build、Python 浏览器测试、Agent Desktop browser runtime 测试和真机 Chrome 验收全部通过。
14. 普通 403、429、404、5xx、DNS/TLS、超时和单独 CAPTCHA iframe 均不会触发 desktop runtime；403/503 只有叠加独立 challenge 证据才升级，CAPTCHA 先在 server BrowserView 人工处理。
15. 每个自动 desktop runtime run 都能追溯到已关闭的 server 父 run 或确定的本地能力预检失败；单任务最多升级一次，且不传递 cookie/storage/表单值。
16. 人工请求卡必须明确展示原因、操作位置、操作步骤、完成方式和超时；密码/验证码只在浏览器中输入。
17. 用户完成 CAPTCHA/登录后，不发送新聊天消息也能触发原 `tool_call_id` 从同一 page/context 继续，最终由原 Agent 自动给出结果。
18. 自动条件事件、完成按钮双击、WebSocket 重连并发发生时只生成一个 resume job；两 Gunicorn worker 下也只恢复一次。
19. “完成并继续”条件不满足时保持人工状态并指出未满足条件；owner/browser 丢失时明确返回 `RESUME_CONTEXT_LOST`，不得从头假装续跑。
20. 人工完成提交/支付等不可逆动作后，恢复 Agent 不重复该动作；任务终态浏览器进程仍回到基线。
21. 真实部署所用 Redis 不支持 Stream 时，应用不调用 `XADD/XREAD`；PostgreSQL resume worker 仍可在两个 Gunicorn worker 下只产生一次恢复效果，worker 崩溃后租约到期可接管。
22. 登录表单、图片验证码、CAPTCHA iframe、MFA/扫码结构证据命中时必须生成 assistance 和 `WAITING_HUMAN`；LLM `done` 或普通文本“请人工登录”不能把 run 标为成功。
23. Phase 3 测试文件必须整文件、随机顺序和连续运行两次均全绿；全局 runtime/monitor task 不得跨事件循环残留。

## 15. 参考资料

- [Playwright Browsers：新 headless 与 Chromium channel](https://playwright.dev/docs/browsers)
- [Playwright Docker/CI：容器与 Xvfb](https://playwright.dev/docs/docker)
- [Playwright Python Browser：优雅关闭 context/browser](https://playwright.dev/python/docs/api/class-browser)
- [Playwright Python Screencast 1.59](https://playwright.dev/python/docs/release-notes#version-159)
- [Playwright BrowserType：connect 与 CDP 的能力/安全边界](https://playwright.dev/docs/api/class-browsertype)
- [Chrome 136 remote debugging 安全变化](https://developer.chrome.com/blog/remote-debugging-port)
- [Chrome DevTools Protocol Page.startScreencast](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-startScreencast)
- [noVNC 官方项目（仅作为运维备选）](https://github.com/novnc/noVNC)
