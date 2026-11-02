# 定时任务接入通知规则开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 数据库三处同步（init-postgres.sql / db_update.yaml / database_system.md） | ✅ 完成（2026-11-02） | db_update 追加 "2026-11-02 12:00:00" 幂等块（ADD COLUMN IF NOT EXISTS），yaml 校验 75 块严格递增通过 |
| Phase 2 | 通知模块后端（规则类型 scheduled.task + list_task_rules + expand 快照路由 + 参数模型） | ✅ 完成（2026-11-02） | conditions {events} 值域校验/recipient_mode 强制 task_owner/GET /task-rules 只读路由/expand notify_rule_id 分支（rule_disabled/rule_not_found/rule_event_filtered 留痕） |
| Phase 3 | 定时任务后端（scheduler/db.py + notify.py + producer + REST API + 聊天工具） | ✅ 完成（2026-11-02） | notify_rule_visible 写侧校验（旧 notify_target_visible/list_visible_notify_targets 移除）；发布侧规则优先/旧目标兜底；工具 list_notify_rules/set_notify_rule |
| Phase 4 | 前端（NotificationRules.vue + TaskPlanView.vue + api 封装） | ✅ 完成（2026-11-02） | 规则页 4 事件复选默认全选 + 接收人只读任务属主 + 生效提示；任务编辑弹窗改通知规则（diff 提交/clear 语义） |
| Phase 5 | 测试与三智能体流程（开发 → 独立测试 → CR） | ✅ 完成（2026-11-02） | 三智能体闭环。后端 369 passed（9 失败均为存量：web_push 缺 py_vapid ×8、留资渲染空格断言 ×1，stash 干净树复核确认）；前端 TaskPlanView 17/17 + notifications 套件 125 全过、build 通过、import 链 OK；CR 无 P0/P1，补 task-rules 路由直测 2 用例（FIXED），P2 渠道中文映射文案已修；待用户指示提交 |

## 背景与结论

**调研结论（2026-11-02）**：通知规则就是大通知模块框架（`src/notifications/`）的一部分——`notification_rules` 表 + 规则引擎展开（`repository.py::_expand_rule_event`）+ 统一 expand/claim/dispatch 外发管道 + 前端 `/t/:tenant/notification-rules`（`NotificationRules.vue`，租户管理员配置：事件 + 规则专属渠道目标）。不是独立一套。

定时任务现状：4 类事件（`task.completed`/`task.failed`/`task.paused`/`task.needs_action`）已接入通知框架，但走"用户直投"路径——任务表 `notification_target_id` 直接指向渠道目标（`notification_targets`），发布时快照 `notify_target_id` 替代个人偏好路由。规则引擎目前只支持 4 种租户级事件（留资/积分/到期/滥用告警），定时任务事件未进规则体系。

**本次改造**：新增"定时任务通知"规则类型（一条规则勾选任务事件子集 + 挂渠道目标），具体任务上选择一条已配置的规则；去掉任务上的渠道选择（`notification_target_id`）。

## 方案设计

1. **规则形态**：新增规则事件类型 `scheduled.task`（展示名"定时任务通知"），仅存在于规则创建白名单，**不注册进 EVENTS 发布注册表**（不是可发布事件，是规则专用聚合类型）。
   - `conditions`：可选 `{events: [...]}`，值域为 4 个任务事件（`task.completed`/`task.failed`/`task.paused`/`task.needs_action`），缺省=全部 4 类
   - `recipient_mode`：固定 `task_owner`（新枚举值；站内信恒发任务属主，规则只决定外发渠道+事件过滤，不参与接收人解析）
   - 渠道目标复用现有 `register_rule_target`（webhook/邮箱挂 rule_id 下）
   - **仅被任务选定时生效**，不对全租户任务自动生效（避免全租户任务通知轰炸）
