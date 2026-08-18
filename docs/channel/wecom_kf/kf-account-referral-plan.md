# 微信客服账号 API 管理 + 引流统计方案

> 版本: v1.3 | 创建: 2026-08-18 | 状态: 已确认（v1.2 定案 4+4 项；v1.3 经 PM 审查修订 5 项，见 9.3 节）
> 关联文档: [微信客服发送消息接口官网说明.txt](微信客服发送消息接口官网说明.txt) / [wecom_kf_design.md](wecom_kf_design.md) / [wecom_kf_deployment_guide.md](wecom_kf_deployment_guide.md)

---

## 一、业务背景与目标

当前微信客服（wecom_kf）渠道的客服账号是**手工配置**的：租户管理员在渠道配置页手动填写客服名称、`open_kfid`（首次收到消息后由系统自动填入）等。整个过程需要管理员去企业微信后台手工创建客服账号、复制 `open_kfid` 回填，无法在系统内闭环，也无法生成推广二维码。

本次目标（对应需求 1~5）：

| # | 需求 | 现状 | 目标 |
|---|------|------|------|
| 1 | 租户管理员增加客服账号，绑定到一个租户用户（员工） | 手工填 open_kfid，无绑定关系 | 表单内选择绑定的租户员工 |
| 2 | 将客服账号创建到企业微信后台 | 需人工去企微后台创建 | 系统调用企微 API `account/add` 自动创建 |
| 3 | 生成客服账号对应的链接/二维码回显 | 无 | 调用 `add_contact_way` 生成客服链接 + 生成二维码回显 |
| 4 | 租户员工拿二维码线下介绍给 C 端客户 | 线下动作 | 系统只负责提供二维码（线下动作不变） |
| 5 | C 端客户扫码聊天 → 记录到 users 表，并建立「C 端客户 ↔ 引流员工」关联，在「外部接待客户」页展示引流统计 | 已记录 users（source=wecom_kf），但无引流关联 | 新增关联 + 引流统计展示 |

---

## 二、关键依据（已核实）

### 2.1 企微客服 API 能力（来自 `ext/wecom_kf_account_api.txt`）

| 接口 | 路径 | 关键参数 | 返回 |
|------|------|---------|------|
| 添加客服账号 | `POST /cgi-bin/kf/account/add` | `name`(≤16字符)、`media_id`(头像临时素材，**必填**) | `open_kfid` |
| 删除客服账号 | `POST /cgi-bin/kf/account/del` | `open_kfid` | — |
| 修改客服账号 | `POST /cgi-bin/kf/account/update` | `open_kfid`、`name`、`media_id`（可部分省略） | — |
| 获取客服账号列表 | `POST /cgi-bin/kf/account/list` | `offset`/`limit` | `account_list[].open_kfid/name/avatar/manage_privilege` |
| 获取客服账号链接 | `POST /cgi-bin/kf/add_contact_way` | `open_kfid`、`scene`(可选，`[0-9a-zA-Z_-]*` ≤32字节) | `url`（客服链接，可据此生成二维码） |

权限前提：自建应用需在「微信客服-可调用接口的应用」中，且已在「通过 API 管理微信客服账号」处配置可管理账号。

**注意**：`account/add` 的 `media_id` 标注为必填。头像来源采用「管理员自定义上传 > 租户 logo（`tenants.logo_file_id`）> 默认占位 PNG」兜底链，统一用现有 `WeComKfApiClient.upload_media` 上传换取 media_id（默认占位 PNG 参考 `adapter._generate_default_thumb` 生成，详见 §3.3 头像处理与 §9.2 微调项 3/4）。

### 2.2 `enter_session` 事件如何携带 scene（已查官方文档确认）

回调 XML **只推送** `kf_msg_or_event` 事件（含 `OpenKfId`），真正的事件内容通过 **`sync_msg` 的 `msg_list`** 返回。`enter_session` 事件在 `msg_list` 中的结构：

```json
{
  "msgtype": "event",
  "event": {
    "event_type": "enter_session",
    "open_kfid": "wkAJ2GCAAASSm4_FhToWMFea0xAFfd3Q",
    "external_userid": "wmAJ2GCAAAme1XQRC-NI-q0_ZM9ukoAw",
    "scene": "123",
    "scene_param": "abc",
    "welcome_code": "aaaaaa"
  }
}
```

- `event.scene` = 生成链接时传给 `add_contact_way` 的 `scene`（**这是引流归因的关键字段**）
- `event.scene_param` = URL 上额外拼接的 `scene_param`（本项目暂不使用，留作扩展）
- `event.welcome_code` = 满足条件时返回，可用于 `send_msg_on_event` 发欢迎语

**现状问题**：当前 `_process_tenant_wecom_kf_messages` 的 sync_msg 循环只处理了 `user_recall_msg` 事件，**没有处理 `enter_session`**；而 `_handle_kf_enter_session` 挂在回调 XML 的 `event == "enter_session"` 分支上（官方文档确认 XML 不会推送该事件），属于死路径。本次改造顺带修复：在 sync_msg 循环内处理 `enter_session`（记录引流 + 发欢迎语）。

### 2.3 现有数据模型

