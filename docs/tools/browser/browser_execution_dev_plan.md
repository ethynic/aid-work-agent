# 浏览器执行架构、可视化与人工接管开发计划

> 日期：2026-07-14
>
> 状态：🔧 部分完成（Phase 0～1 完成；Phase 2 实现完成、真实服务门禁待验证；Phase 3 首轮实现完成、自动续跑等门禁待收口）
>
> 设计基线：[browser_visualization_design.md](./browser_visualization_design.md)
>
> 流程：每个 Phase 代码完成后按三智能体流程串行执行开发自测、独立测试、Code Review；未经验证不进入下一 Phase；不自动提交。
>
> 2026-07-14 修订：客户端改为全新独立 `clients/browser-companion/`；`clients/wecom-personal-rpa/` 明确不在改动范围。
>
> 2026-07-14 v2.2 修订：Companion 技术栈改为 Electron + Node Playwright，首期同时交付 Windows/macOS；增加 Web 安装、拉起、配对、更新引导。
>
> 2026-07-14 v2.3 修订：删除手工配对、长期设备表和独立连接配置；Companion 只由当前 authenticated Agent Web 使用一次性 ticket 拉起，连接绑定 Web presence。
>
> 2026-07-14 v2.4 修订：`auto` 改为服务端 headless 强制优先；增加结构化失败检测、反爬/HTTP 分类和只允许一次的安全 client 升级门。
>
> 2026-07-14 v2.5 修订：人工参与改为原工具调用持久化 suspend/resume；增加明确操作指引、完成条件监测、幂等 resume job 和无需用户再次发消息的 Agent 自动续跑。
>
> 2026-07-14 v2.6 修订：采用 Agent-first 原则；取消独立 Browser Companion 产品与安装包，客户端执行改为 Agent Desktop 的可选 browser runtime。桌面客户端技术栈、认证、发布和生命周期以 Agent Desktop 设计为准，浏览器能力不得阻塞或削弱 Agent 主链路。

## 1. 完成定义

全部满足才标记完成：默认 headless；所有终态/取消/shutdown 零遗留进程；统一 executor 契约；服务端实时视图与人工接管；人工完成后原工具和原 Agent 无新消息自动续跑；Agent Desktop 可选 runtime 可见执行；runtime 关闭/故障时 Agent 主链路正常；确定性路由；多租户/多 worker 安全；自动化测试和真机验收通过；相关设计、缓存、文件和数据库文档同步。

## 2. Phase 总览

| Phase | 交付 | 状态 | 进入条件 | 退出门禁 |
|---|---|---|---|---|
| 0 | 基线、配置和泄漏复现测试 | ✅ 已完成 | 设计批准 | 测试能稳定复现当前泄漏/配置问题 |
| 1 | 本地浏览器生命周期 P0 修复 | ✅ 已完成 | Phase 0 | 六类终态进程回基线 |
| 2 | RunManager + Executor 抽象 + 多租户状态 | 🔧 部分完成 | Phase 1 | 两 worker/两租户契约测试通过 |
| 3 | 服务端可视化 + 工具挂起/人工接管/自动恢复 | 🔧 部分完成 | Phase 2 | 明确指引、完成监测、原工具及 Agent 幂等续跑、超时关闭通过 |
| 4 | Agent Desktop 可选 browser runtime | ⬜ | Phase 3，且 Agent Desktop Phase 0～3 稳定 | 桌面内可见执行和断线回收通过；关闭模块后 Agent 主链路正常 |
| 5 | 自动路由与安全策略 | ⬜ | Phase 4 | 路由矩阵、SSRF、不可逆防重放通过 |
| 6 | 可观测性、容量和运维 | ⬜ | Phase 5 | 指标/告警/压测/故障注入通过 |
| 7 | 灰度迁移、全量验收和文档收口 | ⬜ | Phase 6 | 全验收通过并更新索引状态 |

## 3. Phase 0：基线与红灯测试

> 进度（2026-07-17）：✅ 已完成。默认 headless、生命周期契约和 PID + create_time 安全探针已建立；Phase 1 已补 `BrowserAutomationTool` / `BrowserOrchestrator` 生产执行边界覆盖，strict-xfail 已移除。

