# 租户入口调整计划

## 背景

当前租户管理员通过 `/portal` 登录管理后台。根据新需求，需要：
- 统一入口为 `/t/{tenant_id}`，和普通用户的入口一致
- 平台管理员可通过该URL登录到指定租户进行代管理
- 根据用户角色显示不同菜单
- 管理后台`/portal`，仅用于平台管理员管理租户

---

## 需求变更

### 1. 入口调整
- **原入口**: `/portal` → 作为管理后台，仅平台管理员可访问
- **新入口**: `/t/{tenant_id}` → 作为应用前台，所有用户都可以访问，其中：
  - **平台管理员**：通过 `/t/{tenant_id}` 进入任意租户，可代为管理。可以看到所有菜单，使用所有功能，管理该租户的所有数据。
  - **租户管理员**：通过 `/t/{tenant_id}` 进入本租户管理后台。可以看到所有菜单，使用所有功能，管理该租户的所有数据。
  - **普通用户**：通过 `/t/{tenant_id}` 仅使用数字员工服务
  - **登录流程**：用户访问 `/t/{tenant_id}`，前端检测到未登录，自动跳转到 `/t/{tenant_id}/login`

### 2. 菜单调整

根据角色显示不同菜单：

| 角色 | 可见菜单 |
|------|----------|
| platform_admin | 仪表盘、用户管理、企业知识库、渠道配置、企业设置、聊天功能 |
| tenant_admin | 仪表盘、用户管理、企业知识库、渠道配置、企业设置、聊天功能 |
| 普通用户 | 无管理菜单，仅聊天功能 |

> **注意**：数字员工**管理**是 `/portal` 的功能（见 2.3），不属于 `/t/{tenant_id}` 的菜单。

**删除的菜单项**：
- `/portal/skills` - Skill 管理
- `/portal/reports` - 用量报告
- `/portal/billing` - 计费管理
- `/portal/instances` - 智能体管理（与"数字员工"功能重复）

### 3. 管理后台 `/portal` 保留功能

`/portal` 仅保留以下功能，仅平台管理员可访问：

```typescript
const portalMenuItems = [
  { path: '/portal', label: '仪表盘', icon: '📊' },
  { path: '/portal/tenants', label: '租户管理', icon: '🏢' },
]
```

新增路由：
- `/portal/subagents` - 数字员工管理（复用 `DigitalEmployeeManager.vue`）
- 删除 `/portal/instances`（`InstanceManager.vue` 可删除，功能由数字员工管理替代）

**数字员工管理说明**：
- 数字员工由平台管理员统一配置，无需租户隔离
- 租户管理员不能配置数字员工
- 演示模式无需"数字员工"管理，移入 SaaS 模式即可

---

## 实施方案

### 一、前端修改

#### 1.1 路由调整 (`main.ts`)

新增 `/t/:tenant_id` 路由，保留精简后的 `/portal` 路由：

```typescript
// 管理后台 /portal（仅平台管理员）
{
  path: '/portal/login',
  name: 'portal-login',
  component: () => import('./components/saas/TenantLogin.vue')
},
{
  path: '/portal/reset-password',
  name: 'portal-reset-password',
  component: () => import('./components/saas/ResetPassword.vue')
},
{
  path: '/portal',
  component: () => import('./components/saas/PortalLayout.vue'),
  children: [
    { path: '', name: 'portal-dashboard', component: () => import('./components/saas/TenantDashboard.vue') },
    { path: 'tenants', name: 'portal-tenants', component: () => import('./components/saas/TenantMgmt.vue') },
    { path: 'subagents', name: 'portal-subagents', component: () => import('./components/DigitalEmployeeManager.vue') },
  ]
},
// 租户入口 /t/:tenant_id（所有用户）
{
  path: '/t/:tenant_id',
  component: () => import('./components/saas/PortalLayout.vue'),
  children: [
    { path: '', name: 'tenant-dashboard', component: () => import('./components/saas/TenantDashboard.vue') },
    { path: 'users', name: 'tenant-users', component: () => import('./components/saas/TenantUserManager.vue') },
    { path: 'knowledge', name: 'tenant-knowledge', component: () => import('./components/saas/TenantKnowledgeBase.vue') },
    { path: 'channels', name: 'tenant-channels', component: () => import('./components/saas/ChannelConfig.vue') },
    { path: 'settings', name: 'tenant-settings', component: () => import('./components/saas/TenantSettings.vue') },
    { path: 'chat', name: 'tenant-chat', component: () => import('./components/ChatContainer.vue') },
  ]
}
```

