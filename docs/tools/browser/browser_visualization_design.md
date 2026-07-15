# 浏览器执行架构、可视化与人工接管设计

> 版本：v2.6
>
> 日期：2026-07-14
>
> 状态：设计完成，待开发
>
> 替代：v1.0《浏览器工具可视化设计文档》（2026-05-21）
>
> 关联：[浏览器自动化工具设计](./browser_automation_design.md) · [开发计划](./browser_execution_dev_plan.md)
>
> v2.1 变更：客户端执行改为独立 Browser Companion；禁止依赖或修改企业微信 RPA 客户端。
>
> v2.2 变更：Browser Companion 改为 Windows/macOS 跨平台 Electron 应用；补充“服务端免安装 / 本机执行需安装”边界和 Agent Web 安装、拉起、配对、更新引导闭环。
>
> v2.3 变更：删除手工设备配对和长期客户端凭据；Companion 只允许由当前已登录的 Agent Web 使用一次性 launch ticket 拉起，连接生命周期绑定 Web presence。
>
> v2.4 变更：明确 `auto` 必须服务端 headless 优先；新增 HeadlessFailureDetector、确定性证据规则和 EscalationGate，普通 HTTP/网络/站点故障不得转客户端。
>
> v2.5 变更：把人工参与改为可持久化的工具 suspend/resume；增加结构化操作指引、完成条件监测、自动/手工交还和原 Agent 工具调用续跑，禁止依赖用户再次发消息或 LLM 重新调用工具。
>
> v2.6 变更：确立 Agent-first 原则。客户端执行能力改为 [Agent Desktop](../../system/desktop-agent-client-design.md) 的可选 browser runtime，不再独立决定客户端技术栈、安装包、认证、更新或生命周期；若本文件任何旧 Companion 描述与 Agent Desktop 设计冲突，以 Agent Desktop 设计为准。

## 1. 结论与范围

本次采用一套确定的混合执行架构，不再把问题限定为“给服务端 headless 浏览器加画面”：

1. **服务端执行器**：默认使用 Playwright Chromium 新 headless 模式；一次 `browser_automation` 任务独占一个浏览器进程，终态必须关闭。
2. **客户端执行器**：作为 Agent Desktop 的可选 browser runtime 交付，首期支持范围跟随 Agent Desktop，不单独定义产品平台和发布节奏。runtime 使用一次性 ticket/桌面当前登录态建立短期连接，启动专用、可见、隔离 Profile 浏览器，不连接用户日常 Chrome Profile；与企业微信 RPA 客户端没有代码、进程、身份、协议或发布依赖。
3. **统一编排**：LLM 决策仍在服务端；`PageOps` 改为调用统一 `BrowserExecutor` 契约，不因执行位置复制编排逻辑。
4. **统一视图**：服务端和客户端执行器都向 `BrowserViewHub` 发送 JPEG 帧；前端使用同一 `BrowserView` 查看。
5. **人工接管**：验证码、扫码登录、MFA、支付确认或自动化低置信度时，Agent 暂停；用户在本机浏览器或网页远程视图中操作，点击“完成并交还”后继续。
6. **生命周期边界**：完成、失败、取消、总超时、客户端断线、租约过期和服务关闭均关闭浏览器。`WAITING_HUMAN` 是唯一非终态例外，最多保留 5 分钟，可续租一次；到期仍关闭。
7. **不绕过风控**：不实现 CAPTCHA 破解、指纹伪装或反检测绕过。客户端可见模式解决的是兼容性、已有登录和人工协作，不保证绕过网站自动化政策。
8. **Agent-first 隔离**：browser runtime 可关闭、可缺失、可延迟加载；初始化失败、Worker 崩溃、协议不兼容和更新失败只影响当前浏览器能力，不得阻止 Agent Desktop 启动、登录、对话、文件或其他工具。

本设计覆盖浏览器生命周期、执行路由、客户端协议、Windows/macOS 发布、Web 安装引导、实时画面、人工接管、租户隔离、安全、可观测性、迁移和验收。不覆盖移动端浏览器、Linux 桌面客户端、Safari/Firefox、云端第三方 Browser-as-a-Service 和验证码代答服务。

## 2. 当前实现审查

### 2.1 合理部分

- `BrowserAutomationTool → BrowserOrchestrator → PageOps → BrowserSession` 分层方向正确。
- 语义快照和 ref 驱动比让 LLM 猜 CSS 选择器稳定。
- 已有 `ask_user` 和 `BrowserTaskContext`，说明业务上已经识别到人机协作需求。
- `requirements.txt` 已要求 Playwright 1.59+，可以使用 `page.screencast`。
- 客户端执行必须是独立产品边界，避免浏览器权限、升级和故障影响任何渠道/RPA 客户端。

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
                LocalPlaywrightExecutor   RemoteClientExecutor
                Docker/new headless       Browser Companion Worker
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
| `RemoteClientExecutor` | 通过客户端通道下发命令并等待有序结果 |
| `BrowserRouter` | 强制 server 起步，并按 EscalationGate 最多一次升级 client |
| `BrowserViewHub` | 最新帧转发、背压、观察者授权，不持久化业务画面 |
| `HumanControlCoordinator` | Agent/人工互斥锁、接管租约、输入转发、完成 CAS |
| `HumanCompletionMonitor` | 监测白名单页面完成条件，稳定判定后请求恢复，不读取输入正文 |
| `AgentResumeCoordinator` | 幂等领取 resume job，续跑原 BrowserOrchestrator 和原 Agent tool_call |
| `BrowserClientRegistry` | 在线客户端、能力、当前负载和版本信息 |

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

