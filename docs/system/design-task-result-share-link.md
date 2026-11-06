# 定时任务结果免登录链接设计（任务通知链接免登录化）

## 背景与问题

定时任务（scheduled task）终态通知的链接 `/notification?ref=...&tenant=...` 是**登录态定位符**：后端 `resolve` 按收件人 `recipient_user_id` + scope + 租户严格过滤（src/notifications/repository.py:768），带来两个实际问题：

1. **本人打开也可能 404**：同名多账号 + 换设备换登录路径时，登录进去的 user_id 与通知收件人不一致（2026-11-06 agent2 案例：平台管理员账号建任务、租户同名账号点链接，数据全在却 404）。
2. **未登录无法直接看结果**：手机/新设备点推送必须先登录，体验断裂。

Web 会话已有**回复分享**功能（免登录只读渲染，src/services/message_sharing/），本设计把同等免登录能力用于定时任务终态通知。

## 已确认的设计决策（2026-11-06 与用户逐条定稿）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 产品形态 | 免登录只读结果页 + 页内登录入口（复用分享实现） |
| 2 | 改造范围 | **仅定时任务终态通知**（task.completed / task.failed）；paused / needs_action / manual_rejected 等需交互类保持现有登录入口 |
| 3 | 安全门槛 | 无密码，链接即凭证（share_key 24 字节 URL-safe token，同分享） |
| 4 | 有效期 | **跟随任务生命周期**：任务（含其会话消息）删除即失效，不设时间过期 |
| 5 | 内容范围 | 任务头（任务名/状态/完成时间）+ 本次执行最终回复全文 |
| 6 | 已登录行为 | 本人登录态自动跳完整任务视图；未登录 / 非本人停留只读页 |
| 7 | 技术方案 | **免登录实时读**（不快照）：share_key 只存映射，公开路由实时聚合 |
| 8 | 渠道替换 | web push 点击落地、站内信 link、IM webhook 文案链接**三处全换** |
| 9 | 文件下载 | 免登录页产物文件可直接预览/下载，复用分享 ticket 机制 |
| 10 | 存量切换 | 立即全量切换；已发出的 web push / IM 文案已固化不受影响；旧 `/notification` 入口保留（其他事件类型仍用） |

## 与回复分享的关系

**复用**：share_key 生成方式、公开路由模式（安全头 + IP 限流）、download ticket 机制、SharedReplyPage 只读渲染组件、reply_snapshot 的文件 alias 化思路。

**不复用**：`chat_message_shares` 表与快照存储（实时读无需快照，避免第二份数据与一致性负担）；密码（本次决策无密码）。

## 数据模型

新表 `task_result_shares`（实时读只存映射，不入库内容）：

```sql
CREATE TABLE IF NOT EXISTS task_result_shares (
    share_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    runner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, runner_id)
);
```

- `UNIQUE (task_id, runner_id)`：同一执行重复生产通知幂等（同一 share_key），对齐通知 `event_key='terminal:{runner_id}'` 的幂等口径。
- 不设 `expires_at`：有效期语义由实时读的存活校验承担（见下）。
- 孤儿行（任务删除后残留的映射行）首期不做清理：行数 = 定时任务执行通知数，量小且不泄漏内容；后续如需可在任务删除路径顺带删除。

## 链接与公开路由

- 免登录链接：`{app.public_base_url}/share/task-result/{share_key}`（不带 tenant 参数，归属由 share_key 记录提供）。
- 前端路由挂 `shareRoutes.ts` 免登录路由组：`/share/task-result/:share_key` → `TaskResultSharePage.vue`（渲染复用 SharedReplyPage 的回复渲染部分）。
- 公开 API（新 router，建议 `src/api/task_result_share.py`）：
  - `GET /api/public/task-result-shares/{share_key}`（view；可选 `Authorization` 头）
  - `GET /api/public/task-result-shares/{share_key}/files/{alias}?ticket=&download=`
