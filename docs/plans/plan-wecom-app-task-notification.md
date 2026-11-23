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
| E2E 本地验证（2026-11-13） | 绑定/开启/长任务推送/短任务拦截全链 | ✅ 本地通过 | localhost 容器实测：手机号绑定（waynelu）→ 偏好开启 → 数据分析长任务完成 → 企微收到卡片（delivery=accepted）；短任务零通知；澄清提问不推送（白名单生效）。过程中修复 suggest 接口路径错误（get_userid_by_phone→官方 user/getuserid，95402）与阈值临时调整（60s，已回滚 300 待重启） |
| 评审回炉（外部 CR 5 项） | P1 存量库缺列/卡片 URL 缺 tenant/扫码域名与二维码语义；P2 跨租户绑定冲突+登记失败显示/waiting_input 绕过时长门禁 | ✅ 完成（2026-11-13） | 6df89112；5 项均属实（主控者逐项源码核实，含 404 域名实测复现）；notifications 429 + 前端 136 + build 全绿；integration 真 PG 跨租户用例待隔离库环境 |
| 复审回炉（外部 CR 4 项） | 同租户接手死锁（revoked 行无条件 409）/绑定上下文单键双标签覆盖/平台管理员回调登记 global/integration 用例读未透出字段 KeyError | 🔧 进行中（复审通过，integration 真 PG 与真机回归待环境） | repository 接手语义+冲突收紧、state 锁定发起租户（BindStateStore+X-Tenant-Id）、上下文按 state 分键、integration 归属改库直查+新增接手用例；notifications 434 + 前端 140 + build 全绿（CR 复跑核验）；integration 真 PG 未跑（本机缺隔离库环境，非代码）。复审遗留登记后续优化：①无 active 行时多 revoked 行版本错位可致重绑登记 BINDING_CHANGED（P2：绑定成功但 target_registered=False，UI 不可自愈，登记侧按 digest 行取版本或非 active 放宽版本钉扎可解）；②有 active 行切换身份须先解绑（BINDING_CONFLICT 语义边界，解绑重绑可恢复）；③wecom-bind-context:${state} 孤儿键累积（P3 卫生，可在 startBind 顺带清理）；④state 值格式变更使升级窗口内存量 state（TTL 5 分钟）失效一次，重发即可，确认可接受 |
| 三轮回炉（外部复审 3 项） | profile 遮蔽/换绑死循环/集成用例断言 | ✅ 完成（2026-11-13） | ab8f45c4；钉扎仅 active 行生效（非激活跳过，advisory 锁+锁后判定闭合并发边界）；profile active 优先排序；接手/换绑 integration 真 PG 用例改按状态断言。单测 439（红先验证）+ 真 PG 隔离库 wecom 5 用例独立复现全绿；logout 用例失败为 master 预存签名漂移（初始提交即 4 参） |
| 四轮回炉（外部复审 1 项：Web Push 回归） | stale 请求复活用户刚关闭的浏览器通知 | ✅ 完成（2026-11-13） | dfd527c1；register_target 增 identity_preverified 门控（默认 False=web_push 完全恢复回归前语义，wecom_bind 传 True 保留企微接手/换绑/跳钉扎）；红先 3 用例复现回归后转绿，445 passed；CR 独立复现红先+与 2fdf51c2 基线逐分支等价；integration 真 PG 待隔离库环境 |
| 五轮：租户级配置改造（用户否决平台全局配置） | 企微应用通知凭据从全局 `notifications.wecom_app` 改为租户自备（连接中心渠道配置 wecom_app_notify） | 🔧 进行中（开发+单测+独立测试+CR 完成，integration 真 PG 与真机验收待环境） | 详见下方「五轮」节；notifications 460 + channel_config_db 79 + task_result_sharing 回归 19 = 558 全绿（CR 独立复跑一致）；前端 build + vitest notifications 140 全绿；E2E 旧全局配置口径作废（后续教程走租户配置页）；integration 真 PG 与真机验收待环境 |
| 六轮：企微应用与通知配置合并（2026-11-15，设计 §3 决策 7 追加） | 「企业微信」应用配置加「通知用途」三模式（notify_usage），通知凭据两级回退借用 | 🔧 进行中（开发+单测完成，独立测试/CR 待走） | 详见下方「六轮」节；notifications+channels+channel_config_db+factory+api 用例 1555 过（7 失败经基线核实为 master 预存，与本轮无关）；前端 build + vitest notifications 140 全绿 |
| 七轮：飞书通知接入（2026-11-15，设计 §3 决策 7 追加） | 飞书通知完全复刻企微租户级模式：飞书渠道通知用途三模式 + 手机号识别绑定（无 OAuth）+ FeishuAppAdapter | 🔧 进行中（开发+单测完成，独立测试/CR 待走） | 详见下方「七轮」节；notifications/channels/factory/db/api 单测全绿（channels 5 失败为宿主机预存环境项：Redis 禁用致 idempotency 2 + kf_account_referral 3，与本轮无关）；前端 build + vitest notifications 148 全绿 |
| 八轮：钉钉通知接入（2026-11-16，设计 §3 决策 7 追加） | 钉钉通知完全复刻飞书租户级模式：钉钉渠道通知用途三模式（notify_only 需 app_key/app_secret/agent_id 三项）+ 手机号识别绑定（无 OAuth）+ DingTalkAppAdapter（asyncsend_v2 action_card） | 🔧 进行中（开发+单测完成，独立测试/CR 待走） | 详见下方「八轮」节；notifications+channel_config_db+usage_validation+factory 单测 822 全绿；前端 build + vitest notifications 157 全绿 |
| 九轮：仅通知保存保留回调凭据 + 指引修正（2026-11-23） | 存量企微/飞书/钉钉应用改选「仅通知」保存不再清除库内回调字段，改回免重填；仅通知下接入指引去掉回调配置步骤 | ✅ 完成（2026-11-23，待合并与部署） | 详见下方「九轮」节；独立验证通过：后端 37 过、回归 927 过、前端 5 过、build 通过，审查无 P0/P1；本地真实业务库复验三类渠道回切 200 |

