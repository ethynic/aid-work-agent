## Context

当前系统企业微信渠道集成架构：每个租户通过 `tenant_channel_configs` 表存储渠道凭证（corp_id、agent_id、secret、token、encoding_aes_key），`ChannelFactory.create_from_tenant_config()` 取第一个 verified 配置创建 `WeComAdapter`。回调路由 `/t/{tenant_id}/wecom/callback` 不区分具体是哪个企业微信自建应用。消息处理后通过 `agent_router.get_agent(None, session_id)` 始终返回 `master_agent`。

约束：企业微信每个自建应用有独立的 AgentID、Secret、Token、EncodingAESKey，且回调 URL 是应用级别的配置。需要在系统层面支持同一租户下多个企业微信自建应用并存，每个应用对应不同的数字员工。

## Goals / Non-Goals

**Goals:**
- 支持同一租户配置多个企业微信渠道（多个自建应用）
- 每个渠道配置可关联一个数字员工（subagent_type）
- 回调 URL 携带 `config_id`，精确定位解密凭证
- 消息处理时根据配置自动路由到对应的独立模式子智能体
- 前端渠道配置页面展示可选数字员工列表和独立回调 URL

**Non-Goals:**
- 不改变其他渠道（钉钉、飞书）的回调路由结构（它们暂无多应用需求，但架构上兼容未来扩展）
- 不修改 WeComAdapter 内部逻辑（加解密、消息发送等保持不变）
- 不改变 `agent_instances` 表的 `bound_channel_type` 字段（该字段是实例级别的绑定，与渠道配置级别的绑定是不同维度）
- 不处理群聊 @提及清洗逻辑

## Decisions

### Decision 1: 回调 URL 结构 — `/t/{tenant_id}/wecom/callback/{config_id}`

**方案**: 在回调 URL 路径中加入 `config_id` 路径参数。

**替代方案**:
- 查询参数 `?config_id=xxx` — WeCom 管理后台对回调 URL 的查询参数支持不明确，部分文档暗示仅支持固定路径。路径参数更可靠。
- 遍历尝试所有配置解密 — 实现丑陋，首次消息必须遍历，存在密钥冲突风险。

**选择理由**: 路径参数最可靠，每个应用配置独立 URL，清晰明确。

### Decision 2: subagent_type 存储位置 — `tenant_channel_configs` 表独立字段

**方案**: 新增 `subagent_type TEXT` 字段，存储在 `tenant_channel_configs` 表中（非 JSON 内）。

**替代方案**:
- 放在 `config` JSON 字段内 — 不便于 SQL 查询和索引，与其他业务字段（corp_id、secret）混合
- 使用 `agent_instances` 表关联 — 增加表关联复杂度，渠道配置和实例是不同的概念

**选择理由**: `subagent_type` 是业务路由字段，独立存储方便查询、索引和前端展示。`subagent_type` 为 NULL 时表示不绑定特定数字员工，使用 master_agent。

### Decision 3: 消息路由方式 — `agent_router.get_agent(subagent_type, session_id)`

**方案**: 回调处理中，从渠道配置读取 `subagent_type`，调用 `agent_router.get_agent(subagent_type, session_id)` 获取对应的独立模式子智能体。

**替代方案**:
- 在消息内容前添加前缀标识 — 侵入消息内容，不可靠
- 修改 `Agent.process_message()` 内部判断 — 违反单一职责

**选择理由**: 复用现有的 `AgentRouter` 基础设施，`get_agent()` 已支持按 `subagent_name` 创建/缓存独立模式子智能体。当 `subagent_type` 为 None 时回退到 `master_agent`。

### Decision 4: 前端数字员工选择 — 从 `SubscriptionDB.get_allowed_subagent_types()` 获取列表

**方案**: 前端通过 API 获取当前租户已订阅的数字员工类型列表，在渠道配置表单中展示为下拉选择。

**选择理由**: 复用现有订阅系统，只展示租户有权限使用的数字员工。`subagent_type` 值与 `subagents/` 目录名一致（如 `travel-consultant`、`trade-specialist`）。

## Risks / Trade-offs

- **config_id 暴露在 URL 中** → `config_id` 是系统内部 ID（如 `chan_a1b2c3d4e5f6`），非敏感凭证。实际安全依赖 Token/AESKey 加密，config_id 仅用于路由。
- **子智能体不存在时的 fallback** → `agent_router.get_agent()` 在子智能体找不到时已内置 fallback 到 `master_agent`，不会导致消息丢失。
- **多 worker 环境下子智能体状态** → 独立模式子智能体在每个 worker 中独立缓存，短期记忆从 DB 恢复。与现有架构一致，不引入新问题。