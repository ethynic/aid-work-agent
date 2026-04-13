# SaaS 多租户商业层设计计划

## Context

当前 AID Work Agent 是单租户部署模式（全局 `master_agent` 单例、单一 config.yaml、共享注册表）。需要在其之上构建 SaaS 多租户商业层，支持：
- 企业购买智能体实例，按 Token 配额计费（例：月费 2000 元含 2 亿 token）
- 企业自助管理：IM 对接、用户管理、自定义 Skill、使用报告
- **核心约束**：不修改 `src/core/`、`src/llm/`、`src/tools/`、`src/memory/`、`src/subagents/` 的核心逻辑

### 商业模式详情
- **计费模型**：每个智能体按月订阅，月费包含固定 token 配额（如 2000 元/月含 2 亿 token）。超额可单独购买 token 包或自动停服。
- **管理员认证**：手机号+短信验证码登录；若企业已配置 IM 平台（企业微信/钉钉/飞书），管理员可通过 IM 平台单点登录（SSO），优先从 IM 接口获取手机号。
- **终端用户注册**：IM 入口用户首次使用自动注册（通过 IM API 获取用户信息+手机号）；Web 入口用户由管理员手动创建或批量导入。

### 统一用户模型（公共用户 + 企业租户）
系统同时服务两类用户，**用同一套数据模型和计费基础设施**：

| 维度 | 公共用户（现有） | 企业租户用户（新增） |
|------|-----------------|---------------------|
| tenant_id | `NULL` | 具体 tenant_id |
| 登录方式 | 微信扫码（未绑手机需验证码绑定） | 管理员创建/IM 自动注册 |
| Agent 实例 | 共用全局 `master_agent` | 独立 agent_instance（绑定 subagent_type） |
| Token 配额 | 默认不限量（`token_quota = -1`） | 按订阅套餐（如 2 亿/月） |
| 未来扩展 | 可设置月限额 + 充值付费 | 续费/升级套餐 |
| 数据隔离 | 按 user_id | 按 tenant_id + user_id |
| 前端入口 | 现有 `/` 路由 | `/portal` 管理后台 + `/t/{tenant_id}/` 用户入口 |
| 用量报告 | 查看个人每日用量 | 企业汇总（日/周/月）+ 每用户明细 |

**微信扫码登录流程**：
1. 用户微信扫码 → 获取 `wx_openid`
2. 查 `users` 表是否已绑定手机号 → 已绑定则直接登录
3. 未绑定 → 弹出手机号输入框 → 发送验证码 → 验证通过 → 绑定手机号 → 登录
4. 已有的 `auth.py` 微信登录逻辑需扩展手机号绑定步骤

**支付/订阅模块独立设计**：
`payment.py` + `billing.py` + `subscriptions` 表是**共享基础设施**，同时服务两类用户：
- 企业订阅：`tenant_id` 有值，`user_id` 为 NULL，关联 `agent_instances`
- 公共用户：`tenant_id = NULL`，`user_id` 有值，`token_quota = -1`（不限量）
- 未来公共用户付费时：创建 `tenant_id = NULL, user_id = xxx` 的 subscription，设置具体的 `token_quota`

**本次不实现公共用户的付费改造**，但在数据模型和代码接口中预留坑位（标记 `# TODO: 公共用户付费`）。

**用量报告分级**：
- 公共用户 API：`GET /api/usage/my/daily` — 返回个人每日 token 使用量
- 企业租户 API：`GET /api/saas/reports/summary` — 企业汇总（日/周/月维度）
- 企业租户 API：`GET /api/saas/reports/users` — 租户下每用户使用明细

---

## 一、新增数据库表（7 张表）