## 五轮：租户级配置改造（2026-11-13，设计 §3 决策 7）

- 背景：每个租户的企微通知应用凭据互不相同，且后续钉钉/飞书同模式复用；全局 `notifications.wecom_app` 节删除（`wecom_task_min_seconds` 保留在 NotificationsConfig——平台行为参数非租户凭据；redirect_uri 保留平台站点域名 `app.public_base_url`）。
- 渠道配置：`tenant_channel_configs` 新类型 `wecom_app_notify`（corp_id/agentid/secret 三项，选择该类型即代表启用；secret 复用 wechat_mp codec Fernet 加密落库+掩码回显+不可清空保护；保存即可用，不做主动验证），API 白名单与必填校验同步；通知目标 channel 保持 `wecom_app` 全链不动。
- 运行时：WecomAppAdapter 增 `available_for_tenant/credentials_for_tenant`（凭据实时读库——租户保存配置立即生效；access_token 按租户缓存），全局 `available()` 恒 False fail-closed；repository 新增 `provider_available(adapter, tenant_id)` 统一判定，expand/_append_delivery/prepare_send/register_target/set_preference/test/tenant_test 及 wecom_bind（bind/status/suggest/confirm/url 按 subject 租户）全部租户感知。
- 前端：ChannelConfig.vue 新增「企业微信应用通知」类型（corp_id/agentid/secret，secret 密码框+掩码回显；无回调地址、无验证连接、不绑定数字员工）。

前置（用户/运维，五轮更新）：各租户管理员在企业微信后台创建本租户的独立通知应用（agentid+secret）、配置可信域名与可见范围，凭据填入连接中心「企业微信应用通知」渠道；OAuth 回跳域名仍由平台站点域名承载。E2E 按旧文档准备的 `notifications.wecom_app` 全局配置作废。

## 六轮：企微应用与通知配置合并（2026-11-15，设计 §3 决策 7 追加）

- 背景：连接中心「企业微信」（agent 对话接入）与「企业微信应用通知」（wecom_app_notify，通知专用）两套配置并存；通知仅发生在 web 渠道聊天（producer 已判定，企微渠道聊天无通知），合并无体验负担。
- 渠道配置（src/saas/api/channel_config.py）：wecom 类型 config 新键 `notify_usage`（`agent_only` 缺省 / `both` / `notify_only`，非法值 400）；必填校验按用途分支——notify_only 仅 corp_id/agent_id/secret 三项（纯服务端 API 无回调，token/encoding_aes_key 免填），agent_only/both 保持原 5 项；create/update 同口径。
- 渠道工厂（src/saas/services/channel_factory.py）：`_load_config` 跳过 notify_only 的 wecom 配置（不实例化对话渠道、不接回调，回调路由走既有 404 分支）；`notify_usage` 作为配置元键在 adapter 构造前剔除（verify 直连路径同样覆盖）。
- 凭据回退链（src/notifications/wecom_app.py）：`_default_credentials_source` 改两级回退——① wecom_app_notify 最新一条（存量兼容优先，凭据不全继续回退）② wecom 且 notify_usage ∈ {both, notify_only} 最新一条（借用发送，agent_id→agentid 映射；wecom 类型 secret 明文存储）③ 均无 → None；新增 ChannelConfigDB.get_latest_wecom_notify_usage_decrypted（SQL 参数化）。
- 前端（ChannelConfig.vue）：企微应用表单加「通知用途」单选（notify_only 联动隐藏回调字段/回调地址/关联数字员工，保存时剔除回调字段旧值）；「企业微信应用通知」从新增入口隐藏（存量行保留展示与编辑，编辑弹窗提示迁移）。
- 存量行为零变更：agent_only 缺省、wecom_app_notify 优先、既有 wecom 5 项必填不变。

