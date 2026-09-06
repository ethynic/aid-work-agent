# 用户行为审计日志设计（登录/登出/增删改留痕）

> 状态：设计完成，待开发
> 日期：2026-09-06
> 关联：[计费审计规范](../../.claude/rules/billing_audit.md)（本设计不动计费链路）、[数据库表开发规范](../../.claude/rules/database_dev.md)、[SaaS 租户隔离规范](../../.claude/rules/backend_dev.md)

---

## 1. 背景与目标

用户最初设想在 `chat_records` 中增加 IP / User-Agent 以便计费可溯源，但对话高频发生、逐条记录大量重复。收敛后的需求是：

- **不在每次对话中记录** IP/UA（重复无价值）；
- **新增独立的用户行为日志**，记录安全敏感与审计敏感事件：
  - 登录成功 / 登录失败 / 登出（含 IP、User-Agent，这是溯源的真正落点）
  - 修改密码、重置密码
  - 管理菜单下各子菜单的**增删改**操作（平台管理员 + 租户管理员）
  - 普通用户的关键动作（修改资料、删除历史会话等）

目标：满足安全审计（谁在何时从哪里登录、改了什么）与追责定位，同时控制存储成本。

## 2. 记录范围（事件清单）

### Phase 1：认证与账号安全事件（含 IP/UA）

| 事件 | action | 端点位置 |
|------|--------|---------|
| 登录成功 | `login` | `src/api/auth.py:363`（/api/auth/login）、`:496`（unified-login）、`:684/:719`（手机号登录）、`src/saas/api/tenant_auth.py:345`（password_login）、`:598`（admin_sso_login） |
| 登录失败 | `login_failed` | 同上，失败分支 |
| 登出 | `logout` | `auth.py:878`、`tenant_auth.py:699` |
| 修改密码 | `password_change` | `auth.py:836`（/reset-password）、`tenant_auth.py` 对应端点 |
| 发送验证码 | `verify_code_sent` | `auth.py:809`（防爆破观测，可选） |
| 修改资料 | `profile_update` | `auth.py:775`（PATCH /profile） |
| 渠道账号绑定/解绑 | `channel_bind` / `channel_unbind`（entry=channel） | 渠道账号绑定相关端点（wecom_kf_account 等，实施时逐个确认；具体端点列表开发时补入本表） |

### Phase 2：管理后台增删改（不含 IP/UA 重复记录的顾虑，IP/UA 照记，反正行数少）

| 资源域 | 路由前缀 | 代表文件 |
|--------|---------|---------|
| 租户管理（平台管理员） | `/api/saas/tenants` | `src/saas/api/tenant_mgmt.py`（POST:174 / PUT:224 / DELETE:329） |
| 租户用户（平台管理员） | `/api/saas/users` | `src/saas/api/tenant_users.py` |
| 计费充值、激活码等 SaaS | `/api/saas/*` | `src/saas/api/` 下 20 个文件，逐个挂装饰器 |
| 数字员工定义 | `/api/admin/agent-definitions`、`/api/subagents` | `src/api/admin_subagent.py` |
| 提示词管理 | `/api/admin/prompts` | `src/api/prompt_management.py` |
| 错误日志处理 | `/api/admin/error-logs` | `src/api/admin_error_logs.py` |
| 租户配置 | `/api/saas/tenant/*` | `src/api/` 下 tenant_config_file.py 等 |

### Phase 3：普通用户动作

| 事件 | action |
|------|--------|
| 删除历史会话 | `delete`（resource_type=session） |
| 批量删除 | `batch_delete` |
| 上传/删除知识库文档 | `create` / `delete`（resource_type=knowledge_doc） |

只记**写操作**，不记查询（查询量太大且审计价值低，如需查询审计另行评估）。

## 3. 表设计

系统表（非 bs_ 前缀），表名 `user_behavior_logs`：

