# 定时任务重构（Runner 化）开发计划

> 设计文档：[docs/system/scheduled-task-runner-refactor-design.md](../system/scheduled-task-runner-refactor-design.md)（v5，定稿评审修正）
> 登记条目：docs/ideas.md `20261027-1600`
> 前提：本功能无线上存量用户，**单分支开发、单次发布，无数据迁移与兼容负担**；所有部署动作由用户明确指示后执行。非平凡开发，按 dev-workflow 三智能体流程（开发 → 独立测试 → CodeReview）执行。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 清场与数据模型重建 | ✅ 完成（2026-10-28，验收通过） | 新 scheduled_tasks 表（三处 DDL 一致）+ 新 DAL（claim_due 原子认领/快照、pending 条件变更、删除守卫事务、once 消费规则）+ 单测；旧实现全删（executor/delivery/error_sanitizer/工具/API/渠道补投挂点/旧表/旧测试），manager 瘦身为纯系统 job（幽灵锁回归用例经 CR 恢复）；定向回归约 5100 用例，15 个失败全部经基线 worktree 对比确认为 master 既有；import 链/前端 build/残留 grep 通过。CR 1 个 P1（测试误删）已修。**代码评审（用户）四项修正 + 复核余项关闭，验收复核通过（最终 50 单测绿）**：①统计口径统一 Runner 终态（completed/failed 计数，cancelled 两不计）；②claim_due/submit_manual 锁行事务内从当前行固化快照；③pause_for_occurrence（自动暂停带 occurrence+active 条件）；④pause 仅 active、resume 仅 paused（终态不可复活）；⑤认领参数全部派生自锁定行（新 schedule.py 纯函数，含"扫描后改期"回归）；⑥**cron 精确边界**（验收复核 P2，当即修复）：now 恰等于触发时刻时推进严格大于 now（+1µs 基准），含回归用例。遗留：F4（update_definition 清空语义）留 Phase 4。 |
| Phase 2 | Runner 服务 `scheduled` 来源增量（三语义鉴权 + 非交互模式） | ✅ 完成（2026-10-28，三智能体流程通过） | source 枚举+peers（scheduler/scheduled，web 网关加 scheduled 只读）；authorization `_scheduled` 三语义（提交/幂等重查/persisted 不要求 active，用户 token 只读+属主匹配+提交 403，web 网关无用户 token 拒绝）+ authorize_read_in_tx 行级路由 + 控制操作范围矩阵（用户 reply/pause/resume→SCHEDULED_CONTROL_FORBIDDEN，scheduler 仅无补充 resume）；接单守卫 lock_for_acceptance_in_tx（FOR UPDATE 先于 INSERT 同事务，幂等命中跳过）映射 409；chat 提交任务会话 TASK_SESSION_CHAT_FORBIDDEN；非交互：BaseTool interactive_only/chat_only 标记 + ToolControlSet 装配过滤（definitions/dispatch 同口径）+ 子执行 source 继承 + prompt_augmentations 经 child_factory 到达子执行 ContextAssembler；零历史（ContextAssembler 跳过历史/摘要/压缩）；write_history 投影 scheduled.task_id/trigger；public_runner 增 attempt/scheduled/result.credit_cost（finalizer 结算补入）；web_client EVENT_ERROR_CODES+网关列表 source=scheduled 透传。**68 新单测绿**（66+CR 补 2）；定向回归（services 969/tools 2437/channels 1260/api 384）无新失败（15 既有已基线证明）；测试智能体修 1 处 stub 失同步（_settle 元组）；**CR 修 P0**（SessionQuery 无幂等键走 `_scheduled` 崩溃致任务视图列表端点 500——按 session 唯一定位修复 + 回归）**+ P1**（scheduler 带补充输入 resume 缺口——矩阵追加 answer/attachments 校验 + 回归）。**用户评审六项修正后二次通过（79 新单测绿）**：①释放证明改字段域比较（credit_cost 不参与执行权证明，修复终态后心跳误判 RUNNER_ATTEMPT_EXPIRED 的全来源回归）；②scheduled 鉴权补归属双重核对（属主当前租户归属 + 真实会话行 tenant/user 一致，platform_admin 跨租户沿用）；③设备核对/浏览器授权/续读等既有等待处理入口对 scheduled 开放（local_verification/local_recovery_applicable/browser_web_auth×2/browser_continuation_read，归属检查不变；end-waiting 保持 chat-only 符合矩阵）；④补结算同步公开 result.credit_cost（迟到 receipt 不再展示偏少）；⑤事件读取复用事务外 verified_user_id，事务内仅复核 tokens 权威行；⑥manager.cancel 对 scheduled 行收紧为仅用户（无 user_token 即 403）。**三轮复核收尾（82 新单测绿）**：⑦会话租户归属改严格相等（NULL 不作通配，与 write_history 终态归属同口径，含 NULL/错租户/正确租户回归）；⑧scheduled 公开恢复动作按矩阵过滤（仅 verify/stop，dispatch_unconfirmed 为空集；resume/end 被矩阵与 end-waiting 门拒绝故不展示，chat 动作集不变）。遗留 P2 见风险区。 |
| Phase 3 | 调度循环、持久交接、恢复驱动与通知接线 | 🔧 部分完成（2026-10-28：骨架+三轮评审修复完成，544 相关用例绿，待用户复核） | **骨架**：dispatch.py（run_once=pending 重提→恢复驱动→认领；§4.1 闭合映射表；恢复回合键 `sched-resume:{runner}:{attempt}:{n}`+账本计数+有界尝试+退避）；notify.py；task.paused/task.needs_action 注册+全渠道渲染+偏好集合；expand 目标快照路由；background_runner 启动；config 三字段。**评审九项修正**：①核心交付补齐——src/scheduler/hooks.py（terminal 统计回写+once 消费+recurring 自动暂停，失败 CheckpointFailure 整体回滚）挂入 event_repository terminal 分支；producer terminal/waiting 扩展 scheduled 来源（参数含 task_id/task_name/目标快照）；task.needs_action 注册生产（仅 verification/human_assistance，remote_tool/子执行等待不通知）；park 停车事务尾接线；②暂停与通知账本同一事务（pause_for_occurrence_with_notify/pause_interrupted_with_notify，分两事务丢通知路径消除）；③提交拒绝透传 occurrence_key 作事件幂等尾（恢复后同原因再暂停不再被去重）；④目标路由语义统一（快照目标在主查询替代偏好路由，prepare_send 同口径豁免；站内信恒保留）；⑤恢复自动暂停回合保护（事务内复核 runner 仍处 (runner_id,attempt) interrupted 态，过期决定不改状态不通知）；⑥list_interrupted 不再以 active 一刀切（用户暂停的任务已受理中断执行仍恢复，仅排除 paused(interrupted)/终态）；⑦恢复退避（runner.updated_at 作锚点，submit_retry_seconds 内不重发）；⑧同步 DAL 全部 asyncio.to_thread；⑨db_update.yaml 补通知默认值增量节（三处 DDL 一致）。**84 新单测**（含映射/键/回合保护/路由一致性回归）；回归 scheduler 92+notifications 165+agent_runner 269 无新失败（py_vapid×8、symlink×1 既有）。取舍记录：SCHEDULED_TASK_NOT_ACCEPTABLE 归 submit_rejected 但 pause_for_occurrence 必然 False（守卫失配=非 active/pending 变更）→ 零通知零状态变更；RUNNER_NOT_PARKED 等状态自解跳过。遗留：恢复耗尽后 resume 的 15s 再 pause 行为（逃生口=任务视图取消当前执行，Phase 4 验证）。**二轮评审七项修正后（538 相关用例绿）**：⑩TerminalParameters 可选扩展（task_id/task_name/notify_target_id，scheduled 终态参数过注册模型校验，修复终态事务回滚）；⑪资源解析接受 scheduled runner（actor 子句按来源分流，归属以 runner.user_id+会话核对；needs_action 站内链接）；⑫恢复排除条件 NULL 安全（IS NOT DISTINCT FROM，用户暂停 pause_reason=NULL 的已受理中断仍恢复，含真实 SQL 引擎语义测试）；⑬pause_interrupted_with_notify 锁序改为先 runner 后 task（消除检查后并发修改窗口，与终态链路同向）；⑭自动暂停同步清 pending（恢复侧与终态 CASE 侧，消除暂停后循环提交）；⑮恢复退避改任务级 recovery_retry_at 列（三处 DDL；首试立即，不复用 runner.updated_at）；⑯快照目标免启用时序门禁（eligible=True，受理后启用的新目标可达）。**补交付**：selectable-targets（非管理员目标选项）与 task-deliveries（任务投递状态查询）两接口。**三轮评审修正（544 用例绿）**：⑰record_run_result 参数元组按占位符顺序重排（此前 WHERE task_id 错绑 error_code 致 0 行静默更新）+ hooks 零行更新抛 SCHEDULED_RUN_RECORD_MISSED（不再把丢失回写当成功）+ 真实 SQL 执行回归（sqlite 内存库跑生产 UPDATE，断言终行：once 消费/manual 不消费/recurring 暂停清 pending×4/纯成功不动 pending/once 错误保持 completed）；⑱迟到拒绝按 key 条件清理残留 pending 槽（once 完成后未受理的 manual 请求不再永久重提，key 条件不误伤新槽）。 |
| Phase 4 | 创建与管理入口（工具 + REST API） | ✅ 完成（2026-10-28，三智能体流程通过，待用户复核） | 工具：create/manage 两工具重建（chat_only；时间必填不猜测/once 未来归一化/上限/R56 保守拦截保留；无 prompt 长度上限；profile/instance 从执行上下文继承——context 补 profile_id/instance_id 字段，worker 从 runner 行派生、子执行传各自 profile；manage list/pause/resume/cancel/view/set_notify_target）；F4 清空语义落地（update_definition 增 clear_fields，目前仅 notification_target_id，未知值拒绝）；API 重建+挂载（列表/统计/详情/pause/resume/trigger/delete/schedule 改期/definition 编辑，归属条件进 SQL，409 口径：pause 非 active、resume 非 paused、resume once 过宽限引导改期、trigger pending 占用/非 active、delete BusyError；trigger=manual 槽 uuid hex + 非交互指令，不动 next_due_at）；session 删除守卫（metadata.scheduled_task → 409，JSON 字符串同口径归一化）。**修复**：weekly cron day_of_week 映射按 APScheduler 编号回正（0=周一；旧 mon→1 实际落周二）；golden 清单补 web_fetch（遗留基线失败修复）+ 两新工具。**80 新单测绿**（工具 53 + API/守卫/F4 27）；回归 scheduler/notifications（py_vapid×8 既有）、tools（仅 sms×4 既有）、agent_runner（仅 symlink×1 既有，FakeChildRuntime stub 适配新 kwargs）、api（subagent_template×4 为 HEAD 既有，stash 验证）、D1 openapi（基线 worktree 既有）。**测试智能体修 2 处**（工具 list/view 剥离 pending_snapshot 泄漏、PUT 并发删除 404）并补 2 用例；**CR 修 3 处**（P1：notify_target_id 三写入口补归属校验 notify_target_visible——防他租户/他人目标 ID 注入通知路由；P1：interval_hours float 解析——1.5h 不再静默截断；P2：DELETE 并发/平台管理员假成功→如实 404）+ 9 用例，最终 91 用例绿；CR 核实 P0 七项无缺陷（due 同源衔接/resume 同规则/manual 槽快照一致/clear_fields 白名单/profile 鉴权无绕过——_scheduled execute 分支每次重查订阅授权/守卫不误伤/chat_only 同源）。遗留登记（部分已随二轮评审关闭）。**二轮评审八项修正（642 相关用例绿）**：①附件固化——Input 增 attachments 字段+validate_task_attachments（AttachmentRef 契约白名单归一化），创建落任务行随每次执行提交；②时间必填收紧——_coerce 失败直接 ScheduleConfigError（不再默认值兜底；9.8/NaN/'abc'/None 全拒）；③恢复竞态——resume_recompute 单事务（advisory 锁→FOR UPDATE 锁行→按锁定行当前定义重算→更新），工具与 API 共用，消灭旧定义覆盖并发改期；④恢复语义统一——once 过宽限两入口同转 expired（明确状态转换+回执）；恢复回合重置——recovery_reset_at 列（DDL×3），用户 resume 后 rejected 计数只统计其后（恢复耗尽任务获得新一轮有界尝试，历史控制保留）；⑤list_notify_targets action 落地（list_visible_notify_targets 同口径归属+脱敏回显）；⑥编辑校验——空白 name/prompt 422、attachments 结构校验；⑦配额事务化——create/恢复 advisory lock+计数+写入同事务（ScheduledTaskQuotaError）；⑧API 单次认证（identity 解析一次传递）；共享纯函数迁 scheduler.schedule（API 不再反向依赖工具实现）。**三轮评审五项修正（644 用例绿）**：⑨计数查询时间列改 accepted_at（agent_runner_controls 无 created_at——此前 SQL 错误致恢复控制无法提交；含 sqlite 真实执行回归验证 since 过滤语义与列名）；⑩恢复预算与幂等键序分离（键序=总 rejected 数不过滤、永不回退——重置后新键不撞旧 rejected 控制的幂等分支；预算=since 过滤数——用户恢复后新一轮有界尝试）；⑪附件校验复用 AttachmentRef 契约（size<0/'abc' 拒绝、file_id 超长拒绝不截断、错误统一 ScheduleConfigError 不再裸 ValueError）；⑫API 身份解析经 asyncio.to_thread（同步 DB 认证不再阻塞事件循环）。**第 5 项收尾（本轮已落实验证）**：⑬resume 状态冲突测试 mock 更新到 resume_recompute（返回 None + 调用断言；此前 mock 旧 resume 致未 mock 的 resume_recompute 触真实 DB，仅在本地 DB 可达时碰巧通过，隔离环境正确暴露），旧 resume mock 全仓零残留。 |
| Phase 5 | 前端：任务计划分组与任务执行视图 | ✅ 完成（2026-10-29，四轮评审验收通过） | TaskPlanView.vue（头部/操作区/执行记录/实时进度/通知投递，无输入框；执行记录=runnerClient.list(source='scheduled')，非终态行 SSE 订阅实时刷新，error_code 12 项中文映射，credit_cost/trigger/attempt 展示，markdown 渲染走 DOMPurify）；api/scheduledTask.ts 按新契约重写（含 describeSchedule/pause_reason 文案与后端同口径）；侧栏任务计划分组+普通会话按 metadata.scheduled_task 排除（AllSessions 同）；旧聊天链接→任务视图重定向（generation 失效保护）；NotificationEntry scheduled 事件路由（chat 终态未命中任务时回聊天页——CR 修的 P0 回归）；通知目标选择器（selectable-targets）+投递状态（task-deliveries）；删除旧 ScheduledTasks.vue（零引用死代码）。测试智能体修 weekly 改期弹窗星期映射（APScheduler mon=0，与后端一致）；CR 修 P0（chat 终态通知误路由为任务解析失败）+P1（0 点预填 9 点静默改期——parseInt||9 falsy 陷阱）+once ISO T 分隔。build 通过（1134 模块）；vitest 765 过+7 失败全为既有 office 超时（worktree 基线证明）；关键套件 146/146。遗留 P2：任务列表刷新时机（导航触发）、切换竞态 toast 噪声；P3：GET /api/sessions/{id} 不含 metadata（列表+兜底消化）。**评审 13 项集中修复（137 关键套件绿 + 9 新组件用例）**：①切换隔离——PageContext(tenant+task) generation 校验全部异步回调，reset 清弹窗/定时器（评审复现的 A 内容写入 B/慢响应覆盖/换租户不重载全消）；②新执行发现——页面打开期间 10s 轮询 list 首页+任务行轻刷，立即执行后 2s×8 探测替代固定 3s 猜测；③双缓存收敛——单 Map+revision 接纳规则，观察结束复用 runnerObservationComplete（settled 未完成行继续订阅费用更新）；④结果展示补齐——images 走聊天页 ImageGallery lightbox、progressMessages 渲染、会话消息详情按 metadata.runner_id 过滤（核验 message_projection 已返回该字段，后端零改动）；⑤侧栏刷新——领域级 scheduledTasksVersion 信号（非通用总线）+visibilitychange 节流；⑥改期精度——interval 改分钟输入+实时回显，月日期 1-31 完整；⑦附件新增——复用 /api/upload 组装 AttachmentRef；⑧解析三态——命中/null 确认非任务/undefined 失败可重试，失败不回退聊天页；⑨通知目标 diff 提交；⑩AppHeader+侧栏 toggle 契约+RouterLink 键盘可达+label 关联；⑪分页——首页 20+按需加载更早，AllSessions 整页全过滤自动翻页（上限5）+如实标注；⑫取消改行级绑定 runner_id（顶部仅单一 active 时显示）；⑬投递三态+pending 30s 刷新。monthly 28 上限已随⑥删除。**二轮评审 8 项 P2+1 P3 修复（关键套件 146+后端 119 绿）**：⑭applyRunnerView 主判据改 view_revision（cancel_requested 只递增 view_revision 不再被旧列表响应回退）；⑮侧栏刷新挂 useAgent applyRunner 终态迁移点（5s 合并 bump）+watch 租户/登录/用户三元组清空失效；⑯**后端** SessionDB.list_by_user 四条 SQL 前置过滤任务会话（total 同口径），AllSessions 删跳页补偿；⑰附件上传中禁保存+占位行+弹窗代际丢弃旧回填；⑱task_event_deliveries 返回 expansion_state（前端四态：失败/待展开进 30s 刷新/确认无外发/有投递）；⑲会话详情按 runner 缓存（终态一次/非终态刷新/终态落定清缓存）；⑳resetPage 清目标选项+每次 openEdit 重查；㉑**后端**会话详情透出 scheduled_task + 通知资源解析返回 scheduled_task_id——旧链接/通知按权威标记路由，不再全量任务列表前置；㉒两弹窗接 BaseModal accessible。后端改动 4 文件均属第 3/8 项授权域（models/session/authorization/repository 各一处）。遗留说明：消息端点无游标分页（仅 limit=100），单任务超约 50 次执行后更早执行的会话消息超窗——**已关闭**（三轮评审第 3 项）：messages 端点增 runner_id Query 参数——DAL 先按 metadata->>'runner_id' 过滤再 LIMIT（缓存 key 带 runner 维度），任务执行详情不受最近 100 条窗口限制、无需缓存整份会话消息。**同轮修复**：⑳附件上传中状态从占位列表派生（旧弹窗 finally 不再递减新弹窗计数错误解禁保存）；㉑会话消息在途请求引用身份守卫（终态清缓存后旧 loading 请求返回不得回写过期内容，再次展开重新拉取）。**四轮收尾**：runner 过滤 SQL 修为 (metadata::jsonb)->>'runner_id'（chat_messages.metadata 为 TEXT 列，直接 ->> 是 PostgreSQL 类型错误；同 message_share_db 既有写法），测试补 sqlite 真实执行（json_extract 等价模拟）验证过滤行为。 |
| Phase 6 | 独立测试 / CodeReview / 发布检查 | ✅ 完成（2026-10-29，agent2 浏览器 E2E 10/10 通过） | **全量回归**（非基线失败=0）：scheduler 105 + agent_runner 250 + notifications 189（py_vapid×8 既有）+ services 993（symlink×1 既有）+ tools/db/api 2985（sms×4/template×4 既有，时间敏感用例修 mock 固定）+ channels 1260（×5 既有）+ 前端 vitest 784 过（office×7 既有）；**启动安全**：全部关键模块 import 链通过（main/background_runner/dispatch/hooks/notify/schedule/db/manager/runner api+worker+bootstrap/工具/API/通知）；**§14 十三条失败边界用例覆盖**：12 条已覆盖 + hooks.py 补 6 用例关闭第 12 条缺口（零行更新 CheckpointFailure/异常回滚/防御路径/正常路径）。**部署清单待执行**（等用户指示）：测试库演练 db_update.sql（含 recovery_retry_at/recovery_reset_at/偏好默认值/旧三表 DROP） → config.yaml scheduler 节（poll_seconds=15/submit_retry_seconds=60/recovery_max_attempts=3）与 AGENT_RUNNER_WEB_SERVICE_TOKEN 确认 → 重建 runner-api/runner-worker/aid-agent-background 容器 → 按 §14 验收。**agent2 浏览器 E2E 10/10 通过（2026-10-29）**：T1 对话创建（自包含验证+默认行为约定）/ T2 诚实拒绝（无数据不虚构）/ T3 任务视图（无输入框）/ T4 立即执行（15s 拾取+2s 探测）/ T5 执行记录完整（触发原因/时间/状态/输出/7.75 积分）/ T6 通知（任务投递区正确，铃铛待查）/ T8 暂停恢复 / T9 改期（预填正确）/ T10 删除（会话清空）。**测试中修复**：agent_runners.actor_kind CHECK 约束放宽（0697655d）；通知 X-Tenant-Id 缺失（fab73d8b）+ 铃铛遮罩（a92f9761）已修。 |
| 部署后修复 | 定时任务执行附件不可下载 | ✅ 完成（2026-10-30，agent2 端到端验证通过） | **根因一（文件缺失）**：①`extract_downloadable_file` 只识别单文件 `result.file_id`，analyze_data/cp 批量交付返回 `result.files` 数组未捕获，新执行 downloadableFiles 为空（3f3ba365 修：`extract_downloadable_files` 多文件提取 + durable_control 逐个入 presentation）；②存量旧执行 public_snapshot 已固化、后端修复无法回填，前端 TaskPlanView 增兜底——downloadableFiles 为空时从 progressMessages 的 tool_result 事件提取 `result.files`（63bce640；摘要行补「含 N 个文件」）。**验证（agent2 实测）**：测试用户旧执行 DB 数据形状与兜底逻辑逐字段核对（type/success/result.files/visible 全匹配）+ 用真实 DB 数据本地跑 public_runner 投影确认 API 保留 files；手动触发新执行 → 8 个文件入 downloadableFiles → 任务视图「含 8 个文件」→ 详情展开 8 张 DownloadFileCard → 点击 txt 卡片浏览器下载事件真实触发。**时间线说明**：测试用户反馈「还是不能下载」发生于此两修复（10-30 08:26/11:23 提交）部署（10-30 11:27 构建+容器重建）之前，属验证时机问题而非修复无效。**根因二（点击无反应）**：DownloadFileCard 对可预览类型（图片/Office）点击走「打开预览面板」，但 AttachmentPreviewPanel 仅挂在聊天页 ChatContainer——任务视图（及 saas OfficeSessions/WebSessions 会话页）未挂载面板，openPreview 只改单例状态无渲染，点击静默无效（txt 走直下不受影响，故用户反馈集中在图片/Excel）。修复：DownloadFileCard 增 `downloadOnly` 属性（默认关闭，聊天页行为不变），TaskPlanView 与两个 saas 会话页传开，一律直接下载（微信引导/blob/原生三分支均不依赖面板）；新增 4 用例（downloadOnly 图片/xlsx 不走预览、点击进下载路径、图片默认仍预览回归），43 组件用例绿 + build 1143 模块通过。待部署 agent2 后由用户验证图片/Excel 点击下载。遗留观察：飞书机器人投递 render_failure（独立问题，另行排查）。 |