## 七轮：飞书通知接入（2026-11-15，设计 §3 决策 7 追加）

- 背景：飞书任务通知完全复刻企微租户级模式（渠道用途三模式 + 手机号绑定 + 通知适配器）；本期无 OAuth，绑定仅手机号识别 + confirm 服务端二次核实，无绑定弹窗。
- DB：`users` 加 `feishu_open_id TEXT`（db_update.yaml 幂等块 + deploy/init-postgres.sql + deploy/nuodu/init-postgres.sql 三处同步，照 wecom_userid 先例）；UserDB 增 set_feishu_open_id / get_by_feishu_open_id_in_tenant（IS NOT DISTINCT FROM 兼容平台管理员）。
- 渠道配置（src/saas/api/channel_config.py）：`_wecom_notify_usage/_wecom_required_fields_missing` 泛化为 `_notify_usage/_required_fields_missing`（wecom/feishu 同口径）；feishu notify_only 仅 app_id/app_secret 两项必填（verification_token/encrypt_key 免填），agent_only/both 保持原必填（app_id/app_secret/verification_token，encrypt_key 本就可选）。
- 渠道工厂（src/saas/services/channel_factory.py）：`_is_wecom_notify_only` 泛化 `_is_notify_only`（feishu notify_only 同样跳过对话渠道实例化/回调）。
- 凭据源（src/saas/db/channel_config_db.py + src/notifications/feishu_app.py）：新增 ChannelConfigDB.get_latest_feishu_notify_usage_decrypted（SQL 参数化，notify_usage IN (both, notify_only)）；FeishuAppAdapter（channel=feishu_app）credentials_for_tenant/available_for_tenant 租户级解析，全局 available() 恒 False；tenant_access_token 按租户缓存（expire 提前 300s 作废；本期错误码语义无 token 失效专项码，不做 code 触发的刷新重试）。
- Adapter（src/notifications/feishu_app.py）：出站复用 PinnedHTTPSConnection/public_addresses（域名固定 open.feishu.cn）；发送 `POST /open-apis/im/v1/messages?receive_id_type=open_id`（Bearer token 头，content 为 interactive 卡片 JSON 序列化字符串：header 标题 + lark_md 说明含时长 + button「查看结果」url={public_base_url}/notification?ref=&tenant=）；validate_target 校验 open_id 形态（ou_ + ≥32 位十六进制，宽松量级），endpoint 摘要 sha256(feishu_open_id)；响应 code 分类——0→accepted；230013/230034→target_invalid；230020→retryable 限流；HTTP 429/5xx/网络→retryable；其他 code→permanent。接口字段以 2026-11-15 官方文档查证口径为准（决策 7 追加）。
- 绑定路由（src/notifications/feishu_bind.py，挂 /api/notifications）：GET /feishu/bind/status（纯本地事实）、GET /feishu/bind/suggest（登录用户手机号 → batch_get_id 查 open_id，只读；batch_get_id 不返回姓名，姓名快照恒空）、POST /feishu/bind/confirm（服务端按手机号重新 batch_get_id 核实后落库 users.feishu_open_id + 幂等登记 feishu_app target，identity_preverified=True 对齐企微）、DELETE /feishu/bind（清列 + 撤 feishu_app target，不过租户 gate 对齐企微 unbind）；未配置租户绑定端点 503。无 state/OAuth 弹窗。
- 注册与白名单：service.py 注册 FeishuAppAdapter；PROVIDER_ALLOWED_EVENT_TYPES 加 'feishu_app'（completed/failed），expand 资格判断泛化为按白名单 provider 查表（wecom_app 行为不变）；PreferenceRequest channel/provider 加 feishu_app；repository 增 feishu_bind_profile（与 wecom_bind_profile 共用 _bind_profile）。
- 前端：NotificationSettings.vue 徽标改 x/3 可用 + 加飞书通知行；新增 FeishuTaskNotifyCard.vue 行组件（复制企微行结构精简——去掉 OAuth 弹窗/轮询/UA 检测，保留手机号识别自动绑定；取舍：企微行组件 OAuth 部分占大头，抽取共享行组件需重构已交付企微卡，复制改接口最直接）；未命中行下方提示、未绑定无手动绑定按钮；解绑后不自动重识别（重识别命中会立刻自动重绑，无法保持解绑态，页面刷新后可重新识别）；ChannelConfig.vue 通知用途单选扩展到 feishu（notify_only 隐藏 verification_token/encrypt_key/回调地址/关联数字员工，保存剔除旧值）。
- 已知边界：飞书 batch_get_id 不返回姓名（绑定姓名快照恒空，设置页显示「已绑定」不带姓名）；token 中途失效（如 secret 轮换）按其他 code→permanent 处理（凭据轮换后语义正确）；suggest 手机号按库内存量原样传（无 +86 规整），匹配失败提示用户核对手机号与应用可见范围。