- **C 端客户**：写入 `users` 表，`source='wecom_kf'`，由 `src/saas/services/auto_register.py::ensure_user_registered` 创建/复用（幂等）。
- **客服账号配置**：存在 `tenant_channel_configs.config` JSON 的 `kf_account` 数组中，每条含 `name / open_kfid / subagent_type / welcome_message / servicer_userid_list / allow_agent_transfer`。`ChannelFactory` 读取该数组构造 `WeComKfAdapter`。
- **「外部接待客户」页面**：`frontend/web/components/saas/ExternalCustomerService.vue`（左右布局：左侧外部客户列表 + 右侧聊天记录），数据源 `GET /api/saas/external-customers/users`（`UserDB.list_external_users`，过滤 `source IS NOT NULL`）。

---

## 三、总体方案

### 3.1 方案总览

```
租户管理员（渠道配置页 wecom_kf）
   │ 1. 新增客服账号表单：名称 + 头像（可选上传，缺省用租户 logo 兜底）+ 绑定租户员工 + 子智能体
   │    + 欢迎语 + 人工客服 + 转人工开关 + 到期日期 + 积分上限 + 二维码标题
   ▼
后端新 API POST /api/saas/wecom-kf/accounts
   │ 2. 校验渠道凭证 → 上传头像（自定义/租户 logo/默认占位）→ 企微 account/add → open_kfid
   │ 3. 生成 scene → 企微 add_contact_way → 客服链接 url
   │ 4. 更新 tenant_channel_configs.config.kf_account 追加该账号
   │    （含 tenant_user_id/scene/contact_url/expire_at/credit_limit/qr_title）
   │ 5. 生成二维码 PNG → 返回 base64 data URL
   ▼
前端弹窗回显：二维码标题（qr_title 显示在二维码上方）+ 二维码图片 + 客服链接（可复制/下载）
   │
   ▼（线下动作）
租户员工拿二维码介绍给 C 端客户 → C 端客户微信扫码进入会话
   │
   ▼
企微推送 kf_msg_or_event 回调 → sync_msg 返回 enter_session 事件（含 scene）
   │
   ▼
_process_tenant_wecom_kf_messages 新增 enter_session 分支：
   │ 6. ensure_user_registered 创建/复用 C 端客户（source=wecom_kf）
   │ 7. 按 scene 反查绑定的租户员工 → 写 customer_referrals（first-touch 归因）
   │ 8. welcome_code 存在时发送欢迎语
   ▼
客户发消息 → 路由到智能体前做账号级软拦截：
   │ 9. 到期拦截：now > expire_at 当日 23:59:59 → 回固定话术「该客服账号已过期…」，不计费
   │ 10. 积分拦截：credit_limit>0 且累计消耗 >= 上限 → 回固定话术「该客服账号积分已用完…」，不计费
   ▼
正常流程 → start_record + 调智能体回复（记录 credit_cost）
   ▼
「外部接待客户」页面顶部展示引流统计（员工 → 引流客户数），客户列表显示引流人
```

### 3.2 数据模型设计

#### 3.2.1 扩展 `kf_account` 配置项（`tenant_channel_configs.config.kf_account[]`）

在现有每条 kf_account 上新增 6 个字段：

```json
{
  "name": "售前咨询",
  "open_kfid": "wkAJ2GCAAA...",
  "subagent_type": "...",
  "welcome_message": "...",
  "servicer_userid_list": ["zhangsan"],
  "allow_agent_transfer": true,
  "tenant_user_id": "u_xxx",          // 新增：绑定的租户员工（引流人，可编辑，修改需前端确认框，见 9.3）
  "scene": "kf_4f2a9c1b7e3d5a6b",     // 新增：add_contact_way 场景值，全局唯一，创建后不可变
  "contact_url": "https://work.weixin.qq.com/kf/kfcbf...?enc_scene=...",  // 新增：客服链接，创建后不可变
  "expire_at": "2026-12-31",          // 新增：到期日期（可空=不限）；到期日当天仍有效，次日 00:00 起拦截
  "credit_limit": 1000,               // 新增：积分消耗上限（0=无独立上限，跟随租户积分）；账号创建至今累计
  "qr_title": "爱定义 - 小蔡老师"       // 新增：二维码标题（显示在二维码图片上方，区分不同员工）
}
```

- `scene` 生成规则：`"kf_" + uuid4().hex[:16]`（21 字符，满足 `[0-9a-zA-Z_-]` 与 ≤32 字节约束），创建前检查租户内不重复。
- `contact_url` 持久化，便于后续重新展示，避免重复调用 `add_contact_way`（同一 scene 重复调用会返回新的 enc_scene，会改变 URL，应避免）。
- **不可变字段**：`scene`、`contact_url`、`open_kfid`（编辑账号时不改，保证已发放二维码持续有效）。
- **可编辑字段**：`name`、头像（走企微 `account/update`）、`tenant_user_id`、`expire_at`、`credit_limit`、`qr_title`、`welcome_message`、`servicer_userid_list`、`allow_agent_transfer`。
- **绑定员工可编辑的依据**：二维码 URL 只由 `open_kfid + scene` 决定，`tenant_user_id` 是纯本地归因映射，修改它不影响企微侧与已发放二维码；历史引流已固化在 `customer_referrals`（first-touch），改绑只影响**后续新扫码客户**的归因。前端修改绑定时必须弹确认框：「之前已扫码的客户仍绑定原用户，新扫码的客户会绑定新用户」（员工离职/换岗场景正依赖此能力换绑，避免删账号重发码）。

