# 演示模式与 SaaS 模式区分计划

## 背景

用户反映登录后页面闪回登录页，原因是两套认证系统混淆：
- **演示模式**（/`/`）：任意手机号 + 888888 登录，使用 `/api/auth/*` 接口，token 存储为 `auth_token`
- **SaaS 模式**（`/portal`）：需要是 admin.phones 或 users 表中的管理员，使用 `/api/saas/*` 接口，token 存储为 `saas_token`

当前问题：没有明确的模式开关，接口路径和变量名有误导性。

---

## 模式定义

### 演示模式
- **前端入口**：`/`
- **登录方式**：任意手机号 + 固定密码 888888
- **用途**：快速体验产品功能
- **开关**：`.env` 中 `DEMO_ENABLED=true`（待实现）

### SaaS 模式（生产模式）
- **开关**：`.env` 中 `SAAS_ENABLED=true`（已实现）
- **严格区分 3 类用户**：

| 用户类型 | 前端入口 | 登录条件 | 权限 |
|----------|----------|----------|------|
| 平台管理员 (platform_admin) | `/portal` | config.yaml 的 admin.phones 或 users 表 role='platform_admin' | 管理所有租户的所有数据 |
| 租户管理员 (tenant_admin) | `/portal` | users 表 role='tenant_admin' | 管理本租户的所有数据 |
| 普通用户 | `/t/{tenant_id}` | users 表的正常用户 | 使用数字员工服务 |

---

## 当前代码状态分析

### 1. 入口和路由

| 路由 | 用途 | 认证方式 |
|------|------|----------|
| `/` | 演示模式聊天 | useAuth + /api/auth/* |
| `/portal/*` | SaaS 管理后台 | useTenantAuth + /api/saas/* |
| `/t/{tenant_id}` | 租户前台（待实现） | useAuth + /api/auth/* |

### 2. API 路径

| 前缀 | 当前用途 | 命名问题 |
|------|----------|----------|
| `/api/auth/*` | 演示模式登录、验证码 | "auth" 太通用，SaaS 也容易误用 |
| `/api/saas/*` | SaaS 租户管理 | 清晰 |

### 3. Token 存储键

| 键名 | 用途 |
|------|------|
| `auth_token` | 演示模式 |
| `saas_token` | SaaS 模式 |

**问题**：`auth_token` 命名不明确，saas 模式也可能在代码中误用。

### 4. 环境变量

| 变量 | 作用 |
|------|------|
| `SAAS_ENABLED=true` | 控制后端 SaaS API 是否返回错误 |
| (无) | 演示模式开关 |

---

## 修复方案

### 一、增加配置开关

#### 1.1 后端 .env
```
# 演示模式开关
DEMO_ENABLED=true
```

#### 1.2 后端 config.yaml
```yaml
# 演示模式配置
demo:
  enabled: ${DEMO_ENABLED:-true}
  mock_password: "888888"
```


---

### 二、统一 API 命名（消除歧义）

#### 方案 A：保持现状但增加注释
- `/api/auth/*` → 明确标注为 "演示/公共 API"
- `/api/saas/*` → "租户管理 API"

#### 方案 B：重命名 API 前缀（推荐）
- `/api/auth/*` → `/api/demo/*` 明确演示模式
- `/api/saas/*` → 保持不变

**建议**：采用方案 B，消除误导。

---

### 三、Token 存储键重命名（推荐）

| 当前 | 修改后 | 用途 |
|------|--------|------|
| `auth_token` | `demo_token` | 演示模式 |
| `saas_token` | `saas_token` | SaaS 模式 |

修改位置：
- `frontend/src/composables/useAuth.ts` 第 33、45、64 行
- `frontend/src/composables/useTenantAuth.ts` 第 40、81、113 行
- `frontend/src/api/auth.ts` 中的 API 调用

---

### 四、前端路由和认证守卫

在 `main.ts` 的路由守卫中，根据 URL 路径判断使用哪个认证：

```typescript
// router.beforeEach
const path = to.path
if (path.startsWith('/portal')) {
  // SaaS 模式：检查 useTenantAuth
  const { init: initTenant, isInitialized } = useTenantAuth()
  if (!isInitialized.value) await initTenant()
  if (!isLoggedIn.value) return '/portal/login'
} else {
  // 演示模式：检查 useAuth
  const { init, isInitialized } = useAuth()
  if (!isInitialized.value) await init()
  if (!isLoggedIn.value) showLoginModal.value = true
}
```

---

### 五、后端登录接口适配

#### 5.1 演示模式 `/api/auth/login` 需要根据配置决定
- `DEMO_ENABLED=true`：允许任意手机号 + 888888 登录
- `DEMO_ENABLED=false`：返回"演示模式已关闭"

#### 5.2 SaaS 模式 `/api/saas/auth/login/password` 保持不变
- 需要 admin.phones 中的手机号 + QBTOKEN（配置的管理员密码）
- 或 users 表中 role 为 platform_admin/tenant_admin 的用户 + 密码

---

### 六、需要修改的文件清单

| 文件 | 修改内容 |
|------|----------|
| `.env` | 增加 `DEMO_ENABLED=true` |
| `configs/config.yaml` | 增加 demo 配置节 |
| `src/config/settings.py` | 解析 demo 配置 |
| `src/api/auth.py` 的 login 接口 | 根据 DEMO_ENABLED 决定是否允许任意登录 |
| `frontend/src/composables/useAuth.ts` | auth_token → demo_token |
| `frontend/src/api/auth.ts` | 注释明确为演示模式 API |
| `frontend/src/main.ts` | 路由守卫区分模式 |

---

## 验证方式

1. **演示模式**：
   - 访问 `/`，输入任意手机号 + 888888
   - 能够登录并进入聊天界面

2. **SaaS 模式**：
   - 访问 `/portal/login`
   - 输入 admin.phones 中的手机号 + QBTOKEN（配置的管理员密码）
   - 能够登录并进入管理后台
   - 第二次登录不再闪回

3. **演示模式关闭时**：
   - 设置 `DEMO_ENABLED=false`
   - `/` 页面应提示演示模式已关闭