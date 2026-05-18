## 1. 数据库变更

- [x] 1.1 `tenant_channel_configs` 表新增 `subagent_type TEXT` 字段（修改 `src/saas/db/tables.py` 和 `deploy/init-postgres.sql`）
- [x] 1.2 记录增量迁移 SQL 到 `deploy/db_update.sql`

## 2. 后端核心逻辑

- [x] 2.1 `ChannelConfigDB.list_by_tenant` 返回结果包含 `subagent_type` 字段
- [x] 2.2 `ChannelFactory.create_from_tenant_config` 新增 `config_id` 参数，按 `config_id` 精确查找配置
- [x] 2.3 `channel_routes.py` — WeCom 回调路由从 `/t/{tenant_id}/wecom/callback` 改为 `/t/{tenant_id}/wecom/callback/{config_id}`
- [x] 2.4 `_process_tenant_wecom_background` — 从渠道配置读取 `subagent_type`，调用 `agent_router.get_agent(subagent_type, session_id)` 替代 `get_agent(None, session_id)`
- [x] 2.5 `channel_config.py` API — `create_channel` 和 `update_channel` 接口支持 `subagent_type` 字段

## 3. 前端页面

- [x] 3.1 `ChannelConfig.vue` — 渠道配置表单新增"关联数字员工"下拉选择框（选项来自租户已订阅的数字员工列表，含"不绑定"默认选项）
- [x] 3.2 `ChannelConfig.vue` — 每个渠道配置展示独立回调 URL（`/t/{tenant_id}/wecom/callback/{config_id}`），支持一键复制
- [x] 3.3 新增 API：获取租户可用的数字员工列表（复用 `SubscriptionDB.get_allowed_subagent_types`）

## 4. 测试

- [x] 4.1 单元测试：`ChannelConfigDB` 的新增/查询包含 `subagent_type` 字段
- [x] 4.2 单元测试：`ChannelFactory` 按 `config_id` 精确查找
- [x] 4.3 集成测试：多应用回调路由正确解密和路由
- [ ] 4.4 手动测试：按 `docs/wecom_setup_verification.md` 配置两个自建应用，验证各自路由到对应数字员工

## 5. 部署与文档

- [x] 5.1 更新 `docs/wecom_setup_verification.md`，回调 URL 示例更新为新格式
- [x] 5.2 前端构建验证：`cd frontend && npm run build`