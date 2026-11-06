# 定时任务结果免登录链接实施计划

设计：[design-task-result-share-link.md](../system/design-task-result-share-link.md)（决策已与用户逐条定稿 2026-11-06）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 后端：share 记录 + 公开路由 | 📋 待开发 | |
| Phase 2 | 渠道切换（web push / 站内信 / IM webhook） | 📋 待开发 | |
| Phase 3 | 前端：免登录结果页 | 📋 待开发 | |
| Phase 4 | 独立测试 + CodeReview + 真机验收 | 📋 待开发 | |

## Phase 1 后端：share 记录 + 公开路由

- DB：`deploy/init-postgres.sql`（254/243 两份）新增 `task_result_shares` 表；`deploy/cleanup` 类脚本不涉及
- `src/notifications/`（或 `src/services/task_result_sharing/`）：
  - `ensure_task_result_share_in_tx(cursor, tenant_id, user_id, task, runner)`：事务内幂等创建/读回（`ON CONFLICT (task_id, runner_id) DO NOTHING`）
  - `publish_runner_terminal_in_tx`（producer.py scheduled 分支）事务尾调用上者，返回 share_key
- 公开路由 `src/api/task_result_share.py`：
  - `GET /api/public/task-result-shares/{share_key}`（view：任务头 + 最终回复 alias 化 + `viewer_is_owner`；可选 Bearer）
  - `GET /api/public/task-result-shares/{share_key}/files/{alias}`（ticket 校验 + 重聚合定位 + 流式返回，CSP/Disposition 同 reply share）
  - 安全头 + IP 限流对齐 `src/api/message_share.py`
- 实时读聚合函数：scheduled_tasks / agent_runners / chat_messages（`metadata->>'runner_id'`）三段 join 与 404 语义（`TASK_GONE`）
- 单测：创建幂等、聚合 404 分支、alias 确定性、ticket、viewer_is_owner、内部引用不出库

## Phase 2 渠道切换

- 站内信：`repository._inbox_link` task.completed/failed（params 带 task_id）分支 → 免登录链接；`inbox()` 渲染处批量惰性回填 share 记录；普通聊天终态保持旧链接
- IM webhook / 邮件：`events.scheduled_result_link` → 免登录链接，渲染时惰性创建 share 记录，失败回落旧链接并记日志
- web push：scheduled 终态 payload data 增加 `share_url`；`task-notifications-sw.js` 优先 `share_url`、缺失回退现逻辑
- 单测：三类渠道链接生成与回退分支；惰性回填幂等

## Phase 3 前端

- `shareRoutes.ts` 增 `/share/task-result/:share_key`；新页 `TaskResultSharePage.vue`（复用 SharedReplyPage 渲染）
- 自动跳转（viewer_is_owner）、登录入口（`return_to` 回跳）、404 失效文案
- 单测：跳转判定、只读渲染、登录回跳

## Phase 4 独立测试 + CodeReview + 真机验收

- 三智能体流程：开发自测 → 独立测试回归 → CR
- agent2 真机：设计「测试与验收要点」全项走查（免登录打开/文件下载/自动跳转/非本人只读/任务删除失效）
- 兼容验证：只升级服务端，存量旧通知链接（web push/IM 已发出文案）仍可按原逻辑打开

## 风险与注意

- SW 更新异步生效：推送落地切换对存量客户端有延迟，属预期（回退逻辑保底）
- `chat_messages.metadata.runner_id` 依赖现有落库口径（models.py:1215），聚合查询需覆盖大 metadata 的解析性能（表达式索引如缺，评估补 `(metadata::jsonb)->>'runner_id'` 部分索引，仅 task 会话消息量级小）
- 无密码链接 = 持链接者可见：范围限定终态结果（非交互、非会话全量），暴露面可控；如后续要求加固再加任务级密码（表已预留 password_hash 加列空间，首期不加）