每条命令必须包含 `run_id`、单调递增 `seq`、唯一 `command_id`、`deadline_at`。客户端按 `(run_id, command_id)` 幂等；旧 seq 拒绝；同一 run 严格串行。

## 4. 执行路由

### 4.1 对外参数

`browser_automation` 新增：

- `execution_target`: Agent 可见值仅为 `auto | server`，默认 `auto`。`client` 是服务端升级后的内部目标，不允许 LLM 直接指定。
- 移除对 Agent 可见的 `headless` 参数。headless 是部署策略，不由 LLM 决定。
- `client_id` 不进入工具参数；升级目标只能是当前 `tenant_id + user_id + web_instance_id` 拉起的 Companion 会话。

管理员可用受 RBAC 和审计保护的诊断接口强制 client，但该接口不注册为 Agent 工具，不参与生产自动路由。

### 4.2 `auto` 的确定性规则

`auto` 的主路径固定为 `server headless -> 失败检测 -> client`，不因“登录、可视化、页面可能有验证码”等推测提前选择客户端：

1. 先执行 URL/租户/域名安全策略；`blocked` 直接拒绝。
2. 运行能力预检。只有目标是 RFC1918/企业内网、必须使用客户端证书/系统 WebAuthn、或用户明确要求使用 Companion 专用 Profile 中已有登录态时，才能判定 `LOCAL_CAPABILITY_REQUIRED`。这是“服务端确定不具备能力”，视为服务端预检失败，可直接进入步骤 5；域名配置不得仅因反爬而标记本地执行。
3. 其余任务一律创建 server run，以新 headless 模式执行。登录、MFA、扫码、文件选择器和“希望看到过程”本身都不是提前转客户端的理由；服务端 BrowserView 已提供画面和人工接管。
4. 每次主文档导航、动作失败、动作无进展和返回终态失败前运行 `HeadlessFailureDetector`。只有分类结果为 `ESCALATE_CLIENT` 才进入步骤 5；`RETRY_SERVER`、`WAIT_HUMAN_SERVER` 和 `FAIL_SERVER` 均不得切换。
5. `EscalationGate` 检查未发生或无法确认发生不可逆动作、此前未升级过、当前 Web presence 有效。通过后先完整 finalize 并确认 server 浏览器关闭，再拉起/连接 Companion，新建 client run。
6. client run 从脱敏检查点和当前顶层 URL 重新开始，不迁移 cookie、storage、请求头、表单值或服务端凭据。client 再失败只能人工接管或终止，禁止回切 server，单任务最多升级一次。

Companion 未安装或离线时，原任务保持 `WAITING_CLIENT`，由 Web 展示安装/拉起引导；用户拒绝或等待超时后以 `CLIENT_INSTALL_REQUIRED`/`CLIENT_OFFLINE` 结束。路由选择、证据类别和父子 run 关系写审计枚举。

### 4.3 Headless 失败检测器

`HeadlessFailureDetector` 是纯规则组件，输入为 executor 产生的结构化、脱敏观测，不接收完整 DOM，也不依赖 LLM 自由判断：

- 主文档状态码、响应头白名单、Content-Type、重定向次数和 origin 变化；
- Playwright `requestfailed`、导航错误、page error、console 错误类别；
- title/body 的受控短文本特征哈希、验证码/挑战 iframe provider、关键元素是否存在；
- 相同 URL/动作连续无进展、挑战页与目标页之间循环；
- WebAuthn、客户端证书、本地网络、文件选择器等能力请求。

特征库放在版本化配置中，只用于识别和交接，不用于规避检测。证据等级固定为：

- **强信号**：`cf-mitigated: challenge` 等明确挑战响应头；已知 CAPTCHA/challenge iframe；页面明确声明不支持 headless/自动化；浏览器明确请求客户端证书、系统 WebAuthn 或本地地址。任一强信号足以得到高置信“故障分类”，但分类后的处置仍按下表执行；例如 CAPTCHA 只能直接证明需要人工，不能直接证明必须转 client。
- **中信号**：403/503；challenge/access-check 标题或短文本；挑战脚本加空业务 DOM；同一导航的 JS 重定向循环；相同安全动作两次无进展。必须在同一次导航出现两个不同来源的中信号才得到高置信分类。
- **弱信号**：空白页、单次超时、慢加载、普通 JS 错误、仅检测到 `navigator.webdriver`。弱信号永不触发升级。