### 改动

- 修正 `configs/config.yaml` 的 `tools.browser.headless: true`，保持 `src/config/settings.py` 一致。
- 新增 `tests/unit/tools/browser/test_session_lifecycle.py`：启动半失败、page/context close 抛错、幂等 close、并发 close。
- 新增 `tests/integration/browser/test_browser_process_cleanup.py`：以本测试 Playwright driver 为所有权根记录进程树，覆盖 success/error/cancel/timeout/shutdown/ask_user_expire；Phase 1 另以生产 `execute/finally` 单测覆盖真实入口 wiring。
- 将旧 `tests/test_browser_tool.py` 的 headed 手工测试迁到 `tests/e2e/browser/`，默认 skip，无 CI GUI 依赖。
- 建立带 PID + create_time 校验的测试探针，只观测本测试启动的进程，禁止误杀机器上的其他 Chrome。

### 验证

- 红灯用例已在旧实现上稳定失败；Phase 1 修复后已删除 strict-xfail 并转绿。
- `./scripts/dev_test.sh tests/unit/tools/browser -q`。

## 4. Phase 1：本地生命周期

> 进度（2026-07-17）：✅ 已完成。已落地事务启动、结构化幂等关闭、保守进程所有权守卫、工具/编排器终态 finally、取消与 5 分钟总超时、旧恢复参数退役、15 秒 shutdown、错误与日志脱敏。三智能体流程通过；核心与相邻测试 40 passed，真实 Playwright 六终态进程探针 6 passed。

### 改动

- 重写 `src/tools/browser/session.py`：`BrowserSession.close()` 幂等、逐层 best-effort、引用置空、close report；`close_browser_session` 改 async。
- 新增 `src/tools/browser/process_guard.py`：当前内联实现过渡期只跟踪本 run 资源；禁止按进程名批量终止。
- `BrowserAutomationTool` 和 `BrowserOrchestrator` 使用 `try/finally`；`_ensure_session()` 也在保护范围内。
- 引入 `CancellationToken` 等价的 `asyncio.Event/TaskGroup` 取消传播。
- 删除旧 `ask_user + user_response + session_id` 恢复路径；过渡期遇到旧参数返回 deprecated，不再依赖 LLM 重调工具。
- 主应用 lifespan 注册 `close_all_owned_browser_runs()`，预算 15 秒。
- 移除 Agent 工具 schema 中的 `headless`；旧原子工具停止注册，兼容入口加 deprecated 标记。
- 错误返回改用 `sanitize_error`；移除日志中的 `user_response` 和 fill value。

### 验证

- Phase 0 六类进程测试全绿。
- 对 `page.close/context.close/browser.close/playwright.stop` 分别注入异常，后续层仍执行。
- 重复 close 10 次无异常、无重复 kill、无负计数。

## 5. Phase 2：统一 Run 与 Executor

> 进度（2026-07-17）：🔧 部分完成。生产 `browser_automation` 已切换到
> `BrowserRunManager -> LocalPlaywrightExecutor -> 独占 worker`；PageOps 不再
> 持有 Playwright 对象。4 字节大端、1 MB 上限 IPC、seq/command_id 幂等、
> deadline、owner lease/reaper、租户隔离、Redis 单请求降级与双表审计已落地。
> Phase 3 可视化/人工接管与 remote executor 未提前实现。
> 当前 mock/本机 Playwright 门禁已通过；P1 收口已增加写帧前 owner epoch
> fencing（租约过期、reaper 抢占、同 owner ABA、取消和状态丢失均拒绝写 IPC）
> 及 Windows Job Object `KILL_ON_JOB_CLOSE` 子树托管，attach 失败 fail-closed。
> 因本机无独立 Redis/PostgreSQL 环境，Redis Lua/TTL/掉线恢复与两套 DDL 的
> 真实服务执行尚未验收，因此外部服务门禁完成前仍不得将 Phase 2 标为完成。

### 改动

