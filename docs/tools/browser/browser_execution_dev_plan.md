# 浏览器执行架构、可视化与人工接管开发计划

> 日期：2026-07-22
>
> 状态：🔧 部分完成（Phase 0～1 完成；Phase 2 实现完成、真实服务门禁待验证；Phase 3 真实验收未通过，进入 Phase 3R 修复；Phase 4 继续阻塞）
>
> 设计基线：[browser_visualization_design.md](./browser_visualization_design.md)
>
> 流程：每个 Phase 代码完成后按三智能体流程串行执行开发自测、独立测试、Code Review；未经验证不进入下一 Phase；不自动提交。
>
> 2026-07-14 v2.4 修订：`auto` 改为服务端 headless 强制优先；增加结构化失败检测、反爬/HTTP 分类和只允许一次的安全桌面升级门。
>
> 2026-07-14 v2.5 修订：人工参与改为原工具调用持久化 suspend/resume；增加明确操作指引、完成条件监测、幂等 resume job 和无需用户再次发消息的 Agent 自动续跑。
>
> 2026-07-14 v2.6 修订：采用 Agent-first 原则；桌面执行改为 Agent Desktop 的可选 browser runtime。技术栈、认证、发布和生命周期以 Agent Desktop 设计为准，浏览器能力不得阻塞或削弱 Agent 主链路。
>
> 2026-07-20 v2.7 修订：清除全部独立浏览器客户端设计、计划和依赖。Phase 4 改为 Agent Desktop 项目内的 browser runtime 集成阶段；浏览器计划只负责 `browser/1.0`、RemoteExecutor、服务端路由及联合验收，不再拥有客户端安装、更新、托盘或发布计划。
>
> 2026-07-22 v2.8 修订：真实环境确认 Redis 4.3.0 不支持 `XREAD`，现有 resume worker 持续报错；真实验证码登录页又被 LLM `done` 错判为成功。Phase 3R 改用 PostgreSQL 持久 lease 队列、确定性人工需求检测和跨事件循环测试隔离，不要求升级 Redis。

## 1. 完成定义

全部满足才标记完成：默认 headless；所有终态/取消/shutdown 零遗留进程；统一 executor 契约；服务端实时视图与人工接管；人工完成后原工具和原 Agent 无新消息自动续跑；Agent Desktop 可选 runtime 可见执行；runtime 关闭/故障时 Agent 主链路正常；确定性路由；多租户/多 worker 安全；自动化测试和真机验收通过；相关设计、缓存、文件和数据库文档同步。

## 2. Phase 总览

| Phase | 交付 | 状态 | 进入条件 | 退出门禁 |
|---|---|---|---|---|
| 0 | 基线、配置和泄漏复现测试 | ✅ 已完成 | 设计批准 | 测试能稳定复现当前泄漏/配置问题 |
| 1 | 本地浏览器生命周期 P0 修复 | ✅ 已完成 | Phase 0 | 六类终态进程回基线 |
| 2 | RunManager + Executor 抽象 + 多租户状态 | 🔧 部分完成 | Phase 1 | 两 worker/两租户契约测试通过 |
| 3 | 服务端可视化 + 工具挂起/人工接管/自动恢复 | 🔧 Phase 3R 修复待开发 | Phase 2 | 旧 Redis 无 Stream 依赖；验证码确定性转人工；原工具及 Agent 幂等续跑；全文件测试无事件循环残留 |
| 4 | Agent Desktop 项目内集成 browser runtime（跨项目阶段） | ⏸ 被 Phase 3R 阻塞 | Phase 3R 真实门禁通过，且 Agent Desktop Phase 0～3 稳定 | Desktop runtime 与 RemoteExecutor 契约通过；关闭模块后 Agent 主链路正常 |
| 5 | 自动路由与安全策略 | ⬜ | Phase 5A 服务端规则依赖 Phase 3；Phase 5B 桌面升级链路依赖 Agent Desktop Phase 4 | 路由矩阵、SSRF、不可逆防重放通过 |
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
- Agent 工具 schema 暴露可选 `headless`：默认仍取服务端配置；`false` 仅允许私有可信
  本地交互上下文，普通远程调用返回 `VISIBLE_BROWSER_NOT_ALLOWED`。旧会话参数继续 deprecated。