#### 1.2 菜单配置修改

菜单路径中的 `:tenant_id` 占位符在导航时必须替换为当前 URL 中的实际租户 ID：

```typescript
// 在 PortalLayout.vue 中，根据当前路由的 tenant_id 参数动态生成菜单路径
import { useRoute } from 'vue-router'
const route = useRoute()
const tenantId = computed(() => route.params.tenant_id as string)

// 判断是否在 /t/:tenant_id 路由下
const isTenantRoute = computed(() => !!tenantId.value)

// /t/:tenant_id 下的管理员菜单（platform_admin + tenant_admin）
const tenantAdminMenuItems = computed(() => [
  { path: `/t/${tenantId.value}`, label: '仪表盘', icon: '📊' },
  { path: `/t/${tenantId.value}/users`, label: '用户管理', icon: '👥' },
  { path: `/t/${tenantId.value}/knowledge`, label: '企业知识库', icon: '📚' },
  { path: `/t/${tenantId.value}/channels`, label: '渠道配置', icon: '📡' },
  { path: `/t/${tenantId.value}/settings`, label: '企业设置', icon: '⚙️' },
  { path: `/t/${tenantId.value}/chat`, label: '聊天', icon: '💬' },
])

// 普通用户菜单（仅聊天）
const tenantUserMenuItems = computed(() => [
  { path: `/t/${tenantId.value}/chat`, label: '聊天', icon: '💬' },
])

// /portal 下的菜单（仅平台管理员）
const portalMenuItems = [
  { path: '/portal', label: '仪表盘', icon: '📊' },
  { path: '/portal/tenants', label: '租户管理', icon: '🏢' },
  { path: '/portal/subagents', label: '数字员工', icon: '🤖' },
]

// 根据路由和角色选择菜单
const currentMenuItems = computed(() => {
  if (!isTenantRoute.value) {
    return portalMenuItems  // /portal 下固定显示平台管理员菜单
  }
  if (admin.value?.role === 'platform_admin' || admin.value?.role === 'tenant_admin') {
    return tenantAdminMenuItems.value
  }
  return tenantUserMenuItems.value
})
```

**导航跳转规则**：
- SaaS 模式下，所有页面入口网址都带租户 ID `/t/{tenant_id}`
- 导航跳转时，必须使用当前 URL 中的实际租户 ID 替换 `:tenant_id` 占位符
- 不允许使用 `/t/:tenant_id` 这样的原始路径进行 `router.push`

#### 1.3 企业知识库组件 (`TenantKnowledgeBase.vue`)

**决策：创建新组件** `TenantKnowledgeBase.vue`，而非修改现有 `KnowledgeBase.vue`。

**原因**：
1. `KnowledgeBase.vue`（760行）深度绑定演示模式：使用 `useAuth`（demo_token）、`AppHeader`、`SessionSidebar`、`CredentialManager` 等组件
2. 导航路径硬编码为 `router.push('/')`，不适用于 SaaS 模式
3. 认证体系完全不同（demo_token vs saas_token + X-Tenant-Id Header）
4. 保持演示模式不受影响，避免回归

**实现方式**：
- 复制 `KnowledgeBase.vue` 为 `TenantKnowledgeBase.vue`
- 将 `useAuth` 替换为 `useTenantAuth`
- 将所有 API 调用改为携带 `X-Tenant-Id` Header（从路由参数获取 tenant_id）
- 移除 `AppHeader`、`SessionSidebar`、`CredentialManager` 等演示模式专用组件（SaaS 模式下侧边栏由 `PortalLayout.vue` 提供）
- 导航路径改为 `/t/${tenantId}/chat`
- 文件上传路径需带租户 ID

#### 1.4 企业设置组件 (`TenantSettings.vue`)

现有 `TenantSettings.vue` 已有基础功能（企业名称、联系人、联系电话），需要补充：

- **租户ID**（只读）：显示当前租户 ID
- **企业名称**：已有
- **Logo**：新增上传企业 Logo 功能

修改后的表单字段：

```typescript
const form = ref({
  tenant_id: '',      // 只读
  company_name: '',   // 可编辑
  logo_url: '',       // 可编辑，上传 Logo
})
```

#### 1.5 登录页面改造 (`TenantLogin.vue`)

- 租户登录页，地址必须有租户ID `/t/{tenant_id}`，否则报错："URL网址必须有租户ID"
- 注意和演示模式登录页 `LoginModal.vue` 区分开，演示模式不区分租户
- 前端登录逻辑需要从 URL 获取 `tenant_id` 并传给后端：