- 新增 `src/tools/browser/executor/base.py`、`local.py`、`models.py`、`src/tools/browser/worker_main.py`、`worker_protocol.py`。
- Local executor 每 run 通过 `asyncio.create_subprocess_exec(..., start_new_session=True)` 启动隔离 worker；IPC 使用长度前缀帧；终态先优雅 close，随后按 PGID 做 5s/2s TERM/KILL 兜底。
- `PageOps` 不再持有 Playwright Page，只调用 `BrowserExecutor`。
- 新增 `src/tools/browser/run_manager.py`、`run_store.py`、`reaper.py`、`router.py`。
- Redis keys 加到 `src/core/cache_utils.py`，并更新 `docs/system/cache_usage.md`。
- 新增 `bs_browser_runs` 和 `bs_browser_assistance_requests`：migration、`deploy/init-postgres.sql`、DB service；所有 CRUD 带 tenant_id。Companion 在线状态仅存 Redis，不建长期设备表。
- 更新 `docs/system/database_system_table.md`（若最终分类为业务表则记录在对应业务表文档）和数据库变更记录。
- `session_id` 仅为对话关联；执行主键统一为服务端 UUID `run_id`。
- Redis 不可用降级只允许单请求 server run。

### 验证

- 对 local/fake remote 跑同一 contract test suite。
- 两租户相同 session_id、两 Gunicorn worker 的 owner/lease/取消集成测试。
- reaper 抢占过期 owner 时不会并发执行命令。
- 每条生产命令在 `write_frame` 紧前原子校验 owner epoch token、租约、取消和
  run 状态；失败返回稳定 `OWNER_LEASE_LOST/CANCELLED` 并回收 owned worker。
- Windows worker 在发送 start 前加入独占 Job Object，启用
  `KILL_ON_JOB_CLOSE`；attach 失败不得启动无托管浏览器。
- Phase 2 targeted contract/协议/RunManager/DB/静态边界测试全绿；真实 local
  worker contract 覆盖 start/navigate/snapshot/content/close，fake remote 运行
  同一契约。Phase 0～1 browser unit 回归全绿；真进程六终态测试仍保留 opt-in。

## 6. Phase 3：服务端画面与网页人工接管

> 进度（2026-07-20）：🔧 部分完成。已落地 `ToolSuspension` 控制结果、同会话
> 409 门禁、latest-only 画面 Hub、服务端 JPEG 采样、一次性 view ticket、网页
> pointer/keyboard 状态门禁、人工协作 Redis 状态/CAS、白名单条件与双采样、
> resume job/continuation seq 事件、BrowserView/HumanAssistanceCard 及前端断线补取。
> 当前本机单元测试与前端 build 通过。尚未达到退出门禁：真实 Redis consumer
> group/两 Gunicorn worker owner 路由未验收；恢复结果已接回原 tool_call_id 的
> continuation 事件，但原 Agent 后续 LLM 循环的后台重启仍需收口；真实验证码页
> 同 page/context E2E、自动完成后台检测、人工超时 reaper 和 WebSocket 重连集成证据尚缺。因此不得
> 标记 Phase 3 完成，也未提前实现 Phase 4 remote executor。

### 后端

- 新增 `src/tools/browser/view_hub.py`、`human_control.py`、`human_completion_monitor.py`、`agent_resume_coordinator.py`、`resume_store.py`。
- Agent Runtime 新增 `ToolSuspension` 契约：保存 `agent_execution_id/tool_call_id/run_id/assistance_id`，空 tool_call_id 先规范化生成；挂起不写普通 tool result、不标记计划完成、不继续 LLM，也不保持原 HTTP/SSE coroutine。
- 会话有活动 suspension 时阻止第二个 Agent 循环，API 返回 `409 TOOL_WAITING_HUMAN`；前端禁用普通发送，只保留完成/取消，避免覆盖原 tool_call。
- 新增 `src/api/browser_runs.py` 的 view ticket、view WS、take/complete/extend/cancel API；complete 必须服务端重新快照校验并 CAS。
- Local executor 接 `page.screencast`：1280×720、quality 60、5 FPS、latest-frame backpressure。
- executor 上报脱敏 navigation/DOM-structure 变化；实现白名单完成条件、连续两次稳定判定和 `confirm_only`。
- Redis 增加 assistance、tool suspension、`browser_resume_jobs` 和带 seq 的 `agent_continuation_events` Stream；consumer group + 数据库 UNIQUE assistance_id 确保 exactly-once 领取效果，continuation 事件支持断线补取。
- `AgentResumeCoordinator` 从同一 run/page/context 和步骤索引恢复 orchestrator，工具终态后把结果接回原 tool_call_id，再启动原 Agent continuation。
- Web presence 增加 `agent_continuation_available` 通知；新增 continuation events 补取 API。原 SSE 挂起后可正常结束，恢复事件不得写入已关闭响应。
- pointer/keyboard 只在 `RUNNING_HUMAN` 接收；正文不写日志。

