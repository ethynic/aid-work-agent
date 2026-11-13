# 长任务企业微信应用通知 — 设计文档

> 设计日期：2026-11-13
> 状态：设计定稿（6 项决策已逐条与陆伟确认），待开发
> 开发计划：[plan-wecom-app-task-notification.md](../plans/plan-wecom-app-task-notification.md)
> 需求来源：飞书任务 #100（BOSS直聘爬虫结束后跳出对话页改通知）；上位设计：[统一通知基础设施](notification-infrastructure-design.md)、[Web 任务推送](web-task-push-notification-design.md)（本设计为其投递渠道扩展，web push 渠道保留代码、默认停用）

---

## 一、背景

BOSS 直聘爬虫等长任务在 web 控制台执行，完成后当前实现会跳出租户正在使用的对话页（干扰客服工作）。改为：任务完成/失败时经**企业微信应用消息**主动通知发起用户，点击回到原会话；用户不在线页面自然不跳。

## 二、生产事实（2026-11-13 调查）

- 通知基础设施成熟：`notification_messages` 表 + Adapter 协议（现有 web_push/im_webhook/email）+ targets 注册 API + 后台 dispatcher；新增渠道 = 新增 Adapter，核心不动。
- Runner 终态接线已存在：`event_repository.py:95` 事务尾调 `publish_runner_terminal_in_tx`，`task.completed/task.failed` 事件限定 web 渠道会话已发布。
- `web-task-push-notification-design.md`（2026-10-10）已完成 Web Push 生产者接线与点击回会话流程设计；仅投递渠道（Google/Mozilla 推送服务）国内不可达，真机到达未验收——本设计替换其投递通道，复用其生产者、核验与回跳语义。
- 生产 `users` 表无企微 userid 字段（仅 wx_openid/wx_unionid 公众号身份），绑定为本期新建。
- 企微发送能力已有：`src/channels/wecom/adapter.py` 的 `message/send`（touser、token 刷新、errcode 重试）可作实现参考；通知走独立应用配置，不复用渠道应用。

## 三、设计决策（2026-11-13 逐条确认）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 主渠道 | 企微应用消息，一对一（touser 单 userid）；webpush 保留代码默认停用 |
| 2 | 身份绑定 | 企微 OAuth 授权为主 + 手机号匹配预填确认兜底；设置页可见可解绑 |
| 3 | 触发条件 | 仅 web 渠道会话 且 执行时长 ≥ 5 分钟（可配）；不做页面在线判断 |
| 4 | 消息形态 | textcard：标题=会话名+任务完成/失败，描述=执行耗时；点击跳控制台会话页 |
| 5 | 发送通道 | 企微后台独立通知应用（独立 agentid+secret），与渠道对话应用分开 |
| 6 | 开启方式 | 用户主动开启+绑定后才推；不补发历史任务；全租户通用 |
| 7 | 应用配置归属 | **租户自备**（2026-11-13 变更，用户否决平台全局配置）：每个租户的企微通知应用凭据互不相同，且后续钉钉/飞书同模式。租户管理员在「连接中心 → 渠道配置」新增「企业微信应用通知」渠道类型（channel_type=`wecom_app_notify`，选择即启用），输入 corp_id/agentid/secret 三项保存（secret 加密落库、回显脱敏，保存即可用不做主动验证）；运行时按 target 所属租户读取配置发送，未配置租户不可绑定/不投递。通知目标 channel 保持 `wecom_app` 不变，redirect_uri 仍为平台全局站点域名 |

## 四、详细设计

### 4.1 身份绑定（决策 2）

`users` 表新增 `wecom_userid TEXT`（可空，唯一性按 tenant+userid 应用层校验）。绑定入口在通知设置页：

- **主路径 OAuth**：生成企微 OAuth 授权链接（`open.weixin.qq.com/connect/oauth2/authorize`，应用内打开；PC 端展示二维码链接 `qr/connect`）。回调 `/api/notifications/wecom/bind/callback`：code → `gettoken` + `getuserinfo` 换 userid → 校验成员在通知应用可见范围 → 落库当前登录用户的 `wecom_userid`。state 参数防 CSRF，5 分钟有效期。
- **兜底手机号预填**：绑定页调 `GET /api/notifications/wecom/bind/suggest`——服务端按当前用户 phone 匹配企微通讯录（`user/get` by mobile，仅查不写），命中则展示"识别到企微账号：{姓名}，确认绑定？"，用户点确认后以服务端换取的 userid 落库（同样走服务端核实，不信任前端输入）。
- **解绑**：设置页解绑按钮清空 `wecom_userid` 并撤销对应通知 target。
- 绑定状态展示：已绑定显示企微姓名（缓存于 target 配置），未绑定显示绑定入口。

### 4.2 触发判定（决策 3）

在既有 `publish_runner_terminal_in_tx`（`src/notifications/producer.py:161`）扩展判定，不改变通知核心：

- 发布范围为 `session_kind=web` 的用户自有 runner（沿用 web push 设计的接入范围口径，来源含 chat 与 scheduled）；
- 时长门禁：`source=chat` 时要求 `duration_seconds ≥ wecom_task_min_seconds`（配置项，默认 300）；
- **时长门禁仅作用于 `source=chat`**（2026-11-13 用户拍板口径 B）：scheduled（定时任务）沿用既有通知行为不按时长门禁——定时任务常在用户不在页面时完成，短定时任务的完成通知恰是通知价值场景，不收缩已交付行为；
- 两条件同时满足才发布 `task.completed/task.failed` 事件（复用现有事件与幂等 event_key；阈值判定在发布前，避免短任务空进通知表）。
- 砍掉页面在线/心跳判定（决策 3 修正）：不做 presence，不引入前端心跳。