在 `src/db/database.py` 的 `init_database()` 末尾调用 `init_saas_tables(conn)`，定义在新建的 `src/saas/db/tables.py` 中。共 **8 张新表**。

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `tenants` | 企业/租户 | `tenant_id`, `company_name`, `status`, `plan`, `max_agent_instances`, `max_users`, `settings(JSON)` |
| `tenant_admins` | 租户管理员（独立于 end-user） | `admin_id`, `tenant_id`, `phone`, `password_hash`(可为空-SSO用户), `sso_provider`, `sso_uid`, `role(owner/admin/viewer)` |
| `tenant_admin_tokens` | 管理员 token（与用户 token 分离） | `token`, `admin_id`, `tenant_id`, `expires_at` |
| `subscriptions` | 订阅/计费记录 | `subscription_id`, `tenant_id`(nullable, NULL=公共用户), `user_id`(nullable, 公共用户付费时用), `subagent_type`, `billing_cycle`, `unit_price`, `token_quota`(-1=不限量, >0=具体配额), `tokens_used`, `status`, `expires_at`, `payment_status` |
| `agent_instances` | 部署的智能体实例 | `instance_id`, `tenant_id`, `subscription_id`, `subagent_type`, `display_name`, `status(running/stopped)`, `config(JSON)`, `bound_channel_type`, `allowed_skills(JSON)` |
| `tenant_channel_configs` | 租户 IM 渠道凭证 | `config_id`, `tenant_id`, `channel_type`, `config(JSON, 加密)`, `verified` |
| `tenant_users` | 租户用户映射 | `mapping_id`, `tenant_id`, `user_id(FK→users)`, `department`, `role`, `source(im_auto/admin_manual/batch_import)` |
| `payment_orders` | 支付订单 | `order_id`, `tenant_id`, `subscription_id`, `amount`, `payment_method(wechat/alipay)`, `payment_status`, `paid_at`, `transaction_id` |

**现有表唯一修改**：`users` 表增加 `tenant_id TEXT` 列（nullable，向后兼容）。数据隔离通过 `tenant_users` 映射表实现，不在现有表加 tenant_id 过滤。

---

## 二、新增模块结构

```
src/saas/
    __init__.py
    context.py                 # ContextVar: current_tenant_id, current_instance_id
    middleware.py               # TenantContextMiddleware

    db/
        __init__.py
        tables.py               # init_saas_tables() — 7 张新表 DDL
        tenant_db.py            # tenants 表 CRUD
        tenant_admin_db.py      # tenant_admins + tenant_admin_tokens CRUD
        subscription_db.py      # subscriptions 表 CRUD
        agent_instance_db.py    # agent_instances 表 CRUD
        channel_config_db.py    # tenant_channel_configs 表 CRUD
        tenant_user_db.py       # tenant_users 表 CRUD
        usage_log_db.py         # usage_logs 表 CRUD（新增，用于计费统计）

    api/
        __init__.py
        tenant_auth.py          # /api/saas/auth/* — 管理员登录注册
        tenant_mgmt.py          # /api/saas/tenants/* — 企业信息管理
        agent_instances.py      # /api/saas/instances/* — 智能体实例管理
        subscriptions.py        # /api/saas/billing/* — 订阅管理
        channel_config.py       # /api/saas/channels/* — 渠道配置
        tenant_users.py         # /api/saas/users/* — 企业用户管理
        tenant_skills.py        # /api/saas/skills/* — 自定义 Skill 上传管理
        usage_reports.py        # /api/saas/reports/* — 使用报告

    services/
        __init__.py
        instance_manager.py     # AgentInstanceManager — 核心：管理租户 agent 实例
        channel_factory.py      # 从 DB 配置创建 ChannelAdapter 实例
        skill_resolver.py       # 合并平台 skills + 租户自定义 skills
        usage_tracker.py        # 用量追踪（从 chat_records 聚合，写入 subscriptions.tokens_used）
        billing.py              # 订阅生命周期、到期检查、配额扣减
        payment.py              # 支付集成（微信支付/支付宝），订单管理
        sms.py                  # 短信验证码发送（对接短信服务商）
        sso.py                  # IM 平台 SSO（企业微信/钉钉/飞书 OAuth + 获取手机号）
        auto_register.py        # IM 用户首次消息自动注册

    models/
        __init__.py
        tenant.py               # Pydantic models
        subscription.py
        agent_instance.py
        channel_config.py
        usage.py

frontend/src/
    api/saasTenant.ts           # SaaS API client
    components/saas/
        TenantLogin.vue
        PortalLayout.vue         # 侧边栏布局
        TenantDashboard.vue      # 仪表盘
        InstanceManager.vue      # 智能体实例列表/创建/启停
        InstanceDetail.vue       # 实例配置详情
        ChannelConfig.vue        # IM 渠道凭证配置
        TenantUserManager.vue    # 企业用户管理
        SkillManager.vue         # 自定义 Skill 管理
        UsageReports.vue         # 使用报告图表
        BillingView.vue          # 订阅/计费
        TenantSettings.vue       # 企业设置
```

