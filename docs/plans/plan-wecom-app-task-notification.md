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
| Phase 4 | 通知设置页：企微通知卡片（绑定/开关/解绑）+ 点击回跳定位 | ✅ 完成（2026-11-13） | 2fdf51c2；设置卡片+落地页+status 端点+偏好接线；后端 424+前端 vitest 131+build 全绿；测试棒修 401 死代码（err.status）；CR 仅 P3 卫生项；视觉走查待人工 |
| Phase 5 | 回归测试 + agent2 真机验收 | 🔧 进行中（组合回归完成，待真机） | notifications+db 480 过；agent_runner 577 过（3 失败宿主机预存已基线核实）；前端全量 915 过（12 失败与 master 逐字节同集，预存）；integration 真 PG 与真机验收待部署环境（由用户指示） |
| 评审回炉（外部 CR 5 项） | P1 存量库缺列/卡片 URL 缺 tenant/扫码域名与二维码语义；P2 跨租户绑定冲突+登记失败显示/waiting_input 绕过时长门禁 | ✅ 完成（2026-11-13） | 6df89112；5 项均属实（主控者逐项源码核实，含 404 域名实测复现）；notifications 429 + 前端 136 + build 全绿；integration 真 PG 跨租户用例待隔离库环境 |
| 复审回炉（外部 CR 4 项） | 同租户接手死锁（revoked 行无条件 409）/绑定上下文单键双标签覆盖/平台管理员回调登记 global/integration 用例读未透出字段 KeyError | 🔧 进行中（复审通过，integration 真 PG 与真机回归待环境） | repository 接手语义+冲突收紧、state 锁定发起租户（BindStateStore+X-Tenant-Id）、上下文按 state 分键、integration 归属改库直查+新增接手用例；notifications 434 + 前端 140 + build 全绿（CR 复跑核验）；integration 真 PG 未跑（本机缺隔离库环境，非代码）。复审遗留登记后续优化：①无 active 行时多 revoked 行版本错位可致重绑登记 BINDING_CHANGED（P2：绑定成功但 target_registered=False，UI 不可自愈，登记侧按 digest 行取版本或非 active 放宽版本钉扎可解）；②有 active 行切换身份须先解绑（BINDING_CONFLICT 语义边界，解绑重绑可恢复）；③wecom-bind-context:${state} 孤儿键累积（P3 卫生，可在 startBind 顺带清理）；④state 值格式变更使升级窗口内存量 state（TTL 5 分钟）失效一次，重发即可，确认可接受 |

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