> 2026-07-28 补充：可信本地可见模式已实现。RunManager 将最终模式写入
> `BrowserRunSpec.headless`；桌面宿主通过不进入 Agent schema 的
> `execute_local_interactive()` 入口调用；同时新增确定性异常访问页检测，阻断页不再被 LLM `done`
> 错误映射为成功。待真实可见 Chromium 旁站验收。
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
> Phase 3 可视化/人工接管与 desktop runtime executor 未提前实现。
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
- 新增 `bs_browser_runs` 和 `bs_browser_assistance_requests`：migration、`deploy/init-postgres.sql`、DB service；所有 CRUD 带 tenant_id。Agent Desktop browser runtime 在线状态仅存 Redis，不新建浏览器专用长期设备表。
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

> 进度（2026-07-22）：🔧 真实环境验收未通过，进入 Phase 3R 修复。此前已落地
> `ToolSuspension`、同会话 409、latest-only 画面、一次性 view ticket、网页人工
> 接管、白名单双采样完成监测、持久 resume worker、原 `tool_call_id`
> exactly-once continuation、原 Agent 后台 LLM 续跑、自动完成后台 monitor、人工
> 超时 reaper、continuation seq 补取，以及 BrowserView/HumanAssistanceCard。
> 三智能体流程修复了多工具挂起配对、断线前上下文持久化、延期/reaper 竞态、
> iframe 挑战识别、二次人工暂停路由、续跑 TTL 和前端轮询泄漏；本机 browser
> 单测 177 通过、Phase 3 定向测试 32 通过、Agent 相邻测试 12 通过、前端组件
> 3 通过且 production build、`src.main` import 通过。但测试环境 Redis 4.3.0 对
> `XREAD` 返回 unknown command，两个 resume worker 约每 0.5 秒持续报错；真实
> 验证码页已进入同一 page/context，却被编排器判为 `done`，run 直接 `SUCCEEDED`，
> 未创建 assistance。Phase 3 测试文件整体运行另有 31 通过、1 个跨事件循环残留
> 失败（该用例单独运行通过）。真实 PostgreSQL、两 Gunicorn worker 与六终态进程
> 回收已通过基础验证，但不足以抵消上述失败；Phase 3R 通过前不得开始 Phase 4。
>
> Phase 3R 实施进度（2026-07-22）：🔧 本机实现完成，真实 E2E 待部署补验。
> 已落地：(1) 3R.1 PostgreSQL `bs_browser_resume_jobs` 持久 lease 队列替代 Redis
> Stream，DDL 同步两份 SQL，`BrowserResumeJobDB` 用 `FOR UPDATE SKIP LOCKED` 领取，
> `BrowserResumeWorker` 改短轮询，`ResumeStore` 删除 `xadd/xread`，assistance CAS
> 与原 `tool_call_id`/continuation 去重保证 exactly-once effect；(2) 3R.2 确定性
> `HumanRequirementDetector`，orchestrator 接受 LLM `done` 前先校验，验证码/登录/
> MFA/扫码/文件选择器命中强制转人工，LLM `done` 或文字"请人工登录"不能覆盖；
> (3) 3R.3 `human_control._RUNTIME_LOCK` 与 `view_hub` 锁惰性化，completion task
> 跨循环归属检查，main.py 启动能力探针 `probe_phase3_primitives`，新增 browser
> 测试 conftest 隔离 fixture。本机 browser 单测 194 通过（2 个真实 Playwright
> `local_worker` 用例因本机无 Chromium 失败，属环境问题非回归），Phase 3 定向
> 32 通过、新增 detector 9 通过、PG 队列 10 通过，正反顺序各跑一次均 51 通过
> 无跨事件循环残留，`src.main` import 通过。真实门禁（无 Stream Redis + 真实 PG
> + 两 Gunicorn worker + 真实验证码页）待部署后按 §3R.4 补验；本机 docker 不可用，
> 真实门禁补验通过前 Phase 3R 仍不标完成、Phase 4 保持阻塞。