### 前端

- 新增 `frontend/src/components/BrowserView.vue`、`browser/HumanAssistanceCard.vue`。
- 更新 `frontend/src/types/index.ts`、`api/agent.ts`、`composables/useAgent.ts`、`MessageItem.vue`。
- UI 含暂停原因、操作位置、逐步指引、完成条件状态、接管、完成并继续、取消、租约倒计时、执行位置和断线状态；监听 continuation available、按 last_seq 补取并展示续跑过程；敏感信息要求直接填浏览器。

### 验证

- 单元：票据一次性、越权、控制锁 CAS、白名单 predicate、双采样稳定性、resume job 幂等、慢消费者丢帧。
- 集成：验证码模拟页暂停 → 显示明确指引 → 人工输入 → 自动检测/按钮交还 → 同一 page/context 和原 tool_call_id 继续 → Agent 无新用户消息自动回复。
- 集成：完成事件与按钮双击并发、WebSocket 重连、不同 Gunicorn worker 领取均只恢复一次；第二次人工暂停仍可继续。
- 集成：原 SSE 已关闭后仍能续跑；页面刷新 30 秒内恢复并按 seq 补齐事件，不重复最终消息。
- Agent Runtime：挂起时没有提前 tool result/计划完成；恢复终态后只追加一个匹配原 tool_call_id 的结果；挂起期间并发聊天不会启动第二循环。
- 失败：条件未满足不恢复；租约到期关闭；owner/browser 崩溃返回 `RESUME_CONTEXT_LOST`；不得从起始 URL 静默重开。
- 不可逆：人工完成提交后 checkpoint 标记 `completed_by_human`，恢复 Agent 不重复点击。
- 前端：组件交互测试 + `npm run build`。
- 安全：另一 tenant/user 无法观看或发送输入。

## 7. Phase 4：Agent Desktop 可选 browser runtime

本 Phase 跟随 Agent Desktop 架构，不得反向修改其 UI 技术栈、认证模型、安装包、更新器或启动链路。Agent Desktop 尚未稳定时，本 Phase 等待，不单独创建替代客户端。

### 协议先行

- 在设计文档旁新增 `browser_runtime_protocol.md`，逐字段锁定 browser/1.0 消息和错误码。
- 服务端新增 `src/tools/browser/client_registry.py`、`executor/remote.py`、Web presence、launch ticket/exchange API 和 `/api/v1/browser/clients/ws`。
- 新协议必须自行实现完整消息分片、1 MB 上限、二进制帧、取消、顺序发送锁、心跳和退避，不复用企业微信 RPA 协议。

### 客户端