```typescript
const route = useRoute()
const tenantId = computed(() => route.params.tenant_id as string)

// 登录时将 tenant_id 传给后端
const res = await adminPasswordLogin({
  identifier: identifier.value,
  password: password.value,
  captcha_code: captchaCode.value,
  captcha_id: captchaId.value,
  tenant_id: tenantId.value,  // 新增
})
```

- 登录成功后跳转到 `/t/${tenantId}`（而非 `/portal`）

#### 1.6 聊天功能集成

SaaS 模式下的聊天功能，前端页面和演示模式一样（复用 `ChatContainer.vue`），区别仅在后端接口：

| 对比项 | 演示模式 | SaaS 模式 |
|--------|----------|-----------|
| 前端组件 | `ChatContainer.vue` | 同一个 `ChatContainer.vue` |
| 认证方式 | `useAuth` (demo_token) | `useTenantAuth` (saas_token) |
| 请求 Header | `Authorization: Bearer {demo_token}` | `Authorization: Bearer {saas_token}` + `X-Tenant-Id: {tenant_id}` |
| 后端数据隔离 | 无 | 按租户 ID 隔离 |
| 文件上传路径 | `/uploads/xxx` | `/uploads/{tenant_id}/xxx` |
| 数据库存入 | 无 tenant_id | 带 tenant_id |

**需要修改 `ChatContainer.vue`**：
- 检测当前是否在 SaaS 模式下（通过路由参数 `tenant_id` 判断）
- SaaS 模式下使用 `useTenantAuth` 获取 token，并在所有 API 请求中携带 `X-Tenant-Id` Header
- 演示模式下保持原有逻辑不变

---

### 二、后端修改

#### 2.1 登录接口调整 (`tenant_auth.py`)

**AdminPasswordLoginRequest 模型增加 tenant_id 字段**：

```python
class AdminPasswordLoginRequest(BaseModel):
    """管理员密码+图形验证码登录请求"""
    identifier: str = Field(..., description="手机号或用户名")
    password: str = Field(..., description="密码")
    captcha_code: str = Field(..., description="图形验证码")
    captcha_id: str = Field(..., description="图形验证码ID")
    tenant_id: str = Field(..., description="租户ID")
```

**登录逻辑修改**：
1. 平台管理员（role=platform_admin）：`tenant_id` 可为任意值，后端验证租户存在即可
2. 租户管理员（role=tenant_admin）：`tenant_id` 必须等于用户自身的 `tenant_id`，否则返回错误
3. 普通用户：`tenant_id` 必须等于用户自身的 `tenant_id`

**普通用户登录**：
- 无需单独的登录接口，和租户管理员使用同一个登录接口 `/api/saas/auth/login/password`
- 数据库表都是 `users`，普通用户 role 为 `user`，租户管理员 role 为 `tenant_admin`
- 登录成功后根据 role 判断显示菜单：platform_admin/tenant_admin 显示管理菜单，user 只显示聊天
- 普通用户密码由管理员在用户管理页面创建用户时设置
- 普通用户也可通过"忘记密码"功能使用手机短信验证码重置密码

#### 2.2 平台管理员代管理 - 租户上下文切换机制

**核心机制：前端请求携带 `X-Tenant-Id` Header**

1. **前端**：所有 `/t/{tenant_id}` 下的 API 请求，都携带 `X-Tenant-Id` Header，值为当前 URL 中的 `tenant_id`

```typescript
// saasTenant.ts 中的 getSaasAuthHeader 修改
function getSaasAuthHeader(): Record<string, string> {
  const headers: Record<string, string> = {}
  const token = localStorage.getItem('saas_token')
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  // 从当前路由获取 tenant_id
  const tenantId = getCurrentTenantId()  // 新增工具函数
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }
  return headers
}
```

2. **后端中间件** (`middleware.py`)：从 `X-Tenant-Id` Header 获取目标租户 ID

```python
# TenantContextMiddleware 中新增逻辑
async def dispatch(self, request: Request, call_next):
    # ... 现有逻辑 ...
    
    # 4. 从 X-Tenant-Id Header 获取目标租户（平台管理员代管理）
    x_tenant_id = request.headers.get("X-Tenant-Id")
    if x_tenant_id and not tenant_id:
        tenant_id = x_tenant_id
```

3. **后端 `require_admin()` 修改**：返回的 `tenant_id` 应优先使用 `X-Tenant-Id` Header 中的值

