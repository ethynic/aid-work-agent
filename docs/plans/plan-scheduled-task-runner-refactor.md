# 定时任务重构（Runner 化）开发计划

> 设计文档：[docs/system/scheduled-task-runner-refactor-design.md](../system/scheduled-task-runner-refactor-design.md)（v5，定稿评审修正）
> 登记条目：docs/ideas.md `20261027-1600`
> 前提：本功能无线上存量用户，**单分支开发、单次发布，无数据迁移与兼容负担**；所有部署动作由用户明确指示后执行。非平凡开发，按 dev-workflow 三智能体流程（开发 → 独立测试 → CodeReview）执行。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 清场与数据模型重建 | ✅ 完成（2026-10-28，验收通过） | 新 scheduled_tasks 表（三处 DDL 一致）+ 新 DAL（claim_due 原子认领/快照、pending 条件变更、删除守卫事务、once 消费规则）+ 单测；旧实现全删（executor/delivery/error_sanitizer/工具/API/渠道补投挂点/旧表/旧测试），manager 瘦身为纯系统 job（幽灵锁回归用例经 CR 恢复）；定向回归约 5100 用例，15 个失败全部经基线 worktree 对比确认为 master 既有；import 链/前端 build/残留 grep 通过。CR 1 个 P1（测试误删）已修。**代码评审（用户）四项修正 + 复核余项关闭，验收复核通过（最终 50 单测绿）**：①统计口径统一 Runner 终态（completed/failed 计数，cancelled 两不计）；②claim_due/submit_manual 锁行事务内从当前行固化快照；③pause_for_occurrence（自动暂停带 occurrence+active 条件）；④pause 仅 active、resume 仅 paused（终态不可复活）；⑤认领参数全部派生自锁定行（新 schedule.py 纯函数，含"扫描后改期"回归）；⑥**cron 精确边界**（验收复核 P2，当即修复）：now 恰等于触发时刻时推进严格大于 now（+1µs 基准），含回归用例。遗留：F4（update_definition 清空语义）留 Phase 4。 |
| Phase 2 | Runner 服务 `scheduled` 来源增量（三语义鉴权 + 非交互模式） | ✅ 完成（2026-10-28，三智能体流程通过） | source 枚举+peers（scheduler/scheduled，web 网关加 scheduled 只读）；authorization `_scheduled` 三语义（提交/幂等重查/persisted 不要求 active，用户 token 只读+属主匹配+提交 403，web 网关无用户 token 拒绝）+ authorize_read_in_tx 行级路由 + 控制操作范围矩阵（用户 reply/pause/resume→SCHEDULED_CONTROL_FORBIDDEN，scheduler 仅无补充 resume）；接单守卫 lock_for_acceptance_in_tx（FOR UPDATE 先于 INSERT 同事务，幂等命中跳过）映射 409；chat 提交任务会话 TASK_SESSION_CHAT_FORBIDDEN；非交互：BaseTool interactive_only/chat_only 标记 + ToolControlSet 装配过滤（definitions/dispatch 同口径）+ 子执行 source 继承 + prompt_augmentations 经 child_factory 到达子执行 ContextAssembler；零历史（ContextAssembler 跳过历史/摘要/压缩）；write_history 投影 scheduled.task_id/trigger；public_runner 增 attempt/scheduled/result.credit_cost（finalizer 结算补入）；web_client EVENT_ERROR_CODES+网关列表 source=scheduled 透传。**68 新单测绿**（66+CR 补 2）；定向回归（services 969/tools 2437/channels 1260/api 384）无新失败（15 既有已基线证明）；测试智能体修 1 处 stub 失同步（_settle 元组）；**CR 修 P0**（SessionQuery 无幂等键走 `_scheduled` 崩溃致任务视图列表端点 500——按 session 唯一定位修复 + 回归）**+ P1**（scheduler 带补充输入 resume 缺口——矩阵追加 answer/attachments 校验 + 回归）。**用户评审六项修正后二次通过（79 新单测绿）**：①释放证明改字段域比较（credit_cost 不参与执行权证明，修复终态后心跳误判 RUNNER_ATTEMPT_EXPIRED 的全来源回归）；②scheduled 鉴权补归属双重核对（属主当前租户归属 + 真实会话行 tenant/user 一致，platform_admin 跨租户沿用）；③设备核对/浏览器授权/续读等既有等待处理入口对 scheduled 开放（local_verification/local_recovery_applicable/browser_web_auth×2/browser_continuation_read，归属检查不变；end-waiting 保持 chat-only 符合矩阵）；④补结算同步公开 result.credit_cost（迟到 receipt 不再展示偏少）；⑤事件读取复用事务外 verified_user_id，事务内仅复核 tokens 权威行；⑥manager.cancel 对 scheduled 行收紧为仅用户（无 user_token 即 403）。**三轮复核收尾（82 新单测绿）**：⑦会话租户归属改严格相等（NULL 不作通配，与 write_history 终态归属同口径，含 NULL/错租户/正确租户回归）；⑧scheduled 公开恢复动作按矩阵过滤（仅 verify/stop，dispatch_unconfirmed 为空集；resume/end 被矩阵与 end-waiting 门拒绝故不展示，chat 动作集不变）。遗留 P2 见风险区。 |
| Phase 3 | 调度循环、持久交接、恢复驱动与通知接线 | 📋 待开发 | |
| Phase 4 | 创建与管理入口（工具 + REST API） | 📋 待开发 | |
| Phase 5 | 前端：任务计划分组与任务执行视图 | 📋 待开发 | |
| Phase 6 | 独立测试 / CodeReview / 发布检查 | 📋 待开发 | |

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
- 回滚策略：代码回退即功能下线，新表残留无害。