```sql
CREATE TABLE IF NOT EXISTS user_behavior_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,                      -- 租户ID；platform_admin 全局操作为 NULL
    user_id TEXT NOT NULL,               -- 操作人；登录失败且无用户身份时记用户名/手机号到 detail
    user_role TEXT,                      -- 操作时角色快照（platform_admin/tenant_admin/user）
    action TEXT NOT NULL,                -- 行为类型，见枚举 BehaviorAction
    resource_type TEXT,                  -- 资源类型，见枚举 BehaviorResourceType
    resource_id TEXT,                    -- 资源ID（如租户ID、文档doc_id）
    resource_name TEXT,                  -- 资源名称快照（便于人读，如租户名、文档标题）
    detail JSONB DEFAULT '{}',           -- 变更摘要（字段名列表/关键新值），过滤敏感字段
    client_ip VARCHAR(45),               -- IPv4/IPv6，取 X-Forwarded-For 首段
    user_agent VARCHAR(512),             -- 原始 UA 截断
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg TEXT,                      -- 失败原因（截断 500 字符）
    entry VARCHAR(16),                   -- 入口：web / api / channel（渠道回调），见枚举 BehaviorEntry
    login_method VARCHAR(32),            -- 登录方式：password / sms / sso；仅登录事件有值
    channel VARCHAR(16),                 -- 渠道：wecom / wecom_kf / wecom_personal_rpa / dingtalk / feishu；仅渠道事件有值
    channel_user_id TEXT,                -- 渠道侧用户标识（external_userid 等）；仅渠道事件有值
    token_id TEXT,                       -- 当前 token 的 SHA256 前 8 位（关联登录态，不存原文）
    request_id TEXT,                     -- obs 系统 trace_id，无则 NULL
    http_method VARCHAR(10),             -- CRUD 事件：请求方法（GET/POST/PUT/PATCH/DELETE）
    path VARCHAR(255),                   -- CRUD 事件：请求路径（如 /api/saas/tenants）
    device_type VARCHAR(16),             -- 粗分设备类型：pc / mobile / tablet / unknown，见枚举 BehaviorDeviceType
    device_info VARCHAR(32),             -- 细分设备快照：写入时解析冻结（如 android_wechat），受控词表见 §5.4
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ubl_tenant_time ON user_behavior_logs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ubl_user_time ON user_behavior_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ubl_action_time ON user_behavior_logs(action, created_at DESC);
```

**落库位置**：
- `deploy/init-postgres.sql` 增加建表语句（参照 `log_error` 表 :226 的风格）；
- `deploy/db_update.yaml` 增加一条增量（datetime 取当前时刻）。

## 4. 枚举定义

按枚举规范，定义在 `src/saas/models/enums.py`（TEXT 值 = 数据库存值），前端同步到 `frontend/web/api/enums.ts`：

```python
class BehaviorAction(str, Enum):
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    VERIFY_CODE_SENT = "verify_code_sent"
    PROFILE_UPDATE = "profile_update"
    CHANNEL_BIND = "channel_bind"        # 渠道账号绑定（entry=channel）
    CHANNEL_UNBIND = "channel_unbind"    # 渠道账号解绑（entry=channel）
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    BATCH_DELETE = "batch_delete"
    EXPORT = "export"

class BehaviorEntry(str, Enum):
    WEB = "web"                          # web 前端发起
    API = "api"                          # 脚本/第三方直接调 API
    CHANNEL = "channel"                  # 渠道回调（无用户侧 IP/UA）

class BehaviorResourceType(str, Enum):
    TENANT = "tenant"
    TENANT_USER = "tenant_user"
    SUBAGENT = "subagent"
    PROMPT = "prompt"
    SESSION = "session"
    KNOWLEDGE_DOC = "knowledge_doc"
    CONFIG = "config"
    BILLING = "billing"
    ACCOUNT = "account"        # 自己的账号（改密码/改资料）

class BehaviorDeviceType(str, Enum):
    PC = "pc"
    MOBILE = "mobile"
    TABLET = "tablet"
    UNKNOWN = "unknown"
```