## Phase 1：清场与数据模型重建

目标：旧实现整体下线，新表与新 DAL 就位；系统级 job 不受影响。

1. **DDL**（`deploy/init-postgres.sql` + `deploy/db_update.sql`）：
   - DROP `scheduled_tasks` / `scheduled_task_logs` / `scheduled_task_deliveries`；
   - CREATE 新 `scheduled_tasks`（设计 §9：含 `attachments`/`profile_id`/`instance_id`/`notification_target_id`/`status(active|paused|completed|expired)`/`pause_reason(credit_blocked|profile_forbidden|tenant_unavailable|definition_invalid|submit_conflict|submit_rejected|interrupted)`/`next_due_at`/`pending_occurrence_key`/`pending_trigger`/`pending_retry_at`/`pending_snapshot`/统计列）+ 索引（`(status,next_due_at)`、pending 部分索引、`(tenant_id,user_id,status)`）。
2. **`src/scheduler/db.py` 重写**：create（含任务会话同事务创建）、get/list_by_user/count_by_user；`claim_due`（条件 UPDATE：推进 next_due_at + 写 pending 四字段，快照含 `prompt_augmentations` 在内的**完整提交意图**）；`submit_manual`（manual 槽，不动 next_due_at）；**所有 pending 变更带 `pending_occurrence_key` 条件**（`clear_pending`/`backoff_pending`/`pause`）；`resume`（重算 due：cron now 之后 / interval now 新锚点 / once 宽限 expired）；`reschedule`（仅调度字段变更时调用）；`delete_with_session`（**单事务：锁任务行 → 查会话非终态 runner（agent_runners）→ 有则抛 409 → 删任务+会话+消息**）；`record_run_result`（统计回写）。
3. **删除旧实现**：`src/scheduler/executor.py`、`delivery.py`、`error_sanitizer.py`、`tests/unit/scheduler/` 旧用例、`src/tools/scheduler/scheduled_task_tool.py`、`src/api/scheduled_task.py` 及 `main.py` 挂载行。
4. **`manager.py` 瘦身**：删除用户任务部分，保留锁 + 幽灵锁重试 + BackgroundScheduler + 全部系统 job。
5. **渠道补投挂点摘除**：`channel_routes.py`、wecom_kf adapter 相关改动；全仓 grep 旧符号清理（`deliver_task_result|ScheduledTaskLogDB|ScheduledTaskDeliveryDB|manual_trigger_at|create_scheduled_task|manage_scheduled_task`）。
6. **前端**：暂时摘除 `tenant-scheduled-tasks` 路由（Phase 5 以任务视图重挂）。
7. **文档**：`database_system_table.md` §8 重写。