- 安全头与限流：安全头与 reply share 同款（`Cache-Control: no-store` / `Referrer-Policy: no-referrer` / `X-Robots-Tag: noindex, nofollow` / `X-Content-Type-Options: nosniff`）；view 在鉴权前按 IP 滑窗限流，复用 `SlidingWindowRateLimiter`（无效 Bearer 仍受限；现有 reply share 仅限制密码尝试）。

## share_key 生命周期

- **生成**：`publish_runner_terminal_in_tx`（producer.py scheduled 分支）生产通知的**同一事务内**惰性创建（`ON CONFLICT (task_id, runner_id) DO NOTHING` 后读回）。保持该函数现有 `notification_ref` 返回契约；后续渠道从映射查询 share_key。与通知同事务保证"通知存在则链接存在"。
- **存量通知回填**：站内信 link 与 IM webhook 文案均为**渲染时**动态生成——渲染路径按通知资源（task_id + runner_id）查 `task_result_shares`，缺失则惰性补建（UNIQUE 幂等）。已发出的 web push / IM 文案固化在投递产物中，不回改。
- 外发通知的 adapter 在 `NotificationRepository.expand()` 持有消息锁的事务内渲染；回填沿用同一 cursor，savepoint 只包可回退的映射补建，把 share_key 临时放入渲染消息，不在 adapter 内另开数据库连接。站内信列表在自身连接内批量回填并提交。回填失败仅记异常类型并回落旧链接。
- **失效**：不主动删记录；实时读 join 校验失败即 404（见下）。通知消息自身 24h TTL 与本链接无关（通知过期 ≠ 链接失效）。
- **范围外事件**：paused / needs_action / manual_rejected / 普通聊天终态不创建 share，链接维持 `/notification?ref=`。

## 免登录实时读

### view 流程（单事务）

1. 按 `share_key` 查 `task_result_shares`；不存在 → 404 `SHARE_NOT_FOUND`。
2. join `scheduled_tasks`（task_id 存活 + user_id/租户一致）；不存在 → 404 `TASK_GONE`「任务已删除或链接已失效」。
3. join `agent_runners`（runner_id 与任务 session_id、tenant_id、user_id 一致，`source='scheduled'`，`status IN ('completed','failed')`，`session_kind='web'`）；不存在 → 404 `TASK_GONE`。
4. 读本次执行最终回复：优先取 `chat_messages WHERE session_id=%s AND (metadata::jsonb)->>'runner_id'=%s AND role='assistant'`，筛除 `tool_calls` / `tool_call_id` / `wait_id` 消息，按创建时间及 id 降序取最终回复。仅文件结果本来可能不落 assistant 消息，此时须确认该 runner 的持久 user 消息仍在，才沿用现有回复分享的 projected runner 口径，从同一已验证 Runner 的 `result` / `public_snapshot` 实时投影文件结果。文本/图片 assistant 消息缺失、user 消息已清理或两处均无可见结果均返回 404 `TASK_GONE`。
5. 组装响应：
   - 任务头：`task_name`（scheduled_tasks.name）、`task_status`、`runner_status`、`finished_at`、`tenant_id`、`task_id`、`runner_id`；
   - 回复体：按 reply_snapshot 同款字段口径（content + images + attachments + downloadableFiles），**内部引用全部 alias 化**（见下）；
   - `viewer_is_owner`：请求带 `Authorization` 且解析成功（`fresh_web_subject`，失败静默按匿名），user_id == 记录 `user_id` 且租户匹配 → `true`。普通用户以认证主体 tenant_id 为准；平台管理员代管租户时须请求 `X-Tenant-Id` 与记录 tenant_id 一致，不能仅因 user_id 相同跨租户判本人。
6. **不落库快照、不存 files 映射**：alias 按提取顺序确定性生成（`asset_N`），同一数据重算稳定；`files/{alias}` 请求重跑步骤 1-4 + 提取后按 alias 定位。每次文件请求多几条主键查询，任务结果页文件数少，可接受（设计取舍：零额外存储 vs 轻微重复计算）。

### 内部引用不出库（安全契约，对齐 reply share）