检测器在 `DOMContentLoaded`、其后 2 秒稳定点及动作失败时取样；不会为探测额外重复提交请求。分类矩阵如下：

| 观测/分类 | 默认处置 | 允许转 client |
|---|---|---|
| CAPTCHA/MFA/扫码，服务端 BrowserView 可显示并输入 | `WAIT_HUMAN_SERVER` | 否，先 Web 人工接管 |
| 页面明确声明拒绝 headless/自动化，或服务端人工交还后同一挑战仍持续 | `ESCALATE_CLIENT/HEADLESS_BLOCKED` | 是，且必须过 EscalationGate |
| 明确反爬 challenge header，且稳定点后仍未进入业务页面 | `ESCALATE_CLIENT/ANTI_BOT_CHALLENGE` | 是，且必须过 EscalationGate |
| 401/407 | `FAIL_SERVER/AUTH_REQUIRED` | 否；仅预检已确认本机认证能力时才是 `LOCAL_CAPABILITY_REQUIRED` |
| 单独 403 或 503 | `FAIL_SERVER/ACCESS_DENIED` 或 `SITE_ERROR` | 否；还需独立挑战证据 |
| 429/`Retry-After` | `RETRY_SERVER/RATE_LIMITED` | 否；遵守等待上限，禁止借客户端规避限流 |
| 404/410 | `FAIL_SERVER/NOT_FOUND` | 否 |
| 普通 5xx | `RETRY_SERVER/SITE_ERROR`，预算耗尽后失败 | 否 |
| DNS/TLS/拒绝连接/普通超时 | `RETRY_SERVER/NETWORK_ERROR`，预算耗尽后失败 | 否；只有预检确认目标仅本地网络可达才允许 client |
| 信号冲突或只有弱信号 | `FAIL_SERVER/HEADLESS_DETECTION_INCONCLUSIVE` | 否，向用户说明而不冒险切换 |

普通幂等读取最多重试 2 次，指数退避 2 秒、5 秒；429 服从 `Retry-After` 且不超过任务总超时；非幂等动作不自动重试。LLM 可解释分类结果，但不得改变分类或单独产生 `ESCALATE_CLIENT`。

### 4.4 EscalationGate 与检查点

升级前同时满足：检测结果是高置信 `ESCALATE_CLIENT` 或确定的 `LOCAL_CAPABILITY_REQUIRED`；`irreversible_action_committed=false`；`irreversible_state_unknown=false`；`escalation_count=0`；租户策略允许 client；当前 Agent Web presence 有效。任一条件不满足即停止并返回明确错误。

server run 必须先进入 `ESCALATING_CLIENT -> FINALIZING`，收到 worker `closed` 或完成进程组兜底回收后，才能创建 client 子 run。检查点仅含 `task_id`、脱敏目标 origin/path、已完成的可安全重放步骤类型、失败分类和 `parent_run_id`；禁止含 query、cookie、storage、Authorization、DOM 正文、表单值、截图和键盘内容。若服务端已经输入敏感字段，客户端要求用户重新输入。

### 4.5 headless 兼容策略

服务端按顺序使用：

1. Playwright `channel="chromium"` 的新 headless 模式。
2. 标准 Desktop Chrome viewport、locale、timezone；不伪造不一致 UA。
3. 站点自身明确不支持 headless 或出现验证时转人工/客户端。

不添加 `navigator.webdriver` 篡改、Canvas/WebGL 指纹伪造、验证码识别或代理轮换。这些方案不稳定并引入合规风险。

## 5. 生命周期与资源回收

### 5.1 状态机