完成判据：全仓无旧符号；后端与 background_runner 启动正常、系统 job 不变；相关既有回归绿。

## Phase 2：Runner 服务 `scheduled` 来源增量（三语义鉴权 + 非交互模式）

1. **`contracts.py`**：source 枚举 + `'scheduled'`；`api.py` list_session 同步。
2. **`settings.py`**：peers 增加 scheduler 服务（sources=`['scheduled']`，token 复用）。
3. **`authorization.py` `_scheduled` 分支——三种语义**（设计 §5.2）：
   - 首次接单：在**提交事务内**对任务行 `FOR UPDATE` 复核 `status='active' AND pending_occurrence_key == key(client_request_id)`（挂接点：repository 提交事务的来源守卫缝，参照 ApplicationSources 模式）；任务会话归属/租户/用户 active/profile 订阅授权校验；
   - 幂等重查（execute=False）：任务行存在 + 属主匹配，不要求 active；
   - `authorize_persisted`：任务行存在 + 租户/用户/profile 校验，**不要求 active**（已受理执行继续）；
   - 用户 Bearer token 只读；Principal `actor_kind='system'`。
4. **chat 提交任务会话拒绝**：`source='chat'` 且会话带 `metadata.scheduled_task` 标记 → `TASK_SESSION_CHAT_FORBIDDEN`（`_web_in_tx` 会话行已加载，加标记检查）。
5. **控制操作范围矩阵**（设计 §5.2，控制入口按来源+动作校验）：用户对 scheduled runner 可查询/订阅/cancel（专用端点）；reply、resume（含 answer/attachments）、pause、browser_complete → `SCHEDULED_CONTROL_FORBIDDEN`；scheduler 服务可 resume（无补充输入）；设备核对走既有受权入口（verify-local 等）不变。
6. **非交互模式**：
   - `BaseTool` 增 `chat_only` 与 `interactive_only`（ask_user/clarify 等）标记；装配处（`runtime/profile.py`/`ToolDispatcher.definitions()` 同一口径）按来源过滤：channel 滤 chat_only，scheduled 根/子执行滤 interactive_only；
   - **注入位置**：非交互指令经 prompt_augmentations 提交，ContextAssembler 装配为**受信补充 user 消息**（非系统提示词）；child_factory 将同一 augmentation 传入子执行上下文装配，根/子一致继承；
   - 不新增等待失败分支：verification/human_assistance/remote_tool/子执行等待保留既有生命周期。