租户自定义文件存储：
```
storage/tenants/{tenant_id}/
    skills/{skill_name}/SKILL.md
```

---

## 三、API 路由设计

### 3.1 管理员认证 `/api/saas/auth/*`
```
POST   /api/saas/auth/sms/send       # 发送短信验证码
POST   /api/saas/auth/login          # 手机号+验证码登录
POST   /api/saas/auth/sso/{provider} # IM 平台 SSO 登录（wecom/dingtalk/feishu），自动获取手机号
POST   /api/saas/auth/logout         # 登出
GET    /api/saas/auth/me             # 当前管理员信息
```
**SSO 流程**：企业微信/钉钉/飞书 OAuth → 回调获取用户手机号 → 匹配 tenant_admins.phone → 生成 token → 登录。

### 3.2 企业管理 `/api/saas/tenants/*`
```
GET    /api/saas/tenants/me          # 获取企业信息
PATCH  /api/saas/tenants/me          # 更新企业信息
GET    /api/saas/tenants/me/stats    # 概览统计
```

### 3.3 智能体实例 `/api/saas/instances/*`
```
GET    /api/saas/instances           # 列表
POST   /api/saas/instances           # 创建（选 subagent_type → 创建 subscription → 创建 instance）
GET    /api/saas/instances/{id}      # 详情
PATCH  /api/saas/instances/{id}      # 更新配置
DELETE /api/saas/instances/{id}      # 删除
POST   /api/saas/instances/{id}/start   # 启动
POST   /api/saas/instances/{id}/stop    # 停止
```

### 3.4 渠道配置 `/api/saas/channels/*`
```
GET    /api/saas/channels            # 列表
POST   /api/saas/channels            # 新增（WeCom/DingTalk/Feishu 凭证）
PUT    /api/saas/channels/{id}       # 更新
DELETE /api/saas/channels/{id}       # 删除
POST   /api/saas/channels/{id}/verify  # 验证凭证
```

### 3.5 企业用户 `/api/saas/users/*`
```
GET    /api/saas/users               # 列表
POST   /api/saas/users               # 手动创建单个用户（创建 users 记录 + tenant_users 映射，source=admin_manual）
POST   /api/saas/users/batch         # 批量导入用户（CSV 上传，source=batch_import）
PATCH  /api/saas/users/{user_id}     # 更新
DELETE /api/saas/users/{user_id}     # 移除
```
**IM 自动注册**：不在管理 API 中，而是在渠道回调处理中。当 IM 用户首次发送消息时，`process_channel_message` 检测用户是否已注册，若未注册则调用 IM API 获取用户信息（含手机号）→ 自动创建 `users` + `tenant_users`（source=im_auto）。

### 3.6 自定义 Skill `/api/saas/skills/*`
```
GET    /api/saas/skills              # 列表
POST   /api/saas/skills              # 上传 SKILL.md
PUT    /api/saas/skills/{name}       # 更新
DELETE /api/saas/skills/{name}       # 删除
```

### 3.7 使用报告

**公共用户（无 tenant_id）** `/api/usage/*`：
```
GET    /api/usage/my/daily           # 个人每日 token 用量（预留，本次不实现前端）
```

**企业租户** `/api/saas/reports/*`：
```
GET    /api/saas/reports/summary     # 企业汇总（日/周/月维度），含总 token、会话数、活跃用户数
GET    /api/saas/reports/tokens      # Token 用量趋势图数据（按时间粒度聚合）
GET    /api/saas/reports/users       # 租户下每用户使用明细（user_id, token 用量, 会话数）
GET    /api/saas/reports/sessions    # 会话统计
GET    /api/saas/reports/export      # CSV 导出
```