```text
CREATED -> ROUTING -> STARTING -> RUNNING_AGENT
                               -> WAITING_HUMAN -> RUNNING_HUMAN -> RESUMING -> RUNNING_AGENT
RUNNING_AGENT -> ESCALATING_CLIENT -> FINALIZING(server) -> WAITING_CLIENT -> STARTING(client)

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

客户端执行器执行同一逻辑，并在服务端收到 `closed` ACK 后完成 finalize。ACK 3 秒超时则标记 `CLOSE_UNCONFIRMED`；客户端本地 watchdog 仍必须在命令通道断开 30 秒后关闭浏览器。

### 5.3 超时与回收

| 项目 | 值 |
|---|---:|
| 单命令默认超时 | 30 秒 |
| 自动执行累计超时 | 5 分钟，不计 `WAITING_HUMAN/RUNNING_HUMAN` |
| 单个 browser run 硬墙钟上限 | 15 分钟 |
| RUNNING 空闲超时 | 60 秒 |
| WAITING_HUMAN 初始租约 | 5 分钟 |
| 人工租约续期 | 最多 1 次，每次 5 分钟 |
| 客户端断线宽限 | 30 秒 |
| reaper 周期 | 15 秒 |
| 应用 shutdown 优雅关闭预算 | 15 秒 |

显式“取消任务”和应用 shutdown 必须向 run cancellation token 传播。工具挂起后原 SSE 正常结束、页面刷新或短暂断线不视为取消；由 Web presence 的 30 秒宽限和人工租约决定是否保留。人工等待期间暂停自动执行预算，但不突破人工租约和 15 分钟硬上限；恢复后从剩余自动执行预算继续。reaper 处理 owner worker 崩溃后的过期租约。

## 6. 客户端执行

### 6.1 实现位置

客户端技术栈、安装包、认证、更新与主进程生命周期由 Agent Desktop 设计决定。浏览器方案只在主应用内提供可选 Node.js runtime；不得为了浏览器能力改变 Agent UI 技术栈或主发布边界：

```text
clients/agent-desktop/
├── electron/                       # Agent 主应用，浏览器不得反向依赖 UI
├── browser-runtime/
│   ├── worker/                     # 独立 Node 子进程，Playwright
│   ├── protocol/                   # browser/1.0 DTO 与帧编解码
│   └── process-controller/         # Windows/macOS 进程树托管
└── tests/
```

Agent Desktop main 按需加载 `BrowserRuntimeManager`，后者为每个 run 启动独立 Node Worker。Windows 使用 Job Object（`KILL_ON_JOB_CLOSE`）托管 Worker 与 Chromium 进程树；macOS 使用独立 process group，按 SIGTERM 5 秒、SIGKILL 2 秒回收。浏览器模块不得成为主窗口创建、登录或普通 Agent API 初始化的依赖。`clients/wecom-personal-rpa/` 不增加项目引用、不接收 Browser 消息、不共享配置目录。

### 6.2 浏览器 Profile

- Profile 根目录使用 Electron `app.getPath("userData")/profiles/{installation_id}`：Windows 对应 `%APPDATA%\AidWorkAgent Browser Companion\`，macOS 对应 `~/Library/Application Support/AidWorkAgent Browser Companion/`。`installation_id` 是本机随机 UUID，不包含账号或租户信息。
- 浏览器选择顺序固定为：本机稳定版 Google Chrome（Playwright `channel="chrome"`）→ Companion 发布包携带的匹配版 Chromium；实际选择通过 capability 上报。两者都使用上述专用 Profile。
- 第一次启动显示“这是受 Agent 控制的专用浏览器”，用户可在其中登录。
- 禁止使用默认 Chrome User Data Directory；禁止复制用户日常 cookies。
- Companion 只持有当前 Web presence 派生的短期 session token，不落盘 Agent 账号凭据；服务端永不接收 cookie 或 storage_state。
- 同一 Profile 同时只允许一个 run；客户端全局最多 2 个浏览器 run。
- terminal 时关闭浏览器进程，但保留 Profile 登录态；用户可在设置中清除。

### 6.3 客户端通道

Browser Companion 使用独立连接协议，但身份完全来自当前 Agent Web：

- Web presence：`/api/browser/web_presence/ws`，由已登录 Agent Web 建立；`web_instance_id` 是保存在当前标签页 `sessionStorage` 的 UUID，刷新复用、关闭标签页删除，15 秒心跳，30 秒租约。
- 控制：`/api/v1/browser/clients/ws`，Companion 使用 launch ticket 换取的短期 session token 建立 JSON/二进制 WebSocket。
- 画面上行：同一 WebSocket 的二进制帧，头部为固定长度 run_id/seq/timestamp/尺寸；最大单帧 256 KB。
- 前端观看：`/api/browser/runs/{run_id}/view_ws?ticket=...`，服务端只转发最新帧。
- Companion 自己实现 WebSocket 分片组装、单消息 1 MB 上限、单 writer 锁、心跳和指数退避；不引用企业微信 RPA 的连接类。

客户端上线发送 capability：

```json
{
  "type": "capabilities",
  "protocol_version": "browser/1.0",
  "companion_session_id": "bcs_...",
  "web_instance_id": "web_...",
  "features": ["headed", "screencast", "human_control", "persistent_profile"],
  "browser": "chromium",
  "max_runs": 2
}
```

### 6.4 Agent Web 拉起与临时认证

1. 用户登录 Agent Web 后，页面使用现有登录凭据调用 `POST /api/browser/web_presence/ticket` 获取 60 秒单次 WS ticket，再建立 `web_presence` WebSocket；原始 JWT/cookie 不进入 WebSocket URL。服务端从登录凭据确定 `tenant_id + user_id`，客户端不能自报或覆盖。
2. 用户触发本机浏览器任务时，Web 调用 `POST /api/browser/companion/launch_ticket`，提交当前 `web_instance_id`。服务端确认 presence 在线后签发 60 秒、单次使用的随机 ticket，并在 Redis 只存其 SHA-256 摘要。
3. Web 打开 `aidbrowser://launch?origin={agent_origin}&ticket={ticket}`。安装程序负责注册该自定义协议；用户不填写服务器地址、client_id、secret 或配对码。
4. Companion 校验 origin 必须为 HTTPS（localhost 开发环境例外），首次遇到新 origin 时显示“允许该 Agent Web 控制专用浏览器”的一次性安全确认。确认只记 trusted origin，不保存 Agent 登录凭据。
5. Companion 调用该 origin 的 `POST /api/v1/browser/clients/exchange_launch_ticket`。服务端原子消费 ticket，返回 `companion_session_id` 和 60 秒短期 session token；token 每 30 秒通过存活连接轮换，不落盘。
6. Companion 使用 session token 连接 `/api/v1/browser/clients/ws`。服务端把连接固定绑定到 ticket 中的 `tenant_id + user_id + web_instance_id`，不能切换用户或租户。
7. Web presence 断开、用户退出登录、凭据失效或页面关闭后，服务端给予 30 秒刷新/网络抖动宽限；仍未恢复则取消关联 run、关闭 Companion WSS。Companion watchdog 随即关闭浏览器，60 秒无活动后退出应用。
8. Companion 单独启动时只显示“请从已登录的 Agent Web 发起本机浏览器任务”，不提供任何手工连接配置。