各枚举成员带中文 `display_name` property（参照 `TenantStatus` 风格，enums.py:20）。

## 5. 写入机制

新增模块 `src/services/behavior_log.py`，两个能力：

### 5.1 核心写入函数

```python
async def record_behavior(
    request: Request,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_name: str | None = None,
    detail: dict | None = None,
    user_id: str | None = None,     # 登录失败时用户身份未知，由调用方显式传
    success: bool = True,
    error_msg: str | None = None,
) -> None:
```

- `tenant_id` / `user_id` 默认从 `request.state`（`TenantContextMiddleware` 已注入，middleware.py:78）读取，`X-Tenant-Id` 优先级遵循现有规则；
- IP 解析：`X-Forwarded-For` 首段（生产部署在 nginx 后）→ fallback `request.client.host`；
- UA：`request.headers.get("user-agent", "")[:512]`；
- **写入失败仅 `logger.opt(exception=True).error()`，绝不抛出影响业务主流程**；
- 用 `asyncio.to_thread` 包裹 INSERT（假异步规范）；登录/登出路径若在同步上下文调用，提供同步版本 `record_behavior_sync()`。

### 5.2 路由装饰器（用于批量挂到 CRUD 端点）

```python
def audit_action(action: str, resource_type: str,
                 id_arg: str | None = None, name_arg: str | None = None):
    """装饰 FastAPI 路由函数；从路径参数/请求体提取 resource_id / resource_name"""
```

- 装饰器在业务函数**成功返回后**记 `success=True`，抛异常时记 `success=False` + error_msg 后**原样 re-raise**；
- `resource_name` 优先取请求体中的名称字段（如租户名、文档标题），避免为记日志额外查库；
- 登录/登出/改密码等**不适用装饰器**（需要在成功与失败分支分别记录、且 user 身份特殊），采用显式调用。

### 5.3 detail 内容规范

- **变更前后值摘要（old → new）**：增删改审计的核心是"改了什么"。至少记变更字段名列表；关键业务字段（如租户状态 `active` → `suspended`、积分系数）记新旧值对。注意过滤敏感字段。
- **batch_id**：批量操作（批量删除文档、批量移动分类）生成一个批次 ID 写进每条记录，一次误操作可整体回溯。
- **敏感信息过滤**：写入前递归过滤以下键：`password`、`new_password`、`old_password`、`token`、`api_key`、`secret`、`code`（验证码）。与 `sanitize_error_info` 的模式表保持一致。

### 5.4 设备类型解析（自写正则，不引重依赖）

UA 来源只有两种（web 前端、微信/钉钉/飞书内置浏览器），规则集很小、完全可控：

- **匹配顺序：先判 App 内嵌，再判 OS**。安卓微信 UA 同时含 `Mobile` 和 `MicroMessenger`，若先判 OS 会被误归为 `android_mobile`。
- **受控词表**（写入代码内枚举约束，不进 enums.py 正式枚举）：
  `windows_pc / mac_pc / linux_pc / android_mobile / ios_mobile / harmony_mobile / android_wechat / ios_wechat / unknown`（钉钉/飞书内嵌后续按需扩展）。
- **device_type 粗分**由 device_info 归并：`*_pc` → pc，`*_mobile` / `*_wechat` → mobile。
- **写入时解析冻结**：细分值随事件写入 `device_info`，解析规则日后升级不影响历史记录口径；原始 `user_agent` 列保留，可离线重查更细维度。

### 5.5 其他字段来源

| 字段 | 来源 |
|------|------|
| `token_id` | `SHA256(Authorization 中的 token)[:8]`，关联登录态、不存原文。同一 token 的异常操作可串成时间线，支撑"登出后旧 token 仍在操作"类审计 |
| `request_id` | obs 分布式追踪的 trace_id（有则取，无则 NULL），行为日志可直接跳转到完整请求链路（含 LLM 调用） |
| `http_method` / `path` | `@audit_action` 装饰器从 request 自动取，零成本精确定位端点 |
| `entry` | 装饰器默认 web；登录/渠道事件由调用方显式传 |