### 后端

- 新增 `src/tools/browser/view_hub.py`、`human_control.py`、`human_completion_monitor.py`、`agent_resume_coordinator.py`、`resume_store.py`。
- Agent Runtime 新增 `ToolSuspension` 契约：保存 `agent_execution_id/tool_call_id/run_id/assistance_id`，空 tool_call_id 先规范化生成；挂起不写普通 tool result、不标记计划完成、不继续 LLM，也不保持原 HTTP/SSE coroutine。
- 会话有活动 suspension 时阻止第二个 Agent 循环，API 返回 `409 TOOL_WAITING_HUMAN`；前端禁用普通发送，只保留完成/取消，避免覆盖原 tool_call。
- 新增 `src/api/browser_runs.py` 的 view ticket、view WS、take/complete/extend/cancel API；complete 必须服务端重新快照校验并 CAS。
- Local executor 接 `page.screencast`：1280×720、quality 60、5 FPS、latest-frame backpressure。
- executor 上报脱敏 navigation/DOM-structure 变化；实现白名单完成条件、连续两次稳定判定和 `confirm_only`。
- Redis 仅保留 assistance、tool suspension、控制锁和带 seq 的短期 `agent_continuation_events` JSON 窗口；resume job 改由 PostgreSQL `bs_browser_resume_jobs` 持久化，唯一约束与 CAS 确保 exactly-once 业务效果。
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

### Phase 3R：真实环境兼容性与人工接管修复

#### 3R.0 红灯契约

- 增加真实 Redis 能力用例：环境允许不支持 Streams，但 Browser Phase 3 启动和恢复链路不得调用 `XADD/XREAD/XGROUP`，worker 不得形成高频异常循环。
- 增加真实结构快照用例：含用户名、密码和图形验证码的登录页必须创建 `CAPTCHA_REQUIRED` assistance，LLM 返回 `done` 或文字提示“请人工登录”都不能把 run 标记为成功。
- 将 Phase 3 测试文件做整文件、重复和随机顺序运行；先稳定复现并定位跨 event loop 的 registry/task 残留，再转绿。

#### 3R.1 PostgreSQL 恢复队列

- 在 `deploy/init-postgres.sql` 与 `deploy/db_update.sql` 同步新增 `bs_browser_resume_jobs`：`job_id`、tenant/assistance/run 关联、状态、lease owner/期限、attempts、available_at、白名单错误码和时间戳；`assistance_id` 唯一。
- assistance 的 `resume_queued` CAS 与 job 插入必须在同一 PostgreSQL 事务内完成，避免状态已变更但任务丢失。
- coordinator 使用 `FOR UPDATE SKIP LOCKED` 领取 pending 或 lease 过期任务，按短轮询、有限退避和 lease 回收处理 worker 崩溃；业务结果仍由 assistance 唯一约束、原 `tool_call_id` 和 continuation 去重保证只生效一次。
- 删除 resume job 的 Redis Stream 读写和 consumer group 初始化；Redis 继续承担短状态、控制锁与 continuation 事件窗口，不新增升级 Redis 的部署前提。

#### 3R.2 确定性人工需求检测

- 新增 `HumanRequirementDetector`，在导航后首个 snapshot、每步执行后及接受 LLM `done` 前检查结构信号。
- 检测 challenge iframe、captcha 标签/角色、密码 + 验证码组合、MFA/扫码结构、文件选择器和受阻目标；只读取脱敏结构，不读取或记录输入值。
- 命中后分别产生 `CAPTCHA_REQUIRED`、`AUTH_REQUIRED`、`MFA_REQUIRED`、`FILE_PICKER_REQUIRED`；未满足的人工条件禁止转 `SUCCEEDED`。
- 单独出现“登录”链接或普通密码字段不触发误报；人工完成后仍复用既有双采样条件校验，并从同一 page/context、步骤索引和原 tool call 续跑。