#### 3.2.2 新增引流关系表 `customer_referrals`（系统表）

```sql
CREATE TABLE IF NOT EXISTS customer_referrals (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,                    -- 租户隔离
    referrer_user_id TEXT,             -- 引流租户员工 user_id
    customer_user_id TEXT,             -- C端客户 user_id
    open_kfid TEXT,                    -- 客服账号 ID（追溯来源）
    scene TEXT,                        -- 场景值（追溯来源）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_customer_referrals_customer UNIQUE (customer_user_id)
);
CREATE INDEX IF NOT EXISTS idx_customer_referrals_tenant ON customer_referrals(tenant_id);
CREATE INDEX IF NOT EXISTS idx_customer_referrals_referrer ON customer_referrals(tenant_id, referrer_user_id);
```

- **first-touch 归因**：`UNIQUE(customer_user_id)` + `INSERT ... ON CONFLICT (customer_user_id) DO NOTHING`，保证一个 C 端客户只归属第一个扫码的引流员工（重复扫码不覆盖）。
- 不采用在 `users` 表加 `referrer_user_id` 列，理由：职责分离、不动现有用户表（免迁移存量）、天然支持未来多维度扩展（渠道、时间）。

#### 3.2.3 账号级积分消耗归集（`credit_limit` 依据，无需新表/新字段）

wecom_kf 渠道的智能体回复已按 `SessionRecordManager.start_record(source_type="wecom_kf")` 计费，落 `chat_records`（含 `credit_cost`）；`channel_sessions.channel_chat_id` 存 `open_kfid`。因此按客服账号累计积分消耗可直接 SQL 归集：

```sql
SELECT COALESCE(SUM(cr.credit_cost), 0)
FROM chat_records cr
JOIN channel_sessions cs ON cs.session_id = cr.session_id
WHERE cs.tenant_id = %s AND cs.channel_type = 'wecom_kf' AND cs.channel_chat_id = %s
```

- **口径**：账号创建至今累计（已确认）。
- **性能**：每条客户消息进 agent 前直接查库一次（`idx_chat_records_session` 已覆盖 session_id 维度）。线下引流场景并发低，不做 Redis 缓存（v1.3 审查降级：缓存键 + 失效逻辑 + 多 worker 一致性的复杂度不划算，出现性能问题再加）。
- **竞态**：回复前判断的宽松一致性——极端并发下可能轻微超发；该场景并发低，可接受。
- **`credit_limit=0`**：不查累计、不拦截，跟随租户积分（`tenants.credit_balance` 已有"对话中扣完不中断、下一轮入口拦截"机制，见 deploy/init-postgres.sql 注释）。

### 3.3 后端 API（新增 `src/saas/api/wecom_kf_account.py`）

统一前缀 `/api/saas/wecom-kf`，所有端点 `require_admin` 鉴权 + `tenant_id` 隔离。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/accounts` | POST | 创建客服账号：校验渠道凭证 → 上传头像（自定义，可选；缺省用**租户 logo** 兜底，无 logo 用默认占位 PNG）→ 企微 `account/add` → 生成 scene → `add_contact_way` → 写配置（含 expire_at/credit_limit/qr_title）→ 生成二维码，返回 `{open_kfid, name, contact_url, qr_data_url, scene, tenant_user_id, expire_at, credit_limit, qr_title}` |
| `/accounts` | GET | 列出当前租户全部客服账号（含绑定员工、链接、引流人数、到期/积分/标题） |
| `/accounts/{open_kfid}` | PUT | **编辑**：名称/头像变更 → 企微 `account/update`（头像可选，不改则不传 media_id）；`tenant_user_id`（换绑，仅影响后续新归因，前端需确认框，见 9.3）/expire_at/credit_limit/qr_title/welcome_message/servicer_userid_list/allow_agent_transfer → 直接改配置。**不可变**：open_kfid、scene、contact_url |
| `/accounts/{open_kfid}` | DELETE | **先企微后本地**：调企微 `account/del` → 成功 → 从配置移除 + invalidate adapter；失败 → 回显企微 errmsg，本地不动。企微侧返回"账号不存在"视为本地可删 |
> v1.3 审查决定：**不提供重新生成二维码（换 scene）端点**。`scene` 一旦生成为不可变，换 scene 会使已发放二维码全部作废，与"不可变"原则冲突且无明确业务场景；确需换码时走"删除账号 + 新建"（同样作废旧码，但交互上管理员有明确预期）。

**引流统计端点（放在 `external_customers.py`，供「外部接待客户」页 Tab2 使用）**：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/saas/external-customers/referral-stats` | GET | 引流统计：`?start_date=&end_date=`（默认全部）。返回 `{total_referrals, total_messages, referrers: [{referrer_user_id, referrer_name, referral_count, ratio}]}` |
| `/api/saas/external-customers/users` | GET | 列表参数新增 `referrer_user_id`（Tab1 下钻筛选）；每行返回 `referrer_user_id / referrer_name` |