- `content` 中内部 `/api/files/{id}` / `file_id:` 引用与 `metadata` 的 images / attachments / downloadableFiles 全部改写为 `share-file:{alias}`；
- 响应中出现的文件 URL 一律是公开路由 + ticket 形态；内部 `file_id`、`session_id`、消息 ID 不出现在响应；
- `files/{alias}`：先校验 ticket（`issue_download_ticket(base=share_path, ttl=12h)`，同 reply share），无效时不触发重聚合或文件扫描；有效后重聚合定位 alias → `resolve_scoped_uploaded_file` → 流式返回，CSP sandbox / Content-Disposition 同 reply share。

## 前端页面行为（TaskResultSharePage.vue）

- `onMounted`：先匿名调 view 取得 `tenant_id`，读取本地 `saas_token_{tenant}`（如有）后再带 Bearer 与 `X-Tenant-Id: {tenant}` 调 view 接口，以判定本人身份（平台管理员代管租户也适用）；匿名结果可先展示。
- `viewer_is_owner=true` → `router.replace(/t/{tenant}/tasks/{task_id}?runner_id={runner_id})`（完整任务视图）。
- 否则渲染任务头 + 只读回复 + 文件列表；「登录查看完整任务」按钮 → `/t/{tenant}/login?return_to=/share/task-result/{share_key}`，登录回到本页后走同一自动跳转逻辑。
- 当前登录页的 `notificationReturnUrl` 只接受 `/notification`；需扩展白名单为严格的 `/share/task-result/{share_key}` 规范路径，拒绝其他外部 URL 与任意 query，避免登录后回跳丢失或开放重定向。
- 404 → 「任务已删除或链接已失效」+ 返回登录按钮。

## 渠道切换明细（三处全换）

| 渠道 | 现状 | 改造 |
|------|------|------|
| web push | SW `task-notifications-sw.js:59` 点击落地 `/notification?ref=...&tenant=...`（payload data 构造） | scheduled 终态 payload data 增加 `share_url`（后端发送时构造免登录链接）；SW 优先用 `share_url`，缺失回退现逻辑（兼容存量队列中的推送，SW 更新是异步生效） |
| 站内信 | `repository._inbox_link` 对 task.completed/failed 生成 `/notification?ref=` | 该分支改为免登录链接；渲染时批量惰性回填 share 记录（inbox 列表 limit ≤100，一次查询/批建可承受）。普通聊天终态（无 task_id）保持旧链接 |
| IM webhook / 邮件 | `events.scheduled_result_link` 生成 `/notification?ref=` 文案链接 | 改为免登录链接；渲染时惰性创建 share 记录，失败回落旧登录链接并记日志（不阻塞投递） |

## 数据库落地与兼容回滚

- 新建库的 `deploy/init-postgres.sql` 与 `deploy/nuodu/init-postgres.sql`、存量库的 `deploy/db_update.yaml` 均需包含新表；系统表目录同步登记。

- 旧 `/notification?ref=` 入口与解析逻辑**不动**（paused/needs_action/manual_rejected、普通聊天终态、存量旧 web push 链接仍走原路）。
- 新表独立、内容零迁移；回滚 = 关闭新链接生成逻辑，无数据破坏。
- 不触碰 Runtime / CLI 工具、任务恢复、结果上报、计费链路——已交付旧客户端兼容性不受影响。

## 测试与验收要点

- 单测：share 事务内创建幂等（同 runner 重复通知同 key）；实时读聚合正常 / 任务删除 / runner 非终态 → 404；仅文件结果未落 assistant 消息仍可读，消息与 Runner 投影均无可见结果 → 404；alias 确定性；ticket 校验；`viewer_is_owner` 判定（本人 / 非本人 / 无效 token）。
- 渠道：站内信 link（completed/failed 带 task_id → 新链接；普通聊天终态 → 旧链接）；IM webhook 文案；SW payload `share_url` 优先级与回退。
- 真机（agent2）：免登录打开只读页 → 产物文件预览/下载 → 本人登录态自动跳任务视图 → 非本人停留只读页 → 任务删除后链接失效提示。

## 实施计划

见 [plan-task-result-share-link.md](../plans/plan-task-result-share-link.md)。