2. **任务绑定**：`scheduled_tasks` 新列 `notification_rule_id`，替代 `notification_target_id`（旧列保留用于存量兜底）。
3. **发布侧**：任务事件发布（仍走 `publish_in_tx`，站内信/属主不变）快照 `notify_rule_id` 进参数；任务无规则但有旧 `notification_target_id`（存量任务）时继续快照 `notify_target_id` 兜底，**存量任务外发不失效**。
4. **展开侧**（`repository.py::expand` 用户级路径）：参数含 `notify_rule_id` 时，查该规则（须 enabled 且事件在 conditions.events 内）→ 取规则级 targets（复用 `_rule_event_targets`）投递，跳过偏好门控（与现有快照语义一致）；规则停用/删除/事件被过滤时 route_snapshot 留痕 skipped。`notify_target_id` 旧分支保留不动。

## 实施步骤

### Phase 1 数据库（同步三处）
- `deploy/init-postgres.sql`：`scheduled_tasks` 加 `notification_rule_id TEXT`
- `deploy/db_update.yaml`：追加幂等迁移块（`ADD COLUMN IF NOT EXISTS`，datetime 严格递增，格式参照现有块）
- `docs/system/database_system_table.md` §8.1 scheduled_tasks 结构同步

### Phase 2 通知模块后端
- `src/notifications/repository.py`：规则白名单与 `_validate_rule_conditions` 支持 `scheduled.task`（允许 `{events}` 键，校验值域/去重/1-4 项）；创建/更新时该类型强制 `recipient_mode='task_owner'`；新增 `list_task_rules(subject)`（本租户 enabled 的 `scheduled.task` 规则 + 渠道目标脱敏摘要）；`expand()` 快照分支改造
- `src/notifications/contracts.py`：`RuleCreate`/`RuleUpdate` 的 `recipient_mode` 枚举加 `task_owner`
- `src/notifications/api.py`：新路由 `GET /api/notifications/task-rules`（普通用户 `subject` 依赖）
- `src/notifications/events.py`：`TerminalParameters`/`PausedParameters`/`NeedsActionParameters` 加可选字段 `notify_rule_id`（≤128）

### Phase 3 定时任务后端
- `src/scheduler/db.py`：`notify_rule_visible()` 写侧校验；`create`/`update_definition`（`notification_rule_id`/`clear_notification_rule` 互斥）/`pause_*_with_notify` RETURNING 带新列
- `src/scheduler/notify.py` + `src/notifications/producer.py::_scheduled_parameters`：快照二选一（rule 优先，target 兜底）
- `src/api/scheduled_task.py`：`UpdateDefinitionRequest` 字段切换
- `src/tools/scheduler/scheduled_task_tool.py`：创建参数与 `manage_scheduled_task` 的 `list_notify_targets`/`set_notify_target` → `list_notify_rules`/`set_notify_rule`

### Phase 4 前端
- `frontend/web/api/notifications.ts`：加 `taskRules()`
- `frontend/web/components/saas/NotificationRules.vue`：`EVENT_OPTIONS` 加"定时任务通知"；条件区 4 事件复选（默认全选）；接收人只读"任务属主"；列表匹配范围显示事件摘要；标注"需在定时任务编辑中选用后生效"
- `frontend/web/components/scheduled/TaskPlanView.vue`：编辑弹窗"通知目标"→"通知规则"；保存 payload 换字段；头部标签换规则名
- `frontend/web/api/scheduledTask.ts`：类型字段切换

### Phase 5 测试与流程
- 后端单测：`scheduled.task` 条件校验、expand 快照 rule 路由（命中/停用留痕/事件过滤/存量 target 兜底）、producer/notify 快照、REST API 新字段、工具切换
- 前端：TaskPlanView / NotificationRules 相关 vitest
- 按 dev-workflow 三智能体串行（开发 → 独立测试 → CR）

## 验证

1. `pytest tests/unit/notifications/ tests/unit/scheduler/ tests/unit/api/test_scheduled_task_api.py tests/unit/tools/scheduler/test_scheduled_task_tool.py -m unit`
2. `cd frontend && npx vitest run`（相关用例）
3. 行为验证（测试内覆盖）：管理员建 `scheduled.task` 规则挂 webhook → 任务选规则 → task.paused 发布 → expand 后 delivery 落规则 target；规则停用留痕 skipped；存量 `notification_target_id` 任务继续按旧渠道投递