辅助内部逻辑：
- **绑定员工下拉数据**：复用现有 `GET /api/saas/users`（`listTenantUsers()`）即可，无需新端点。
- **scene 反查**：`resolve_scene(tenant_id, scene)` → 遍历该租户 wecom_kf 配置的 `kf_account` 找 `scene` 匹配项，返回 `(config_id, kf_account)`。租户 wecom_kf 配置通常 1 条、kf_account 个位数，遍历成本可忽略。
- **渠道校验**：创建前 `ChannelConfigDB.get_by_tenant_and_id` 取凭证 + 校验 `verified=1`，未验证时返回 400 引导先「验证连接」。
- **头像处理**：创建请求可选带头像（base64/multipart，上限如 2MB），后端调企微 `media/upload`（临时素材）换 `media_id`。**兜底优先级：管理员上传头像 > 租户 logo（`tenants.logo_file_id` → `uploaded_file:{file_id}` 取图 → 缩放后上传）> 默认占位 PNG**（复用 `adapter._generate_default_thumb`）。头像类型校验（PNG/JPG）与大小上限，企微返回错误时回显 errmsg。
- **二维码生成**：新增依赖 `qrcode`（`requirements.txt`），用 `qrcode.make(contact_url)` + Pillow（已依赖）转 PNG，返回 base64 data URL（无公网文件端点，避免鉴权漏洞）。

### 3.4 前端

#### 3.4.1 渠道配置页（`ChannelConfig.vue`）客服账号区改造

- 客服账号条目新增「绑定租户员工」下拉（数据源 `listTenantUsers()`，显示 username/昵称）。
- **「+ 添加客服账号」弹窗式**：字段 = 名称 + **头像（可选上传）** + 绑定员工 + 子智能体 + 欢迎语 + 人工客服 + 转人工开关 + **到期日期** + **积分上限（0=无上限）** + **二维码标题** → 调 `POST /api/saas/wecom-kf/accounts` → 成功后在弹窗内回显**二维码标题（显示在二维码图片上方）+ 二维码图片 + 客服链接**（可复制、可下载二维码），并自动回填 `open_kfid`。
- **「编辑」按钮 + 编辑弹窗**：复用创建弹窗（预填），打开 `PUT /accounts/{open_kfid}`。**绑定员工下拉可编辑**（换绑），但修改时必须弹确认框：「之前已扫码的客户仍绑定原用户，新扫码的客户会绑定新用户」，确认后才提交；open_kfid / scene / 链接只读展示。名称/头像变更调企微 `account/update`，其余本地字段直接保存。
- 列表每条客服账号显示：名称、open_kfid、绑定员工、到期日期、积分上限、二维码/链接（查看按钮，含 qr_title）、引流人数。
- **删除**：确认框（提示"删除后已发放二维码将失效"）→ `DELETE /api/saas/wecom-kf/accounts/{open_kfid}`；后端先调企微 `account/del`，成功才删本地；失败则前端回显企微 errmsg。
- 说明：新建/编辑账号走**独立 API**，不复用现有渠道「保存」静默流程（涉及企微 API 副作用，需明确交互与错误回显）。

#### 3.4.2 「外部接待客户」页引流统计（详见第五节）

### 3.5 关键流程细节

#### 创建客服账号（后端 `_create_kf_account`）

```
1. require_admin + 取 tenant_id
2. 校验 wecom_kf 渠道配置存在且 verified；否则 400「请先配置并验证企业微信客服渠道」
3. 校验绑定员工：tenant_user_id 属于该租户（UserDB.get_by_id + tenant_id 匹配）
4. 头像：管理员上传 > 租户 logo（tenants.logo_file_id）> 默认占位 PNG → upload_media(image) → media_id
5. 企微 account/add {name, media_id} → open_kfid（失败：返回企微 errcode/errmsg）
6. 生成 scene = "kf_" + uuid4().hex[:16]（租户内查重）
7. 企微 add_contact_way {open_kfid, scene} → contact_url
8. qrcode.make(contact_url) → PNG → base64 data URL
9. 更新 ChannelConfigDB：config.kf_account 追加该账号
   （含 tenant_user_id/scene/contact_url/expire_at/credit_limit/qr_title），并 invalidate adapter 缓存
10. 返回 {open_kfid, name, contact_url, qr_data_url, scene, tenant_user_id, expire_at, credit_limit, qr_title}
```

#### 编辑客服账号（后端 `_update_kf_account`）

```
1. require_admin + 校验配置存在；定位该 open_kfid 的 kf_account
2. 绑定员工（tenant_user_id）可修改：仅更新本地配置，不调企微 API（二维码 URL 与其无关）；改绑只影响后续新扫码客户的归因，first-touch 历史不受影响
3. 若 name 或头像变更：
     name → 直接传给企微 account/update {open_kfid, name}
     头像 → 重新上传换 media_id → account/update {open_kfid, name, media_id}
     （account/update 的 media_id 可不填=不改头像；失败：回显企微 errmsg，不写本地）
4. 更新本地配置：tenant_user_id / expire_at / credit_limit / qr_title / welcome_message / servicer_userid_list / allow_agent_transfer
5. invalidate adapter 缓存；scene/contact_url/open_kfid 不变 → 已发放二维码继续有效
```

#### 删除客服账号（后端 `_delete_kf_account`）