7. **零历史装配**：ContextAssembler 按 source='scheduled' 跳过历史读取。
8. **`transaction_repository.write_history`**：scheduled 来源投影 `request_data.scheduled` 进消息 metadata。
9. **`public_view.public_runner` 扩展**：scheduled 来源增 `scheduled:{task_id,trigger}`（来自 input.request_data）；`result.credit_cost`（结算后补充，finalizer 公共投影）；`attempt`（通用补充，恢复回合键与任务视图用）。
10. **Web 网关读取接线**：`agent_runner_web.py`/`web_client.py` 透传 source='scheduled' 用户 token 读取（发现/快照/事件/controls cancel）。

单测：三语义鉴权（首次接单守卫：active+pending 匹配、暂停后拒绝、幂等重查放行、persisted 不要求 active；伪造服务 401/任务行缺失 404/profile 403）、chat 提交任务会话拒绝、**控制操作矩阵**（reply/带补充输入 resume → SCHEDULED_CONTROL_FORBIDDEN；cancel 专用端点放行；scheduler 无补充 resume 放行）、零历史、非交互（根+子 definitions 与 dispatch 一致过滤；augmentation 经 ContextAssembler 补充 user 消息到达根与子上下文；设备等待生命周期不变）、write_history 投影、public 扩展字段、chat_only 渠道不可见。回归：agent_runner 既有用例。

