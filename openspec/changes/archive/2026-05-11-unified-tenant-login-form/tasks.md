## 1. 后端API实现

- [x] 1.1 在 `src/api/auth.py` 中添加 `UnifiedLoginRequest` 数据模型
- [x] 1.2 在 `src/api/auth.py` 中创建 `/api/auth/unified-login` 端点函数
- [x] 1.3 实现统一验证逻辑：图形验证码 → 租户代码 → 用户凭证 → 用户-租户归属检查
- [x] 1.4 实现错误收集和统一错误响应格式
- [x] 1.5 复用现有 `verify_captcha`、密码验证和租户查询函数
- [x] 1.6 编写单元测试验证API的各种场景（成功、各种错误情况）

## 2. 前端统一登录组件

- [x] 2.1 创建 `frontend/src/components/UniversalLogin.vue` 组件文件
- [x] 2.2 复制 `TenantLogin.vue` 的UI样式和布局作为基础
- [x] 2.3 添加租户代码输入字段到表单
- [x] 2.4 实现用户名/手机号切换功能（复用现有逻辑）
- [x] 2.5 实现租户代码记忆功能：localStorage 存储/读取
- [x] 2.6 实现与 `/api/auth/unified-login` API 的交互逻辑
- [x] 2.7 实现字段级错误显示（租户代码、用户名/手机号、密码、验证码）
- [x] 2.8 添加加载状态和提交按钮禁用逻辑

## 3. 前端API集成层

- [x] 3.1 在 `frontend/src/api/auth.ts` 中添加 `unifiedLogin` 函数
- [x] 3.2 定义统一登录请求/响应类型接口
- [x] 3.3 实现错误处理逻辑，解析字段级错误

## 4. 路由配置更新

- [x] 4.1 修改 `frontend/src/main.ts` 中的根路由配置，将 `TenantEntry.vue` 替换为 `UniversalLogin.vue`
- [x] 4.2 验证演示模式开关逻辑：`VITE_DEMO_ENABLED=true` 时仍显示 `ChatContainer.vue`
- [x] 4.3 确保所有现有路由保持不变（`/t/{tenant_id}/login`、`/portal/login` 等）

## 5. 测试与验证

- [x] 5.1 设置 `VITE_DEMO_ENABLED=false`，访问 `/` 测试统一登录表单显示
- [x] 5.2 测试租户代码记忆功能：登录成功后刷新页面验证自动填充
- [x] 5.3 测试各种错误场景：无效租户代码、错误密码、过期验证码等
- [x] 5.4 测试向后兼容性：直接访问 `/t/{tenant_id}/login` 仍显示原有登录页
- [x] 5.5 测试平台管理员登录：`/portal/login` 仍正常工作
- [x] 5.6 测试演示模式：`VITE_DEMO_ENABLED=true` 时显示聊天界面
- [x] 5.7 测试大小写不敏感的租户代码输入

## 6. 文档与清理

- [x] 6.1 更新前端开发文档，说明新的统一登录表单
- [x] 6.2 清理临时调试代码和日志
- [x] 6.3 验证所有任务已完成，变更准备就绪