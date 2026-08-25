# SaaS 租户管理 Portal 测试指南

## Portal 页面清单

| 路由 | 组件 | 功能 |
|------|------|------|
| `/portal/login` | TenantLogin.vue | 管理员手机号+验证码登录 |
| `/portal` | TenantDashboard.vue | 仪表盘：实例数、用户数、Token 用量、活跃订阅 |
| `/portal/instances` | InstanceManager.vue | 智能体实例 CRUD + 启动/停止 |
| `/portal/channels` | ChannelConfig.vue | IM 渠道（企微/钉钉/飞书）凭证配置 |
| `/portal/users` | TenantUserManager.vue | 企业用户管理 + CSV 批量导入 |
| `/portal/skills` | SkillManager.vue | 自定义 Skill 上传/编辑/删除 |
| `/portal/reports` | UsageReports.vue | 用量报告 + Token 趋势图 + 用户明细 |
| `/portal/billing` | BillingView.vue | 套餐选择 + 订阅 + 支付 + 用量进度条 |
| `/portal/settings` | TenantSettings.vue | 企业名称/联系人/联系电话编辑 |

## 测试方式

### 方式一：单元/集成测试（Vitest + MSW）

```bash
cd frontend && npm test                     # 运行全部
cd frontend && npx vitest run saas          # 只跑 SaaS 相关
```

需要先在 `src/__tests__/mocks/handlers.ts` 添加 SaaS API mock handlers，然后编写测试文件。

### 方式二：浏览器手动测试

1. **启动后端**（需要 SaaS 模块已注册）
   ```bash
   python -m src.main  # port 8000
   ```

2. **启动前端 dev server**
   ```bash
   cd frontend && npm run dev  # port 3000
   ```

3. **访问** `http://localhost:3000/portal/login`

---

## 手动测试用例

### TC-01 登录流程

**前置条件**：后端已启动，存在租户管理员账号（手机号已在 `tenant_admins` 表中）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 访问 `/portal/login` | 显示登录页，含手机号和验证码输入框 |
| 2 | 输入手机号，点击"获取验证码" | 按钮变为 60s 倒计时 |
| 3 | 输入验证码，点击"登录" | 登录成功，跳转到 `/portal` 仪表盘 |
| 4 | 刷新页面 | 仍停留在 `/portal`（token 从 localStorage 恢复） |
| 5 | 点击侧边栏"退出登录" | 清除 token，跳转回 `/portal/login` |

**异常场景**：
- 空 手机号 → 点击登录 → 提示"请输入手机号和验证码"
- 错误验证码 → 提示"登录失败"相关错误信息

### TC-02 仪表盘

**前置条件**：已登录

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 访问 `/portal` | 显示 4 个统计卡片：实例数、用户数、Token 用量、活跃订阅 |
| 2 | 检查 Token 用量卡片 | 显示进度条 + 已用/总量数字 |
| 3 | 检查侧边栏 | 显示企业名称、管理员姓名 |

### TC-03 智能体实例管理

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"智能体管理" | 显示实例列表（或空状态） |
| 2 | 点击"创建实例" | 弹出表单：名称、类型、套餐、计费周期 |
| 3 | 填写名称，选择选项，点击"确认创建" | 新实例出现在列表中，状态为"已停止" |
| 4 | 点击"启动" | 状态变为"运行中"（绿色标签） |
| 5 | 点击"停止" | 状态变为"已停止"（灰色标签） |
| 6 | 点击"删除"，确认 | 实例从列表中移除 |

### TC-04 渠道配置

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"渠道配置" | 显示渠道列表（或空状态） |
| 2 | 点击"添加渠道" | 弹出表单：渠道类型下拉框（企微/钉钉/飞书） |
| 3 | 选择"企业微信" | 显示 4 个凭证字段（CorpID、Secret、Token、AESKey） |
| 4 | 填写凭证，点击"保存" | 新渠道出现在列表中，显示"未验证"标签 |
| 5 | 点击"验证" | 调用验证接口，标签变为"已验证"（或显示错误） |
| 6 | 点击"编辑" | 弹出编辑表单，可修改凭证 |
| 7 | 点击"删除"，确认 | 渠道从列表移除 |

**切换渠道类型测试**：
- 选择"钉钉" → 显示 App Key、App Secret 2 个字段
- 选择"飞书" → 显示 App ID、App Secret、Verification Token、Encrypt Key 4 个字段

