# Runner 执行槽隔离与运行告警开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 废弃 `browser_automation` 工具入口 | ✅ 完成（2026-11-06） | 目录/指引零残留，16 个 real_browser 套件显式下线+兜底用例；三轮评审通过；资源盘点结论见 Phase 1 完成记录 |
| Phase 2 | 单并发 Worker 副本、独立租约监督与可扩槽部署 | 📋 待开发 | — |
| Phase 3 | 执行计时、运行告警、平台飞书目标与 Portal 页面 | 📋 待开发 | — |
| Phase 4 | agent2 容量测试、旧客户端兼容及发布检查 | 📋 待开发 | agent2 部署与真机测试需用户当场明确指示 |

> 日期：2026-11-05；2026-11-06 按两轮独立架构评审修订；状态：待整体评审，尚未开发、提交或部署。设计依据：[AgentRunner 架构 §10](../system/agent-application-architecture-design.md#10-runner-执行槽隔离与运行告警2026-11-05待开发)；事故证据：[Excel 阻塞复盘](../incidents/excel-process-shared-runner-blocking-20261105.md)。Excel 读取算法修复由另一任务负责。

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
- 增加仅供收尾回收判断的共享槽位状态：Worker 在执行前写 `BUSY(runner_id, attempt)`，本次 `execute()` 连同 Trace、清理全部返回后、下一次领取前写 `IDLE`。监督进程只在数据库显示同一 attempt 已提交结束状态而共享状态仍是对应 `BUSY` 时开始两分钟计时；`IDLE` 或新 attempt 的 `BUSY` 立即取消旧计时。共享状态更新和超时回收的最后身份核对使用同一跨进程锁，核对通过才结束旧进程；确认退出后以新 `worker_id` 启动新进程。不能只凭数据库终态杀进程。执行进程异常退出时立即停止补续租；旧 Runner 沿现有租约过期、中断和外部效果核验处理，不自动重放。监督进程自身退出不能留下无人续租但仍执行的孤儿 Worker。
- 保持 Worker 先提交业务终态/等待状态，再在 `finally` 写 Trace、清理资源的原顺序，计费仍由原 finalizer 按用量记录结算。只有 Phase 1 已核对无跨任务保留资源，才启用两分钟收尾超时回收；不能证明资源归属时标记槽位不可用并告警，不以回收冒险中断其它业务。收尾超时可造成 Trace 或清理不完整，已提交的状态和费用不回退；仍在执行的长工具不因运行时长被终止。未来若引入会在等待期间保留进程内对象的工具，须先复核回收条件。Trace 数据库操作本身设置合理超时并记录失败，不改变原采集和计费路径。
- 调整 Compose 与环境脚本中的固定容器名、单实例假设及健康检查，使多个独立副本可按配置启动；每副本 CPU/内存限额与总数由 Phase 4 实测定值。保留 Runner API/渠道/Runtime 的现有协议。
- 定向验证一个同步阻塞工具超过 30 秒只占一个 Worker，另一 Worker 自行接单、续租并完成；阻塞 attempt 不被误接管；领取提交后监督进程可从数据库发现 attempt、旧 `worker_id` 无法补租、异常退出及恢复核验；同一 Worker 顺序处理不同租户 Runner；业务终态已提交而 Trace/清理卡住时 2 分钟后只回收仍为对应 `BUSY` 的进程，新任务已开始或 Worker 已空闲时不误杀，状态和费用不回退；正常 Trace、等待、暂停/取消、休眠唤醒及旧 attempt 隔离不回退。

## Phase 3：只读告警与 Portal

- 在各类领取事务中登记本次执行起点，覆盖普通排队、控制恢复、定时唤醒和持续任务；不从可变 `updated_at` 推算。登记平台默认/按 `(tenant_id, profile_id)` 覆盖的阈值、`agent_runner_alerts` 及单行 `agent_runner_monitor_status`。按系统表规范同时更新 `deploy/init-postgres.sql`、`deploy/db_update.yaml` 和系统表说明；告警唯一键按 `runner_id + attempt` 防重复。
- 用同一镜像部署单进程的独立 `runner-monitor` 容器，每分钟仅读当前仍在执行的 Runner 及未结束告警的少量字段。给数据库操作有界超时，进程退出由容器重启策略拉起；将最近成功扫描时间写入平台监控状态，超过 3 分钟未成功扫描时 Portal 显示停滞，容器 healthcheck 可报告 `unhealthy`，但不能把此状态当作 Docker 会自动重启的依据。监控不实时调用 Worker、不读取全量 checkpoint、不修改 Runner 状态。
- 对每个候选，在写入事务中锁定并重读 Runner，核对 `runner_id + attempt`、领取起点、当时阈值及仍为 `running`/`finalizing`，只在数据库时间已达到阈值时原子写入告警与通知事件。Runner 提交结束状态后结束告警；之后的 Trace/清理不计入长时间执行告警。新 attempt 重新计时；两次扫描间越过阈值又完成的执行允许没有告警，不补写虚假的当前运行告警。
- 增加平台事件 `runner.long_running` 和专用发布／展开／发送准入：`producer=runner-monitor`、`event_key=runner_id:attempt`、`scope_key=global`、`tenant_id=NULL`、`recipient_user_id=NULL`、`policy_key=platform_runner_alert`。复用事件级唯一索引，事务内重复扫描取回同一 `notification_ref`。平台目标使用现有 `notification_targets` 的全局 scope，固定 `installation_ref=runner_alert_platform`，只允许平台管理员在平台上下文管理；展开至多一个 `feishu_bot` 外发目标，发送前再次核对平台目标，绝不进入租户规则、租户目标或个人偏好。不得借用任一租户 ID 调用现有租户事件接口。
- 复用现有飞书适配器、凭据加密及通知投递程序。未配置平台目标时保留事件，展开状态记为 `expanded`、快照记 `platform_target_missing`，不生成投递，后续配置不追发旧事件；目标更换、撤销或失效时保留投递 `skipped`/`failed` 及原因，Portal 告警不消失。飞书仅含 Runner ID、租户标识、数字员工、执行时长及 Portal 详情链接；原问题、用户和模型上下文不进入群消息。
- Portal 增加独立 **Runner 告警** 页面，查看当前/历史及监控最近成功扫描时间。详情按 Runner ID 读取原问题及已保存的最近模型返回和上下文，按现有平台管理员权限与脱敏规范开放；缺数据明确显示，不推断执行步骤。本期不建设平台运行工作台。
- 定向验证阈值调整对在执行任务下次扫描生效、完成/持久休眠时告警结束而本次 Trace/清理未返回前仍不接新任务、扫描与完成并发不生成错误的当前运行告警、新 attempt 重新计时、同次执行只建一次告警及一条逻辑通知事件、事务中断后重复扫描可重新写入两者、飞书重试不重复建告警、平台目标与租户目标严格隔离、目标失效仍保留告警及投递原因、监控进程停滞在 Portal 可见且不影响 `runner-api`。

## Phase 4：容量与交付验证

- 经用户明确授权部署 agent2 后，逐级测试槽数和每槽 CPU/内存上限，记录容器首次启动、连续排队与顺序复用时的吞吐、排队时间、API/DB/模型负载、常驻进程内存变化、资源峰值及告警准确性；正式服按同机资源余量选择配置，不直接按内存倍数换算。
- 覆盖“只升级服务端、客户机保持已交付旧版本”的受影响组合，核对原工具、恢复、结果回报和计费。部署脚本及回退检查随多槽拓扑更新，不能遗漏其它环境的固定容器名假设。
- 代码开发按项目非平凡任务流程完成独立测试、CodeReview 和主控发布前检查；每阶段实际完成时先更新本计划进度，再同步 `docs/ideas.md`。提交与任何环境部署分别等待用户明确指示。