### 4.3 企微应用 Adapter（决策 1/4/5）

新增 `src/notifications/wecom_app.py`，实现既有 `Adapter` 协议，channel=`wecom_app`：

- **目标配置**：`{wecom_userid, bound_user_name, tenant_id}`；`validate_target` 校验 userid 格式。目标注册 = 绑定落库成功后另起事务登记（失败不回滚绑定，响应 `target_registered` 可见），非用户手填。
- **发送**：独立通知应用的 `corp_id + agentid + secret`——~~新配置节 `notifications.wecom_app`~~（2026-11-13 决策 7 变更：全局配置节已删除）改为**按 target 所属租户**读租户渠道配置（`tenant_channel_configs`，channel_type=`wecom_app_notify`，连接中心「企业微信应用通知」，secret 加密落库）；adapter 仍按 channel 全局单例注册，但可用性与发送凭据按租户延迟解析（`available_for_tenant`/`credentials_for_tenant`，凭据每次实时读库——租户保存配置立即生效；token 按租户缓存），token 过期按 40014/42001 刷新重试一次参考渠道实现；`message/send` msgtype=`textcard`，`touser=单 userid`，`url=控制台通知回跳地址`。
- **文案**：title=`{会话名称}相关任务已完成/执行失败`（会话名快照与清洗口径沿用 web push 设计 §5）；description=`任务已执行 {时长}，点击查看结果`；不含简历内容、回复正文等业务数据（脱敏口径同既有设计）。
- **结果规范化**：errcode 映射 SendResult——0 成功；40031/43004/81013（userid 不存在/无权限/不在可见范围）→ target_invalid（撤销目标并标记绑定失效，引导重新绑定）；42009/45009 限流 → retryable；网络/5xx → 有限重试。每个目标独立 delivery 记录（通知核心既有机制），保证一对一可追溯。
- **出入站安全**：复用 `web_push.py` 的 PinnedHTTPSConnection/域名白名单模式，固定 qyapi.weixin.qq.com。

### 4.4 点击回跳（决策 4）

复用 web push 设计已定流程：textcard url 指向 `/notification?ref=...` → 登录核验 `GET /api/notifications/messages/{ref}` → 定位原租户/会话 → 跳转原会话页。前端 `ChatContainer.vue` 的已有会话定位逻辑按 web push 设计 §5 的要求实现（不裸拼 `_sid`）。

### 4.5 开启与偏好（决策 6）

- 通知设置页（复用既有 web push 开关页面骨架）新增"企微任务通知"卡片：未绑定→绑定流程；已绑定→开关（target 已随绑定登记；启用=启用偏好，关闭=停用偏好，解绑=撤销 target）。
- 开启后新接单任务才纳入；不补发历史（沿用 web push 设计口径）。
- 企微应用可见范围即天然开关：不在范围内的成员 OAuth/匹配绑定直接失败并提示。

### 4.6 配置与运维

- ~~`configs/config.yaml` + `src/config/settings.py` 新增：`notifications.wecom_app.{corp_id, agentid, secret, enabled}`~~（2026-11-13 决策 7 变更：该全局节已删除——通知应用凭据为租户自备，租户管理员在「连接中心 → 渠道配置」保存，见决策 7 与 §4.3）。保留平台全局配置：`notifications.wecom_task_min_seconds=300`（时长阈值，平台行为参数非租户凭据）、通知回跳域名（复用站点域名 `app.public_base_url`，含绑定 OAuth redirect_uri）。
- 企微管理端配置改由各租户自助（原「一次性平台配置」口径作废）：每个租户在企业微信后台创建自己的独立通知应用（corp_id/agentid/secret）、配置可信域名与可见范围，凭据填入连接中心渠道配置；成员绑定入口与 OAuth 回跳域名仍由平台站点域名承载。
- 观测：投递走通知核心 delivery 台账；失败原因分类可见（未绑定/不在范围/发送失败/租户未配置）。

## 五、明确不做

- 不做页面在线心跳/presence（决策 3 修正砍掉）。
- 不做钉钉/飞书工作通知渠道（同模式后续可加，接口已通用）。
- 不做过程进度推送、任务取消/结算提醒（沿用 web push 设计首期口径）。
- 不动 Desktop/Runtime 与渠道对话链路；web push 渠道代码保留、默认停用。

## 六、风险与验证

| 风险 | 缓解 |
|---|---|
| 企微后台配置（可信域名/回调）遗漏致 OAuth 失败 | 部署文档列运维清单；绑定失败页展示具体 errcode 提示 |
| 用户换号/离职后 touser 失效 | errcode 40031/81013 自动撤销目标并标记绑定失效 |
| 5 分钟阈值误伤/误放 | 配置化；上线后按投递台账与用户反馈调整 |
| 通知与渠道对话混淆 | 独立通知应用（独立 agentid），用户可单独设置 |

验证：
- 单测（`./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q`）：触发判定（时长阈值边界/非 web 会话不推）、OAuth 回调换 userid 与 state 校验、手机号预填只读不写、Adapter errcode 分类映射、解绑撤销 target。
- 真机验收（agent2 测试机打通，用户安排真实企微账号）：绑定 OAuth 全流程 → 发起 >5 分钟任务 → 收到 textcard → 点击回原会话；<5 分钟任务不推；解绑后不再推；"只升级服务端"组合不影响旧客户端。
