## Context

当前系统在演示模式关闭时，根路径 `/` 显示 `TenantEntry.vue` 组件，仅包含租户代码输入。用户需要：
1. 输入租户代码 → 调用 `/api/tenant/enter` 验证 → 重定向到 `/t/{tenant_id}`
2. 在租户登录页输入用户名/手机号、密码、图形验证码 → 调用 `/api/auth/login/password` 验证 → 进入租户系统

这种两步流程增加了用户操作成本，降低了用户体验的流畅性。现有 `universal-tenant-login` 变更已实现租户代码验证机制，但未提供一站式登录体验。

## Goals / Non-Goals

**Goals:**
1. 提供单页面统一登录表单，用户一次性完成租户验证和用户认证
2. 实现租户代码记忆功能，自动填充上次成功登录的租户代码
3. 保持向后兼容，现有登录流程不受影响
4. 提供统一的错误处理，一次性返回所有验证错误
5. 复用现有图形验证码和密码验证机制

**Non-Goals:**
1. 不修改现有租户认证逻辑（仍使用手机号/用户名+密码+图形验证码）
2. 不改变平台管理员登录流程（继续使用 `/portal/login`）
3. 不修改数据库结构（复用现有 `tenants` 和 `users` 表）
4. 不实现多因素认证或社交登录
5. 不提供租户代码自助注册功能

## Decisions

### 1. 统一登录API设计

**方案选择**: 新建 `/api/auth/unified-login` API，而非前端串联调用现有API

**Rationale**:
- **前端串联方案**: 需要两次API调用（`/api/tenant/enter` + `/api/auth/login/password`），增加网络延迟和错误处理复杂度
- **新建API方案**: 一次请求完成所有验证，提供最佳用户体验，简化前端逻辑

**实现细节**:
- **路径**: `POST /api/auth/unified-login`
- **请求模型**:
  ```python
  class UnifiedLoginRequest(BaseModel):
      tenant_code: str        # 4-8位字母数字
      identifier: str         # 手机号或用户名
      password: str           # 密码
      captcha_code: str       # 图形验证码
      captcha_id: str         # 图形验证码ID
  ```
- **验证顺序**:
  1. 图形验证码验证（复用现有 `verify_captcha`）
  2. 租户代码验证（调用 `TenantDB.get_by_code`）
  3. 用户凭证验证（复用现有密码验证逻辑）
  4. 用户-租户归属检查（`user.tenant_id == tenant.tenant_id`）
- **错误响应**: 统一返回所有验证错误，格式：
  ```json
  {
    "success": false,
    "errors": [
      {"field": "tenant_code", "message": "租户代码不存在"},
      {"field": "password", "message": "密码错误"}
    ]
  }
  ```

### 2. 前端组件设计

**组件结构**: 新建 `UniversalLogin.vue`，复用 `TenantLogin.vue` 的UI样式和验证码逻辑

**字段设计**:
- **租户代码输入**: `<input v-model="tenantCode" placeholder="例如：ALIBB">`
- **用户名/手机号切换**: 复用 `TenantLogin.vue` 的切换按钮
- **密码输入**: `<input type="password">`
- **图形验证码**: 复用现有验证码组件

**租户代码记忆**:
- **存储键**: `last_tenant_code`（localStorage）
- **存储时机**: 登录成功时自动保存
- **读取时机**: 组件挂载时自动填充
- **清除时机**: 用户手动清空或浏览器清除数据

### 3. 路由配置更新

**根路径路由**: 修改 `main.ts` 中的根路由配置
```typescript
// 当前
component: () => import.meta.env.VITE_DEMO_ENABLED === 'true'
  ? import('./components/ChatContainer.vue')
  : import('./components/TenantEntry.vue')

// 更新后
component: () => import.meta.env.VITE_DEMO_ENABLED === 'true'
  ? import('./components/ChatContainer.vue')
  : import('./components/UniversalLogin.vue')
```

**向后兼容**:
- 保留 `/t/{tenant_id}/login` 路由（现有租户登录页）
- 保留 `/api/tenant/enter` API（现有租户验证）
- 保留 `/portal/login` 路由（平台管理员登录）

### 4. 错误处理策略

**前端错误处理**:
- 表单提交时禁用提交按钮，显示加载状态
- API返回错误时，在对应字段下方显示错误信息
- 支持字段级错误和通用错误

**后端错误处理**:
- 验证顺序：图形验证码 → 租户代码 → 用户凭证 → 用户-租户归属
- 错误收集：收集所有验证错误，一次性返回
- 错误分类：字段级错误（`tenant_code`, `identifier`, `password`, `captcha_code`）

## Risks / Trade-offs

### 风险与缓解措施

1. **新API与现有登录逻辑不一致**
   - **风险**: 统一登录API可能遗漏现有登录逻辑的某些检查
   - **缓解**: 复用现有验证函数，确保逻辑一致性

2. **租户代码记忆导致隐私问题**
   - **风险**: localStorage 中存储的租户代码可能被其他用户看到
   - **缓解**: 租户代码非敏感信息（公开可见），且用户可手动清除

3. **错误处理复杂度增加**
   - **风险**: 统一错误响应需要前端特殊处理
   - **缓解**: 设计清晰的错误格式，提供前端工具函数

4. **向后兼容性破坏**
   - **风险**: 修改根路由可能影响现有用户
   - **缓解**: 保留所有现有路由和API，仅新增功能

### 权衡

- **用户体验 vs 实现复杂度**: 选择新建API方案提供最佳体验，但增加后端开发工作量
- **功能完整性 vs 开发速度**: 实现完整的统一表单，但分阶段部署（先API后前端）
- **错误提示粒度 vs 用户友好性**: 提供字段级错误，但避免过度技术性描述