### TC-05 用户管理

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"用户管理" | 显示用户列表表格 |
| 2 | 点击"添加用户" | 弹出表单：用户名、手机号、部门、角色 |
| 3 | 填写信息，点击"确认" | 新用户出现在列表中 |
| 4 | 点击"CSV 导入" | 弹出文件选择对话框 |
| 5 | 上传 CSV 文件（列：phone,username,department,role） | 显示导入结果：成功数/总数 |
| 6 | 点击某用户"删除"，确认 | 用户从列表移除 |

**CSV 文件示例**：
```csv
phone,username,department,role
13900001111,张三,技术部,user
13900002222,李四,市场部,admin
```

### TC-06 Skill 管理

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"Skill 管理" | 显示 Skill 列表（名称、路径、创建时间） |
| 2 | 点击"上传 Skill" | 弹出编辑器：名称输入 + SKILL.md 内容 textarea |
| 3 | 填写名称和 Markdown 内容，点击"保存" | 新 Skill 出现在列表中 |
| 4 | 点击某 Skill"编辑" | 弹出编辑器（名称只读），可修改内容 |
| 5 | 点击"删除"，确认 | Skill 从列表移除 |

### TC-07 用量报告

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"用量报告" | 显示摘要卡片 + Token 趋势图 + 用户明细表 |
| 2 | 点击"7天"/"30天"/"90天"按钮 | 数据切换到对应时间范围 |
| 3 | 检查趋势图 | 显示柱状图，每日一根柱子 |
| 4 | 检查用户明细 | 显示用户名、Token 消耗、会话数、平均值 |
| 5 | 点击"导出" | 下载 JSON 文件 |

### TC-08 计费管理

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"计费管理" | 显示 3 个套餐卡片（基础/标准/高级） |
| 2 | 点击某套餐"选择套餐" | 卡片高亮，出现计费周期下拉和"订阅"按钮 |
| 3 | 选择周期，点击"订阅" | 创建订阅，如有待支付订单则弹开支付页 |
| 4 | 检查"当前订阅"区域 | 显示 Token 用量进度条（已用%/配额） |
| 5 | 检查"支付订单"列表 | 显示套餐、周期、状态、支付状态、时间 |

### TC-09 企业设置

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 点击"企业设置" | 显示表单：企业名称、联系人、联系电话 |
| 2 | 修改字段内容 | 输入框内容更新 |
| 3 | 点击"保存设置" | 显示"保存成功"绿色提示 |
| 4 | 刷新页面 | 表单显示更新后的值 |

### TC-10 未登录保护

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 清除 localStorage 中的 `saas_token` | — |
| 2 | 访问 `/portal` | 自动跳转到 `/portal/login` |
| 3 | 访问 `/portal/instances` | 自动跳转到 `/portal/login` |

---

## 自动化测试计划

### 需添加的 MSW Handlers

在 `src/__tests__/mocks/handlers.ts` 中添加以下 SaaS API mock：