### 5.6 渠道场景（无登录事件）

飞书、钉钉、企微、企微客服（wecom_kf）渠道会话**没有登录步骤**，用户直接对话。处理原则：

- **渠道对话本身不逐条入行为日志**——与不在 `chat_records` 逐条记 IP/UA 同理，高频重复无审计价值。渠道侧的用户触达追溯由会话/消息表承担。
- **有审计价值的渠道事件**：渠道账号绑定/解绑（action=`channel_bind` / `channel_unbind`，Phase 1 随认证事件落地）、个人微信 RPA 账号上下线等低频状态事件。渠道配置类变更已归管理后台 CRUD 范围。
- **字段语义**（渠道事件）：

  | 字段 | 取值 |
  |------|------|
  | `entry` | `channel` |
  | `channel` / `channel_user_id` | 渠道标识 + 渠道侧用户标识（external_userid 等），未注册为系统用户也能追溯 |
  | `client_ip` | **回调来源 IP**（server-to-server，非用户侧 IP）。仅用于回调伪造排查，管理后台展示时需标注"渠道回调来源"避免误读 |
  | `user_agent` / `device_type` / `device_info` | NULL（无浏览器上下文） |
  | `token_id` | NULL（非 token 会话） |

## 6. 空间估算与清理策略

### 6.1 空间估算

- 单行约 0.3~0.5 KB（detail 通常为空或很小）；
- 登录/登出事件：数百用户量级，每天数百条；
- 管理后台增删改：低频，每天数十~数百条；
- **合计每年 < 100 万行、< 500 MB**，相对 `chat_records`（每条对话都落大文本）可忽略。

### 6.2 清理策略（scheduler 周期任务）

在 `src/scheduler/manager.py` 的 `_register_system_jobs()`（manager.py:84）注册每日任务：

- 默认保留 **180 天**，可配置（`configs/config.yaml` + settings.py 同步字段 `behavior_log.retention_days`）；
- `DELETE FROM user_behavior_logs WHERE created_at < NOW() - INTERVAL '180 days'`，分批删除（每批 1 万行循环，避免长锁）；
- 清理任务执行结果记 `logger.info`（删除行数、耗时）。

初版不引入分区表，量级不需要；若未来单表超 5000 万行再评估按月分区。

## 7. 查询与展示（Phase 4，可选）

- 平台管理员：全局操作日志页（支持按租户/用户/行为/时间筛选）；
- 租户管理员：本租户操作日志页（`X-Tenant-Id` 隔离）；
- 复用列表页规范（BaseTable + BasePagination + `usePageContext`），页面元数据在 `configs/page_metadata.yaml` 登记。

API 设计：`GET /api/admin/behavior-logs`（租户管理员）、`GET /api/saas/behavior-logs`（平台管理员），列表按 `created_at DESC`。

## 8. 分阶段实施

| 阶段 | 内容 | 说明 |
|------|------|------|
| Phase 1 | 表 + 写入模块 + 登录/登出/改密/渠道绑定事件全量挂点 | 核心价值最先落地，IP/UA + 设备快照随登录事件入库；渠道事件按 §5.6 语义落库 |
| Phase 2 | `audit_action` 装饰器挂平台管理员 `/api/saas/*` CRUD | 按文件逐个挂，改动小、可分批提交 |
| Phase 3 | 租户管理员 `/api/admin/*` CRUD + 普通用户动作 | 删除会话、知识库文档增删 |
| Phase 4 | 管理后台查询页面 | 前端列表页，按页面规范走 page_metadata 登记 |

**回滚安全**：写入失败不影响业务；下线只需移除装饰器与显式调用，表保留。

## 9. 计费审计边界说明

本模块只记行为，**不触碰** `chat_records` 计费链路，不新增 LLM/Embedding/ASR 调用点，不涉及 [billing_audit.md](../../.claude/rules/billing_audit.md) 的核对义务。
