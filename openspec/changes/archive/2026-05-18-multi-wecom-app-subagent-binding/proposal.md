## Why

当前每个租户只能配置一套企业微信渠道凭证，一个企业微信自建应用只能对应一个通用智能体（master_agent）。当租户需要部署多个企业微信自建应用、分别由不同数字员工（subagent）提供服务时（如"旅游咨询顾问"和"外贸获客智能体"），系统无法区分消息来源，也无法将消息路由到对应的数字员工。

## What Changes

- 企业微信回调 URL 从 `/t/{tenant_id}/wecom/callback` 变更为 `/t/{tenant_id}/wecom/callback/{config_id}`
- `tenant_channel_configs` 表新增 `subagent_type` 字段，支持将渠道配置与数字员工关联
- 渠道配置支持同类型多渠道并存（一个租户可以有多个 wecom 配置）
- 回调路由根据 `config_id` 精确定位渠道凭证进行解密，不再取"第一个 verified"
- 后台消息处理时根据配置中的 `subagent_type` 路由到对应的独立模式子智能体
- 前端渠道配置页面新增"关联数字员工"下拉选择框，展示可选数字员工列表
- 每个渠道配置展示独立的回调 URL，方便复制到对应企业微信自建应用后台

## Capabilities

### New Capabilities

- `channel-subagent-binding`: 渠道配置与数字员工的绑定能力，支持在渠道配置中指定关联的数字员工（subagent_type），消息处理时自动路由到对应的独立模式子智能体

### Modified Capabilities

<!-- 无现有 spec 需要修改 -->

## Impact

- **数据库**: `tenant_channel_configs` 表新增 `subagent_type` 字段
- **后端路由**: `src/saas/api/channel_routes.py` — WeCom 回调路由新增 `{config_id}` 路径参数
- **后端工厂**: `src/saas/services/channel_factory.py` — 改为按 `config_id` 查找配置
- **后端 API**: `src/saas/api/channel_config.py` — 创建/更新接口支持 `subagent_type` 字段
- **消息处理**: `_process_tenant_wecom_background` 改为调用 `agent_router.get_agent(subagent_type, session_id)` 而非 `get_agent(None, session_id)`
- **前端**: `ChannelConfig.vue` — 表单新增数字员工选择、回调 URL 展示变更
- **部署**: 新的回调 URL 格式为 `/t/{tenant_id}/wecom/callback/{config_id}`