### 6.5 是否需要安装，以及 Web 引导闭环

执行模式与安装要求固定如下：

| 场景 | 是否安装 Companion | 执行位置 |
|---|---|---|
| 公网页面读取、搜索、采集 | 不需要 | server headless |
| 在 Agent Web 中观看服务端浏览器 | 不需要 | server + Web BrowserView |
| 在 Agent Web 中远程处理服务端验证码/MFA | 不需要 | server + Web 人工接管 |
| 使用本机 Chrome、本机登录态、本地网络/证书 | 需要，首次每台设备安装一次 | Browser Companion |
| 任务已满足 client 升级条件，用户直接看到本机可见浏览器并操作 | 需要 | Browser Companion |

普通 Agent Web 页面受浏览器同源策略、进程隔离和权限模型限制，不能直接控制用户的其他标签页、读取 Chrome Profile 或启动本机 Playwright。因此“本机执行”不能只靠网页 JavaScript 实现；必须安装 Companion 或浏览器扩展。本项目首期选择 Companion，以保留 Playwright 完整能力。

Web 引导流程：

1. 用户发起需要 client 的任务；`BrowserRouter` 未找到在线 Companion，返回 `CLIENT_INSTALL_REQUIRED`，而不是通用失败。
2. Agent Web 展示 `BrowserCompanionInstallCard`，根据浏览器平台提供 Windows x64 `.exe` 或 macOS universal `.dmg`；不支持的平台提供“改用服务端模式”。
3. 已安装用户点击“打开并连接”。Web 调用 `POST /api/browser/companion/launch_ticket` 获取 60 秒一次性 ticket，然后打开 `aidbrowser://launch?...`。
4. 浏览器阻止自定义协议时，安装卡显示“再次打开”及浏览器放行指引；不提供设备码、服务器地址或密钥手工配置回退。
5. Companion 上线后服务端发布 `browser_companion_online` SSE，安装卡自动变为“已连接”并自动继续被暂停的原任务；不要求刷新页面、点击第二次继续或重发整段任务。
6. 版本低于服务端最低协议时返回 `CLIENT_UPDATE_REQUIRED`；安装卡显示下载更新。Companion 首期检查签名发布源并提示更新，不静默跨大版本升级。
7. 用户拒绝安装时结束升级：高置信 headless 失败保留原失败说明，本地能力缺失返回无法继续；不得把已失败任务静默重跑 server，也不得伪装成 server 可完成。

发布产物：Windows 生成签名 NSIS 安装包；macOS 生成 universal DMG，并完成 Developer ID 签名与 Apple notarization。未签名/未 notarize 的构建只能用于开发，不能进入生产下载页。

## 7. 可视化与人工接管

### 7.1 画面链路

- 服务端执行器：`page.screencast.start(on_frame=...)`，JPEG quality 60，最大 1280×720，最高 5 FPS。
- 客户端执行器：固定 Node.js Playwright 1.59+，使用其 Screencast API；版本不满足时 capability 注册失败，不运行兼容分支。
- Hub 为每个 run 只保留最新一帧；消费者慢时丢旧帧，不累积队列。
- 无观看者时降为每个动作后一帧；有观看者时开启实时流。
- 帧不写日志、不进 Redis、不进数据库。审计截图必须显式开启并走租户加密临时存储。

### 7.2 接管流程

