# SaaS 模式代码审查报告

**审查日期**: 2026-04-22
**审查范围**: SaaS 多租户相关前后端代码（排除智能体聊天、LLM 调用逻辑）
**审查人**: Claude Code

---

## 一、严重问题 (Critical)

### 1. 【已修复】密码哈希使用 SHA256，无盐值 —— 可被暴力破解 

**文件**: `src/db/models.py:20-22`

```python
def hash_password(password: str) -> str:
    """简单密码哈希（生产环境应使用bcrypt）"""
    return hashlib.sha256(password.encode()).hexdigest()
```

**问题**: SHA256 是快速哈希，无盐值，攻击者可使用彩虹表或 GPU 暴力破解。代码注释已承认需使用 bcrypt，但一直未更换。

**影响**: 全系统所有使用密码的用户（含租户管理员、平台管理员）密码均不安全。

**建议**: 替换为 `bcrypt` 或 `argon2-cffi`，迁移策略：系统还未正式上线，所有数据可以清除，无需考虑旧密码的迁移。

---

### 2. 【已修复】验证码明文返回前端，形同虚设

**文件**: `src/api/auth.py:194-207`

```python
@router.get("/captcha")
async def get_captcha():
    captcha = generate_captcha()
    logger.info(f'后端日志：生成验证码, captcha_id={captcha["captcha_id"]}, code={captcha["code"]}')
    return {
        "success": True,
        "captcha_id": captcha["captcha_id"],
        "code": captcha["code"]  # 验证码明文返回！
    }
```

**问题**: 图形验证码的答案通过 API 响应明文返回前端，任何人调用接口即可获取验证码，完全失去防机器人作用。前端 `TenantLogin.vue:151-155` 和 `ResetPassword.vue:195-199` 也在所有环境下展示验证码。

**影响**: 登录和重置密码接口的图形验证码防护完全失效，可被脚本批量爆破。

**建议**: 后端应返回验证码图片（SVG/base64），不返回 `code` 字段。前端始终用图片渲染。

---

### 3. 【已修复】重置密码短信验证码硬编码为 "888888"

**文件**: `src/api/auth.py:622`

```python
if request.sms_code != "888888":
    # TODO: 短信平台确定后，改为调用 verify_sms_code
    return {"success": False, "message": "短信验证码错误或已过期，过期时间5分钟"}
```

**问题**: 重置密码的短信验证码硬编码为固定值 "888888"，任何人知道手机号即可重置密码。这不是 TODO 的问题，是当前生产安全的致命缺陷。

**前端也明文提示**: `ResetPassword.vue:151` 显示 "测试环境短信验证码固定为：888888"。

**影响**: 任何已注册用户的密码都可被重置。

**建议**: 立即替换为 `verify_sms_code(request.phone, request.sms_code)` 调用。

---

### 4. 【已修复】演示模式 phone/login 接口存在万能密码 "888888"

**文件**: `src/api/auth.py:374-424`

```python
MOCK_PASSWORD = "888888"
# 密码为空，输入 888888 可以登录
# 密码已设置，验证密码或 888888
if request.password == MOCK_PASSWORD or password_hash == hash_password(request.password):
```

**问题**: `PhoneLoginRequest` 接口（手机号密码登录）中，"888888" 作为万能密码始终有效——即使已设置密码的用户也能用 "888888" 登录。这与演示模式无关，是硬编码后门。

**影响**: 所有设置了密码的账号都可被 "888888" 登录。

**建议**:  `MOCK_PASSWORD` 逻辑仅在 `demo_enabled` 为 true 时才生效。

---

### 5. 【已修复】CORS 允许所有来源 + 允许凭证

**文件**: `src/main.py:243-249`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**问题**: `allow_origins=["*"]` 配合 `allow_credentials=True` 在 SaaS 多租户系统中意味着任何网站都可以携带用户的 token 发起跨域请求。虽然浏览器在 `credentials=True` + `origins=*` 时实际不发送 cookie（FastAPI/Starlette 也会回退），但这个配置本身表明安全意识不足，且若未来改为 cookie 认证会直接出问题。

**建议**: 配置明确的 `allow_origins` 白名单（前端部署域名为 *.aidingyi.cn 和 localhost）。

---

## 二、高危问题 (High)

### 6. 【已修复】登录接口缺少速率限制

**文件**: `src/api/auth.py` 全部登录端点，`src/saas/api/tenant_auth.py` 全部登录端点

**问题**: 所有登录接口（密码登录、验证码登录、SSO 登录）均无速率限制。攻击者可无限次尝试暴力破解密码。

**影响**: 可被暴力枚举密码，尤其在 SHA256 无盐的条件下更易被攻破。