```
1. require_admin + 定位 kf_account
2. 调企微 account/del {open_kfid}
   - 成功（errcode=0）→ 继续
   - 企微返回"账号不存在"类 errcode → 视为本地可删，继续
   - 其他失败 → 返回 {success:False, error:"删除失败", debug:企微errmsg}，本地不动
3. 从配置移除该 kf_account → invalidate adapter 缓存
4. 历史引流记录（customer_referrals）保留，统计不受影响
```

#### 消息入口拦截（到期日期 / 积分上限）

插入位置：`_process_tenant_wecom_kf_messages` 消息循环中，`should_transfer_to_human` 判断之后、`agent_router.get_agent` 路由之前（约 channel_routes.py:2154 附近）。两条拦截**均不调智能体、不 `start_record`（不计费）**，直接向客户回固定话术（复用现有发送 + 落库通道，写 channel_messages 便于前端展示）。

```
对每条客户消息（msgtype=text/voice，非事件）：
  kf = 当前 open_kfid 对应的 kf_account 配置

  # 拦截 1：到期日期（expire_at 可空）
  if kf.get("expire_at"):
      expire_dt = parse_date(kf["expire_at"]) + 23h59m59s   # 到期日当天仍有效
      if now > expire_dt:
          reply(MSG_EXPIRED)   # 「该客服账号已过期，请联系管理员更新有效期或更换二维码」
          continue

  # 拦截 2：积分上限（credit_limit > 0 才查；0=跟随租户积分，已有租户级拦截）
  if kf.get("credit_limit", 0) > 0:
      used = get_account_credit(tenant_id, open_kfid)        # §3.2.3 SQL 归集，直接查库
      if used >= kf["credit_limit"]:
          reply(MSG_CREDIT_EXHAUSTED)  # 「该客服账号积分已用完，请联系管理员重新分配积分或更换二维码。」
          continue

  # 正常流程
  start_record(...) + process_and_persist(...)
```

> 固定话术定义为**系统常量**（已确认）：`MSG_EXPIRED` 与 `MSG_CREDIT_EXHAUSTED`，集中放 wecom_kf 渠道常量处；拦截回复本身不计费（不调 LLM）。
>
> 转人工会话无需拦截豁免（实测确认，2026-08-18）：已转人工的会话，企业微信直接将消息送达员工侧企微，不会回调到我方智能体，因此拦截逻辑天然不会命中人工消息，无需额外分支。

#### C 端客户扫码引流（`_process_tenant_wecom_kf_messages` 新增分支）

```
在 sync_msg msg_list 循环中，msgtype == "event" 且 event.event_type == "enter_session":
  scene = event.get("scene", "")
  external_userid = event.get("external_userid", "")
  open_kfid = event.get("open_kfid", "")
  welcome_code = event.get("welcome_code", "")

  # 1. 确保 C 端客户注册（幂等，source=wecom_kf）
  customer_user_id = await ensure_user_registered("wecom_kf", external_userid, tenant_id, source="wecom_kf")

  # 2. 引流归因（first-touch）
  if scene and customer_user_id:
      binding = resolve_scene(tenant_id, scene)   # 反查 kf_account
      if binding and binding.kf_account.get("tenant_user_id"):
          INSERT INTO customer_referrals ... ON CONFLICT (customer_user_id) DO NOTHING

  # 3. 发送欢迎语（welcome_code 有效时）
  if welcome_code and kf_config.get("welcome_message"):
      await adapter.send_welcome_message(welcome_code, kf_config["welcome_message"])
```

> 说明：`ensure_user_registered` 在 enter_session 时即创建用户记录，因此「只扫码未聊天」的客户也会计入引流数。若希望只统计真正聊过天的客户，可在统计查询上再过滤 `channel_sessions` 存在会话（已确认口径为扫码即算，见 9.1，不做此过滤）。

---

## 四、「外部接待客户」页面引流统计 UI 方案（已确认：双 Tab）

需求：在「外部接待客户」页面显示**哪个租户员工引流得到多少个 C 端客户**。

**已确认方案**：页面顶部增加 **2 个 Tab**，不新增独立路由/菜单（仍是「外部接待客户」单页面）：

| Tab | 内容 |
|-----|------|
| **客户对话记录** | 即现有页面内容（左侧客户列表 + 右侧聊天记录）。改动：左侧客户列表中，在客户名称后**增加显示引流人**（如「由 张伟 引流」，无引流的显示「直接咨询」或隐藏） |
| **引流统计** | 新增统计视图，页面样式参考 **积分用量页**（`/t/{tenant_id}/token-usage`），自上而下三层：日期段选择 → 统计卡片 → 员工维度表格（可下钻） |

### 引流统计 Tab 布局（参考积分用量页）

```
┌────────────────────────────────────────────────────────┐
│ [客户对话记录] [引流统计]  ← 顶部 Tab                     │
├────────────────────────────────────────────────────────┤
│ 日期段选择（默认「近30天」） [近7天] [近30天] [自定义]       │
├────────────────────────────────────────────────────────┤
│ 统计卡片（横向）                                          │
│ ┌──────────┐ ┌──────────┐                               │
│ │ 总引流数  │ │ 总对话消息数│                              │
│ │   32     │ │   158    │                               │
│ └──────────┘ └──────────┘                               │
├────────────────────────────────────────────────────────┤
│ 员工维度表格（BaseTable）                                 │
│ 员工 │ 引流客户数 │ 占比                                  │
│ 张伟 │   12     │ 37.5%   │  ← 行可点击 → 下钻           │
│ 李娜 │    8     │ 25.0%   │                              │
│ ...                                                      │
└────────────────────────────────────────────────────────┘
```

