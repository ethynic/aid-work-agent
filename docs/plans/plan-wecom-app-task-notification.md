# 长任务企业微信应用通知 — 开发计划

> 关联设计：[wecom-app-task-notification-design.md](../system/wecom-app-task-notification-design.md)
> ideas 条目：20261113-1126（docs/ideas.md 渠道集成分区）
> 流程：高风险（身份绑定 + 通知投递，跨通知核心与企微出站）→ 三智能体（开发 → 独立测试 → CR）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 绑定：users.wecom_userid 字段 + OAuth 绑定回调 + 手机号预填确认 + 解绑 | ✅ 完成（2026-11-13） | 4424f65c + a06c6f70（CR P2：state 绑定发起用户）；26 用例 + notifications 326 + UserDB 回归 283 全绿，vue-tsc 通过；真机 OAuth/企微外呼待部署清单 |
| Phase 2 | 触发判定：runner 终态 web 会话 + 时长阈值（默认 300s） | ✅ 完成（2026-11-13） | 446ceb05；口径 B：时长门禁仅 source=chat，scheduled 沿用既有通知不收缩；14 用例 + notifications 340 + agent_runner 577（3 失败为宿主机预存）；integration 真 PG 回归 18 skipped 待容器 |
| Phase 3 | 企微应用 Adapter：textcard 发送、errcode 分类、出站安全 | ✅ 完成（2026-11-13） | 0d0644a8（含 master 同步 8f4e750c）；Adapter+绑定登记接线（幂等 upsert，失败不回滚绑定、响应可见）+TerminalParameters.duration_seconds 时长渲染（其他渠道零影响有专项测试）；99 新增 + 434 回归全绿；integration 真 PG 用例待容器；CR P2 遗留：设计 §4.3 登记事务措辞待 Phase 4 文档修订同步 |
| Phase 4 | 通知设置页：企微通知卡片（绑定/开关/解绑）+ 点击回跳定位 | 🔧 进行中（开发+自测完成，待独立测试/CR） | 设置卡片（未绑定扫码/手机号确认绑定→已绑定开关+解绑；PC 二维码由 bind/url 返回 qr_data_url）+ /notification/wecom-bind 落地页 + GET /wecom/bind/status；偏好 wecom_app 接线核查（PreferenceRequest→registry 命中 WecomAppAdapter，available gate 专项测试）；点击回跳复用既有 /notification?ref= 链路（NotificationEntry + ChatContainer `_load_session`，不裸拼 _sid）；Phase 3 CR 遗留已同步：设计 §4.3 措辞改为「绑定落库成功后另起事务登记，失败不回滚绑定、响应 target_registered 可见」；wecom_bind 41 过 1 跳过（qrcode 库仅容器有）、notifications vitest 131 过、build 通过；页面视觉待浏览器人工走查 |
| Phase 5 | 回归测试 + agent2 真机验收 | 📋 待开发 | — |

前置（用户/运维）：企微管理端创建独立通知应用（agentid+secret）、可信域名与 OAuth 回调域名、可见范围；配置写入 `configs/config.yaml` `notifications.wecom_app`。

## Phase 1 — 身份绑定（设计 §4.1）

1. `users` 表加 `wecom_userid TEXT`（db_update.sql 增量；应用层 tenant+userid 唯一校验）
2. OAuth：绑定链接生成（企微内 oauth2 / PC 二维码 qr/connect，state 5 分钟）→ `/api/notifications/wecom/bind/callback` code 换 userid → 可见范围校验 → 落库+登记 target
3. 手机号兜底：`GET /api/notifications/wecom/bind/suggest` 按当前用户 phone 查企微通讯录（只读），前端确认后服务端落库
4. 解绑：清空 wecom_userid + 撤销 target

## Phase 2 — 触发判定（设计 §4.2）

1. `publish_runner_terminal_in_tx` 扩展：web 会话（source=chat & session_kind=web）且 `duration_seconds ≥ 配置阈值` 才发布 task.completed/failed
2. 配置：`notifications.wecom_task_min_seconds=300`（settings.py + config.yaml 同步）
3. 阈值判定在发布前（短任务不进通知表）；事件幂等 event_key 语义不变

## Phase 3 — 企微应用 Adapter（设计 §4.3）

1. `src/notifications/wecom_app.py` 实现 Adapter 协议，channel=`wecom_app`；目标配置 `{wecom_userid, bound_user_name, tenant_id}`
2. 独立通知应用配置节 token 缓存/刷新；textcard（touser 单 userid、title 会话名快照、description 耗时、url 通知回跳）
3. errcode 映射：40031/43004/81013→target_invalid（撤销+绑定失效）；限流→retryable；网络/5xx→有限重试
4. 出站安全复用 web_push.py 的 PinnedHTTPS + qyapi 域名白名单
5. dispatcher 注册 wecom_app adapter（后台 worker 启用投递）

## Phase 4 — 前端与回跳（设计 §4.4/§4.5）

1. 通知设置页新增"企微任务通知"卡片：绑定入口（OAuth 链接/PC 二维码）、手机号确认绑定、开关（target 注册/停用）、解绑、绑定状态展示
2. 点击回跳：`/notification?ref=...` 登录核验 → 定位原会话（ChatContainer 会话定位按 web push 设计 §5 实现，不裸拼 `_sid`）
3. 复用 Base* 组件与语义 token，不引入视觉改版；`cd frontend && npm run build`

## Phase 5 — 回归与真机验收

1. 单测：触发判定边界（299/300/301s、非 web 会话）、OAuth state/code 换 userid、suggest 只读、Adapter errcode 分类、解绑撤销
2. agent2 真机验收：OAuth 绑定 → >5 分钟任务收 textcard → 点击回原会话；<5 分钟不推；解绑后不推；旧客户端兼容（只升级服务端组合）
3. 收尾：进度表、ideas.md 状态、`文档: docs/plans/plan-wecom-app-task-notification.md`
4. 部署由用户当场指示