**建议**: 对 `/api/auth/login`、`/api/saas/auth/login/password` 等接口添加 IP/手机号级别的速率限制，该速率限制可以在 .env 中定义，默认 5 次/分钟。

---

### 7. 【已修复】Token 无并发会话控制

**文件**: `src/api/auth.py:114-128`

```python
def generate_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    # 直接 INSERT，不清理旧 token
    cursor.execute("INSERT INTO tokens (token, user_id, expires_at) VALUES (%s, %s, %s)", ...)
```

**问题**: 每次登录都创建新 token，旧 token 不失效。用户可以同时在无数设备上登录，无法检测账号被盗。

**建议**: 限制最大并发 token 数，可以在 .env 中定义，默认2个（即一个用户可以在2台设备上同时登录）。

---

### 8. 【已修复】平台管理员密码明文比较（QBTOKEN）

**文件**: `src/api/auth.py:294-300`，`src/saas/api/tenant_auth.py:339-348`

```python
is_platform_admin = (
    is_phone
    and admin_phones
    and identifier in getattr(admin_phones, "phones", [])
    and qb_token
    and request.password == qb_token  # 明文比较
)
```

**问题**: 平台管理员密码直接与配置中的 `QBTOKEN` 明文比较。如果配置泄露（如 config.yaml 进入版本控制），所有平台管理员账号泄露。

**建议**: QBTOKEN 应使用环境变量存储，且密码比较应走 hash 流程而非明文比较。同时给出一个 python 方法，计算 hash，用于后期修改 `QBTOKEN`

---

### 9. 【已修复】SaaS 认证 token 前缀泄露信息

**文件**: `src/saas/api/tenant_auth.py:268, 423`

```python
token = f"saas_{secrets.token_urlsafe(32)}"
```

**问题**: SaaS 管理员 token 以 `saas_` 前缀开头，而普通用户 token 无前缀。这泄露了 token 的类型信息，攻击者可据此判断 token 类型，缩小攻击面。

**建议**: 统一 token 格式，不加类型前缀。

---

### 10. 【已修复】管理员认证逻辑在 middleware 和 API 层重复实现

**文件**: `src/saas/middleware.py:81-140` vs `src/saas/api/tenant_auth.py:137-215`

**问题**: `TenantContextMiddleware._resolve_admin_tenant()` 和 `get_current_admin()` / `require_admin()` 各自独立实现了 token 验证 + 用户查询逻辑，代码高度重复。middleware 中有自己的 token 过期检查逻辑，`tenant_auth.py` 中又有另一套。

**影响**: 两处逻辑如果更新不同步会导致安全漏洞。例如 middleware 的过期检查会 `datetime.strptime` 兼容 SQLite，但 `tenant_auth.py:166` 直接比较不过做兼容处理：

```python
# tenant_auth.py:166 — 对 SQLite 返回的字符串直接比较，会抛异常
if datetime.now() > row["expires_at"]:
```

**建议**: 抽取统一的 token 验证服务，所有调用方复用。

---

## 三、中危问题 (Medium)

### 11. 【已修复】密码规则正则表达式格式异常

**文件**: `src/api/auth.py:627`

```python
password_rule = getattr(settings, "password_rule", r"^(%s=.*[A-Za-z])(%s=.*\d).{8,50}$")
```

**问题**: 默认正则中的 `%s=` 是 Python 格式化占位符残留，不是有效的正则语法。这意味着如果 `settings.password_rule` 未配置，密码验证正则将不匹配预期格式，可能导致合法密码被拒绝或非法密码通过。

**建议**: 修正为 `r"^(?=.*[A-Za-z])(?=.*\d).{8,50}$"`。

---

### 12. 【已修复】管理员自动创建用户时缺少 tenant_id

**文件**: `src/api/auth.py:306-319`，`src/saas/api/tenant_auth.py:350-364`

```python
# auth.py:312 — INSERT 不含 tenant_id 字段
cursor.execute(f"""
    INSERT INTO users (user_id, username, phone, role, created_at, updated_at)
    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
""", (user_id, identifier, identifier, "platform_admin", now, now))
```

**问题**: 两处自动创建平台管理员用户的代码都不含 `tenant_id` 字段。如果数据库 `users.tenant_id` 没有默认 NULL，会插入失败。即使成功插入，后续 `get_user_info_with_admin()` 等逻辑可能因缺少 `tenant_id` 字段出现 KeyError。

**建议**: 显式设置 `tenant_id=None`。

---

### 13. 【已修复】`/api/auth/login` 和 `/api/saas/auth/login/password` 逻辑高度重复

**文件**: `src/api/auth.py:231-371` vs `src/saas/api/tenant_auth.py:304-456`