### 交互与数据

1. **日期段选择**：默认「近30天」；提供快捷区间（近7天/近30天）+ 自定义起止日期，**不提供「全部」**（v1.3 审查调整：避免「总对话消息数」三表 join 全表聚合）。过滤基准 = `customer_referrals.created_at`（引流发生时间）。
2. **统计卡片**：
   - **总引流数**：`customer_referrals` 在日期段内计数（口径：**扫码即算**，已确认）。
   - **总对话消息数**：日期段内，被引流客户的会话消息总数。路径：`customer_referrals.customer_user_id → channel_sessions.user_id → channel_messages.session_id`，过滤 `channel_messages.created_at` 在日期段内（`is_recalled=FALSE`）。
3. **员工维度表格**：`referrer_user_id` 分组 → `referral_count`（引流客户数）+ `ratio`（该员工引流数 / 总引流数，百分比）。员工名 LEFT JOIN `users`，员工被删时显示「已删除员工」并保留计数。
4. **下钻**：点击某员工行 → 跳转到「客户对话记录」Tab，客户列表自动按该员工过滤（`GET /users?referrer_user_id=xxx`），并在 Tab 顶部显示当前筛选条件（如「张伟 的引流客户」，可清除恢复全部）。

### 后端配套（已在 3.3 列出）

- `list_external_users` LEFT JOIN `customer_referrals` + `users`，返回 `referrer_user_id / referrer_name`；新增 `referrer_user_id` 筛选参数。
- 新增 `GET /api/saas/external-customers/referral-stats`（日期段参数，返回总引流数/总消息数/员工分组）。
- `customer_referrals.created_at` 需要能按日期过滤——现有 `created_at` 字段已满足，**无需新增字段**。

---

## 五、数据库变更

| 文件 | 变更 |
|------|------|
| `deploy/init-postgres.sql` | 新增 `customer_referrals` 表 + 2 个索引 |
| `deploy/db_update.sql` | 追加同样 DDL（注释标注日期 2026-08-18 与用途） |

不修改 `users` 表结构（避免存量数据迁移）。

---

## 六、涉及文件清单

### 后端
| 文件 | 改动 |
|------|------|
| `src/saas/api/wecom_kf_account.py` | **新增**：客服账号创建/编辑/删除 API + 到期/积分字段校验 |
| `src/saas/api/external_customers.py` | `list_external_users` 加 referrer 返回与筛选；新增 `/referral-stats`（日期段 + 总引流/总消息/员工分组） |
| `src/saas/api/channel_routes.py` | `_process_tenant_wecom_kf_messages` 增加 enter_session 分支（引流归因 + 欢迎语）+ **消息入口拦截（到期日期 / 积分上限固定话术）** |
| `src/channels/wecom_kf/adapter.py` | 暴露默认头像生成（复用 `_generate_default_thumb`）；租户 logo 兜底取图；kf_account 增加 `tenant_user_id` 传递 |
| `src/channels/wecom_kf/api_client.py` | 新增 `account_add / account_del / account_update / account_list / add_contact_way` 方法 |
| `src/db/models.py` | 新增 `CustomerReferralDB`（insert/record/first-touch/stats）+ 账号级积分归集查询（§3.2.3） |
| `src/main.py` | 注册新 router |
| `requirements.txt` | 新增 `qrcode`（Pillow 已存在） |

### 前端
| 文件 | 改动 |
|------|------|
| `frontend/web/components/saas/ChannelConfig.vue` | 客服账号区改造：绑定员工下拉 + 创建/编辑弹窗（头像/到期/积分/标题 + 二维码标题回显）+ 编辑按钮（绑定员工只读）+ 删除确认 |
| `frontend/web/components/saas/ExternalCustomerService.vue` | 顶部双 Tab（客户对话记录 / 引流统计）；Tab1 客户列表加引流人显示；Tab2 引流统计视图（日期段 + 统计卡 + 员工表格 + 下钻跳转） |
| `frontend/web/api/saasTenant.ts` | 新增 wecom-kf 账号/引流统计 API |
| `frontend/web/api/externalCustomers.ts` | 列表参数加 `referrer_user_id`；新增 `/referral-stats` |

### 数据库
| 文件 | 改动 |
|------|------|
| `deploy/init-postgres.sql` | 新增 `customer_referrals` 表 |
| `deploy/db_update.sql` | 增量 DDL |

---

## 七、开发步骤（分期）