### 3.8 订阅计费 `/api/saas/billing/*`
```
GET    /api/saas/billing/subscriptions     # 订阅列表
POST   /api/saas/billing/subscriptions     # 购买新订阅（选 subagent_type + 套餐 → 创建支付订单）
GET    /api/saas/billing/plans             # 可用套餐列表（如：基础版2000元/月含2亿token）
GET    /api/saas/billing/orders            # 支付订单列表
POST   /api/saas/billing/pay/{order_id}    # 发起支付（返回微信/支付宝支付链接）
POST   /api/saas/billing/pay/callback      # 支付回调（微信/支付宝异步通知）
GET    /api/saas/billing/usage             # 当前 token 用量/剩余配额
```
**计费模型**：subscription 包含 `token_quota` 和 `tokens_used`。每次对话产生的 token 数（从 `chat_records.total_token_count`）累计到 `subscriptions.tokens_used`。超出配额时：
1. 发送告警通知到管理员
2. 可选：自动停服 或 允许超额按量计费

---

## 四、核心机制：租户上下文流转

### 4.1 ContextVar（`src/saas/context.py`）
```python
current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)
current_instance_id: ContextVar[str | None] = ContextVar("current_instance_id", default=None)
```

### 4.2 中间件（`src/saas/middleware.py`）

TenantContextMiddleware 按 URL 前缀分发：

| URL 模式 | 解析方式 |
|---------|---------|
| `/api/saas/*` | 从 `Authorization` header 取管理员 token → 查 `tenant_admin_tokens` → 得 `tenant_id` |
| `/api/chat/*` | 从 `Authorization` header 取用户 token → 查 `users.tenant_id` → 得 `tenant_id` |
| `/t/{tenant_id}/*/callback` | 从 URL path 取 `tenant_id` → 查 `agent_instances.bound_channel_type` → 得 `instance_id` |

解析后设置 `request.state.tenant_id` / `request.state.instance_id` + ContextVar。

### 4.3 Agent 实例管理（`src/saas/services/instance_manager.py`）

**核心类 `AgentInstanceManager`**：管理 `Dict[instance_id, AgentRouter]`，每个租户的每个 agent 实例一个独立的 `AgentRouter`。

```python
class AgentInstanceManager:
    _routers: Dict[str, AgentRouter]  # instance_id → AgentRouter

    async def start_instance(instance_id, tenant_id, subagent_type, config):
        # 创建独立 AgentRouter（共享底层 master_agent 单例，session 级隔离已由 ShortTermMemory 保证）
        router = AgentRouter()
        self._routers[instance_id] = router
        # 如果绑定了渠道，创建 channel adapter
        # 更新 DB status='running'

    async def stop_instance(instance_id):
        # cleanup router, 更新 DB status='stopped'

    def get_agent(instance_id, subagent_name, session_id) -> Agent:
        router = self._routers.get(instance_id)
        return router.get_agent(subagent_name, session_id) if router else None
```

**关键设计**：`AgentRouter` 已有 `session_id:subagent_name` 缓存机制。每个实例一个 Router = 独立缓存空间，但共享 `master_agent` 单例（线程安全，session 隔离）。

### 4.4 Token 配额检查

在 `chat_stream()` 和 `chat()` 中，agent 处理消息前检查配额：
```python
# 在 agent.process_message_sync 之前
from src.saas.services.billing import check_token_quota
quota_ok, reason = check_token_quota(instance_id=instance_id, user_id=user_id)
if not quota_ok:
    return JSONResponse({"success": False, "error": "Token 配额已用尽，请联系管理员续费"})
```

`check_token_quota` 逻辑：
- **企业用户**（instance_id 有值）：查关联 subscription 的 `token_quota` vs `tokens_used`
- **公共用户**（无 instance_id）：目前始终返回 True（不限量）；预留 `# TODO: 公共用户月限额检查`

对话完成后，`usage_tracker.py` 从 `chat_records.total_token_count` 累加到对应 `subscriptions.tokens_used`。

---

## 五、渠道路由变更

### 5.1 新增租户级回调路由（`src/saas/api/channel_routes.py`）