**问题**: 两个密码登录接口的实现逻辑几乎完全相同（验证码校验 → 查用户 → 平台管理员检查 → 自动创建 → 密码验证 → 生成 token），但各自独立实现。任何 bug 修复或安全加固都需要同步两处。

**建议**: 抽取公共认证服务层，两个端点调用统一的服务方法。

---

### 14. 【已修复】租户删除不级联清理关联数据

**文件**: `src/saas/api/tenant_mgmt.py:276-298`

**问题**: 删除租户仅调用 `TenantDB.delete(tenant_id)` 删除 `tenants` 表记录，但未清理：
- 该租户下的用户（`users.tenant_id`）
- agent_instances
- channel_configs
- subscriptions / payment_orders

**影响**: 删除租户后，关联数据变成孤儿数据。用户仍可能通过旧 token 访问系统。

**建议**: 删除租户时使用软删除，标记租户为 `deactivated` 而非物理删除。

---

### 15. 【已修复】平台管理员可查看所有租户的用户列表，无分页

**文件**: `src/saas/api/tenant_users.py:41-52`

```python
if admin.get("role") == "platform_admin":
    users = UserDB.list_users()  # 无分页、无租户过滤
```

**问题**: 平台管理员查看用户列表时调用 `UserDB.list_users()` 返回全量用户，无分页。在用户量大的情况下性能极差且数据泄露风险高。

**建议**: 添加分页参数和租户过滤。

---

### 16. 【已修复】租户列表查询使用硬编码 limit=1000

**文件**: `src/saas/api/tenant_mgmt.py:151`

```python
tenants = TenantDB.list_tenants(limit=1000)
```

**问题**: 硬编码 1000 条限制，无分页参数，超量时不返回剩余数据且不提示。

**建议**: 添加分页参数 `page` / `page_size`。

---

## 四、低危问题 (Low)

### 17. 【已修复】前端 resetPassword API 参数不匹配后端

**文件**: `frontend/src/api/auth.ts:224-230` vs `src/api/auth.py:63-67`

**前端发送**:
```typescript
interface ResetPasswordRequest {
  phone: string
  captcha_code: string   // 后端不接收
  captcha_id: string     // 后端不接收
  sms_code: string
  new_password: string
}
```

**后端接收**:
```python
class ResetPasswordRequest(BaseModel):
    phone: str
    sms_code: str
    new_password: str
    # 无 captcha_code, captcha_id 字段
```

**问题**: 前端多发了 `captcha_code` 和 `captcha_id` 两个字段，后端不接收。虽然 Pydantic 默认忽略多余字段不影响功能，但说明前后端接口定义不一致，可能引发维护混乱。

**建议**: 同步前后端接口定义。

---

### 18. 【已修复】前端 localStorage 暴露敏感信息

**文件**: `frontend/src/composables/useAuth.ts:64-65`，`frontend/src/composables/useTenantAuth.ts:123-125`

```typescript
localStorage.setItem('demo_token', newToken)
localStorage.setItem('user_info', JSON.stringify(userInfo))  // 包含手机号等
localStorage.setItem('saas_admin', JSON.stringify(adminInfo)) // 包含手机号
```

**问题**: 用户手机号等敏感信息存储在 localStorage 中，任何 XSS 漏洞都可读取。

**建议**: 敏感用户信息仅存必要字段（如 user_id、role），手机号等通过 API 获取。

---

### 19. 【已修复】TenantLogin.vue 缺少密码强度前端校验

**文件**: `frontend/src/components/saas\TenantLogin.vue`

**问题**: 登录页面无密码强度提示，而重置密码页面（`ResetPassword.vue`）有密码规则提示。用户可能设置弱密码而不自知。

**建议**: 注册/修改密码时前端增加密码强度校验和提示。

---

## 五、代码质量问题

### 20. 【已修复】SQL 拼接占位符模式冗余

**文件**: `src/api/auth.py:269-272`，多处

```python
placeholder = "%s"
cursor.execute(f"SELECT * FROM users WHERE username = {placeholder}", (identifier,))
```

**问题**: 使用 `f"..."` + 变量占位符 `%s` 的方式比直接写 `%s` 更难读，且给人 "SQL 拼接" 的直觉不安感。虽有参数化，但写法不标准。

**建议**: 直接使用 `%s`，不需要赋值 `placeholder` 变量。

---

### 21. 【已修复】重复 import 语句

**文件**: `src/saas/api/tenant_auth.py` 中 `import secrets`, `from datetime import datetime, timedelta` 在文件顶部和函数内部多处重复 import。

**建议**: 统一到文件顶部 import。

---