#### 3R.3 隔离与可观测性

- runtime registry 只保存当前事件循环拥有的 task；测试 fixture 在每例结束时注销并等待本例 task，关闭的旧 event loop 不再参与 gather/cancel。
- Redis 能力探测只验证实际使用的命令；不兼容时输出一次聚合告警并关闭相关分布式能力，禁止每 worker 每秒重复堆栈。
- 增加 resume queue lag、lease reclaim、resume result、human-requirement reason 指标；日志只记录 ID、状态和白名单原因码。

#### 3R.4 部署与验收

1. 先执行两份 DDL，再滚动重启 API worker；确认旧 worker 清空且无活动 assistance 后切换 coordinator。
2. 在不支持 Streams 的现有 Redis、真实 PostgreSQL 和两个 Gunicorn worker 下验证领取、并发双击、worker 崩溃、lease 回收与只恢复一次。
3. 在真实验证码登录页验证：自动创建 assistance → 人工登录 → 同一 page/context 交还 → 原 `tool_call_id` 完成 → 原 Agent 无新用户消息继续回复。
4. 重跑 browser 全量、Phase 3 整文件重复/随机顺序、Agent 相邻测试、前端组件/build、六终态进程回收和 `src.main` import。
5. 回滚只回滚应用代码，不删除新增表；切回前必须先处理所有 pending/processing assistance，避免悬挂任务。

Phase 3R 的退出门禁是：现有 Redis 无 Stream 错误、验证码场景稳定转人工、恢复任务跨 worker 只生效一次、测试无跨 event loop 残留、真实 E2E 保留证据。全部满足后才允许 Phase 4 进入开发。

Phase 3R 实施按项目三智能体流程串行执行：开发与自测 → 独立测试及启动安全检查 → 独立 Code Review；全部通过后由主控再次执行关键 import/测试终检。任何一步失败都回到修复阶段，不以缩小断言、跳过真实 Redis/PostgreSQL 用例或只跑单个测试代替门禁。

## 7. Phase 4：Agent Desktop 项目内集成 browser runtime

本 Phase 的客户端工作归属 [Agent Desktop 开发计划 Phase 4](../../system/desktop-agent-client-dev-plan.md)。浏览器计划不创建客户端工程、安装包、更新器、托盘或独立发布流程，只维护服务端协议、RemoteExecutor 和联合验收依赖。

### Phase 4A：浏览器侧协议与服务端适配

- 新增 `browser_runtime_protocol.md`，逐字段锁定 `browser/1.0` 消息、帧和错误码；该协议仅连接服务端与 Agent Desktop 内置 runtime。
- 服务端新增 `desktop_runtime_registry.py`、`executor/desktop.py`、短期 desktop browser session、Agent Web launch ticket 和 `/api/v1/browser/desktop/ws`。
- 非破坏迁移 Phase 2 预留命名：状态 `ESCALATING_CLIENT/WAITING_CLIENT` 改为 `ESCALATING_DESKTOP/WAITING_DESKTOP`，`execution_target='client'` 改为 `desktop_runtime`，`executor_client_id` 改为 `executor_installation_id`；同步代码、双份 DDL 和历史数据兼容读取，确认迁移完成后再删除旧值兼容。
- 协议实现完整消息分片、1 MB 上限、二进制帧、取消、顺序发送锁、心跳和退避；不混入聊天 SSE，不复用企业微信 RPA 协议。
- `DesktopRuntimeExecutor` 与 Local executor 运行同一 contract suite；服务端只信任认证后绑定的 `tenant_id + user_id + installation_id`。

### Phase 4B：Agent Desktop 项目实现

