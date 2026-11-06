# 定时任务结果免登录链接实施计划

设计：[design-task-result-share-link.md](../system/design-task-result-share-link.md)（决策已与用户逐条定稿 2026-11-06）

实施假设：本次覆盖设计中定时任务 `task.completed` / `task.failed` 的三种通知渠道及免登录结果页；不改变普通聊天终态和需交互事件。公开链接持有者可读本次最终回复及其文件，删除任务或消息后实时失效。开发在 `codex/task-result-share-link` 分支、当前工作区进行；真机部署及验收须另获用户明确部署指示。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 后端：share 记录 + 公开路由 | ✅ 完成（2026-11-06） | 79 通过、1 跳过；隔离 PG 实测通过；CR 通过 |
| Phase 2 | 渠道切换（web push / 站内信 / IM webhook） | 📋 待开发 | |
| Phase 3 | 前端：免登录结果页 | 📋 待开发 | |
| Phase 4 | 汇总回归、独立测试与 CodeReview；真机验收待授权 | 📋 待开发 | |

## Phase 1 后端：share 记录 + 公开路由

- DB：`deploy/init-postgres.sql`、`deploy/nuodu/init-postgres.sql` 两份全量建表定义与 `deploy/db_update.yaml` 存量库增量迁移同步新增 `task_result_shares`；核对运行时使用的实际迁移入口，不只修改初始化脚本。新增系统表同步登记到 `docs/system/database_system_table.md`；`deploy/cleanup` 类脚本不涉及
- `src/notifications/`（或 `src/services/task_result_sharing/`）：
  - `ensure_task_result_share_in_tx(cursor, task, runner)`：事务内核对归属、幂等创建/读回（`ON CONFLICT (task_id, runner_id) DO NOTHING`）
  - `publish_runner_terminal_in_tx`（producer.py scheduled 分支）在现有事务中调用上者；保留现有 `notification_ref` 返回契约，通过持久化映射供后续渠道读取 share_key
- 公开路由 `src/api/task_result_share.py`：
  - `GET /api/public/task-result-shares/{share_key}`（view：任务头 + 最终回复 alias 化 + `viewer_is_owner`；可选 Bearer）
  - `GET /api/public/task-result-shares/{share_key}/files/{alias}`（ticket 校验 + 重聚合定位 + 流式返回，CSP/Disposition 同 reply share）
  - 安全头沿用 `src/api/message_share.py`；view 在鉴权前按 IP 滑窗限流（现有 reply share 仅限制密码尝试），同步读库与鉴权使用线程池
- 实时读聚合函数：`scheduled_tasks` / `agent_runners` / `chat_messages`（`metadata::jsonb->>'runner_id'`）核对同一任务、租户、用户、会话与终态；最终回复筛除工具消息，仅文件结果无 assistant 消息且同 runner 持久 user 消息仍在时沿用 projected runner 口径实时读取 `result` / `public_snapshot`。复用 `reply_snapshot` 的字段投影与文件 alias 化，再以当前分享路径签发 ticket。任务/消息已清理或无可见结果返回 `TASK_GONE`
- 产物文件先验证 ticket，再按本次回复的 alias 重新定位，复用回复分享的 scoped file 解析及预览重写口径；公开响应不得包含内部文件 ID、session ID 或消息 ID
- 验证：创建幂等、聚合与 404 分支、仅文件结果、跨租户隔离、alias 确定性、ticket、viewer_is_owner、内部引用不出库；迁移在隔离库验证

## Phase 2 渠道切换

- 站内信：`repository._inbox_link` task.completed/failed（params 带 task_id）分支 → 免登录链接；`inbox()` 渲染处按通知的 `resource_ref.id`（runner_id）定位任务并批量惰性回填 share 记录；普通聊天终态保持旧链接
- IM webhook / 邮件：`events.scheduled_result_link` → 免登录链接，渲染时惰性创建 share 记录，失败回落旧链接并记脱敏日志；通知已送达的旧文案不回改
- web push：scheduled 终态 payload 增加同源相对路径 `share_url`，SW 存入 notification data 并在点击时优先使用；缺失回退现逻辑，校验相对路径形态，避免任意跳转
- 单测：三类渠道链接生成与回退分支；惰性回填幂等

## Phase 3 前端

- `shareRoutes.ts` 增 `/share/task-result/:share_key`；新页 `TaskResultSharePage.vue` 复用现有 `ReplyContent`、附件预览及 Base* 组件
- 先匿名读取 view 取得 tenant_id，再尝试对应 `saas_token_{tenant}` Bearer 与 `X-Tenant-Id` 重新读取（含平台管理员代管租户）；`viewer_is_owner=true` 自动跳转任务视图，其他情况只读渲染。登录入口以 `return_to` 回跳本页，404 显示失效文案
- 扩展登录回跳白名单 `notificationReturnUrl`，仅放行严格的任务结果分享路径并保留原 `/notification` 规则
- 验证：跳转判定、只读渲染、文件预览/下载、登录回跳；前端 build 和受影响页面窄屏检查

## Phase 4 汇总回归与验收

- 每个开发 Phase 执行开发自测 → 独立测试回归与启动安全检查 → 只读 CR；通过后由 CR 提交并推送本 Phase。Phase 4 汇总跨渠道回归与证据，不把已通过的相同代码状态机械重测
- agent2 真机：设计「测试与验收要点」全项走查（免登录打开/文件下载/自动跳转/非本人只读/任务删除失效）；所有环境的部署必须等用户明确指示“部署到 XX”，未授权时记录为待验收
- 兼容验证：只升级服务端，存量旧通知链接（web push/IM 已发出文案）仍可按原逻辑打开

## 风险与注意

- SW 更新异步生效：推送落地切换对存量客户端有延迟，属预期（回退逻辑保底）
- `chat_messages.metadata.runner_id` 依赖现有落库口径（models.py:1215），聚合查询需覆盖大 metadata 的解析性能（表达式索引如缺，评估补 `(metadata::jsonb)->>'runner_id'` 部分索引，仅 task 会话消息量级小）
- 无密码链接 = 持链接者可见：范围限定终态结果（非交互、非会话全量），暴露面可控；如后续要求加固再加任务级密码（表已预留 password_hash 加列空间，首期不加）