```python
def require_admin(request: Request) -> dict:
    admin = get_current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    
    # 平台管理员代管理：使用 X-Tenant-Id Header
    x_tenant_id = request.headers.get("X-Tenant-Id")
    if admin["role"] == "platform_admin" and x_tenant_id:
        # 验证目标租户存在
        target_tenant = TenantDB.get_by_id(x_tenant_id)
        if not target_tenant:
            raise HTTPException(status_code=404, detail="目标租户不存在")
        admin["tenant_id"] = x_tenant_id  # 切换到目标租户
    elif admin["role"] == "tenant_admin":
        # 租户管理员只能访问自己的租户
        if x_tenant_id and x_tenant_id != admin.get("tenant_id"):
            raise HTTPException(status_code=403, detail="无权访问其他租户")
    
    return admin
```

4. **后端 API 数据隔离**：
   - 每次接口请求，都从 `X-Tenant-Id` 或 `admin["tenant_id"]` 获取租户 ID
   - 存入数据库的数据，都带租户 ID
   - 上传的文件，存放路径带租户 ID：`/uploads/{tenant_id}/xxx`
   - 查询数据时，都按租户 ID 过滤

#### 2.3 平台管理员权限

- 平台管理员（role=platform_admin）可以通过任意 `tenant_id` 访问 `/t/{tenant_id}`
- 后端验证：平台管理员可访问所有租户，租户管理员只能访问自己租户，访问其他租户应报错

---

### 三、认证体系说明

#### 3.1 `/t/{tenant_id}` 下使用 `useTenantAuth.ts`

SaaS 模式统一使用 `saas_token`，与演示模式的 `demo_token` 完全隔离。

#### 3.2 两套 Token 互不干扰

| 项目 | 演示模式 (`useAuth`) | SaaS 模式 (`useTenantAuth`) |
|------|----------------------|------------------------------|
| localStorage key | `demo_token` | `saas_token` |
| 用户信息 key | `user_info` | `saas_admin` |
| 租户信息 key | 无 | `saas_tenant` |
| 路由 | `/`、`/chat/:subagent` | `/t/:tenant_id/*` |
| 后端 token 前缀 | 无前缀 | `saas_` 前缀 |

两套 token 的 localStorage key 完全不同，不会互相覆盖。

#### 3.3 平台管理员同时访问 `/portal` 和 `/t/{tenant_id}`

平台管理员需要分别登录两次：
1. 在 `/portal/login` 登录 → 获取 `saas_token`，存入 `localStorage['saas_token']`
2. 在 `/t/{tenant_id}/login` 登录 → 获取 `saas_token`，存入 `localStorage['saas_token']`

**问题**：两次登录都写入同一个 `localStorage['saas_token']`，后者会覆盖前者。

**解决方案**：将 SaaS token 的 localStorage key 区分为两种：

| 场景 | localStorage key | 说明 |
|------|------------------|------|
| `/portal` 管理后台 | `portal_token` | 平台管理员的 portal token |
| `/t/{tenant_id}` 租户入口 | `saas_token` | 租户管理员/平台管理员/普通用户的 token |

对应修改：
- `useTenantAuth.ts`：需要支持两种 token 存储 key，根据路由判断使用哪个
- 或者拆分为两个 composable：`usePortalAuth.ts` 和 `useTenantAuth.ts`

**推荐方案**：在 `useTenantAuth.ts` 中，根据当前路由判断使用哪个 token key：

```typescript
function getTokenKey(): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    return 'portal_token'
  } else if (path.startsWith('/t/')) {
    return 'saas_token'
  }
  return 'demo_token'  // 演示模式
}
```

**注意**：演示模式的 token 也需要从 `auth_token` 改为 `demo_token`，与 `useAuth.ts` 配合修改。

#### 3.4 修复 `getAdminInfo` API 返回格式与 `useTenantAuth.init()` 不匹配

**问题**：`useTenantAuth.ts` 第49行检查 `info?.admin && info?.tenant`，但后端 `/api/saas/auth/me` 返回的是 `{ user: {...}, tenant: {...} }`，没有 `admin` 字段。

**修复方案**：修改后端 `/api/saas/auth/me` 返回格式，与前端期望一致：

```python
# 修改前
return {
    "user": {...},
    "tenant": {...}
}

# 修改后（或修改前端）
return {
    "admin": {...},   # 改名，与前端 useTenantAuth 匹配
    "tenant": {...}
}
```