## 八轮：钉钉通知接入（2026-11-16，设计 §3 决策 7 追加）

- 背景：钉钉任务通知完全复刻飞书租户级模式（渠道用途三模式 + 手机号绑定 + 通知适配器）；本期无 OAuth，绑定仅手机号识别 + confirm 服务端二次核实，无绑定弹窗。接口字段以 2026-11-16 官方文档查证口径为准（决策 7 追加）。
- DB：`users` 加 `dingtalk_userid TEXT`（db_update.yaml 幂等块 + deploy/init-postgres.sql + deploy/nuodu/init-postgres.sql 三处同步，照 feishu_open_id 先例）；UserDB 增 set_dingtalk_userid / get_by_dingtalk_userid_in_tenant（IS NOT DISTINCT FROM 兼容平台管理员）。
- 渠道配置（src/saas/api/channel_config.py）：`_NOTIFY_USAGE_CHANNEL_TYPES` 加 dingtalk；notify_only 必填 app_key/app_secret/agent_id 三项（工作通知发送需应用 AgentId；token/encoding_aes_key 免填），agent_only/both 保持原必填（存量行为不变）；create/update 同口径（create 白名单分支改按 `_NOTIFY_USAGE_CHANNEL_TYPES` 判定）。
- 渠道工厂（src/saas/services/channel_factory.py）：`_NOTIFY_USAGE_CHANNEL_TYPES` 加 dingtalk（notify_only 同样跳过对话渠道实例化/回调）。
- 凭据源（src/saas/db/channel_config_db.py + src/notifications/dingtalk_app.py）：新增 ChannelConfigDB.get_latest_dingtalk_notify_usage_decrypted（SQL 参数化，notify_usage IN (both, notify_only)，照 feishu 同构）；DingTalkAppAdapter（channel=dingtalk_app）credentials_for_tenant/available_for_tenant 租户级解析（config 缺 agent_id 按未配置），全局 available() 恒 False；access_token 按租户缓存（expires_in 提前 300s 作废；发送返回 40014 刷新重试一次）。
- Adapter（src/notifications/dingtalk_app.py）：出站复用 PinnedHTTPSConnection/public_addresses（域名固定 oapi.dingtalk.com）；access_token `GET /gettoken?appkey=&appsecret=`（token/appkey/appsecret 均在 URL query，后续 oapi 接口 token 放 query 非 Bearer 头）；发送 `POST /topapi/message/corpconversation/asyncsend_v2?access_token=`（body：agent_id 数字、userid_list 字符串、msg.msgtype=action_card、btn_orientation 数字 1、btn_list「查看结果」action_url={public_base_url}/notification?ref=&tenant=；**异步受理**——errcode=0 即 accepted，provider_id 记 task_id，送达结果 getsendresult 本期不查）；validate_target 校验 userid 形态（非空/无空白控制字符/≤64，宽松边界），endpoint 摘要 sha256(dingtalk_userid)；errcode 分类——0→accepted；60104/81013→target_invalid；-1/799913→retryable 限流；HTTP 429/5xx/网络→retryable；40014→刷新重试一次；其余（40035 缺参数等）→permanent。token 在 URL query——日志只打接口名与 errcode，不打完整 URL（含 token）。
- 绑定路由（src/notifications/dingtalk_bind.py，挂 /api/notifications）：GET /dingtalk/bind/status（纯本地事实）、GET /dingtalk/bind/suggest（登录用户手机号 → getbymobile 查 userid，只读；getbymobile 不返回姓名，姓名快照恒空；errcode 非 0 如 60104 视为未命中）、POST /dingtalk/bind/confirm（服务端按手机号重新 getbymobile 核实后落库 users.dingtalk_userid + 幂等登记 dingtalk_app target，identity_preverified=True 对齐企微/飞书）、DELETE /dingtalk/bind（清列 + 撤 dingtalk_app target，不过租户 gate）；未配置租户绑定端点 503。无 state/OAuth 弹窗。
- 注册与白名单：service.py 注册 DingTalkAppAdapter；PROVIDER_ALLOWED_EVENT_TYPES 加 'dingtalk_app'（completed/failed）；PreferenceRequest channel/provider 加 dingtalk_app；repository 增 dingtalk_bind_profile（共用 _bind_profile）。
- 前端：NotificationSettings.vue 徽标改 x/4 可用 + 加钉钉通知行；新增 DingTalkNotifyCard.vue 行组件（逐文件对照 FeishuTaskNotifyCard 复刻改接口——手机号识别自动绑定、未命中行下方提示、suggest 失败 503 静默、解绑后不自动重识别）；ChannelConfig.vue 通知用途单选扩展到 dingtalk（notify_only 联动补填 agent_id 字段、保存剔除 token/encoding_aes_key 旧值、无回调字段需隐藏）；api/notifications.ts 补 dingtalk 绑定/偏好封装与错误码。
- 已知边界：getbymobile 不返回姓名（绑定姓名快照恒空，设置页显示「已绑定」不带姓名）；asyncsend_v2 异步受理——errcode=0 记 accepted 不代表已送达（getsendresult 本期不查）；钉钉 userid 失效场景少，未知 errcode 统一 permanent 靠手机号重绑兜底；suggest 手机号按库内存量原样传（无 +86 规整），匹配失败提示用户核对手机号。

