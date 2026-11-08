# ai-lead-gen 邮件打开追踪接入方案（D 项，备后实施）

> 状态：方案定稿，未实施（2026-11-08）。来源：上游 b2b-lead-gen v3.3.0-3.3.11 的
> 云端打开追踪链路（`tracking_key.py` / `fetch_opens.py` / 像素端点）。
> 本轮技能包合并（A+B+C+E）已完成，D 项依赖服务端新端点，独立立项。

## 1. 背景与目标

上游的打开追踪把「邮件是否被打开」从发件端猜测变成硬指标：

- 发送时在邮件正文插入 1x1 透明像素 `<img src="https://<host>/api/track/p.gif?...">`；
- 收件方客户端加载图片即产生一次打开记录（campaign、message_id、时间、UA）；
- 本地 `fetch_opens.py` 按游标分页回拉，合并进打开记录（幂等）。

上游把接收端统一部署在 `https://www.aidingyi.cn/api/track/p.gif`——该域名正是
我方生产环境（129.211.65.243，agent.aidingyi.cn 体系），但**我方服务端目前没有
`/api/track/*` 任何端点**（已 grep 确认）。要启用打开追踪必须先在服务端新建这两个端点。

## 2. tracking_key 机制（零配置，按发件邮箱隔离）

上游 `tracking_key.py` 的口径，直接沿用：

- `tenant = md5(from_email.lower())[:8]`——稳定、按发件人隔离、匿名（不泄露邮箱明文）；
- `secret = md5(from_email.lower() + "|" + random_salt)`，`random_salt` 在
  「第一次为该邮箱生成」时随机产生并持久化，保证发送 / 回拉用同一把 key
  （读取接口用 secret 鉴权，防止拉到别人的打开记录）；
- 上游 key store 落 `~/.workbuddy/b2b_tracking_keys.json`；**我方改造**：
  落租户级 `storage/tenants/{tenant_id}/lead-gen/tracking_keys.json`
  （复用 `sender_profile.py` 的租户路径解析模式，见 `default_profile_path()`），
  也可直接复用 `AID_TENANT_ID/AID_USER_ID` 可信身份。
- 高级用法：SMTP 配置里手动填 `tracking_key: "tenant.secret"` 覆盖自动生成。
- `get_or_create_key(from_email)` 缺 `from_email` 时在发送前 fail-fast（不静默失效）。

## 3. 服务端两个新端点（本轮 D 项核心工作量）

### 3.1 `GET /api/track/p.gif` —— 像素打点（匿名写入）

```
请求：/api/track/p.gif?k=<tenant>.<secret>&c=<campaign>&m=<message_id>[&r=<收件人>]
响应：1x1 透明 GIF，Cache-Control: no-store，Content-Type: image/gif
```

- 入库记录：`{tenant, campaign, message_id, recipient?, opened_at, user_agent, ip}`
- **不要求登录态**（收件方邮箱客户端发起）；`k` 里的 secret 只用于归属，不做强鉴权
  （像素 URL 本来就会出现在邮件源码里，靠 tenant 隔离 + 回拉端点鉴权）；
- 写入要**异步化 / 轻量化**：单行 INSERT，不走重逻辑，不阻塞图片响应；
  建议经 Redis 队列缓冲批量落库，避免瞬时打开风暴打满 DB 连接。
- 建议存储：新表 `bs_ai_lead_gen_opens`（tenant_id TEXT, campaign TEXT,
  message_id TEXT, recipient TEXT, opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  user_agent TEXT, ip TEXT）——业务表规范：`bs_` 前缀 + tenant_id + created_at，
  由技能侧 `lead_db.py init_tables` 一并建表或随 `db_update.yaml` 增量。

### 3.2 `GET /api/track/opens` —— 游标分页回拉（secret 鉴权）

```
请求：?k=<tenant>.<secret>&secret=<secret>[&c=<campaign>][&cursor=<id>][&limit=N]
响应：{"count": N, "next_cursor": "<id|空>", "opens": [{id, campaign, message_id,
       recipient, opened_at, user_agent}]}
```

- 鉴权：URL 参数 `k=<tenant>.<secret>` 与 `secret` 一致才返回该 tenant 的记录
  （上游口径；我方可选增强：额外校验 `AID_TENANT_ID` 身份）；
- 游标按记录 `id` 单调递增翻页；`fetch_opens.py` 已有游标停滞 / 回退防御
  （`seen_cursors` 去重、未推进即停），服务端只需保证 `next_cursor` 单调；
- `limit` 建议默认 2000、服务端硬上限（如 5000）。

### 3.3 路由与安全注意

- 路由挂 `/api/track/*`，**必须加入** `TenantContextMiddleware` 的匿名白名单
  （等价于现有票据/公开端点模式），否则会被租户中间件拦截；
- `p.gif` 入参全部服务端校验（tenant 格式 hex8、message_id 长度），防注入；
- 限速：对 `/api/track/p.gif` 按 IP 做基础限速（收件方图片代理会高频拉取）。

## 4. 技能侧后续链路（端点就绪后）

| 事项 | 说明 |
|---|---|
| 移植 `tracking_key.py` | 按第 2 节口径改 key store 为租户级路径 |
| 移植 `fetch_opens.py` | 默认端点改我方域名；合并目标从 opens.csv 改为 `bs_ai_lead_gen_emails.opens_at` / 回复表回写 |
| `send_emails.py` 注入像素 | 正文尾部插像素 img（track_opens 默认 true，可在 campaign config 关闭） |
| `campaign_metrics.py` / `validate_campaign.py` | 打开率、回复率、送达率全链路有效性验证（上游「十一、全自动有效性验证」），依赖 opens 数据 |
| `hook_library.py` | 强钩子库（按业务+市场分区的钩子实例与战绩），依赖打开/回复效果数据沉淀 |
| reply_monitor 联动 | 打开但未回复的联系人可在每日循环里二次跟进打分 |

## 5. 实施顺序建议

1. 服务端建表 + 两个端点 + 中间件白名单 + 限速（本方案第 3 节）；
2. 技能侧移植 `tracking_key.py` / `fetch_opens.py` + `send_emails.py` 像素注入；
3. 真机冒烟：发一封带像素的测试邮件 → 收件端打开 → `fetch_opens` 回拉命中；
4. 再评估 `campaign_metrics` / `validate_campaign` / `hook_library` 三件套。

## 6. 关联

- 本轮技能包合并记录：docs/ideas.md「AI 获客」条目
- 上游实现参照：`ext_package/b2b-lead-gen/scripts/{tracking_key,fetch_opens}.py`
- 我方补丁基线：`src/skills/ai-lead-gen-1.0.0/`（v1.1.0）