1. 编排器检测 CAPTCHA/MFA/扫码/文件选择器，或用户点击“接管”，生成 `HumanAssistanceRequest`。请求必须包含 `assistance_id/run_id/agent_execution_id/tool_call_id/reason_code`、用户可执行指引、操作位置、完成模式、白名单完成条件和到期时间。
2. `HumanControlCoordinator` 先保存浏览器检查点，再把原工具调用持久化为 `SUSPENDED_TOOL`。两项都成功后 CAS 获取控制锁，browser run 变为 `WAITING_HUMAN`；Agent 停止发命令。任何一步失败都不得向用户谎报“可以继续”。
3. Agent Web 必须展示 `HumanAssistanceCard`，明确告诉用户：为什么暂停、在哪个浏览器操作、按顺序做什么、完成后是否会自动继续、无法自动识别时点击哪个按钮、剩余时间。密码、验证码要求用户直接输入浏览器，不在聊天中回复。
4. 用户点击“开始接管”，状态变 `RUNNING_HUMAN`。本机用户操作 Companion 可见窗口；网页用户的 pointer/keyboard 经授权 WebSocket 直达 executor，不进入 LLM 上下文、不持久化。
5. executor 在人工控制期间只上报导航、DOM 结构变化和脱敏完成条件，不上报按键、输入值、cookies 或表单正文。`HumanCompletionMonitor` 在条件连续两次、间隔 1 秒成立后发出 `human_completion_detected`。
6. 自动条件成立时，或用户点击“完成并继续”且服务端重新校验通过时，`HumanControlCoordinator` 用 `assistance_id` 做 CAS：`RUNNING_HUMAN/WAITING_HUMAN -> RESUMING`。重复点击、重复事件和 WebSocket 重连只能成功一次。
7. CAS 成功后丢弃在途人工输入、释放人工控制锁、执行新 snapshot，并写入唯一 `browser_resume_job`。`AgentResumeCoordinator` 领取 job，从原 run、原 page/context、原步骤索引继续 `BrowserOrchestrator`；不是创建新 browser task，也不重新导航起始 URL。
8. 原工具下一次真正完成、失败或再次需要人工时，Agent Runtime 才把对应 tool result 接回原 `tool_call_id` 并继续原 Agent 执行。前端收到 `agent_continuation_started`，无需用户再发消息。
9. 用户选择取消、租约到期或上下文丢失时终止原工具并关闭浏览器。owner/browser 已崩溃时返回 `RESUME_CONTEXT_LOST`，不得假装从原页面继续；只有检查点声明所有步骤可安全重放且未发生不可逆操作时，才可另行让用户确认是否重开。

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

`BrowserView.vue` 必须显示：执行位置、连接状态、当前 URL origin、Agent/人工控制状态、剩余租约、接管/完成并继续/取消按钮。`HumanAssistanceCard.vue` 展示结构化指引和自动检测状态；接管时自动展开。Companion 同步显示相同指引和完成按钮。禁止把密码、验证码或键盘内容显示在进度消息中。

## 8. 数据、缓存和 API

### 8.1 PostgreSQL

新增业务审计表 `bs_browser_runs`。Companion 连接是 Web presence 的临时执行会话，不新增长期设备绑定表；`companion_session_id`、capabilities 和在线状态只存 Redis，任务审计仍写入 `bs_browser_runs`：

| 字段 | 说明 |
|---|---|
| `id` | BIGSERIAL |
| `tenant_id` | TEXT，所有查询必带 |
| `user_id` | TEXT，创建者 |
| `run_id` | TEXT UNIQUE，不可猜 UUIDv4 |
| `parent_run_id` | 可空；client 升级 run 指向已关闭的 server run |
| `session_id` | TEXT，关联对话，仅用于审计 |
| `execution_target` | server/client |
| `executor_client_id` | 可空；返回前脱敏 |
| `state` | 状态机枚举 |
| `routing_reason` | 枚举，不存自由文本 |
| `failure_class/evidence_level` | 可空白名单枚举，不存页面证据正文 |
| `escalation_count` | 0 或 1 |
| `started_at/finished_at` | TIMESTAMP |
| `close_reason/error_code` | 白名单枚举 |
| `steps_count` | INTEGER |
| `created_at/updated_at` | TIMESTAMP |

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

### 8.2 Redis

在 `CacheKeys` 登记：

- `browser_run:{tenant_id}:{run_id}`：状态摘要，TTL 10 分钟，terminal 主动删除。
- `browser_owner:{tenant_id}:{run_id}`：worker/client owner lease，TTL 30 秒，每 10 秒续租。
- `browser_control:{tenant_id}:{run_id}`：agent/user 控制锁，TTL 随状态。
- `browser_view_ticket:{tenant_id}:{run_id}:{jti}`：一次性观看票据，TTL 60 秒，连接后删除。
- `browser_web_presence:{tenant_id}:{user_id}:{web_instance_id}`：Web 在线租约，TTL 30 秒，每 15 秒续租。
- `browser_launch_ticket:{ticket_hash}`：绑定 tenant/user/web_instance/origin 的单次拉起票据，TTL 60 秒，exchange 时原子删除。
- `browser_companion_session:{tenant_id}:{user_id}:{companion_session_id}`：临时 capability 与连接状态，TTL 60 秒，WSS 存活时续租，断开主动删除。
- `browser_assistance:{tenant_id}:{assistance_id}`：脱敏完成条件、步骤索引和控制状态，TTL 等于人工租约，CAS 更新。
- `agent_tool_suspension:{tenant_id}:{agent_execution_id}:{tool_call_id}`：原工具挂起引用，TTL 为人工租约加 2 分钟收尾时间。
- Redis Stream `browser_resume_jobs`：字段仅含 tenant_id、assistance_id、run_id、job_id；consumer group 领取，数据库 UNIQUE assistance_id 保证只恢复一次。
- Redis Stream `agent_continuation_events:{tenant_id}:{continuation_id}`：带递增 seq 的续跑事件，TTL 15 分钟；前端重连按 last_seq 补取，最终 Agent 消息仍按现有会话消息机制持久化。

