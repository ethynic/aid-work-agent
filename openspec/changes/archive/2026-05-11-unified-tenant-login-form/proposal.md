## Why

当前系统在演示模式关闭时，根路径 `/` 显示仅包含租户代码输入的表单。用户需要先输入租户代码，验证通过后跳转到租户登录页 `/t/{tenant_id}/login`，再输入用户名/手机号和密码进行二次验证。这种两步登录流程增加了用户操作步骤，降低了用户体验的流畅性。

本变更旨在提供一站式登录体验，用户在单个表单中一次性完成租户验证和用户认证，简化登录流程，提升专业性和用户满意度。

## What Changes

- **新增统一登录API**：创建 `/api/auth/unified-login` 端点，一次性验证租户代码和用户凭证
- **新增前端统一登录组件**：创建 `UniversalLogin.vue` 组件，包含租户代码、用户名/手机号、密码和图形验证码输入字段
- **实现租户代码记忆功能**：登录成功后自动将租户代码保存到 localStorage，下次访问时自动填充
- **更新前端路由配置**：当 `VITE_DEMO_ENABLED=false` 时，根路径 `/` 显示统一登录组件而非仅租户代码输入页
- **向后兼容**：保留现有 `/api/tenant/enter` API 和 `/t/{tenant_id}/login` 页面，确保现有流程不受影响

## Capabilities

### New Capabilities
- `unified-tenant-login-form`: 提供统一租户登录表单，支持单页面完成租户代码验证和用户认证，包含租户代码记忆功能

### Modified Capabilities
<!-- 无现有能力变更 -->

## Impact

- **后端API**：
  - 新增 `/api/auth/unified-login` 端点（需整合租户验证和用户认证逻辑）
  - 现有 `/api/tenant/enter` 和 `/api/auth/login/password` API 保持不变
- **前端应用**：
  - 新增 `UniversalLogin.vue` 组件
  - 修改 `main.ts` 中的根路由配置
  - 新增前端API调用层
- **数据库**：无结构变更，仅利用现有 `tenants` 和 `users` 表
- **配置**：无需新增配置项，复用现有环境变量