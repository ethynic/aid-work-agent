## 1. 数据库变更

- [x] 1.1 在 `deploy/db_update.sql` 中添加租户代码字段迁移语句（无UNIQUE约束）
- [x] 1.2 更新 `deploy/init-postgres.sql` 中的 `tenants` 表定义
- [x] 1.3 更新 `src/saas/db/tables.py` 中的表创建逻辑
- [x] 1.4 为现有租户生成默认租户代码的迁移脚本
- [x] 1.5 为 `tenant_code` 字段创建非唯一数据库索引（提升查询性能）

## 2. 数据模型更新

- [x] 2.1 更新 `src/saas/models/tenant.py` 中的 Pydantic 模型
- [x] 2.2 在 `TenantCreate` 模型中添加 `tenant_code` 字段及验证
- [x] 2.3 在 `TenantUpdate` 模型中添加可选的 `tenant_code` 字段
- [x] 2.4 更新 `TenantResponse` 模型包含 `tenant_code` 字段

## 3. 数据库访问层更新

- [x] 3.1 在 `src/saas/db/tenant_db.py` 中添加 `get_by_code` 方法（支持大小写不敏感查询）
- [x] 3.2 更新 `create` 方法支持 `tenant_code` 参数
- [x] 3.3 更新 `update` 方法支持 `tenant_code` 修改
- [x] 3.4 添加租户代码唯一性验证逻辑

## 4. 后端API开发

- [x] 4.1 修改 `src/main.py` 中的 `/` 路由逻辑，支持演示模式开关
- [x] 4.2 在 `src/main.py` 中添加 `/api/tenant/enter` API端点
- [x] 4.3 实现租户代码验证、状态检查和重定向逻辑（包含大小写转换）
- [x] 4.4 更新 `src/saas/api/tenant_mgmt.py` 中的租户创建API
- [x] 4.5 更新 `src/saas/api/tenant_mgmt.py` 中的租户更新API
- [x] 4.6 添加租户代码唯一性验证到租户管理API

## 5. 前端管理界面更新

- [x] 5.1 更新租户创建表单，添加租户代码输入字段
- [x] 5.2 实现租户代码实时格式验证（4-8位字母数字）
- [x] 5.3 实现租户代码唯一性检查（通过API）
- [x] 5.4 更新租户编辑表单支持租户代码修改
- [x] 5.5 在租户列表和详情页显示租户代码

## 6. 配置与工具

- [x] 6.1 在 `configs/config.yaml` 中添加租户代码格式配置（可选）
- [x] 6.2 创建租户代码批量更新工具脚本
- [x] 6.3 更新 `.env.example` 文档说明

## 7. 测试

- [x] 7.1 编写数据库迁移测试
- [x] 7.2 编写租户代码验证API测试
- [x] 7.3 编写租户管理界面集成测试
- [x] 7.4 测试演示模式开关行为
- [x] 7.5 测试现有租户迁移脚本
- [x] 7.6 测试大小写不敏感的租户代码登录

## 8. 文档

- [x] 8.1 更新API文档，包括新的 `/api/tenant/enter` 端点
- [x] 8.2 更新租户管理操作指南
- [x] 8.3 添加租户代码使用说明文档