- 在 `clients/agent-desktop/browser-runtime/` 实现可选模块；不得引用 `clients/wecom-personal-rpa` 下任何项目。
- Agent Desktop main 按需加载 runtime；每个 run 启动独立 Node Playwright Worker，Windows 使用 Job Object，macOS 使用独立 process group 托管进程树。
- runtime 使用独立 feature flag 和故障边界；关闭、缺失、启动失败或协议不兼容不得影响主应用启动、登录、对话、文件和其他工具。
- 固定 Node Playwright 1.59+；运行时优先启动本机稳定版 Chrome `channel="chrome"`，未安装时使用发布包携带的匹配版 Chromium。
- 实现专用 Profile、单 run 命令队列、幂等 command_id、frame publisher、断线 watchdog、终态关闭。
- Companion 渲染与 Web 相同的 HumanAssistance 指引/完成状态；人工页面事件走同一完成监测与 resume 协议，不在本地另写恢复逻辑。
- 实现 Agent Web launch：`sessionStorage` 稳定的 `web_instance_id`、presence WS ticket、60 秒单次 launch ticket、`aidbrowser://launch`、ticket exchange、短期 session token 自动轮换。
- 删除服务器地址、client_id、secret、设备码等手工配置入口；Companion 单独启动只提示从 Agent Web 拉起。
- Agent Desktop 在既有托盘/设置中提供浏览器状态、“停止当前任务”“清除 Profile”；浏览器模块不得新建第二套托盘或更新器。
- Profile 使用 Electron `app.getPath("userData")/profiles/{installation_id}`；服务端不接收 storage state。
- browser runtime 随 Agent Desktop 的签名安装包发布，不产出独立安装包；不修改企业微信 RPA 发布脚本。

### Web 安装引导

- 新增 Agent Desktop 安装/打开卡和客户端状态 composable；组件命名不得固化为独立 Companion 产品。
- 新增平台识别、Agent Desktop 下载/打开、`aidagent://browser-launch`、协议放行指引、在线 SSE 和自动继续原任务；不提供手工 code 回退。
- 新增 `CLIENT_INSTALL_REQUIRED`、`CLIENT_UPDATE_REQUIRED` 的 Agent 工具事件映射，不把它们渲染为普通错误。
- 未安装时 server 浏览器功能必须保持可用；只有高置信 headless 升级或确定的本地能力预检失败才展示安装要求。

### 验证

- browser runtime unit：launch ticket 单次消费、origin 校验、短期 token 轮换、协议分片、乱序/重复、watchdog、Profile 互斥、close 异常。
- Python/TypeScript 协议 golden fixtures 双向反序列化。
- Windows 10/11 x64、macOS 13+ Intel、macOS 13+ Apple Silicon 真机：安装、协议拉起、显示浏览器、复用 Profile、验证码人工操作、断网关闭、服务端重启不重放命令、卸载。
- 前端：未安装/协议被阻止/连接中/在线/版本过低/拒绝安装/Web 退出七种状态组件测试。
- 端到端：关闭 Web、登出、JWT 过期、刷新页面 30 秒内恢复、超过宽限四种 presence 场景。
- 静态边界检查：browser runtime 与企业微信 RPA 工程互不引用；浏览器模块不进入 Agent renderer 依赖图，不成为主应用启动依赖。
- Agent 回归：runtime 禁用、Worker 启动失败、协议版本不兼容、执行中崩溃四种情况均不影响非浏览器功能。

## 8. Phase 5：路由与安全

### 改动

- Agent 工具只暴露 `execution_target=auto|server`；`client` 只由升级状态机内部使用，管理员诊断强制入口独立 RBAC/审计。
- 新增 `src/tools/browser/headless_failure_detector.py`、`challenge_signatures.py`、`escalation_gate.py`；executor 上报脱敏 response/requestfailed/pageerror/console/DOM 特征 DTO。
- 域名策略配置：`server_allowed/local_capability_required/blocked`，管理员维护，默认 `server_allowed`；禁止用域名配置把反爬页面直接送往客户端。
- 实现强/中/弱证据规则和分类矩阵：强信号一个或不同来源中信号两个产生高置信分类，再由分类矩阵决定人工、失败、重试或 `ESCALATE_CLIENT`；LLM 不参与路由裁决。
- 明确 401/403/404/410/429/5xx、DNS/TLS/timeout 处置；429 永不转 client，普通 403/503 没有挑战证据时永不转 client，单独 CAPTCHA/MFA/扫码先进入 server Web 人工接管。
- 实现 `ESCALATING_CLIENT`/`WAITING_CLIENT` 状态、单次升级限制、server 完整关闭屏障、父子 run 审计和脱敏检查点；client 失败不回切 server。
- 新增 `url_policy.py`：scheme、DNS、IP、redirect、download 检查。
- 定义 irreversible action 标记；执行后禁止自动换执行器。
- CAPTCHA/MFA/扫码/文件选择器只转人工，不自动破解。
- 容器改非 root 并启用 sandbox；若环境暂不能切换，显式配置例外并告警，不能静默 `--no-sandbox`。