或者修改前端 `useTenantAuth.init()`：

```typescript
// 修改前
if (info?.admin && info?.tenant) {

// 修改后
if (info?.user && info?.tenant) {
  admin.value = info.user
  tenant.value = info.tenant
```

**推荐**：修改前端 `useTenantAuth.init()`，因为后端 `/api/saas/auth/me` 返回 `user` 语义更准确，且其他接口也使用 `user` 字段名。

---

### 四、需要修改的文件清单

| 文件 | 修改内容 |
|------|----------|
| **前端** | |
| `frontend/src/main.ts` | 添加 `/t/:tenant_id` 路由，精简 `/portal` 路由，新增 `/portal/subagents` 路由 |
| `frontend/src/components/saas/PortalLayout.vue` | 根据路由和角色动态显示菜单，支持 `/portal` 和 `/t/:tenant_id` 两种布局 |
| `frontend/src/components/saas/TenantLogin.vue` | 从 URL 获取 tenant_id 传给后端，登录后跳转到 `/t/${tenantId}` |
| `frontend/src/components/saas/TenantKnowledgeBase.vue` | **新建**，基于 `KnowledgeBase.vue` 改造，替换认证和导航，API 携带 X-Tenant-Id |
| `frontend/src/components/saas/TenantSettings.vue` | 增加租户ID（只读）、Logo 上传功能 |
| `frontend/src/components/saas/InstanceManager.vue` | **删除**（功能由 `/portal/subagents` 的 DigitalEmployeeManager 替代） |
| `frontend/src/composables/useTenantAuth.ts` | 修复 `info?.admin` 为 `info?.user` |
| `frontend/src/composables/usePortalAuth.ts` | **新建**，管理 `/portal` 认证状态，使用 `portal_token` 等独立 key |
| `frontend/src/api/saasTenant.ts` | `AdminPasswordLoginRequest` 增加 `tenant_id` 字段，`getSaasAuthHeader` 增加 `X-Tenant-Id` Header |
| `frontend/src/components/ChatContainer.vue` | 支持 SaaS 模式，检测路由中 `tenant_id`，SaaS 模式下使用 `useTenantAuth` + `X-Tenant-Id` |
| `frontend/src/components/DigitalEmployeeManager.vue` | 适配 SaaS 模式认证（从 `/admin/subagents` 移到 `/portal/subagents`） |
| **后端** | |
| `src/saas/api/tenant_auth.py` | `AdminPasswordLoginRequest` 增加 `tenant_id` 字段，复用同一登录接口，修改 `require_admin()` 支持平台管理员代管理 |
| `src/saas/middleware.py` | 从 `X-Tenant-Id` Header 获取目标租户 ID |
| `src/saas/api/tenant_mgmt.py` | 支持平台管理员通过 `X-Tenant-Id` 代管理租户数据 |

---

## 验证方式

1. **租户管理员登录**：
   - 访问 `/t/{tenant_id}/login`
   - 登录后跳转到 `/t/{tenant_id}`
   - 菜单显示：仪表盘、用户管理、企业知识库、渠道配置、企业设置、聊天

2. **平台管理员登录（代管理租户）**：
   - 访问 `/t/{tenant_id}/login`
   - 登录后跳转到 `/t/{tenant_id}`
   - 菜单显示：仪表盘、用户管理、企业知识库、渠道配置、企业设置、聊天
   - API 请求携带 `X-Tenant-Id` Header，后端按目标租户返回数据

3. **平台管理员登录（管理后台）**：
   - 访问 `/portal/login`
   - 登录后跳转到 `/portal`
   - 菜单显示：仪表盘、租户管理、数字员工

4. **普通用户访问**：
   - 访问 `/t/{tenant_id}`
   - 需要登录验证
   - 登录后不显示管理菜单，仅有聊天功能

5. **权限验证**：
   - 租户管理员访问其他租户 `/t/{other_tenant_id}` 应被拒绝
   - 平台管理员可访问任意租户

6. **Token 隔离验证**：
   - 平台管理员在 `/portal` 和 `/t/{tenant_id}` 分别登录，互不影响
   - 两个页面的 localStorage key 不同（`portal_token` vs `saas_token`）

7. **SaaS 模式聊天验证**：
   - 普通用户在 `/t/{tenant_id}/chat` 聊天
   - 后端接口携带 `X-Tenant-Id` Header
   - 数据按租户 ID 隔离存储
   - 上传文件路径为 `/uploads/{tenant_id}/xxx`