### Phase 1：后端核心
1. `api_client.py` 增加 5 个企微 API 方法（account_add/del/update/list、add_contact_way）。
2. 数据库：`customer_referrals` 表（init + db_update）。积分归集复用现有表，无新 DDL。
3. `wecom_kf_account.py`：创建（头像链：上传 > 租户 logo > 默认占位；含 expire_at/credit_limit/qr_title）/编辑（`account/update` + 本地字段，含换绑 tenant_user_id）/删除（先企微后本地）。不提供重新生成二维码端点（见 §3.3 说明）。
4. 固定话术常量：`MSG_EXPIRED` / `MSG_CREDIT_EXHAUSTED`（wecom_kf 渠道常量处）。
5. `channel_routes.py`：sync_msg 循环处理 enter_session（引流归因 + 欢迎语）+ **消息入口拦截（到期日期 / 积分上限）**。
6. `external_customers.py`：`list_external_users` 加 referrer 返回与筛选 + 新增 `/referral-stats`（日期段 + 总引流/总消息/员工分组）。
7. 单测：账号创建（mock 企微 API）、头像链兜底、scene 反查、first-touch 归因、引流统计分组/日期段、enter_session 处理、到期/积分拦截（边界：到期日当天有效 / credit_limit=0 不拦 / 达上限拦截话术不计费）、编辑不可变字段（open_kfid/scene/contact_url）、换绑 tenant_user_id 仅影响后续归因、删除先企微后本地。

### Phase 2：前端
8. `ChannelConfig.vue` 客服账号区改造：创建/编辑弹窗（头像上传 + 到期日期 + 积分上限 + 二维码标题 + 绑定员工只读）+ 二维码标题回显 + 编辑按钮 + 删除确认（失败回显）。
9. `ExternalCustomerService.vue` 双 Tab：Tab1 客户列表加引流人 + `referrer_user_id` 筛选；Tab2 引流统计视图（日期段 + 统计卡 + 员工表格 + 下钻跳转）；`externalCustomers.ts` / `saasTenant.ts` API。
10. 前端 tsc + build + 路由/组件单测。

### Phase 3：联调验证
11. 真机验证（需企微后台开通微信客服 + 可调用接口应用）：
    - 创建客服账号成功 → 企微后台能看到新账号；二维码标题回显正确；
    - 扫二维码进入会话 → enter_session 事件带 scene → 引流归因落库；
    - 「外部接待客户」页统计正确；
    - 到期拦截：把某账号 expire_at 设为昨天 → 发消息收到固定话术「该客服账号已过期…」；
    - 积分拦截：把某账号 credit_limit 设为极小值 → 累积达上限后发消息收到「该客服账号积分已用完…」；
    - 编辑名称/头像 → 企微后台同步变化，已发二维码仍可扫码；
    - 删除账号 → 企微后台删除 + 本地移除；伪造企微失败验证前端回显。
12. 文档登记（本方案 + ideas.md 状态更新）。

---

## 八、风险与注意事项

1. **`account/add` 的 `media_id` 必填**：头像兜底链为「管理员上传 > 租户 logo（`tenants.logo_file_id`）> 默认占位 PNG」。租户 logo 需先经 `uploaded_file:{file_id}` 取图、缩放至合适尺寸（如 640px）再上传，避免大图；无 logo 或取图失败时才落默认占位 PNG（复用 `_generate_default_thumb`）。头像上传需校验类型（PNG/JPG）与大小上限（如 2MB），企微返回错误时回显 errmsg。
2. **企微权限前置**：自建应用必须在企微后台「微信客服-可调用接口的应用」+「通过 API 管理微信客服账号」中授权，否则 `account/add` 返回权限错误（errcode 相关）。需要在创建失败时把企微 errmsg 回显给管理员，并在渠道配置指南中补充说明。
3. **`add_contact_way` 同一 scene 重复调用会更换 enc_scene**：`contact_url` 必须持久化，二维码以持久化的 URL 为准，避免重复调用导致已发放二维码失效。
4. **多 worker 缓存一致性**：修改 `kf_account` 后必须 `ChannelFactory.invalidate_adapter(tenant, "wecom_kf", config_id)`，否则 adapter 仍持有旧配置（`_auto_fill_open_kfid` 已有先例）。
5. **删除员工后引流记录**：`referral-stats` 用 LEFT JOIN users 取名，员工被删时显示「已删除员工」并保留计数。
6. **无 session 会话客户**：口径已确认为**扫码即算**（enter_session 即建用户），无需 channel_sessions 过滤。相应的「总对话消息数」对这类客户为 0，与口径一致。
7. **「总对话消息数」统计路径**：`customer_referrals.customer_user_id → channel_sessions.user_id → channel_messages.session_id`，需过滤 `is_recalled=FALSE` 且按 `channel_messages.created_at` 落日期段。数据量大时注意该聚合查询走 `idx_channel_messages_session` 索引。
8. **计费**：本次无新增 LLM/Embedding/ASR 调用点，不涉及计费变更。**到期/积分拦截的固定话术回复必须绕过计费**（不调智能体、不 `start_record`），否则固定话术本身会消耗账号积分，破坏"积分用完即停"语义。
9. **不新建独立菜单**：引流统计以「外部接待客户」页面内 Tab 形式实现，无需 page_metadata 注册、无需新增路由。
10. **积分归集查询性能**：每条消息进 agent 前直接 SUM 一次 `chat_records`（`idx_chat_records_session` 已覆盖）。线下引流场景并发低，v1.3 审查决定**不做 Redis 缓存**，出现性能问题再引入。返回的 SUM 与 `credit_limit` 比较用**浮点容差**（credit_cost 为 NUMERIC(12,2)）。
11. **修改账号不可变字段**：后端 `PUT` 对 `scene` / `contact_url` 变更请求应忽略或 400，防止误改导致已发放二维码失效。`tenant_user_id` 可修改（换绑），前端修改时必须弹确认框提示归因规则；`open_kfid` 为定位键不可改。

