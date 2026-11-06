# Runner 执行槽隔离与运行告警开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 废弃 `browser_automation` 工具入口 | ✅ 完成（2026-11-06） | 目录/指引零残留，16 个 real_browser 套件显式下线+兜底用例；三轮评审通过；资源盘点结论见 Phase 1 完成记录 |
| Phase 2 | 单并发 Worker 副本、独立租约监督与可扩槽部署 | ✅ 完成（2026-11-06） | 独立测试 92 通过；CR 无未解决 P0/P1/P2；agent3 双槽与空闲故障回收已验 |
| Phase 3 | 执行计时、运行告警、平台飞书目标与 Portal 页面 | ✅ 完成（2026-11-06） | 独立测试 Runner 76、通知 64、前端 2 通过，构建通过；agent3 monitor 健康 |
| Phase 4 | agent3 容量测试、旧客户端兼容及发布检查 | 🔧 进行中 | agent3 三档 8/8 真实任务完成结算；同步阻塞、告警、旧包及飞书仍待验 |

> 日期：2026-11-05；2026-11-06 按两轮独立架构评审修订；Phase 1～3 已完成开发，分支 `codex/runner-slot-phase2` 已提交并按用户指示部署到 agent3。Phase 4 的真实业务容量、旧客户端交付组合和飞书投递仍待验；正式环境未部署。设计依据：[AgentRunner 架构 §10](../system/agent-application-architecture-design.md#10-runner-执行槽隔离与运行告警2026-11-05)；事故证据：[Excel 阻塞复盘](../incidents/excel-process-shared-runner-blocking-20261105.md)。Excel 读取算法修复由另一任务负责。

## 成功标准

1. 一个 Worker 在同步工具里持续运行时，其他空槽仍能领取、续租和完成其它 Runner；该 Worker 的监督进程仍可为其合法 attempt 补续租，不能因现有 30 秒租约误判合法长计算。每槽一个常驻、单并发 Worker，仍由 Worker 自行领取和核验恢复资格；本次执行连同原有 Trace、清理返回后才顺序复用进程。槽数与 CPU/内存上限可配置。
2. 原有 checkpoint、会话占有、暂停/取消、持久休眠、恢复核验、结果上报和计费语义维持；执行进程异常退出不自动重放未知外部效果。已交付 Runtime/CLI 不因服务端升级失去原有能力。
3. 从本次领取事务记录的起点起，业务执行达到平台默认 30 分钟或该租户数字员工覆盖阈值、且下一次 1 分钟扫描写入时仍在执行，监控以同一事务写入唯一告警及一条逻辑通知事件；两次扫描间越过阈值又结束的执行可能没有告警。Runner 提交完成或持久等待/休眠等本次执行结束状态时关闭告警。最多 2 分钟的本次收尾另由监督进程按精确忙碌身份处理。飞书投递结果不明后重试可能产生少量群消息重复。正常长任务只告警不被终止。Portal 能看当前/历史及最近已保存的模型返回，飞书发送失败不抹掉 Portal 告警。
4. `browser_automation` 不再提供给新任务，标记废弃；其它浏览器、Runtime/CLI 能力不因本项被连带删除。

## Phase 1：浏览器工具目录收敛

- 从工具注册、发现及新任务可见清单移除 `browser_automation`，在相关文档标明废弃。该工具是当前 `create_browser_tools()` 提供的唯一服务端浏览器工具；移除后新任务不再获得这项能力，这是已确认的产品决定。用户确认该工具无历史使用，不增加存量迁移；不清理其它浏览器流程或已交付 Runtime/CLI 工具。
- 清点 `src/skills/` 中引用该工具的运行时指引，更新或停用无法继续运行的指引；当前已知 `competitor-research-1.0.0/SKILL.md` 仍会提示调用它。核对 Worker 是否还有其它跨任务保留的进程内资源，作为 Phase 2 收尾超时回收的前置条件。
- 验证新任务工具目录及运行时指引均不再暴露该名称，已交付 Runtime/CLI 浏览器能力仍可用。完成本阶段及资源归属核对后才能启用 Phase 2 的业务结束后进程回收。

### Phase 1 完成记录（2026-11-06，待评审）

改动：`BrowserAutomationTool` 设 `catalog=False` 退出自动发现（`src/tools/browser/automation_tool.py`）；删除包工厂 `create_browser_tools()`（`src/tools/browser/__init__.py`，类保留供恢复链路与生命周期测试）；黄金清单测试同步（`tests/unit/tools/test_tool_discovery.py`，仿 attraction_search/hotel_search 软下线先例）；竞品研究指引改用 `web_fetch`（`src/skills/competitor-research-1.0.0/SKILL.md`、`subagents/competitor-research/SUBAGENT.md`）；废弃标注（工具设计文档、工具 effect 清单、竞品子代理设计文档）。

清点结论（超出计划点名范围的运行时指引）：引用 `browser_automation` 的运行时指引仅竞品研究一处（SKILL.md + SUBAGENT.md，同一流程两份文本）；两份文本中原有 `browser_get_content` 指引早已不可运行（旧版浏览器工具长期 `catalog=False`），本次一并改为 `web_fetch`。

Worker 跨任务进程内资源盘点（Phase 2 收尾超时回收前置核对，结论：可启用）：

1. `browser_process['retained_managers']`（`browser_owner.py:93`）：BrowserRunManager 集合，唯一新增入口是 `browser_automation` 执行绑定 run；正常关闭以 PG 与资源一致为条件移除（`browser_owner.py:152`），Worker 退出时 `close_worker_browser_managers(boot)` 全量关闭（`worker.py:83`）。本阶段后无新增入口，且确认无历史使用，不存在存量。
2. `browser_process['human_commands']`：人工接管完成监视 asyncio 任务，仅该工具执行路径产生，`factory.close()` 等待/取消（`worker.py:76-81`）。同上无新增入口。
3. `browser_process['sidecar']`：浏览器视图监听器，仅 `browser_owner.view_enabled` 时随 `factory.open()` 常驻，与单次业务执行无归属关系；Phase 2 回收实现须将其视为进程级资源、不参与每任务清理。
4. 进程级配置型缓存（`runtime/resource_cache.py`）：仅不可变/签名校验的 skill 与子代理注册表，属既有 M0 隔离契约内的合法共享，不涉及外部句柄。

验证：`tests/unit/tools/test_tool_discovery.py` 17 通过、`test_execution_lifecycle.py`+`test_tool_execution_cleanup.py` 41 通过；装配后 STANDALONE 目录无任何 `browser*` 工具、`web_fetch` 在册、本地代理工具（Runtime/CLI 侧 `boss_*`/`wecom_*`）未受影响；恢复链路模块 import 完整。独立测试与独立 CodeReview 复核均无 P0/P1。

评审修正（2026-11-06 第二轮，P1+P2）：

1. P1 指引去名：`SKILL.md` 与 `SUBAGENT.md` 中连"已下线说明"也一并移出，两份运行时文本现对已下线工具名零提及（`src/skills/`、`subagents/` grep 为 0）；废弃记录只保留在设计文档。
2. P2 套件下线：16 个以 mock 回复 `browser_automation` 经真实 Worker 驱动的 `real_browser` 集成套件（`test_browser_continuation.py`、`test_worker_browser_authority.py` 等）在本阶段显式 skip（模块级 `pytest.mark.skip`，理由注明本计划；`pytest -m real_browser` 显示 skipped，不再出现按标记运行即失败的套件），Phase 2 按 Worker 新拓扑处置或重建。保留不受影响的用例（逐文件核对调用方式后归类）：直连实例化 `BrowserAutomationTool` 的生命周期测试（`test_execution_lifecycle.py`、helper `browser_observation_legacy_api.py`）；仅以工具名作 checkpoint 数据、不经注册表的 PG 存储契约用例（`test_browser_binding_storage.py`）；直接构造 `BrowserRunManager` 验证资源生命周期的 `test_browser_manager_lifetime.py`（关闭失败资源不被 GC 丢弃、重试关闭后释放——`retained_managers` 保留规则的行为基线，Phase 2 收尾超时回收依赖该语义）。
3. P2 新增聚焦用例（`test_tool_discovery.py::TestRetiredBrowserToolNotDispatchable`）：全量装配后的新任务即使收到未声明的 `browser_automation` 调用，也只返回"工具不存在"软错误，且 `_ACTIVE_MANAGERS` 零增长（不创建浏览器运行资源）。

修正后验证：黄金清单（含新用例）18 通过；抽测 `test_worker_browser_authority.py -m real_browser` → 2 skipped；上述 grep 与装配断言复核通过。第三轮评审修正：`test_browser_manager_lifetime.py` 初判误加 skip（实际直接构造 manager、不经 Worker 工具目录），已移除 skip 保留 `real_browser` 标记；其余 16 个 skip 套件逐一复核均为 `tool_reply('browser_automation')` Worker 派发、零直接构造，skip 范围正确。`test_browser_manager_lifetime.py` 真实执行需 PG/Redis helper/Chromium 环境（与改动前相同门槛），本环境未运行，收集验证通过。

## Phase 2：执行槽隔离

- 将现有 `runner-worker` 调整为多个独立副本，每副本根 Runner 并发数为 1。Worker 自行执行 `_acquire_next()`、普通及恢复领取核验、浏览器 owner、Engine/工具、业务提交、计费和 Trace；同一进程顺序复用，不做每任务进程重建。不同 Runner 的上下文继续按现有方式逐次创建和清理。
- 每副本另设一个轻量监督进程。它启动 Worker 时给出本次唯一 `worker_id`，检查执行进程存活，仅查询数据库中仍归属于 `runner_id + attempt + worker_id` 的合法执行并补续租。Worker 领取事务提交后，监督进程即可查到 attempt，无需额外领取通知；每次补租仍核对数据库归属。Worker 原有心跳及暂停/取消读取、安全点处理保留；监督进程不领取任务、不迁移恢复核验、不把请求送达当成暂停/取消完成。父子不传活对象或完整上下文。
- 增加仅供收尾回收判断的共享槽位状态：Worker 在执行前写 `BUSY(runner_id, attempt)`，本次 `execute()` 连同 Trace、清理全部返回后、下一次领取前写 `IDLE`。监督进程只在数据库显示同一 attempt 已提交结束状态而共享状态仍是对应 `BUSY` 时开始两分钟计时；`IDLE` 或新 attempt 的 `BUSY` 立即取消旧计时。共享状态更新和超时回收的最后身份核对使用同一跨进程锁，核对通过才结束旧进程；确认退出后监督进程退出本槽容器，由容器重启清理所有旧子进程并以新 `worker_id` 建槽，不在同一容器内直接复用旧进程命名空间。不能只凭数据库终态杀进程。执行进程异常退出时立即停止补续租并退出容器；旧 Runner 沿现有租约过期、中断和外部效果核验处理，不自动重放。监督进程作为 Linux 容器主进程，其自身退出不能留下无人续租但仍执行的孤儿工具。
- 保持 Worker 先提交业务终态/等待状态，再在 `finally` 写 Trace、清理资源的原顺序，计费仍由原 finalizer 按用量记录结算。只有 Phase 1 已核对无跨任务保留资源，才启用两分钟收尾超时回收；不能证明资源归属时标记槽位不可用并告警，不以回收冒险中断其它业务。收尾超时可造成 Trace 或清理不完整，已提交的状态和费用不回退；仍在执行的长工具不因运行时长被终止。未来若引入会在等待期间保留进程内对象的工具，须先复核回收条件。Trace 数据库操作本身设置合理超时并记录失败，不改变原采集和计费路径。
- 调整 Compose 与环境脚本中的固定容器名、单实例假设及健康检查，使多个独立副本可按配置启动；每副本 CPU/内存限额与总数由 Phase 4 实测定值。保留 Runner API/渠道/Runtime 的现有协议。
- 定向验证一个同步阻塞工具超过 30 秒只占一个 Worker，另一 Worker 自行接单、续租并完成；阻塞 attempt 不被误接管；领取提交后监督进程可从数据库发现 attempt、旧 `worker_id` 无法补租、异常退出及恢复核验；同一 Worker 顺序处理不同租户 Runner；业务终态已提交而 Trace/清理卡住时 2 分钟后只回收仍为对应 `BUSY` 的进程，新任务已开始或 Worker 已空闲时不误杀，状态和费用不回退；正常 Trace、等待、暂停/取消、休眠唤醒及旧 attempt 隔离不回退。

### Phase 2 完成记录（2026-11-06，本地开发，待逐阶段评审）

- Worker 根 Runner 并发固定为 1；独立监督进程用独立 1–2 连接数据库池按 `worker_id + runner_id + attempt` 发现并补续租，原 Worker 自行领取、恢复核验、暂停/取消安全点、业务提交、计费与 Trace 路径保留。`BUSY/IDLE` 由跨进程文件锁保护；仅提交了本次业务结束且同一 `BUSY` 连续两分钟时回收。正常停止先向 Worker 发信号，给原取消/清理路径 30 秒。
- `skill_execute` 可启动独立进程组；Worker 异常退出或收尾超时后，监督进程不在同一容器内启动新 Worker，而是以非零状态退出，由容器进程命名空间清理旧工具后按 restart policy 建立新 `worker_id`。Linux 入口要求监督进程为 PID 1。三个环境 overlay 的 Worker 停止宽限为 75 秒；仿真使用 `on-failure` 保持按需停机且可异常重启。
- Compose 的 `deploy.replicas` 按 `AGENT_RUNNER_WORKER_SLOTS` 配置，移除固定 Worker 容器名；每副本 CPU/内存可配置。默认内存保留原 overlay 上限：生产 6G、诺度 3G、仿真 4G；agent2/agent3 更新脚本维持原实际 1G 限额。容量和数据库连接总量仍须在 Phase 4 按目标环境实测，尤其诺度 PostgreSQL `max_connections=40`。
- 开发者定向自测 32 通过；独立测试在最终核心状态运行相关 Runner 用例 92 通过、0 跳过；独立 CodeReview 无未解决 P0/P1/P2。`git diff --check`、Python/YAML 解析、Shell 语法及生产/test/dev/仿真/诺度 Compose 只读配置校验通过。另有未改动的 `test_durable_sleep_boundaries.py` 旧 fixture 6 例因缺 `RuntimeExecution.catalog` 失败，不计为本阶段通过。
- 本地交接时尚未部署；后续 agent3 已验证 Linux 容器重启清理独立进程组。PostgreSQL 双槽业务阻塞与租约竞争、旧 Runtime/CLI 二进制组合和容量压测仍待验。

## Phase 3：只读告警与 Portal

- 在各类领取事务中登记本次执行起点，覆盖普通排队、控制恢复、定时唤醒和持续任务；不从可变 `updated_at` 推算。登记平台默认/按 `(tenant_id, profile_id)` 覆盖的阈值、`agent_runner_alerts` 及单行 `agent_runner_monitor_status`。按系统表规范同时更新 `deploy/init-postgres.sql`、`deploy/db_update.yaml` 和系统表说明；告警唯一键按 `runner_id + attempt` 防重复。
- 用同一镜像部署单进程的独立 `runner-monitor` 容器，每分钟仅读当前仍在执行的 Runner 及未结束告警的少量字段。给数据库操作有界超时，进程退出由容器重启策略拉起；将最近成功扫描时间写入平台监控状态，超过 3 分钟未成功扫描时 Portal 显示停滞，容器 healthcheck 可报告 `unhealthy`，但不能把此状态当作 Docker 会自动重启的依据。监控不实时调用 Worker、不读取全量 checkpoint、不修改 Runner 状态。
- 对每个候选，在写入事务中锁定并重读 Runner，核对 `runner_id + attempt`、领取起点、当时阈值及仍为 `running`/`finalizing`，只在数据库时间已达到阈值时原子写入告警与通知事件。Runner 提交结束状态后结束告警；之后的 Trace/清理不计入长时间执行告警。新 attempt 重新计时；两次扫描间越过阈值又完成的执行允许没有告警，不补写虚假的当前运行告警。
- 增加平台事件 `runner.long_running` 和专用发布／展开／发送准入：`producer=runner-monitor`、`event_key=runner_id:attempt`、`scope_key=global`、`tenant_id=NULL`、`recipient_user_id=NULL`、`policy_key=platform_runner_alert`。复用事件级唯一索引，事务内重复扫描取回同一 `notification_ref`。平台目标使用现有 `notification_targets` 的全局 scope，固定 `installation_ref=runner_alert_platform`，只允许平台管理员在平台上下文管理；展开至多一个 `feishu_bot` 外发目标，发送前再次核对平台目标，绝不进入租户规则、租户目标或个人偏好。不得借用任一租户 ID 调用现有租户事件接口。
- 复用现有飞书适配器、凭据加密及通知投递程序。未配置平台目标时保留事件，展开状态记为 `expanded`、快照记 `platform_target_missing`，不生成投递，后续配置不追发旧事件；目标更换、撤销或失效时保留投递 `skipped`/`failed` 及原因，Portal 告警不消失。飞书仅含 Runner ID、租户标识、数字员工、执行时长及 Portal 详情链接；原问题、用户和模型上下文不进入群消息。
- Portal 增加独立 **Runner 告警** 页面，查看当前/历史及监控最近成功扫描时间。详情按 Runner ID 读取原问题及已保存的最近模型返回和上下文，按现有平台管理员权限与脱敏规范开放；缺数据明确显示，不推断执行步骤。本期不建设平台运行工作台。
- 定向验证阈值调整对在执行任务下次扫描生效、完成/持久休眠时告警结束而本次 Trace/清理未返回前仍不接新任务、扫描与完成并发不生成错误的当前运行告警、新 attempt 重新计时、同次执行只建一次告警及一条逻辑通知事件、事务中断后重复扫描可重新写入两者、飞书重试不重复建告警、平台目标与租户目标严格隔离、目标失效仍保留告警及投递原因、监控进程停滞在 Portal 可见且不影响 `runner-api`。

### Phase 3 完成记录（2026-11-06，本地开发，待逐阶段评审）

- 各领取路径记录 `execution_started_at`，业务结束或旧 `finalizing` attempt 被重领时在同一事务关闭告警。监控按本次起点、当前阈值和数据库状态复核，原子写入唯一告警与平台通知事件；独立 `runner-monitor` 定期扫描并报告最近成功扫描时间。主库和诺度新库初始化 SQL、增量迁移及系统表说明已同步。
- 平台通知使用独立全局发布、展开、发送准入及固定飞书目标；未配置或失效目标保留告警和投递状态。缺少 `PUBLIC_BASE_URL` 时不发送无详情链接的飞书消息。Portal 提供当前/历史、监控状态、阈值和平台飞书目标管理；详情原问题作为有权管理员可见的原始输入，模型返回与上下文仅为既有 `mask_tool_args` 脱敏摘录，普通长字符串保留前 200 字符。历史 attempt 不显示后续 attempt 的模型事实；自由文本内嵌任意凭据不保证被识别。
- 开发者定向自测 Runner 72、通知 64、前端 2 通过。独立测试在最终代码状态运行 Runner 76、通知 64、前端 API 2 例均通过；前端构建、相关 Python 编译、生产/test/dev/local/仿真/诺度 Compose 只读解析及监控 CLI 帮助检查通过。隔离 TestClient 验证新路由的鉴权与响应；完整 `src.main` 导入在本机被既有 Wanx/Qwen 密钥依赖阻断，使用测试期初始化替身与网络/数据库连接禁用后核对了 8 条路由。独立 CR 修复并复核旧 `finalizing` attempt 告警未关闭的问题后，无遗留 P0/P1/P2。
- 独立测试对开发者全量 notifications 套件的 10 个失败做了窄范围复现：`test_web_push.py::test_real_library_encryption_normalizes_provider_results_without_redirect` 的 8 个参数用例均在第 57 行因本机缺少 `py_vapid` 失败；`test_im_webhook.py::TestRender::test_render_terminal_task_completed` 预期“任务执行完成”，实际为“定时任务执行结果”；`test_rule_engine.py::TestLeadCapturedContract::test_render_template_with_previous_capture` 预期 `a@b.com留资`，实际为 `a@b.com 留资`。独立测试从未提交 Phase 3 改动前的 `HEAD` 提取通知渲染函数，在内存中执行同样输入，确认两处实际输出逐字相同；Web Push 源码和上述三份旧测试相对 `HEAD` 均未改动，本机 `py_vapid` 不可导入。结论为 8 项环境依赖缺失、2 项既有断言失配，未发现 Phase 3 回归；全量套件仍不计为通过。
- 本地交接时尚未执行真实 PostgreSQL 迁移或运行 Docker；后续 agent3 迁移与容器健康检查见 Phase 4 记录。真实事务竞争或回滚、飞书投递、旧 Runtime/CLI 交付组合和容量压测仍待验。

## Phase 4：容量与交付验证

- 按用户本次授权使用 agent3 进行真机验收，逐级测试槽数和每槽 CPU/内存上限，记录容器首次启动、连续排队与顺序复用时的吞吐、排队时间、API/DB/模型负载、各 Worker 与监督进程数据库连接总量、常驻进程内存变化、资源峰值及告警准确性；将独立 runner-monitor 的资源与数据库连接一并计入。agent3 与 agent2 在同一宿主机，但数据库和配置独立；正式服按同机资源余量选择配置，不直接按内存倍数换算。
- Compose 默认仅 1 槽、每 Worker 并发 1；默认配置不能证明“一个槽被同步工具阻塞时另一槽继续完成”的事故目标。须在 agent3 完成真实业务容量与并发验证，再为目标环境显式配置至少 2 槽并复验，才能宣称此项成功标准成立；当前 agent3 双槽拓扑和空闲故障回收通过不等于阻塞业务验收。
- 覆盖“只升级服务端、客户机保持已交付旧版本”的受影响组合，核对原工具、恢复、结果回报和计费。部署脚本及回退检查随多槽拓扑更新，不能遗漏其它环境的固定容器名假设。
- 代码开发按项目非平凡任务流程完成独立测试、CodeReview 和主控发布前检查；每阶段实际完成时先更新本计划进度，再同步 `docs/ideas.md`。提交与任何环境部署分别等待用户明确指示。

### Phase 4 本地准备与真机验收方案（2026-11-06）

本节最初仅做本地/只读准备；用户随后明确授权提交并部署 agent3，实际初验记录见下。默认 `AGENT_RUNNER_WORKER_SLOTS=1` 只可作为单槽基线，不能证明事故目标。目标环境至少配置 2 槽并在真实负载下验证资源余量后，才能判定“长同步工具不阻塞其他 Runner”成立。

| 验收面 | 在 agent3 取得的证据 | 通过条件 |
|---|---|---|
| 迁移与拓扑 | 迁移时间/表列、实际 Compose project 与 2 个独立 Worker 容器、各自 `worker_id`、单个 monitor、主 API 到 runner-api 路由 | 迁移先于 monitor，副本不共用容器名/进程；旧客户端仍可接入 |
| 阻塞隔离与租约 | 同一批中一个受控同步工具占槽超过 30 秒租约，另一个普通 Runner 被第二槽领取；记录 runner/attempt/worker_id、领取和完成时间、lease 与恢复判定 | 第二个 Runner 在第一个尚未结束时完成；第一个保持原 attempt 并正常结算，不被误接管或重放 |
| 顺序复用与终态 | 每槽连续处理不同租户任务；暂停/取消、持久等待/唤醒、收尾 Trace/清理延迟、Worker 异常退出的受控样本 | 租户上下文不串用；业务终态、费用和 Trace 沿原路径；异常退出不自动重放未知外部效果；仅精确旧 `BUSY` 超时回收 |
| 告警与通知 | 将测试 profile 阈值暂设 1 分钟，运行跨阈值样本，记录告警/事件唯一键、结束时间、monitor 最近成功扫描与投递状态；试验后恢复原阈值 | 同 attempt 一条告警/逻辑事件，正常长任务不被终止；结束后历史保留；飞书失败不抹告警，停滞超过 3 分钟在 Portal 可见 |
| 旧客户端 | 取实际已交付 Runtime＋CLI 版本/包，在“只升级服务端”的组合执行原 BOSS 工具发现与调用、原任务恢复、未知结果原回执重投、结果上报与计费核对 | 原能力无需升级即可继续；缺新能力仅影响对应新功能，原调用不被新全局门禁阻断 |

容量分级先记录 agent3 实际 CPU/内存、PostgreSQL `max_connections` 与现有 API/background/日志库连接占用，再在相同任务输入下测单槽基线和双槽；只有双槽留有余量时才考虑更高槽数。每档记录排队时间、完成吞吐、CPU/常驻内存峰值与 OOM、业务库/日志库活跃连接、DB/模型延迟与错误、monitor 扫描延迟和重启次数。业务库连接预算须含每槽 Worker 默认池上限 15、监督进程上限 2、单 monitor 上限 2、runner-api、主 API、background 及运维余量；monitor healthcheck 另有短连接。实际池配置与连接数以目标环境读数为准，不按内存上限直接推槽数。发现 DB 连接耗尽、OOM、错误接管、重复外部效果或原有 API 明显退化时停止升档，保留现场证据并按[部署清单](../../deploy/agent-runner-deploy-checklist.md)的排空/回退顺序处理。容量结论需同时给出槽数、每槽 CPU/内存和同机余量，不能仅以测试通过给正式环境套用数值。

### Phase 4 本地核对记录（2026-11-06，部署前）

- `git diff --quiet HEAD -- clients src/local_tools src/api/agent_runner_web.py src/services/agent_runner/adapter_contract.py src/services/agent_runner/runtime_manifest.py src/services/agent_runner/recovery.py src/services/agent_runner/usage_repository.py src/services/agent_runner/usage_pricing.py src/core/agent_engine` 退出码 0；Device API/CLI 生产消费契约、恢复格式和计费实现未因本阶段改写。Worker 改动仅涉及单并发槽位状态、续租监督及已有执行事务的计时/告警；Phase 3 迁移只加可空 `execution_started_at` 与新表，没有修改旧客户端 wire。此为源码兼容核对，不等于已交付二进制验收。
- 宿主 Python 定向运行 `test_master_upgrade_compatibility.py`、`test_runner_adapter_contract.py`、`test_boss_cli_recovery_ownership.py`、`test_boss_tool_billing.py`：**53 passed、0 failed、14 warnings**。额外纳入旧 `test_agent_local_tool_registration.py` 时共 **56 passed、4 failed**；四项均断言已过时的 master/BOSS/WeCom 工具可见集合，该测试、Agent 装配、工具清单及 recruiting-operator 配置相对 `HEAD` 无改动，`configs/config.yaml` 仅改 Runner 并发与 Browser view 注释，不影响这些断言；不把该组合记为全绿，也不借本任务改动旧测试。
- 通过 WSL Docker Compose 对 agent2 主配置叠加 Runner overlay 做只读解析，临时传入 `AGENT_RUNNER_WORKER_SLOTS=2`、`AGENT_RUNNER_WORKER_MEMORY=1g`：解析结果 `worker.replicas=2`、每副本 `cpus=1`/`mem_limit=1073741824`、无固定 `container_name`，单个 monitor 服务 `mem_limit=268435456`。`bash -n` 检查 agent2/agent3/诺度更新脚本通过；未启动容器。
- 已校正[部署清单](../../deploy/agent-runner-deploy-checklist.md)中 monitor 的数据库写入边界、Browser view 跨容器可达性与端口说明、agent2/agent3 当前拆库事实、默认单槽不能验收及回退记录。当前工作树没有已交付 Runtime＋CLI 的实际发行包；真实旧包组合、PostgreSQL 事务竞争、两槽业务阻塞与容量结论仍待验证。[Excel 事故复盘](../incidents/excel-process-shared-runner-blocking-20261105.md)已由远程 `master` 正式登记；主工作区另有未跟踪长版报告，未在本任务改写或覆盖。

### Phase 4 agent3 部署与初验记录（2026-11-06）

- 用户明确授权提交并部署 agent3。分支 `codex/runner-slot-phase2` 的代码提交 `aebdf8eb` 与合入远程 `master` 的提交 `8508a972` 已推送 Codeup/GitHub；agent3 从原 `82ea04b2` 更新到 `8508a972`，工作区无已跟踪改动。更新前 agent3 `running/finalizing/queued` Runner 数为 0；独立业务库备份 `/var/www/agent3/log/aid_work_agent3_pre_8508a972.dump` 为 246673719 字节、权限 600，`pg_restore -l` 可列出 1822 行。备份未做实际恢复演练。
- 更新脚本先单槽启动并完成前端编译；主 API `/health` 返回 200，Runner API、Worker、monitor 和 background 均健康。业务库迁移记录到 `2026-11-06 11:10:00`，monitor 最近 3 分钟内成功扫描；未登录访问两个新平台接口均返回 401。随后将 agent3 扩至 2 个独立 Worker 容器，分别有独立 `worker_id`，每槽 CPU 限额 1、内存限额 1 GiB；`agent3` 的 `.env` 已保存 `AGENT_RUNNER_WORKER_SLOTS=2`、`AGENT_RUNNER_WORKER_MEMORY=1g` 并设权限 600，Compose 配置解析通过。
- 空闲故障试验两次：槽 1 Worker 子进程被受控终止后，该槽容器分别重启并换新 `worker_id`，槽 2 的子进程未重启；第二次在槽 1 先启动独立进程组的 `sleep`，容器重启后该进程未残留。试验期间无运行或排队 Runner，因此只证明空闲槽位的 Linux 故障清理，不证明业务任务的 checkpoint、费用或外部副作用恢复。
- 双槽空闲观察：宿主内存 7.3 GiB，约 2.2 GiB available；每 Worker 常驻约 87 MiB、monitor 约 42 MiB；agent3 业务库当前连接约 17/80，监控扫描有效。该值不是模型/Excel 混合负载峰值，不能推导正式服容量。agent3 未配置活动的平台飞书目标，真实飞书投递未测；已交付旧 Runtime＋CLI 包也未在此环境取得。服务器 sparse-checkout 不含 `tests/`，容器内定向 pytest 因文件不存在而未运行，不能计为通过。真实同步阻塞超过 30 秒时另一槽完成任务、租约与计费、告警写入/发送及旧客户端组合仍是 Phase 4 未完成项。

### Phase 4 agent3 真实混合负载（2026-11-06）

- 按用户指示编写并独立测试/审查 [`scripts/agent3_runner_load.py`](../../scripts/agent3_runner_load.py)，通过公网 Nginx→主 API→独立 Runner API→双 Worker/业务库走真实 Web 请求。在专用测试租户经正式接口导入 20,000 行无人员字段的合成订单 CSV；历史数据分析问题只提取聚合类别，未复用其他租户原文或数据。完整结果、样本和限制见[实测报告](../research/agent3-runner-end-to-end-load-test-20261106.md)。
- 单条、三并发、四并发共 8/8 任务 `completed + settled`，全为 attempt 1。三并发中普通问答和旅行顾问在长数据分析尚未结束时完成；四并发中两条数据分析由不同 Worker 同时执行。四并发 Worker 采样内存峰值 295.9/283.2 MiB，业务库连接峰值 17/80；无 Worker OOM 或新增重启。4 条分析消耗合计 4,810,924 token、228.95 积分，进一步升档前须评估成本，不能把本轮样本当持续容量定值。
- 原 agent3 `.env` 将 `AGENT_RUNNER_WEB_ENABLED` 显式关为 `false`，本轮临时开启后测试、结束时曾恢复；用户随后要求 Runner 模式常开，现已设回 `true` 并重建主 API，运行时配置与 6 容器健康均复核。主 API 重建时发现 `.env` 原 `600` 权限使容器运行用户无法读取，出现 9 次启动重试；已改为组 `999`、权限 `640`。未完成的精确定向同步阻塞、30 分钟告警/飞书及旧 Runtime＋CLI 组合仍留 Phase 4。
- 槽数按实测资源而非逻辑 CPU 线程数定值：agent3 当前 4 逻辑 CPU、约 7.3 GiB 内存，双槽负载后可用约 1.8 GiB；业务库 `max_connections=80`，单 Worker 默认池上限 15 加监督进程 2。直接设 8 槽的理论连接上限为 136（未计其它服务），8×1 GiB 容器内存上限也超过整机内存；保持 2 槽，下一档只考虑逐级实测 3 槽，不据本轮推导正式环境槽数。

### Phase 4 正式服容量初评（2026-11-06，只读；尚未部署）

- 正式服为 4 逻辑 CPU、约 15.1 GiB 内存，检查时约 9.6 GiB available；仍运行旧的单 `aid-runner-worker`，没有部署本阶段多槽拓扑。业务库 `max_connections=80`、检查时约 20 个连接。近 7 天完成 502 个 Runner，最忙一小时接收 88 个；按接收至结束区间统计，峰值有 13 个未结束任务，**其中包含排队，不代表 13 个同时执行**。这些是一次只读快照及历史量，不能单独推断用户排队分位数或所需槽数。
- agent3 的 2 槽只是 8 GiB 测试机的验证配置，正式服不继承此数。生产候选从 **4 个单并发 Worker 槽**做混合负载与大文件实测；若排队目标仍不满足且资源留有余量，再测 **6 槽**。8 槽只作为进一步扩容候选，不按 CPU 线程数直接设定。建议以业务可接受的高峰排队时长为验收指标，首轮暂以排队 p95 不超过 30 秒作测试目标，产品目标确认后替换。
- 扩槽前须在生产同等配置下核对两个实际约束：一是内存，现有生产 overlay 每槽默认上限 6 GiB，4/6 槽时须按大文件、浏览器和多任务峰值重新设每槽上限，并核对同机 API、background、PostgreSQL 的余量；二是数据库，Worker 默认业务池上限 15 加监督进程最多 2 连接，4 槽理论上限 68，叠加当前约 20 个连接已超过数据库 80 连接上限。先对 Worker 单独试配较小 `DB_POOL_MAX` 并在混合负载下验证连接峰值与错误，再确定 4/6 槽配置；不能仅凭当前低连接数直接升档。模型服务限流、CPU 忙时与原 API 延迟也应纳入同一轮观测。
- 本节是容量建议，不构成正式服部署或验收完成记录；Phase 4 仍须完成同步阻塞隔离、旧客户端组合、告警投递与正式环境容量验证。
