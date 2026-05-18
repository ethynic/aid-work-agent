## ADDED Requirements

### Requirement: 渠道配置关联数字员工

系统 SHALL 支持在渠道配置中指定关联的数字员工（subagent_type），`tenant_channel_configs` 表 MUST 包含 `subagent_type` 字段。该字段为可选 TEXT 字段，值为 `subagents/` 目录下的子智能体目录名（如 `travel-consultant`、`trade-specialist`），NULL 表示不绑定特定数字员工。

#### Scenario: 创建渠道配置时指定数字员工
- **WHEN** 租户管理员创建企业微信渠道配置，填写凭证信息并选择数字员工"旅游咨询顾问"（travel-consultant）
- **THEN** 系统保存渠道配置，`subagent_type` 字段值为 `travel-consultant`

#### Scenario: 渠道配置不指定数字员工
- **WHEN** 租户管理员创建渠道配置时不选择数字员工
- **THEN** 系统保存渠道配置，`subagent_type` 字段值为 NULL，消息处理时使用 master_agent

### Requirement: 回调 URL 包含渠道配置 ID

企业微信回调 URL SHALL 包含 `config_id` 路径参数，格式为 `/t/{tenant_id}/wecom/callback/{config_id}`。系统 MUST 根据 `config_id` 精确定位渠道配置，使用对应的 Token 和 EncodingAESKey 进行消息解密。

#### Scenario: 多应用回调正确解密
- **WHEN** 企业微信 App1（config_id=chan_aaa）和 App2（config_id=chan_bbb）分别向各自的回调 URL 发送加密消息
- **THEN** 系统分别使用 chan_aaa 和 chan_bbb 对应的 Token/EncodingAESKey 成功解密各自的消息

#### Scenario: config_id 不存在
- **WHEN** 回调请求中的 `config_id` 在数据库中不存在
- **THEN** 系统返回 404 错误，记录 warning 日志

### Requirement: 消息按数字员工路由

企业微信消息处理流程 SHALL 根据渠道配置中的 `subagent_type` 将消息路由到对应的数字员工。当 `subagent_type` 不为 NULL 时，MUST 调用 `agent_router.get_agent(subagent_type, session_id)` 获取独立模式子智能体；为 NULL 时 MUST 使用 master_agent。

#### Scenario: 消息路由到独立子智能体
- **WHEN** 用户向绑定 `subagent_type=trade-specialist` 的企业微信应用发送消息"帮我找客户"
- **THEN** 系统使用"外贸获客智能体"子智能体处理该消息，回复内容符合外贸获客领域

#### Scenario: 未绑定数字员工时使用主智能体
- **WHEN** 用户向未绑定数字员工的企业微信应用发送消息
- **THEN** 系统使用 master_agent 处理该消息

### Requirement: 租户多渠道配置并存

系统 SHALL 支持同一租户下同类型渠道存在多个配置。`ChannelFactory.create_from_tenant_config()` 不再取"第一个 verified 配置"，而是 MUST 按 `config_id` 精确查找。

#### Scenario: 同一租户配置两个企业微信应用
- **WHEN** 租户管理员为同一租户创建两个企业微信渠道配置，分别绑定"旅游咨询顾问"和"外贸获客智能体"
- **THEN** 两个配置均处于 verified 状态，各自独立工作，互不干扰

### Requirement: 前端渠道配置表单支持数字员工选择

前端渠道配置表单 SHALL 提供数字员工下拉选择框，选项来源为当前租户已订阅的数字员工列表。下拉框 MUST 包含"不绑定（默认）"选项，允许不选择数字员工。

#### Scenario: 编辑渠道配置时选择数字员工
- **WHEN** 租户管理员在渠道配置编辑页面，从数字员工下拉框中选择"外贸获客智能体"
- **THEN** 保存后该渠道配置与"外贸获客智能体"子智能体关联

#### Scenario: 下拉框展示已订阅的数字员工
- **WHEN** 租户仅订阅了 trade-specialist 和 travel-consultant 两个数字员工
- **THEN** 数字员工下拉框仅展示这两个选项（含"不绑定"选项），不展示未订阅的数字员工