## Phase 3：调度循环、持久交接、恢复驱动与通知接线

1. **`src/scheduler/dispatch.py`（新）**：
   - `compute_next_due`：cron `get_next_fire_time(after=now)`（停机合并）；interval 原锚点 + k×interval > now 最小值；once NULL；时区 config；
   - `run_due_loop`：每轮①到期 pending（retry_at 到期）重提，②恢复驱动（§下），③认领新 due；
   - `submit_pending`：按 `pending_snapshot` 提交（幂等键 task_id:occurrence_key，**快照含 prompt_augmentations，重试原样重建，不重新生成**）；结果映射按设计 §4.1 闭合表：202 → 条件清 pending；402/403/422 → 对应 paused+reason + `task.paused` 通知 + 清 pending；**409 IDEMPOTENCY_* → paused(submit_conflict) + 通知 + ERROR 日志 + 清 pending（不得静默）**；**其余确定性 4xx → paused(submit_rejected) + 通知 + 清 pending**；5xx/未知（含超时）→ 条件 backoff，**不当作确定未接单**；
   - **恢复驱动（回合键 + 账本计数）**：发现任务会话 interrupted 的 scheduled runner → 以 scheduler 身份提交 `resume` 控制，**client_request_id = 'sched-resume:{runner_id}:{attempt}:{n}'**——`attempt` = runner 本次中断回合的执行 attempt 号（agent_runners.attempt，每次领取/恢复 +1，恢复成功后再中断必是新回合不撞已 consumed 旧控制；attempt 补入 public_runner），`n` = 本回合已 rejected 的 resume 控制数（从 agent_runner_controls 按键前缀统计，重启不归零）；同回合重试同键幂等（防并发/响应丢失重复）；`RUNNER_RESUME_PENDING`（待消费）与网络未知不计失败；**受理前 402/403（无 rejected 记录）直接按原因暂停任务+通知（402→credit_blocked，403→profile_forbidden/tenant_unavailable），不无限重复提交**，其余未列举确定性拒绝 → paused(interrupted)+通知；worker 侧 rejected 落账后 n 递增换键再试，达 `recovery_max_attempts` → 任务 `paused(interrupted)` + `task.paused` 通知；网络类暂时故障退避；恢复用 resume **无补充输入**。