Redis 不可用时：禁止创建需要跨请求恢复或客户端执行的 run；只允许单请求 server run，且必须在请求 `finally` 中关闭。不能静默降级为跨 worker 内存会话。

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
- `POST /api/browser/companion/launch_ticket`：当前 Web presence 获取 60 秒一次性拉起票据。
- `POST /api/v1/browser/clients/exchange_launch_ticket`：Companion 原子交换 ticket 获取短期 session。
- `WS /api/v1/browser/clients/ws`：Browser Companion 独立控制与帧通道。
- SSE：`browser_run_ready`、`browser_run_state`、`browser_human_required`、`browser_human_completion_detected`、`browser_resume_queued`、`browser_resume_started`、`agent_continuation_started`、`browser_run_closed`

原请求 SSE 在 `SUSPENDED_TOOL` 后正常结束。恢复时 `AgentResumeCoordinator` 将带 seq 事件写入 continuation stream，同时经当前 Web presence WebSocket 发 `agent_continuation_available` 通知；前端随即拉取并展示续跑事件。刷新或 30 秒内重连后使用 last_seq 补取，不能要求用户重新发送聊天消息来制造新请求。

所有接口使用现有认证和 `X-Tenant-Id`，并校验 `tenant_id + user_id + run_id`。平台管理员只能在明确租户上下文中观看，不得默认跨租户列出。

## 9. 安全与合规

- URL Policy 拒绝 `file:`, `data:`, `javascript:`、localhost、RFC1918、链路本地、云 metadata；每次重定向和 DNS 变化复检。
- 下载默认拒绝可执行文件；文件写入租户 temp，限制 50 MB，任务结束删除或经用户确认注册。
- 填表值默认标记 sensitive，不进入 steps、日志、异常、截图文件名或 LLM 回显。
- 浏览器视图票据一次性、短 TTL；WebSocket Origin 校验；全链路 TLS。
- 客户端首次启用 Browser capability 需管理员开启、终端用户确认；托盘持续显示“浏览器受 Agent 控制”。
- 人工输入只转发到 executor；服务端不得记录按键正文。
- 不暴露 Playwright WS/CDP endpoint 到公网。官方文档明确提示知道 Playwright wsPath 的任意进程或网页可能取得 OS 用户控制权。
- 客户端不调试默认 Chrome Profile。Chrome 136+ 也要求 remote debugging 使用非默认 `user-data-dir`。

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
- `browser_client_escalations_total{reason,outcome}`
- `browser_client_escalation_rejected_total{reason}`
- `browser_frame_dropped_total{source}`
- `browser_client_online`、`browser_client_command_timeout_total`

日志只记录 `run_id`、脱敏 client_id、动作类型、耗时、状态和白名单错误码。禁止记录 URL query、页面正文、表单值、验证码和截图 base64。

默认容量：每个 API 实例 server run 上限 4；每租户 server run 上限 2；每客户端上限 2；超限排队最多 30 秒，之后 `CAPACITY_EXCEEDED`。容量值进入配置并同时维护 Settings/YAML。

## 11. 失败语义

错误码固定为：`WEB_PRESENCE_REQUIRED`、`CLIENT_INSTALL_REQUIRED`、`CLIENT_UPDATE_REQUIRED`、`CLIENT_OFFLINE`、`CLIENT_VERSION_UNSUPPORTED`、`HEADLESS_BLOCKED`、`ANTI_BOT_CHALLENGE`、`HEADLESS_DETECTION_INCONCLUSIVE`、`LOCAL_CAPABILITY_REQUIRED`、`CLIENT_ESCALATION_BLOCKED`、`IRREVERSIBLE_STATE_UNKNOWN`、`AUTH_REQUIRED`、`ACCESS_DENIED`、`RATE_LIMITED`、`SITE_ERROR`、`NOT_FOUND`、`NETWORK_ERROR`、`HUMAN_REQUIRED`、`HUMAN_TIMEOUT`、`HUMAN_COMPLETION_NOT_MET`、`TOOL_SUSPEND_FAILED`、`TOOL_WAITING_HUMAN`、`RESUME_CONTEXT_LOST`、`RESUME_ALREADY_CONSUMED`、`COMMAND_TIMEOUT`、`TASK_TIMEOUT`、`BROWSER_CRASHED`、`NAVIGATION_BLOCKED`、`SSRF_BLOCKED`、`CAPACITY_EXCEEDED`、`CANCELLED`、`CLOSE_UNCONFIRMED`、`INTERNAL_ERROR`。

对用户返回稳定中文说明；debug 字段使用脱敏后的错误类别，不直接返回 `str(e)`。

## 12. Codex 可借鉴边界