## 九轮：仅通知保存保留回调凭据与指引修正（2026-11-23，决策 7 修复追加）

- 问题：存量应用（已填回调 Token / EncodingAESKey）改选「仅通知」保存后，前端不提交表单已隐藏的回调字段，update 整体覆盖 config 把库内回调凭据清除；关掉弹窗或刷新后改回「仅智能体接入 / 智能体接入 + 通知」，回调输入框为空，保存报「缺少必填字段: token（回调Token…）」。另外仅通知下顶部「接入步骤」指引无条件显示，仍引导「将下方回调地址填入 URL 栏」，与已隐藏的回调地址区块矛盾。
- 后端（src/saas/api/channel_config.py）：update 切到 notify_only 时，未提交的回调字段回填库内旧值（`_NOTIFY_ONLY_RETAINED_FIELDS`：wecom token/encoding_aes_key、feishu verification_token/encrypt_key、dingtalk token/encoding_aes_key）；显式提交以提交值为准，库内无旧值不补键；agent_only/both 必填校验不变。仅通知用途不注册回调链路，保留不影响运行；保留逻辑在服务端，已交付旧前端同样受益。
- 前端（ChannelConfig.vue）：仅通知下接入指引换用 `notifyOnlySteps`（去掉回调配置步骤并说明无需配置回调地址；保留各渠道发送前提——飞书「机器人」能力并发布、成员在可用范围，钉钉「工作通知」接口权限）；保存逻辑不变（隐藏字段不提交，旧值由后端保留），相关注释同步。
- 验证：tests/unit/api/test_channel_config_usage_validation.py 新增 6 例（先红后绿）；新增 frontend/web/__tests__/components/ChannelConfig.notifyUsage.test.ts 5 例；独立验证（子智能体）复核：后端 37 过、指定回归 927 过、前端 5 过、build 与 compileall/import 通过，变异检验确认新增用例有效，审查无 P0/P1。本地真实业务库（真实路由 + token 鉴权）复验 wecom/feishu/dingtalk 改仅通知后回调字段保留、回切 200；本地真实页面走查仅通知下指引/字段/回调地址显示与保存（仅前端，本地 API 进程为旧代码）。tests/unit/wechat_mp 与 test_kf_account_referral 的失败经基线对比为无库模式预存，与本轮无关。
- 遗留（预存，非本轮引入）：钉钉 agent_only/both 后端仍必填 token/encoding_aes_key，而表单无这两个输入项——新建钉钉对话接入配置、以及直接以仅通知新建的钉钉配置改回对话接入，都会 400 且无处填写；需另行决策（后端必填表与表单对齐或补表单项）。

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