2. **`src/scheduler/hooks.py`（新）**：`record_scheduled_run_in_tx`——统计回写（manual/scheduled 终态均计）；**仅 trigger='scheduled' 终态消费 once**（置 completed）；recurring 执行侧 CREDIT_BLOCKED/PROFILE_FORBIDDEN → paused+reason。
3. **`event_repository.py`**：terminal 分支挂 hooks（懒加载，失败整体回滚）。
4. **通知增量**：
   - `producer.py`：scheduled terminal 分支（接收人=runner.user_id，参数含 session_id/task_id/**快照 notify_target_id**）；
   - **新增 `task.needs_action`（触发范围收窄）**：仅 scheduled runner 停车在**确需用户动作**的等待（verification 设备确认、human_assistance 人工辅助/登录等；按下钻至叶子的实际等待原因判断）时生产（park 事务尾钩子，与现有 waiting producer 同挂点新分支）——参数 `{task_name, action_label, task_id, notify_target_id?}`，action_label 说明要做什么；**remote_tool 远程结果等待与子执行等待不生产通知，仅经快照 progress 更新任务视图进度**；event_key 含 runner_id+停车标识幂等；
   - `events.py`：注册 `task.needs_action` 与 `task.paused` 的**参数均含可选 notify_target_id 快照**（`{task_name, action_label/task reason_label, task_id, notify_target_id?}`）与文案/外发渲染；`TerminalParameters` 增可选 session_id/task_id/notify_target_id；偏好默认事件集合补 `task.paused`/`task.needs_action`；
   - **统一资源路由**：expand 按"资源→任务行"解析（runner_result/runner_waiting 经 agent_runners.session_id，scheduled_task 直接）+ 事件参数快照 notify_target_id 投递（校验目标当前有效，失效记投递异常）；无快照目标 → 个人偏好；站内信恒开；
   - **目标选项读取接口**（非管理员：个人 Web Push + 租户级目标，名称/provider/引用，无凭据）；
   - **投递状态查询接口**（按任务/runner 资源查 notification_messages + deliveries）；
   - 外发文案携带任务视图入口链接与动作指引。
5. **`src/background_runner.py`**：启动 dispatch 循环（含恢复驱动）。
6. **配置**：config.yaml scheduler 节（timezone/poll_seconds/max_tasks_per_user/submit_retry_seconds/recovery_max_attempts）+ settings 对齐。

单测：claim 原子性与 pending 槽互斥、推进规则（cron now 合并/interval 锚定/once NULL+宽限 expired）、快照不可变（编辑后提交仍用快照；**augmentation 不重新生成**）、条件 clear/backoff（迟到响应不清新 pending）、拒绝映射闭合表（含 409 submit_conflict 与未列举 4xx submit_rejected 均暂停+通知）、402/422 暂停+通知幂等、恢复驱动（**回归场景一：同一 runner"中断→恢复成功→再次中断"，两次均创建并消费恢复控制（attempt 键区分回合，不撞已 consumed 旧控制）；回归场景二：resume 受理前被 402/403 拒绝 → 任务按原因暂停+通知，不无限重复提交**；同键幂等防并发/响应丢失重复；RUNNER_RESUME_PENDING 与网络未知不计数；账本计数重启不归零；被拒递增/耗尽暂停+通知）、needs_action 触发范围（**远程结果/子执行等待不通知**，设备确认/人工辅助通知）、hooks（once 消费规则/manual 不消费/recurring 暂停）。集成（真库）：插任务行 → 认领 → 提交 → worker 执行 → 会话消息、chat_records 计费、统计回写、通知（含目标投递）全链路；"认领后杀进程重启重提""接单响应丢失重试"两条路径必须覆盖。

## Phase 4：创建与管理入口（工具 + REST API）

1. **`src/tools/scheduler/scheduled_task_tool.py` 重写**：
   - 两工具 `chat_only`；
   - create：产品校验收敛（时间必填不猜测 / once 未来时间归一化 / 每用户上限 / R56 引导；**删除 prompt 2000 上限**，服从请求容量闸）；描述要求自包含 + **按相关性**的默认行为约定（无模板无关键词校验）；profile/instance 从执行上下文继承（缺字段在 worker 装配处补齐）；可选 `notify_target_id`（manage 增 list_notify_targets，经 Phase 3 读取接口）；创建即 active + 计算首 due；
   - manage：list/pause/resume/cancel 适配新状态机；view = 任务统计 + 最近执行摘要 + 指引任务视图；支持修改通知目标。
2. **`src/api/scheduled_task.py` 重写 + `main.py` 挂载**：列表（+session_id/pause_reason/last_status/next_due_at/notification_target_id）、详情、pause、resume、delete（调用 Phase 1 事务守卫 DAL）、trigger（manual 槽）、编辑（**任务内容/附件/通知目标不动调度；仅调度字段变更重算 due**）。读写维持 tenant+user 条件进 SQL。
3. **普通会话删除守卫**：session 删除 API 拒绝带 `scheduled_task` 标记的会话。

单测：创建校验（缺时间拒绝/once 过去拒绝/超限/R56；无长度上限）、profile 继承、API 归属校验、删除守卫（活跃 runner 409/pending 409）、编辑不重算调度、trigger 不改 next_due_at、会话删除守卫。

## Phase 5：前端：任务计划分组与任务执行视图

1. **会话侧栏分组**：普通会话 / 任务计划；任务计划来自 `/api/scheduled-tasks`；任务会话从普通会话列表排除。
2. **任务执行视图**（新组件 + 路由）：
   - 头部：说明/调度规则/状态（pause_reason 映射文案）/下次执行；
   - 操作区：暂停/恢复/改期/编辑（内容、附件、通知目标）/立即执行一次/**取消当前执行**（非终态 runner 经网关 cancel，如实展示取消核对中的状态）/删除（409 引导重试）；
   - 执行记录：runners 列表（经网关 source='scheduled'）——`scheduled.trigger` 触发原因、accepted_at/finished_at、status、result.output / error_code 映射文案（含执行前失败与空输出）、`result.credit_cost` 费用；内容详情读会话消息；
   - 当前执行进度：订阅 runner 事件流实时刷新；
   - 通知投递状态（Phase 3 查询接口）；
   - 无聊天输入框；定时执行不切换页面。
3. **旧聊天链接路由**：打开带 `scheduled_task` 标记的会话 → 跳转任务视图。
4. **通知目标选择器**：创建（工具选项）+ 编辑表单；经目标选项读取接口；默认"站内 + 个人偏好"。
5. **通知跳转**：terminal/paused/needs_action 通知点击 → 任务视图（NotificationEntry 资源解析支持 scheduled/system）。
6. `frontend/web/api/scheduledTask.ts` 重写。

完成判据：`npm run build` 通过；手动验收——对话创建（缺信息对话补齐、无试跑、可带通知目标）→ 任务计划分组 → 到点执行形成完整执行记录（触发原因/时间/状态/结果/费用）→ 通知到达所选目标（含入口链接）且点击进任务视图 → 立即执行不改调度 → 暂停/恢复/改期/编辑（不动调度）/取消当前执行/删除全通 → 旧会话链接跳任务视图。

## Phase 6：独立测试 / CodeReview / 发布检查

1. 独立测试智能体：全量回归（scheduler 新单测、agent_runner、notifications、wecom_kf adapter 补投移除后用例、工具过滤影响面）+ 设计 §14 十三条失败边界与八条验收标准逐条用例覆盖。
2. CodeReview 智能体独立审查；意见修复后主控者提交前验证。
3. 发布前关键启动检查：runner-api / runner-worker / background_runner 健康，dispatch 循环与恢复驱动注册，系统 job 不受影响。
4. 部署清单（**等用户明确指示后执行**）：测试库演练 `db_update.sql` → config.yaml 与 env → 重建 runner-api / runner-worker / aid-agent-background → 按 §14 验收。
5. 同步 `docs/ideas.md`。

## 风险与注意点

- **pending 快照是幂等正确性的另一半**：键固定而内容不固定同样会撞 IDEMPOTENCY_INPUT_MISMATCH；快照必须含 prompt_augmentations（重试不重新生成），集成用例必须覆盖"认领后编辑任务再重试提交"；
- **409 与未列举 4xx 不得静默**：任何确定性拒绝都必须落到任务状态 + 通知（评审三轮 P1），"按设计不可达"不作为只记日志的依据；
- **接单守卫必须在提交事务内持任务行锁**：独立前置查询无法与暂停/删除协调；Phase 2 实现时确认守卫缝挂接在 `repository.submit_in_tx` 同事务；
- **三种鉴权语义不得合并**：幂等重查要求放行非 active（找回原 runner），首次接单要求 active+pending 匹配，persisted 不要求 active——合并任何两个都会重新引入竞态或丢失已受理执行；
- **once 消费只认 trigger='scheduled'**：terminal 钩子必须读 request_data.scheduled.trigger，manual 终态误置 completed 是回归用例必测项；
- **needs_action 不能通过扩展 waiting producer 的 source 过滤实现**：现有 producer 只认 clarification 表单；且触发范围仅限需用户动作的等待，远程结果/子执行等待只更新进度，防通知噪声；
- **目标路由以事件参数快照为准**：expand 不得回读任务行当前 notification_target_id；terminal/paused/needs_action 三类事件参数必须一致携带快照，缺一类就会"完成通知进群、暂停通知进个人"；
- **恢复驱动回合键**：控制 client_request_id 必须含中断回合 attempt（`sched-resume:{runner_id}:{attempt}:{n}`，评审四轮 P1）——只按 rejected 计数会在"恢复成功后再次中断"时撞已 consumed 旧控制导致永远无法二次恢复；受理前 402/403 不产生 rejected 记录，必须直接映射任务暂停+通知，不得等待 rejected 落账；RUNNER_RESUME_PENDING 是待消费不是失败；
- **恢复驱动不做时间取消**：resume 控制被拒不等于该取消 runner；取消走任务视图用户操作，按既有取消核对语义收尾，不承诺立即释放 claim；
- **非交互过滤一致性**：definitions 与 dispatch 同一过滤点；augmentation 与过滤必须同时到达子执行；
- **terminal/needs_action 生产在终态/停车事务内**：失败整体回滚，不吞异常；
- **Phase 1 CR 遗留（Phase 3/4 落地时处理）**：`update_definition` 的可选字段无法清空回 NULL（如把通知目标改回默认），Phase 4 编辑 API 设计时需增加清空语义（显式哨兵值或专用清除参数）。已解决项（2026-10-28 代码评审修正）：submit_manual/claim_due 快照改在锁行事务内从当前任务行构建（调用方不再传快照，漏传 NULL 问题不复存在）；提交拒绝的自动暂停必须走 `pause_for_occurrence`（带 occurrence+active 条件，返回 False 时**不得发布暂停通知**）；统计口径统一 Runner 终态（completed/failed/cancelled）；pause 仅 active、resume 仅 paused（Phase 4 API 按返回值提示状态冲突）；
- **Phase 2 CR 遗留（Phase 3/4 落地时处理）**：`filter_source_visible_tools` 只改 registry 不更新 final_names（当前无 chat_only 工具无实害，Phase 4 工具声明 chat_only 后验证 definitions 一致性）；scheduled 执行仍处理 `_handle_remember_intent`（写侧，零历史契约只约束读，知悉即可）；
  已随评审修复关闭（2026-10-28 二轮评审）：scheduler 经通用 cancel 取消 scheduled runner 已按矩阵收紧（manager.cancel 对 scheduled 行无用户 token 即 403，仅用户可取消）；scheduled 鉴权已补归属双重核对（属主当前租户归属 + 真实会话行存在且 tenant/user 一致，platform_admin 跨租户规则沿用）；事件读取复用事务外 verified_user_id，事务内仅复核 tokens 权威行；
- **Phase 3 CR 遗留已全部关闭（Phase 4 二轮评审）**：恢复耗尽后 resume 的回合重置已实现（recovery_reset_at 列——用户 resume 后 rejected 计数重新统计，旧 runner 获新一轮有界恢复尝试，历史控制记录保留）；快照目标替代偏好路由语义已按定稿验收；双路由留痕随重构消除。
- 回滚策略：代码回退即功能下线，新表残留无害。
