# 定时任务时间窗与配额约束开发计划

设计依据：[定时任务重构设计 §15](../system/scheduled-task-runner-refactor-design.md#15-时间窗最短间隔与用户任务数2026-11-03-待开发扩展)。本计划仅描述实施，尚未开始开发；不修改运行代码、不执行部署。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 领域规则、配置与数据库字段 | ✅ 完成（2026-11-03） | schedule.py 时间窗/整数小时/阈值函数；config.yaml min_interval_hours；init-postgres.sql + db_update.yaml 幂等迁移；database_system_table.md 已登记 |
| Phase 2 | 创建、改期、认领、恢复和结束收尾 | ✅ 完成（2026-11-03） | create/update_schedule 两列同事务；claim_due 窗口判定；submit_manual/接单守卫/list_interrupted 放行窗口 completed 的 manual；终态钩子窗口收尾 + finalize_window_ended 扫描；resume 窗口结束转 completed；租户上限覆盖值 |
| Phase 3 | 任务视图、执行结果通知链接与工具文案 | ✅ 完成（2026-11-03） | /limits 端点；改期弹窗整数小时+起止时间；触发按钮覆盖窗口 completed；runner_id 直达（Entry 带 query、TaskPlanView 单条读取+定位滚动）；tenant_mgmt GET/PATCH /me/scheduler + TenantSettings 计划任务区；站内信/邮件/群机器人附「查看本次执行结果」链接；npm run build 通过 |
| Phase 4 | 独立测试、CodeReview 与发布前验证 | ✅ 完成（2026-11-03） | 独立测试 371 通过/0 失败（§15.4 边界脚本 29/30，1 项理论微秒边界与既有语义一致）；CR 出 1 P0（租户设置端点缺角色门）+2 P1+5 P2+3 nit，全部修复并经 CR 复核关闭；修复后后端 379 通过、前端 build+225 用例通过、import 链验证 OK。**二轮评审（2026-11-04）5 项全部认可并修复**：改期 UPDATE 状态条件+并发 409；task.manual_rejected 通知全链路（completed 任务手动执行被拒创建者可见）；聊天/定时终态文案按 task_id 区分；终态隐藏改期按钮；时间窗秒数保留（step=1+毫秒兼容）。修复后后端 444 通过、前端 build+227 用例通过。**三轮评审（2026-11-04）2 项 P2 修复**：SW 推送白名单/文案纳入 manual_rejected（worker.test vm 沙箱验证展示与点击跳转链路）；task.manual_rejected 进默认事件集合（TASK_EVENT_TYPES 六元+旧五元/旧默认兼容扩展+init-postgres DEFAULT 更新，db_update 历史迁移不改——旧行由读取侧兼容覆盖），补默认偏好下 expand 实际投递测试。回归：后端 641 通过（9 失败均为已鉴定存量）、前端 build+132 用例通过。**四轮评审（2026-11-04）3 P1 + 2 P2 全部修复**：①capped 恢复改小时级真实重试（条件恢复可自愈，补自愈链路测试）；②企微日报/补录 create 传租户有效上限、营销绑定激活改走 resume_recompute（配额/窗口/重算一致，达上限与窗口结束均拒绝绑定）；③企微入口窗口随 create 落库、cron 直传加"至多每小时一次"粒度校验（分钟级拒绝）；④SW 补 paused/needs_action 白名单与文案；⑤resolve 附站内信同款 title/body，manual_rejected 点击带 notice_ref，任务视图横幅展示被拒原因（含资源归属校验）。回归：后端 710 通过（9 失败仍为已鉴定存量）、前端 build+232 用例通过。遗留：wecom_conversation 23 个 SQL 用例与 DB 配额拒绝分支需 --isolated-db 环境补验；public_base_url 外部可达性属发布验收 |

## 成功标准

用户可创建“指定起止时间内每天固定时刻执行”的循环任务；interval 从开始时刻首次执行，之后只按整数小时循环，且不得低于 1 小时及配置阈值；手动“立即执行一次”不受时间窗限制，也不改变自动调度。每租户每用户 active 任务默认最多 5 个，租户管理员可在企业设置中修改，配置文件提供无覆盖值时的默认数。完成/失败通知能让任务创建者点开并定位到**本次**执行结果，不自动公开分享。旧任务、once、已受理执行及 Runner 计费通知保持现有语义。

## Phase 1：领域规则、配置与数据库

1. 在 `src/scheduler/schedule.py` 增可选 `start_at/ends_at` 的解析、窗口合法性、窗内首个触发点与推进计算；明确 cron 在 `start_at` 恰为触发点时可取该点，interval 在开始时刻首次触发，后续再加整数小时间隔。`compute_resume_due` 恢复时取恢复时刻为新首点；用同一套窗口规则供创建、改期与恢复使用。
2. 在 `configs/config.yaml` 的 `scheduler` 增 `min_interval_hours: 1`；读配置时只接受 ≥1 的整数。将 `build_schedule_fields` 当前 `interval_hours > 0` 改为“整数小时且 ≥ 配置阈值”的校验（数字或整数字符串均可），拒绝布尔值、小数、分钟/秒等输入；通过后再乘 3600 存入现有 `interval_seconds`。`max_tasks_per_user` 键和默认 5 保持不变。
3. `deploy/init-postgres.sql` 增两列；`deploy/db_update.yaml` 追加一次幂等 `ADD COLUMN IF NOT EXISTS` 迁移；同步 `docs/system/database_system_table.md`。旧行 NULL 表示无时间窗，不回填、不重算其既有 next_due_at。租户上限复用现有 `tenants.settings`，不新增配额表。

## Phase 2：写入与调度状态

1. `src/tools/scheduler/scheduled_task_tool.py` 创建工具解析 `time_config.start_at/ends_at` 并传给 `ScheduledTaskDB.create`；`src/api/scheduled_task.py` 改期入口走同一领域函数，传给 `ScheduledTaskDB.update_schedule`。两列与调度定义同事务更新，避免时间窗和 `next_due_at` 脱节；内容编辑接口不改时间窗。
2. `src/scheduler/schedule.py::max_tasks_per_user` 改为接收 tenant_id，优先读取 `tenants.settings.scheduler.max_tasks_per_user`，没有覆盖值则取 `configs/config.yaml` 的默认 5；只允许正整数。`src/scheduler/db.py::create` 和 `resume_recompute` 保持 active COUNT 与已有 advisory lock，统一传入当前租户有效值。降低上限不改现有任务状态，超额时拒绝新建/恢复；暂停释放名额。
3. `claim_due` 在任务行锁内确认本次计划点处于窗口且当前未过结束边界；计算窗内下次点。最后一个点仍保留 pending 和 active，直到受理、运行终态；`src/scheduler/hooks.py` 在计划执行终态且无后续点时置 completed。过窗且无 pending、无非终态 Runner 的任务由 `dispatch` 轻量扫描收尾；恢复时窗口已结束也置 completed。`trigger` 手动执行在窗前/窗内/窗后均可申请且不改周期；因时间窗自动完成后触发也保持 completed。同步调整 DB 手动 pending、首次接单守卫、API 状态校验、确定性提交拒绝的 pending 清理、interrupted 恢复候选及终态回写：仅该类 manual 可在 completed 下接单/恢复，失败不把 completed 改为 paused，scheduled 自动接单仍拒绝；paused 与 once 的原状态规则不变。不新增定时器或执行状态。
4. 公开任务字段包含起止时间；管理列表、任务详情按已有 tenant+user 过滤。确认 `once` 不接受窗口字段且原 10 分钟宽限不变。

## Phase 3：前端与对话入口

1. `frontend/web/api/scheduledTask.ts` 增字段和请求类型，`describeSchedule` 显示有效期；`frontend/web/components/scheduled/TaskPlanView.vue` 改期弹窗给循环类型增可选起止时间，once 隐藏；间隔输入改为整数小时并显示配置阈值，提交前提供即时校验，错误以服务端回执为准。`GET /api/scheduled-tasks/limits` 返回当前阈值与租户有效上限（在动态任务 ID 路由之前注册），避免前端硬编码第二套值。
2. `src/saas/api/tenant_mgmt.py` 加 `GET/PATCH /api/saas/tenants/me/scheduler`，沿用 `require_admin` 和受信 tenant_id；只改 `tenants.settings.scheduler.max_tasks_per_user`，以事务更新 JSON 文本并保留其他租户设置键。传正整数保存覆盖值，传 null 清除覆盖、恢复配置文件默认值。`frontend/web/components/saas/TenantSettings.vue` 增“计划任务”设置区域：显示只读的全局最短间隔，编辑/重置本租户每人活跃任务上限。平台全局默认值仍由 `configs/config.yaml` 管理。
3. 创建工具描述与结果、管理列表说明“时间窗/整数小时/活跃任务上限”的准确口径；自然语言“一个月内”若起止不明确，先向用户确认具体日期和时刻。
4. 通知链接复用已有 `notification_ref → runner_result` 归属解析：`src/notifications/repository.py` 将 `task.completed/failed` 纳入站内信列表与 `_inbox_link`，生成现有 `/notification?ref=...&tenant=...` 路径；`src/notifications/im_webhook.py`、`smtp_email.py` 的完成/失败文本附“查看本次执行结果”绝对链接，地址由 `settings.app.public_base_url` 加通知路径构成。Web Push 保留现有 ref 点击入口。只有 scheduled 终态事件加该链接，普通聊天终态不误导到任务页；未配置公开站点地址时外发不生成假链接，记录配置缺口且不阻断通知。
5. `frontend/web/notifications/NotificationEntry.vue` 利用既有受保护的 `GET /api/notifications/messages/{ref}` 结果，只对 scheduled `runner_result` 把 `runner_id` 带到 `/t/:tenant_id/tasks/:task_id?runner_id=...`。`TaskPlanView.vue` 按该 ID 调已有 Runner 单条读取接口，校验返回的 session_id 与任务会话一致，再展开并滚动到指定执行行；不能只在首页 20 条里搜索。无权限、任务删除或执行记录不存在时显示明确反馈；普通聊天与 paused/needs_action 路由不变。

## Phase 4：验证与交接

- 领域单测：整月 daily、起点恰在触发点、结束边界不含、窗口无有效点、interval 开始时首次触发、暂停恢复时首次触发、过窗停机；配置 0.5/1/1.5/2 小时和非法配置。每个用例说明避免的错误，例如“到结束边界不能多执行一次”。
- DAL/调度回归：同用户并发创建/恢复、暂停释放名额、终态释放名额、跨租户隔离、租户覆盖值与配置默认值；最后一次 pending 的提交重试、执行终态、通知与计费仍发生；过窗任务不补跑。保留原 once 与无窗口用例。
- API/工具/前端：创建和改期字段传递、错误回执、任务视图回显、旧任务 NULL 字段、配置阈值显示；租户管理员保存/清除上限覆盖值后普通用户看到有效值；手动触发在窗前/后及窗口自动 completed 后成功且不改变自动计划点，completed 下手动提交失败能退出 pending，手动运行中断可恢复并正确回写结果。只运行受影响单测、相关前端用例和关键启动/构建检查；按 `.claude/rules/dev_workflow.md` 做独立测试与 CodeReview。
- 通知链接：同一任务连续两次完成/失败通知分别打开对应 `runner_id`，包括已不在执行列表首页的旧记录；站内信、邮件、群机器人、Web Push 点击后仅属主可见，其他群成员/跨租户/未登录（登录前）均不能读取；普通聊天通知仍进聊天页；未配置 `public_base_url` 时无伪造 URL、通知仍可送达。发布验收检查外部地址可达；通知账本到期清理后的旧 ref 按资源不存在提示，不另造永久分享令牌。
- 完成每阶段即更新本表与 `docs/ideas.md`；全部开发完成后将索引条目移至 `docs/ideas_finished.md`。提交、部署均须另按 AGENTS.md 获取用户明确指示。