- 在 `clients/agent-desktop/browser-runtime/` 实现可选模块，代码、测试和发布状态登记在 Agent Desktop 计划中。
- Agent Desktop main 延迟加载 runtime；每个 run 启动独立 Node Playwright Worker，Windows 使用 Job Object，macOS 使用独立 process group。
- runtime 使用独立 feature flag 和故障边界；关闭、缺失、启动失败、更新中或协议不兼容不得影响主应用启动、登录、对话、文件和其他工具。
- 复用 Agent Desktop 的 `CredentialStore`、API 基址、installation identity、签名安装包、更新器、托盘和设置页；不得新增浏览器专用长期凭据或配置入口。
- 复用既有 `aidagent://browser-launch` 处理 Agent Web 跨应用拉起；Agent Desktop renderer 发起任务时直接启用 runtime，不走深链。
- 实现专用 Profile、单 run 命令队列、幂等 command_id、frame publisher、断线 watchdog 和终态关闭；Playwright 不进入 Vue renderer。

### 联合验证

- browser runtime unit：短期 session、launch ticket 单次消费、协议分片、乱序/重复、watchdog、Profile 互斥、close 异常。
- Python/TypeScript golden fixtures 和 local/desktop executor contract tests 通过。
- Windows 10/11 x64、macOS 13+ Intel/Apple Silicon：Agent Desktop 安装、登录、runtime 启用、可见浏览器、Profile 复用、验证码人工操作、断网关闭、更新重启不重放命令。
- Agent Web 未安装 Agent Desktop、Desktop 离线、runtime 禁用、版本过低、拒绝打开五种状态有明确引导；server headless 和网页人工接管始终可用。
- 静态边界：browser runtime 不进入 Agent renderer 依赖图；与企业微信 RPA 工程互不引用；不产生第二套桌面 shell、托盘、更新器或安装产物。
- Agent 回归：runtime 禁用、Worker 启动失败、协议版本不兼容、执行中崩溃四种情况均不影响非浏览器功能。

## 8. Phase 5：路由与安全

### Phase 5A：服务端规则与安全（可在 Agent Desktop runtime 完成前开发）

- Agent 工具只暴露 `execution_target=auto|server`；`desktop_runtime` 只由升级状态机内部使用，管理员诊断强制入口独立 RBAC/审计。
- 新增 `src/tools/browser/headless_failure_detector.py`、`challenge_signatures.py`、`escalation_gate.py`；executor 上报脱敏 response/requestfailed/pageerror/console/DOM 特征 DTO。
- 域名策略配置：`server_allowed/local_capability_required/blocked`，管理员维护，默认 `server_allowed`；禁止用域名配置把反爬页面直接送往桌面执行。
- 实现强/中/弱证据规则和分类矩阵：强信号一个或不同来源中信号两个产生高置信分类，再由分类矩阵决定人工、失败、重试或 `ESCALATE_DESKTOP`；LLM 不参与路由裁决。
- 明确 401/403/404/410/429/5xx、DNS/TLS/timeout 处置；429 永不转 desktop runtime，普通 403/503 没有挑战证据时永不转 desktop runtime，单独 CAPTCHA/MFA/扫码先进入 server Web 人工接管。
- 新增 `url_policy.py`：scheme、DNS、IP、redirect、download 检查。
- 定义 irreversible action 标记；执行后禁止自动换执行器。
- CAPTCHA/MFA/扫码/文件选择器只转人工，不自动破解。
- 容器改非 root 并启用 sandbox；若环境暂不能切换，显式配置例外并告警，不能静默 `--no-sandbox`。

### Phase 5B：Agent Desktop 升级链路（依赖 Phase 4）