### 验证

- 路由决策表 100% 分支覆盖；所有 `auto` 非预检能力失败用例都先创建 server run。
- 新增本地 fixture 页和集成用例：普通/挑战型 403、普通/挑战型 503、429 + Retry-After、404、5xx、CAPTCHA iframe、明确 headless unsupported、人工交还后挑战持续、重定向循环、空白页、DNS/TLS/timeout、本地网络/客户端证书能力。
- 验证“一个强信号或两个独立中信号”只决定高置信分类，处置仍必须查分类矩阵；单独 CAPTCHA 进入 server 人工接管，单独状态码、弱信号、LLM 建议均不能升级。
- 验证升级前 server 进程归零；checkpoint 无 query/cookie/storage/header/DOM/表单值；不可逆状态已提交或未知时拒绝升级；client 失败不回切。
- SSRF：IPv4/IPv6、十进制/混合地址、DNS 重绑定、redirect、metadata。
- 恶意客户端伪造 tenant/run/seq/command_id 全部拒绝。
- 不可逆操作后 client 掉线不重试、不迁移。

## 9. Phase 6：生产化

### 改动

- Prometheus 指标和告警：close failure、orphan、client offline、command timeout、capacity、headless 分类、client 升级/拒绝/结果、human assistance 结果、resume job 结果和恢复延迟。
- 每实例 4、每租户 2、每客户端 2 的信号量；排队 30 秒。
- 调试 trace/screenshot 默认关闭；开启时用租户 temp + 加密 + 24h 清理，并更新 `docs/system/file_usage.md`。
- Dashboard 展示聚合状态，不展示页面正文/截图。
- 增加故障注入开关，仅测试环境可用。

### 验证

- 4 个 server run 持续 30 分钟，CPU/内存回到基线，无 orphan。
- client/server 各 2 个并发，第三个按配置排队/失败。
- kill API worker、kill Chromium、断 Redis、断客户端网络、慢 WebSocket 消费者故障注入。

## 10. Phase 7：迁移与上线

### 灰度顺序

1. `browser.runtime.enabled=false` 部署数据库/Redis/新 API，不切流量。
2. 开启本地生命周期和 server executor，5% browser run 灰度。
3. 开启只读实时视图，观察带宽和 close 指标。
4. 开启 server 人工接管。
5. 分别安装一台 Windows 和一台 macOS Browser Companion，按 tenant allowlist 灰度 remote executor。
6. 通过真机验收后逐租户开放 `auto`；保留“关闭 client 升级”和独立 RBAC 管理员诊断入口，不提供 Agent 强制 client 开关。
7. 旧全局 session 实现停止注册；观察一个发布周期后删除兼容代码。

### 回滚

- 任何阶段可关闭 `browser.runtime.enabled` 回到旧 `browser_automation`，但 Phase 1 的可靠关闭和默认 headless 不回滚。
- client 路径独立开关；关闭后 `auto` 只选择 server。
- DB 只新增表，不做破坏性迁移；回滚不删表。

### 最终验证

- Python browser unit/integration/e2e。
- Browser Companion Node/TypeScript 单元、集成测试及 Windows/macOS Release build/publish。
- 前端 test + production build。
- Docker 非 root 启动/import/config 加载检查。
- 按设计 §14 全部验收项逐项保留证据。
- 更新本计划状态、设计版本、`docs/ideas.md`；完成开发后将条目移入 `docs/ideas_finished.md`。

## 11. 不允许的范围漂移

- 不接入验证码破解服务。
- 不做自动化指纹伪装。
- 不控制用户默认 Chrome Profile。
- 不在首期支持 Linux 桌面客户端。
- 不把 CDP/Playwright endpoint 暴露公网。
- 不在此次顺便重写语义快照或更换 Browser Agent 框架。
- 不修改、不引用、不复用 `clients/wecom-personal-rpa/` 的工程、协议、身份或发布产物。