```
POST   /t/{tenant_id}/wecom/callback
GET    /t/{tenant_id}/wecom/callback
POST   /t/{tenant_id}/dingtalk/callback
GET    /t/{tenant_id}/dingtalk/callback
POST   /t/{tenant_id}/feishu/callback
GET    /t/{tenant_id}/feishu/callback
```

回调时：从 path 取 `tenant_id` → 查 `tenant_channel_configs` 构造 adapter → 查 `agent_instances`（bound_channel_type 匹配）→ 通过 `instance_manager.get_agent()` 获取对应实例的 Agent → 处理消息。

### 5.2 现有回调保持不变
`/wecom/callback`、`/dingtalk/callback`、`/feishu/callback` 保持原样，用于向后兼容。

---

## 六、前端路由

```typescript
// 新增路由
{ path: '/portal/login', component: TenantLogin },
{
  path: '/portal',
  component: PortalLayout,
  meta: { requiresTenantAuth: true },
  children: [
    { path: '', component: TenantDashboard },
    { path: 'instances', component: InstanceManager },
    { path: 'instances/:id', component: InstanceDetail },
    { path: 'channels', component: ChannelConfig },
    { path: 'users', component: TenantUserManager },
    { path: 'skills', component: SkillManager },
    { path: 'reports', component: UsageReports },
    { path: 'billing', component: BillingView },
    { path: 'settings', component: TenantSettings },
  ]
}
```

使用独立的 `saas_token` 存 localStorage，与普通用户 token 分离。路由守卫检查 `meta.requiresTenantAuth`。

### 现有前端预留改动（不实现，仅留坑位）
- `LoginModal.vue`：微信扫码后增加手机号绑定步骤（`need_bind_phone` 状态处理）
- 现有用户界面中预留"我的用量"入口（`# TODO: 公共用户用量页面`）

---

## 七、对现有文件的最小修改

### 7.1 `src/db/database.py`（+3 行）
在 `init_database()` 末尾添加：
```python
from src.saas.db.tables import init_saas_tables
init_saas_tables(conn)
```
并在 `users` 的 CREATE TABLE 中加 `tenant_id TEXT,` 和对应索引。

### 7.2 `src/main.py`（+20 行）
1. 注册中间件：`app.add_middleware(TenantContextMiddleware)`
2. Include SaaS APIRouter：`app.include_router(saas_auth.router)` 等 8 个
3. 在 `chat_stream()` 和 `chat()` 中，将 `agent_router.get_agent(...)` 改为：
```python
instance_id = getattr(request.state, 'instance_id', None)
if instance_id:
    from src.saas.services.instance_manager import instance_manager
    agent = instance_manager.get_agent(instance_id, subagent_name, session_id)
if not agent:
    agent = agent_router.get_agent(subagent_name, session_id)
```
4. 在 `lifespan()` 中初始化 `instance_manager` 并启动所有 `status='running'` 的实例

### 7.3 `src/api/auth.py`（+15 行）
1. `UserDB.create()` 接受可选 `tenant_id` 参数，写入 users 表。
2. 微信扫码登录增加手机号绑定步骤：
   - 新增 `POST /api/auth/bind-phone` — 微信用户绑定手机号（验证码验证）
   - 修改微信登录回调：检测 `users.phone` 是否为空 → 若空返回 `{"need_bind_phone": true, wx_openid}` → 前端弹出手机号绑定框

### 7.4 `src/channels/callback.py`（+15 行）
在 `process_channel_message()` 中添加 IM 自动注册逻辑：
```python
# 在 session 获取之前
from src.saas.services.auto_register import ensure_user_registered
user_id = await ensure_user_registered(channel_type, message.user_id, tenant_id)
```
以及新增 tenant-scoped 回调路由（新文件 `src/saas/api/channel_routes.py`，不修改现有回调）。

### 7.4 `configs/config.yaml`（+6 行）
```yaml
saas:
  enabled: true
  tenant_skills_dir: "storage/tenants"
  default_max_instances: 5
  default_max_users: 50
```

### 7.5 `src/config/settings.py`（+10 行）
添加 `SaasConfig(BaseModel)` 和 `Settings` 中的 `saas: SaasConfig` 字段。