Codex 当前公开产品表面区分：应用内 Browser Use、控制用户现有 Chrome 状态的 Chrome 扩展、以及桌面 Computer Use。可借鉴的是“按执行表面选择工具”和“用户能观察/接管”，不是其未公开内部协议。本方案对应为 server browser、client dedicated browser、human control 三层；不声称复刻 Codex 内部实现。

## 13. 方案取舍

| 备选 | 结论 | 原因 |
|---|---|---|
| Docker headed + Xvfb + noVNC 作为主方案 | 不采用；仅运维诊断 | 用户仍操作服务器浏览器，不具备本地登录态；VNC 桌面暴露面更大 |
| 只做 server screencast | 不足 | 能看不能稳定处理本地登录、扫码、客户端证书和复杂验证码 |
| 直接连接用户日常 Chrome CDP | 禁止 | 高权限、Profile 风险、Chrome 136 限制、误操作日常标签页 |
| 浏览器扩展首期实现 | 暂不采用 | 需要用 `chrome.debugger` 重建 Playwright 等待/定位能力；独立 Companion 能继续复用 Playwright 且不污染业务客户端 |
| 第三方 browser cloud | 暂不采用 | 数据出境、租户隔离、成本和供应商锁定；不解决本机登录态 |
| 自动验证码识别/代答 | 禁止 | 合规和可靠性不可接受 |

## 14. 验收标准

1. Docker 默认配置不传参数时以 headless 启动。
2. 正常、导航失败、LLM 异常、命令超时、取消和 shutdown 六条路径后，目标 Chromium/Playwright 进程数回到基线。
3. `WAITING_HUMAN` 5 分钟到期自动关闭；续租上限生效。
4. 两租户同时使用同名 session 不会互见状态、帧或控制权。
5. Gunicorn 两 worker 下任意 API worker 都能读取 run 状态，只有 owner 执行命令。
6. server/client 两执行器通过同一套 executor contract tests。
7. 用户能实时观看、接管、输入验证码、交还并继续；输入内容不出现在日志/DB/LLM steps。
8. 客户端断网 30 秒后本地 watchdog 关闭浏览器；恢复连接不重放已完成的不可逆命令。
9. SSRF 测试覆盖 DNS 重绑定、重定向到私网、IPv6 loopback 和 metadata 地址。
10. Windows 10/11 x64 与 macOS 13+（Intel、Apple Silicon）均完成安装、Web 拉起、临时认证、可见执行、人工接管、断线回收和卸载验收。
11. 未安装 Companion 时 server 模式正常使用；只有高置信 headless 升级或确定的本地能力预检失败才出现安装卡，Web 拉起 Companion 上线后自动从原任务继续，全程没有手工服务器/账号/密钥/设备码配置。
12. 关闭 Agent Web 或退出登录后 30 秒内，关联 client run 被取消，Companion 浏览器关闭且连接失效。
13. 前端 build、Python 浏览器测试、Companion 测试和真机 Chrome 验收全部通过。
14. 普通 403、429、404、5xx、DNS/TLS、超时和单独 CAPTCHA iframe 均不会触发 client；403/503 只有叠加独立 challenge 证据才升级，CAPTCHA 先在 server BrowserView 人工处理。
15. 每个自动 client run 都能追溯到已关闭的 server 父 run 或确定的本地能力预检失败；单任务最多升级一次，且不传递 cookie/storage/表单值。
16. 人工请求卡必须明确展示原因、操作位置、操作步骤、完成方式和超时；密码/验证码只在浏览器中输入。
17. 用户完成 CAPTCHA/登录后，不发送新聊天消息也能触发原 `tool_call_id` 从同一 page/context 继续，最终由原 Agent 自动给出结果。
18. 自动条件事件、完成按钮双击、WebSocket 重连并发发生时只生成一个 resume job；两 Gunicorn worker 下也只恢复一次。
19. “完成并继续”条件不满足时保持人工状态并指出未满足条件；owner/browser 丢失时明确返回 `RESUME_CONTEXT_LOST`，不得从头假装续跑。
20. 人工完成提交/支付等不可逆动作后，恢复 Agent 不重复该动作；任务终态浏览器进程仍回到基线。

## 15. 参考资料

- [Playwright Browsers：新 headless 与 Chromium channel](https://playwright.dev/docs/browsers)
- [Playwright Docker/CI：容器与 Xvfb](https://playwright.dev/docs/docker)
- [Playwright Python Browser：优雅关闭 context/browser](https://playwright.dev/python/docs/api/class-browser)
- [Playwright Python Screencast 1.59](https://playwright.dev/python/docs/release-notes#version-159)
- [Playwright BrowserType：connect 与 CDP 的能力/安全边界](https://playwright.dev/docs/api/class-browsertype)
- [Chrome 136 remote debugging 安全变化](https://developer.chrome.com/blog/remote-debugging-port)
- [Chrome DevTools Protocol Page.startScreencast](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-startScreencast)
- [noVNC 官方项目（仅作为运维备选）](https://github.com/novnc/noVNC)
