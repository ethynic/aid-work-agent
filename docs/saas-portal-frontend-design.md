# SaaS 租户管理前端设计

## Context

后端 SaaS 多租户 30+ API 已全部就绪（Phase 1-6），但前端 Vue 管理页面完全缺失。需要开发完整的租户管理 Portal，包括登录、仪表盘、智能体管理、渠道配置、用户管理、Skill 管理、用量报告、计费、企业设置共约 10 个页面组件。

## 技术栈（与现有前端一致）

- Vue 3 Composition API + TypeScript
- Tailwind CSS（无 UI 组件库）
- Vue Router 4
- 原生 fetch API + getAuthHeader() 模式
- localStorage 存储 token

## 文件清单

### 新建文件（14 个）

```
frontend/src/
├── api/saasTenant.ts                    # SaaS API client（所有 /api/saas/* 调用）
├── composables/useTenantAuth.ts         # 租户管理员认证状态
├── components/saas/
│   ├── TenantLogin.vue                  # 登录页（手机号+验证码）
│   ├── PortalLayout.vue                 # 侧边栏布局（导航菜单+router-view）
│   ├── TenantDashboard.vue              # 仪表盘（概览统计卡片）
│   ├── InstanceManager.vue              # 智能体实例列表+创建+启停
│   ├── ChannelConfig.vue                # IM 渠道凭证配置
│   ├── TenantUserManager.vue            # 企业用户管理+CSV导入
│   ├── SkillManager.vue                # 自定义 Skill 上传管理
│   ├── UsageReports.vue                 # 用量报告（Token 趋势+用户明细）
│   ├── BillingView.vue                  # 套餐选择+支付+用量展示
│   └── TenantSettings.vue              # 企业信息设置
```

### 修改文件（1 个）

- `frontend/src/main.ts` — 添加 `/portal` 路由组（login + 嵌套子路由）

## 实施步骤

### Step 1: API Client (`api/saasTenant.ts`)

统一封装所有 SaaS API 调用，使用 `saas_token`（独立于公共用户 `auth_token`）：

- `sendAdminSmsCode()`, `adminLogin()`, `adminLogout()`, `getAdminInfo()`
- `getTenantInfo()`, `updateTenantInfo()`, `getTenantStats()`
- `listInstances()`, `createInstance()`, `startInstance()`, `stopInstance()`
- `listChannels()`, `createChannel()`, `updateChannel()`, `deleteChannel()`, `verifyChannel()`
- `listTenantUsers()`, `createTenantUser()`, `batchImportUsers()`, `removeTenantUser()`
- `listSkills()`, `uploadSkill()`, `updateSkill()`, `deleteSkill()`
- `getUsageSummary()`, `getTokenTrend()`, `getUserUsage()`, `exportReport()`
- `getPlans()`, `listSubscriptions()`, `createSubscription()`, `payOrder()`, `getUsage()`

认证 header 用 `localStorage.getItem('saas_token')`，与普通用户 token 分离。

### Step 2: Composable (`composables/useTenantAuth.ts`)

类似 `useAuth.ts` 但管理 SaaS 管理员状态：
- `admin`, `tenant`, `saasToken`, `isLoggedIn`
- `init()` — 从 localStorage 恢复
- `setLogin(token, admin, tenant)` — 存储
- `logout()` — 清除
- `getAuthHeader()` — 返回 `{ Authorization: Bearer ${saasToken} }`

### Step 3: TenantLogin.vue

手机号+验证码登录表单：
- 输入手机号 → 发送验证码 → 输入验证码 → 登录
- 登录成功存储 `saas_token` + admin 信息 + tenant 信息
- 跳转 `/portal`

### Step 4: PortalLayout.vue

侧边栏导航布局：
- 左侧固定宽度侧边栏（240px），深色背景
- 顶部企业名称+管理员信息
- 菜单项：仪表盘/智能体/渠道/用户/Skill/报告/计费/设置
- 右侧 `<router-view />` 显示子页面
- 未登录自动跳转 `/portal/login`

### Step 5: TenantDashboard.vue

概览仪表盘：
- 4 个统计卡片：实例数、用户数、Token 用量（已用/总量）、活跃订阅数
- 调用 `getTenantStats()` + `getUsage()`

### Step 6: InstanceManager.vue

智能体实例管理：
- 列表：显示名称/类型/状态/操作（启动/停止/删除）
- 创建按钮：弹出表单（选 subagent_type + 套餐 + 显示名称）
- 状态标签：running（绿色）/ stopped（灰色）

### Step 7: BillingView.vue

计费页面：
- 套餐卡片（basic/standard/premium 三选一）
- 当前订阅列表 + Token 用量进度条
- 支付订单列表

### Step 8: UsageReports.vue

用量报告：
- 时间范围选择器（7天/30天/90天）
- Token 趋势图（简单的 div 柱状图，不用图表库）
- 用户用量明细表格

### Step 9: ChannelConfig.vue

渠道配置：
- 添加渠道（选类型 wecom/dingtalk/feishu → 填凭证字段）
- 渠道列表 + 验证/编辑/删除

### Step 10: TenantUserManager.vue

用户管理：
- 用户列表表格（用户名/手机号/部门/角色/操作）
- 添加用户弹窗
- CSV 批量导入（文件上传）
- 删除确认

### Step 11: SkillManager.vue

Skill 管理：
- Skill 列表
- 上传新 Skill（名称 + SKILL.md 内容编辑器）
- 编辑/删除

### Step 12: TenantSettings.vue

企业设置：
- 企业名称/联系人/联系电话编辑表单
- 保存按钮

### Step 13: 路由注册 (`main.ts`)

```typescript
// SaaS 租户管理 Portal
{
  path: '/portal/login',
  component: () => import('./components/saas/TenantLogin.vue')
},
{
  path: '/portal',
  component: () => import('./components/saas/PortalLayout.vue'),
  children: [
    { path: '', component: () => import('./components/saas/TenantDashboard.vue') },
    { path: 'instances', component: () => import('./components/saas/InstanceManager.vue') },
    { path: 'channels', component: () => import('./components/saas/ChannelConfig.vue') },
    { path: 'users', component: () => import('./components/saas/TenantUserManager.vue') },
    { path: 'skills', component: () => import('./components/saas/SkillManager.vue') },
    { path: 'reports', component: () => import('./components/saas/UsageReports.vue') },
    { path: 'billing', component: () => import('./components/saas/BillingView.vue') },
    { path: 'settings', component: () => import('./components/saas/TenantSettings.vue') },
  ]
}
```

## 验证

1. `cd frontend && npm run build` — 确认无编译错误
2. `cd frontend && npm test` — 前端测试通过
3. 浏览器访问 `/portal/login` → 登录 → 查看各页面
