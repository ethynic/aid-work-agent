## 1. 验证与准备

- [x] 1.1 测试后端 `/api/tenant/enter` API 可用性
- [x] 1.2 检查数据库 `tenants` 表是否已有 `tenant_code` 字段
- [x] 1.3 验证现有租户是否有默认租户代码
- [x] 1.4 在前端 `.env` 文件中添加 `VITE_DEMO_ENABLED=false` 配置

## 2. 前端环境配置

- [x] 2.1 更新 `frontend/.env` 文件，添加 `VITE_DEMO_ENABLED=false`
- [x] 2.2 更新 `frontend/.env.example` 文件，添加配置说明
- [x] 2.3 在前端代码中读取 `import.meta.env.VITE_DEMO_ENABLED` 环境变量

## 3. 创建前端租户入口组件

- [x] 3.1 创建 `frontend/src/components/TenantEntry.vue` 组件
- [x] 3.2 实现租户代码输入表单（复制后端HTML的UI样式）
- [x] 3.3 实现前端格式验证（4-8位字母数字）
- [x] 3.4 实现与 `/api/tenant/enter` API 的交互逻辑
- [x] 3.5 实现成功/错误消息显示
- [x] 3.6 实现验证成功后的自动重定向

## 4. 更新前端路由配置

- [x] 4.1 修改 `frontend/src/main.ts` 中的路由配置
- [x] 4.2 根据 `VITE_DEMO_ENABLED` 动态选择根路径组件
- [x] 4.3 确保演示模式开启时仍显示原有聊天界面
- [x] 4.4 测试路由切换功能

## 5. 更新现有组件

- [x] 5.1 修改 `frontend/src/components/ChatContainer.vue`
- [x] 5.2 仅在演示模式开启时显示登录弹窗
- [x] 5.3 更新组件逻辑，读取 `VITE_DEMO_ENABLED` 环境变量

## 6. 数据库与后端验证（确保依赖可用）

- [x] 6.1 运行数据库迁移脚本（如果 `tenant_code` 字段不存在）
- [x] 6.2 测试 `/api/tenant/enter` API 的完整功能
- [x] 6.3 验证租户代码大小写不敏感处理
- [x] 6.4 验证租户状态检查逻辑（active/suspended/expired）

## 7. 集成测试

- [x] 7.1 设置 `VITE_DEMO_ENABLED=false`，访问 `/` 测试租户入口页面
- [x] 7.2 输入有效租户代码，验证重定向到 `/t/{tenant_id}`
- [x] 7.3 输入无效租户代码，验证错误提示
- [x] 7.4 设置 `VITE_DEMO_ENABLED=true`，访问 `/` 测试演示模式
- [x] 7.5 测试大小写不敏感的租户代码输入

## 8. 文档与清理

- [x] 8.1 更新前端开发文档，说明新的环境变量配置
- [x] 8.2 更新部署文档，说明生产环境配置
- [x] 8.3 清理临时调试代码
- [x] 8.4 验证所有任务已完成