### 7.6 不修改的文件（零改动）
- `src/core/agent.py` / `agent_router.py` — 直接复用
- `src/core/skill_*.py` — 通过新建的 `SkillResolver` 包装调用
- `src/subagents/*` — 直接复用 `AgentFactory.create_standalone_subagent()`
- `src/channels/*/adapter.py` — 通过 `ChannelFactory` 动态创建
- `src/llm/*`, `src/tools/*`, `src/memory/*`, `src/knowledge/*` — 零改动

---

## 八、实施阶段

### Phase 1：基础设施
1. 创建 `src/saas/` 模块骨架
2. 实现 `db/tables.py`（8 张新表 + users 表加 tenant_id）
3. 实现 `context.py`（ContextVar）
4. 实现 `middleware.py`（TenantContextMiddleware）
5. 实现 `models/` 下所有 Pydantic 模型
6. 修改 `src/db/database.py`、`src/main.py` 注册入口
7. 添加 `SaasConfig` 到 `src/config/settings.py`

### Phase 2：认证与企业信息
1. 实现 `sms.py`（短信验证码服务）
2. 实现 `sso.py`（IM 平台 SSO：企业微信/钉钉/飞书 OAuth + 获取手机号）
3. 实现 `tenant_admin_db.py` + `tenant_auth.py`（管理员登录：手机号+验证码 / IM SSO）
4. 实现 `tenant_db.py` + `tenant_mgmt.py`（企业信息 CRUD）
5. 前端：`TenantLogin.vue` + `PortalLayout.vue` + `TenantSettings.vue`

### Phase 3：计费与订阅
1. 实现 `payment.py`（微信支付/支付宝对接）
2. 实现 `billing.py`（订阅生命周期、配额管理、到期检查）
3. 实现 `subscription_db.py` + `subscriptions.py`（订阅管理 + 支付流程）
4. 实现 `usage_tracker.py`（每次对话后从 chat_records 累加 tokens_used 到 subscription）
5. 前端：`BillingView.vue`（套餐选择 + 支付 + 用量展示）

### Phase 4：智能体实例管理（核心）
1. 实现 `instance_manager.py`（AgentInstanceManager）
2. 实现 `agent_instance_db.py` + `agent_instances.py`（CRUD + 启停）
3. 修改 `src/main.py` chat handler 的 agent 获取逻辑
4. 在 `lifespan()` 中初始化 `instance_manager` 并恢复 running 实例
5. 前端：`InstanceManager.vue` + `InstanceDetail.vue` + `TenantDashboard.vue`

### Phase 5：渠道与 Skill 定制
1. 实现 `channel_factory.py` + `channel_config.py`（渠道配置管理）
2. 实现 `auto_register.py`（IM 用户首次消息自动注册，在渠道回调中触发）
3. 修改渠道回调处理：tenant-aware 路由 + 自动注册逻辑
4. 实现 `skill_resolver.py` + `tenant_skills.py`（Skill 上传管理）
5. 实现租户级回调路由（`/t/{tenant_id}/.../callback`）
6. 前端：`ChannelConfig.vue` + `SkillManager.vue`

### Phase 6：用户管理与报告
1. 实现 `tenant_user_db.py` + `tenant_users.py`（用户管理 + 批量导入）
2. 实现 `usage_reports.py`（使用报告，从 chat_records + usage_logs 聚合）
3. 前端：`TenantUserManager.vue` + `UsageReports.vue`

---

## 九、验证方案

1. **单元测试**：`tests/unit/test_saas_models.py`、`tests/unit/test_instance_manager.py` — mock DB 测试 CRUD 和实例管理逻辑
2. **集成测试**：`tests/integration/test_saas_flow.py` — 完整租户创建→购买实例→启动→聊天→查看报告流程
3. **API 测试**：用 FastAPI `TestClient` 测试所有 `/api/saas/*` 端点的认证、权限、数据隔离
4. **端到端验证**：
   - 创建两个租户，各购买一个实例
   - 分别通过各自渠道发送消息，验证消息隔离
   - 管理员登录 portal，验证只能看到自己企业的数据
   - 上传自定义 Skill，验证只对自己实例生效