```typescript
// SaaS 认证
http.post('/api/saas/auth/sms/send', () => {
  return HttpResponse.json({ success: true, message: '验证码已发送', expires_in: 300 })
})

http.post('/api/saas/auth/login', async ({ request }) => {
  const body = await request.json() as any
  if (body.phone && body.code === '888888') {
    return HttpResponse.json({
      success: true,
      token: 'saas_test_token',
      admin: { admin_id: 'a1', phone: '13800138000', name: '测试管理员', role: 'admin' },
      tenant: { tenant_id: 't1', company_name: '测试企业', plan: 'basic', status: 1 }
    })
  }
  return HttpResponse.json({ success: false, message: '验证码错误' }, { status: 401 })
})

http.get('/api/saas/auth/me', ({ request }) => {
  if (request.headers.get('Authorization') === 'Bearer saas_test_token') {
    return HttpResponse.json({
      admin: { admin_id: 'a1', phone: '13800138000', name: '测试管理员', role: 'admin' },
      tenant: { tenant_id: 't1', company_name: '测试企业', plan: 'basic', status: 1 }
    })
  }
  return HttpResponse.json({ error: 'Unauthorized' }, { status: 401 })
})

http.post('/api/saas/auth/logout', () => HttpResponse.json({ success: true }))

// SaaS 租户
http.get('/api/saas/tenants/me', () => {
  return HttpResponse.json({
    success: true,
    tenant: { tenant_id: 't1', company_name: '测试企业', contact_name: '张三', contact_phone: '13800138000', plan: 'basic', status: 1 }
  })
})

http.get('/api/saas/tenants/me/stats', () => {
  return HttpResponse.json({
    success: true,
    stats: { total_instances: 3, active_instances: 2, total_users: 15, total_tokens_used: 125000 }
  })
})

// SaaS 实例
http.get('/api/saas/instances', () => {
  return HttpResponse.json({
    success: true,
    instances: [
      { instance_id: 'i1', subagent_type: 'customer_service', display_name: '客服助手', status: 'running', created_at: '2026-01-01 00:00:00' },
      { instance_id: 'i2', subagent_type: 'hr_assistant', display_name: 'HR 助手', status: 'stopped', created_at: '2026-02-01 00:00:00' }
    ]
  })
})

// SaaS 渠道
http.get('/api/saas/channels', () => {
  return HttpResponse.json({
    success: true,
    channels: [
      { config_id: 'c1', channel_type: 'wecom', config: {}, verified: true, created_at: '2026-01-01 00:00:00' }
    ]
  })
})

// SaaS 用户
http.get('/api/saas/users', () => {
  return HttpResponse.json({
    success: true,
    users: [
      { mapping_id: 'm1', user_id: 'u1', phone: '13900001111', username: '张三', department: '技术部', role: 'admin', created_at: '2026-01-01' }
    ]
  })
})

// SaaS Skills
http.get('/api/saas/skills', () => {
  return HttpResponse.json({
    success: true,
    skills: [
      { name: 'custom-faq', path: '/skills/custom-faq', created_at: '2026-01-01 00:00:00' }
    ]
  })
})

// SaaS 报告
http.get('/api/saas/reports/summary', () => {
  return HttpResponse.json({
    success: true, period: 'month',
    summary: { total_tokens: 125000, total_sessions: 500, active_users: 15, avg_tokens_per_session: 250 }
  })
})

http.get('/api/saas/reports/tokens', () => {
  return HttpResponse.json({
    success: true,
    trend: Array.from({ length: 7 }, (_, i) => ({
      date: `2026-04-0${i + 1}`, tokens: 10000 + Math.floor(Math.random() * 5000)
    }))
  })
})

http.get('/api/saas/reports/users', () => {
  return HttpResponse.json({
    success: true,
    users: [
      { user_id: 'u1', username: '张三', total_tokens: 50000, total_sessions: 100, avg_tokens_per_session: 500 }
    ]
  })
})

// SaaS 计费
http.get('/api/saas/billing/plans', () => {
  return HttpResponse.json({
    success: true,
    plans: [
      { name: 'basic', display_name: '基础版', price: 99, token_quota: 100000, max_instances: 3, max_users: 50 },
      { name: 'standard', display_name: '标准版', price: 299, token_quota: 500000, max_instances: 10, max_users: 200 },
      { name: 'premium', display_name: '高级版', price: 999, token_quota: 2000000, max_instances: -1, max_users: -1 }
    ]
  })
})

http.get('/api/saas/billing/subscriptions', () => {
  return HttpResponse.json({
    success: true,
    subscriptions: [
      { subscription_id: 's1', plan_name: 'basic', billing_cycle: 'monthly', status: 'active', payment_status: 'paid', created_at: '2026-01-01' }
    ]
  })
})

http.get('/api/saas/billing/usage', () => {
  return HttpResponse.json({
    success: true,
    usage: [{ subscription_id: 's1', plan_name: 'basic', token_quota: 100000, tokens_used: 12500, tokens_remaining: 87500, usage_percentage: 12.5 }]
  })
})
```

### 测试文件结构

```
src/__tests__/
├── api/
│   └── saasTenant.test.ts          # API client 模块导出 + 请求测试
├── composables/
│   └── useTenantAuth.test.ts       # 认证状态管理测试
└── components/saas/
    ├── TenantLogin.test.ts          # 登录页组件测试
    ├── TenantDashboard.test.ts      # 仪表盘渲染测试
    ├── InstanceManager.test.ts      # 实例管理交互测试
    ├── ChannelConfig.test.ts        # 渠道配置测试
    ├── TenantUserManager.test.ts    # 用户管理测试
    └── BillingView.test.ts          # 计费页面测试
```