---

## 九、待确认问题（已确认 2026-08-18）

| # | 问题 | 确认结果 |
|---|------|---------|
| 1 | 「引流成果」的口径：扫码即算 or 产生过会话才算？ | ✅ **扫码即算**（enter_session 建用户即计入），统计不过滤 channel_sessions |
| 2 | 1 客服账号 = 1 员工，还是支持多员工共用（多 scene）？ | ✅ **1 账号 = 1 员工**，scene 与绑定员工一一对应，Phase 1 不做解耦 |
| 3 | 客服账号头像是否支持管理员自定义上传？ | ✅ **支持自定义上传**（可选）；未上传时兜底链：**租户 logo > 默认占位 PNG** |
| 4 | 「外部接待客户」页引流统计 UI 采用哪种方案？ | ✅ **双 Tab**：Tab1 客户对话记录（客户列表显示引流人）+ Tab2 引流统计（参考积分用量页：日期段 → 统计卡 → 员工表格，可下钻跳转 Tab1 筛选） |

### 9.2 微调项评估确认（2026-08-18）

| # | 微调项 | 评估结论 |
|---|--------|---------|
| 1 | 到期日期（`expire_at`）：到期后智能体不回复，回固定话术 | ✅ 采纳。**到期日当天仍有效，次日 00:00 起拦截**；拦截点 = 消息入口（channel_routes.py:2154 前），不调智能体、不计费 |
| 2 | 积分上限（`credit_limit`）：达上限后停止回复，回固定话术 | ✅ 采纳。**账号创建至今累计**（经 chat_records + channel_sessions 归集，§3.2.3）；0 = 无独立上限，跟随租户积分（已有租户级拦截） |
| 3 | 二维码标题（`qr_title`）：文本框，显示在二维码图片上方 | ✅ 采纳。存 kf_account 配置，纯前端渲染；建议格式「企业简称 - 员工昵称」 |
| 4 | 默认头像用租户企业 logo 兜底 | ✅ 采纳。`tenants.logo_file_id` 已存在；兜底链：自定义头像 > 租户 logo > 默认占位 PNG |
| 5 | 修改客服账号 UI | ✅ **提供编辑**（企微官方支持 `account/update` 改名称/头像）。**绑定员工可换绑**（v1.3 审查调整，原定不可变：二维码 URL 与 tenant_user_id 无关，换绑不影响已发二维码，仅影响后续新归因，前端需确认框）；open_kfid/scene/contact_url 不可变 -> 已发二维码持续有效 |
| 6 | 删除客服账号：先调企微 delete，成功才删本地，失败回显 | ✅ 采纳。企微侧"账号不存在"视为本地可删；历史引流记录保留 |

---

### 9.3 PM 审查修订（2026-08-18，v1.2 -> v1.3）

| # | 审查发现 | 决定 |
|---|---------|------|
| 1 | `scene`「不可变」与 `regenerate-qr` 端点（重新生成 scene）前后矛盾 | ✅ **砍掉 `regenerate-qr` 端点**，确需换码走"删除账号 + 新建"（见 §3.3 说明） |
| 2 | 「绑定员工不可变」理由不成立（二维码 URL 与 tenant_user_id 无关），且员工离职后只能删账号重发码 | ✅ **改为可换绑**，修改时前端弹确认框：「之前已扫码的客户仍绑定原用户，新扫码的客户会绑定新用户」 |
| 3 | 拦截范围是否覆盖转人工消息 | ✅ 无需处理：实测已转人工会话消息由企微直达员工侧，不进我方智能体（结论已记入 §3.5） |
| 4 | Redis 缓存 `kf_credit:{open_kfid}` 属过度设计（低并发，索引已覆盖） | ✅ **砍掉缓存，直接查库**，性能问题出现再引入（§3.2.3 / §8.10） |
| 5 | 「总对话消息数」三表 join + 默认「全部」日期段有全表聚合风险 | ✅ **日期段默认「近30天」，不提供「全部」**（§4） |

### 9.4 遗留记录（暂不处理）

以下小问题已记录，本期不处理：

| # | 问题 | 备注 |
|---|------|------|
| 6 | 空值语义不统一：`expire_at` 可空=不限，`credit_limit` 0=不限 | 两种约定并存，开发时注意判空口径 |
| 7 | `credit_limit` 事后调低会瞬间触发拦截（累计口径） | 可选优化：编辑保存时回显"当前累计已消耗 X" |
| 8 | 过期/积分耗尽账号，客户扫码仍会先收到欢迎语 | enter_session 不做拦截判断，可接受 |
| 9 | 统计 `ratio` 需处理总引流数为 0 的除零 | 前端/SQL 层兜底 |
| 10 | `customer_referrals` UNIQUE 约束不含 `tenant_id` | 当前单 corp 场景无碍，跨租户复用企微时有隐患 |

> **开发状态**：方案已确认 → 按第七节 Phase 1→3 实施，开发开始时在 `docs/ideas.md` 将 #56 状态改为 🔧 部分完成。