- 实现 `ESCALATING_DESKTOP`/`WAITING_DESKTOP` 状态、单次升级限制、server 完整关闭屏障、父子 run 审计和脱敏检查点。
- 通过 `desktop_runtime_registry` 选择当前用户已登录、在线、runtime 启用且协议兼容的 Agent Desktop installation；桌面执行失败不回切 server。
- 对外状态统一为 `DESKTOP_INSTALL_REQUIRED`、`DESKTOP_RUNTIME_DISABLED`、`DESKTOP_RUNTIME_OFFLINE`、`DESKTOP_UPDATE_REQUIRED`，复用 Agent Desktop 安装/打开/设置/更新 UI。

### 验证

- 路由决策表 100% 分支覆盖；所有 `auto` 非预检能力失败用例都先创建 server run。
- 新增本地 fixture 页和集成用例：普通/挑战型 403、普通/挑战型 503、429 + Retry-After、404、5xx、CAPTCHA iframe、明确 headless unsupported、人工交还后挑战持续、重定向循环、空白页、DNS/TLS/timeout、本地网络/客户端证书能力。
- 验证“一个强信号或两个独立中信号”只决定高置信分类，处置仍必须查分类矩阵；单独 CAPTCHA 进入 server 人工接管，单独状态码、弱信号、LLM 建议均不能升级。
- 验证升级前 server 进程归零；checkpoint 无 query/cookie/storage/header/DOM/表单值；不可逆状态已提交或未知时拒绝升级；desktop runtime 失败不回切。
- SSRF：IPv4/IPv6、十进制/混合地址、DNS 重绑定、redirect、metadata。
- 恶意 runtime 伪造 tenant/user/installation/run/seq/command_id 全部拒绝。
- 不可逆操作后 Agent Desktop/runtime 掉线不重试、不迁移。

## 9. Phase 6：生产化

### 改动

- Prometheus 指标和告警：close failure、orphan、desktop runtime offline、command timeout、capacity、headless 分类、desktop 升级/拒绝/结果、human assistance 结果、resume job 结果和恢复延迟。
- 每实例 4、每租户 2、每 Agent Desktop runtime 2 的信号量；排队 30 秒。
- 调试 trace/screenshot 默认关闭；开启时用租户 temp + 加密 + 24h 清理，并更新 `docs/system/file_usage.md`。
- Dashboard 展示聚合状态，不展示页面正文/截图。
- 增加故障注入开关，仅测试环境可用。

### 验证

- 4 个 server run 持续 30 分钟，CPU/内存回到基线，无 orphan。
- desktop runtime/server 各 2 个并发，第三个按配置排队/失败。
- kill API worker、kill Chromium、断 Redis、退出 Agent Desktop、禁用 runtime、更新重启、断桌面网络、慢 WebSocket 消费者故障注入。

## 10. Phase 7：迁移与上线

### 灰度顺序

1. `browser.runtime.enabled=false` 部署数据库/Redis/新 API，不切流量。
2. 开启本地生命周期和 server executor，5% browser run 灰度。
3. 开启只读实时视图，观察带宽和 close 指标。
4. 开启 server 人工接管。
5. 通过 Agent Desktop 统一签名安装包向 Windows/macOS 测试设备交付 browser runtime，默认关闭，按 tenant + installation allowlist 灰度 `DesktopRuntimeExecutor`。
6. 通过真机验收后逐租户开放 `auto`；保留“关闭 desktop runtime 升级”和独立 RBAC 管理员诊断入口，不提供 Agent 强制 desktop runtime 开关。
7. 旧全局 session 实现停止注册；观察一个发布周期后删除兼容代码。

### 回滚

- 任何阶段可关闭 `browser.runtime.enabled` 回到旧 `browser_automation`，但 Phase 1 的可靠关闭和默认 headless 不回滚。
- desktop runtime 路径使用独立 feature flag；关闭后 `auto` 只选择 server，不回滚或卸载 Agent Desktop。
- DB 只新增表，不做破坏性迁移；回滚不删表。

### 最终验证

- Python browser unit/integration/e2e。
- Agent Desktop browser runtime Node/TypeScript 单元、集成测试，并复用 Agent Desktop Windows/macOS Release build/publish 门禁。
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
