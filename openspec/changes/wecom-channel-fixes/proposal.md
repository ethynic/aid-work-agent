## Why

企业微信渠道代码已实现消息收发、加解密、签名验证等核心能力，但在实际接入和测试过程中发现了 6 个明显问题，影响用户体验和系统稳定性：

1. **群聊 @前缀未清洗**: 用户在群聊中 @应用名称 发消息时，`@智能助手 ` 前缀会原样传给 Agent，导致 Agent 将前缀理解为消息内容。
2. **新用户无欢迎消息**: 用户首次进入应用（subscribe 事件）时，系统直接返回 "success"，无任何引导，新用户面对空聊天界面不知所措。
3. **多 worker 部署下去重失效**: `MessageDeduplicator` 基于进程内内存字典实现，Gunicorn 多 worker 模式下每个进程独立缓存，同一消息可能被多个 worker 重复处理。
4. **速率限制配置未消费**: `configs/config.yaml` 中配置了 `rate_limit.enabled` 和 `max_per_minute`，但 `adapter.py` 中没有任何限流逻辑。
5. **会话 metadata 序列化不规范**: `channel_session_manager` 使用 `str(metadata)` 直接序列化字典存入数据库，非标准 JSON，读取时可能无法正确反序列化。
6. **未校验消息接收方**: 回调中未验证 `ToUserName` / `AgentID`，存在被其他应用消息误触发的风险。

本变更旨在修复上述问题，提升企业微信渠道的健壮性和用户体验，不引入新的外部依赖。

## What Changes

- **清洗群聊 @前缀**: 在 `WeComAdapter.parse_message()` 中检测并去除群聊消息开头的 `@应用名称 ` 前缀
- **新增 subscribe 欢迎消息**: 在 `callback.py` 中处理 `subscribe` 事件，主动发送一条欢迎消息给用户
- **修复多 worker 去重**: 将 `MessageDeduplicator` 的内存缓存改为基于 PostgreSQL / Redis 的分布式去重（优先复用现有数据库，不引入 Redis 依赖）
- **启用速率限制**: 在 `WeComAdapter.send_long_message()` 和消息接收流程中消费 `rate_limit` 配置
- **规范 metadata 序列化**: 将 `str(metadata)` 替换为 `json.dumps(metadata)`
- **增加 ToUserName 校验**: 在回调处理中验证消息是否发给本应用

## Capabilities

### New Capabilities
- `wecom-welcome-message`: 用户首次关注/进入企业微信应用时，自动收到欢迎引导消息
- `wecom-group-at-cleaning`: 群聊中 @应用的消息能正确清洗前缀后处理
- `wecom-rate-limiting`: 企业微信渠道支持按配置对用户进行速率限制

### Modified Capabilities
- `wecom-message-dedup`: 消息去重支持多 worker 部署场景
- `wecom-session-persistence`: 会话 metadata 使用标准 JSON 序列化
- `wecom-callback-security`: 回调消息增加接收方校验

## Impact

- **后端应用**:
  - `src/channels/wecom/adapter.py` — 增加 @前缀清洗、速率限制消费
  - `src/channels/callback.py` — 增加 subscribe 欢迎消息、ToUserName 校验
  - `src/channels/idempotency.py` — 改为基于数据库的分布式去重
  - `src/channels/session.py` — 修复 metadata 序列化
  - `src/config/settings.py` — 确认 rate_limit 配置模型
- **前端应用**: 无变更
- **数据库**: 可能新增去重记录表（如果使用 DB 方案）
- **配置**: 无需新增配置项，消费已有配置
