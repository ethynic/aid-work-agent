## Context

企业微信渠道已实现完整的自建应用消息收发链路，代码分布在 `src/channels/wecom/` 和 `src/channels/callback.py`。当前系统在生产环境单 worker 部署下可正常运行，但存在以下已知缺陷:

- 群聊 `@应用名称 消息` 会将 @前缀传给 Agent
- subscribe 事件无业务处理
- 消息去重依赖进程内内存字典
- rate_limit 配置存在但无消费代码
- 多处使用 `str()` 序列化 dict 存入数据库
- 回调未验证消息接收方身份

## Goals / Non-Goals

**Goals:**
1. 群聊消息中的 @应用名称前缀被清洗后再传给 Agent
2. 用户首次关注/进入应用时收到欢迎消息
3. 消息去重在多 worker (Gunicorn) 部署下仍然有效
4. 速率限制配置被实际消费，防止滥用
5. 数据库中的 metadata/context_data 使用标准 JSON 格式
6. 回调消息校验 `ToUserName` / `AgentID`，防止误触发

**Non-Goals:**
1. 不引入新的外部依赖（如 Redis），优先复用现有 PostgreSQL
2. 不改写企业微信加解密逻辑（crypto.py 保持不变）
3. 不修改 Agent 核心处理逻辑
4. 不新增管理后台 UI

## Decisions

### 1. 群聊 @前缀清洗

**方案选择**: 在 `WeComAdapter.parse_message()` 中检测 `Content` 是否以 `@` 开头，且包含应用名称，清洗后存入 `content["text"]`。

**Rationale**:
- 清洗逻辑放在适配器层最合理，因为 @前缀是企业微信渠道特有的格式，Agent 层不应关心
- `parse_message()` 已经按 msg_type 分支处理，text 消息的分支是最佳插入点
- 应用名称在初始化时已知（通过 `get_user_info` 或配置），但更简单的方式是：匹配 `@\S+\s+` 正则，去除任意 @用户名前缀

**实现细节**:
```python
# adapter.py parse_message() 中 text 分支
if msg_type == "text":
    raw_text = root.findtext("Content", "")
    # 清洗群聊 @前缀: "@应用名称 实际消息" -> "实际消息"
    # 匹配 "@任意非空白字符 " 开头的模式
    cleaned_text = re.sub(r"^@\S+\s+", "", raw_text)
    content["text"] = cleaned_text
```

> 注意：不尝试精确匹配应用名称（需要额外 API 调用），而是清洗所有 `@xxx ` 前缀。这在群聊场景下是安全的，因为用户发消息时如果@了应用，前缀总是以 `@应用名称 ` 形式出现。

### 2. Subscribe 欢迎消息

**方案选择**: 在 `callback.py` 的 event 处理分支中，对 `subscribe` 事件主动发送一条欢迎消息。

**Rationale**:
- 当前代码直接将所有 event 类型返回 "success"，不做任何处理
- subscribe 是用户首次接触应用的关键时刻， welcome 消息能显著降低用户困惑
- 使用主动消息 API（`send_text`）发送，而非被动回复模式

**实现细节**:
```python
# callback.py wecom_callback_post() 中 event 处理
if message.message_type == "event":
    event_type = message.content.get("event", "")
    if event_type == "subscribe":
        welcome = (
            "你好！我是智能助手，可以帮你处理日常任务。\n"
            "直接发送消息即可开始对话。\n"
            "输入「帮助」查看支持的功能。"
        )
        asyncio.create_task(adapter.send_text(welcome, message.user_id))
    return PlainTextResponse("success")
```

### 3. 多 Worker 去重

**方案选择**: 基于现有 PostgreSQL 数据库实现分布式去重，新增 `channel_message_dedup` 表。

**Rationale**:
- **Redis 方案**: 性能好，但需要引入新依赖和运维成本
- **DB 方案**: 零新增依赖，去重表极轻量（仅 msg_id + timestamp），TTL 清理可通过已有定时任务或惰性清理实现
- 当前项目已有 PostgreSQL，所有 worker 共享同一数据库

**实现细节**:

```python
# idempotency.py — 重构为基于 DB 的去重

class MessageDeduplicator:
    def __init__(self, ttl_seconds: int = 300):
        self._ttl = ttl_seconds

    async def is_duplicate(self, message_id: str) -> bool:
        """基于 PostgreSQL 的分布式去重"""
        now = time.time()
        cutoff = now - self._ttl

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 尝试插入，如果已存在则返回重复
            try:
                cursor.execute("""
                    INSERT INTO channel_message_dedup (message_id, created_at)
                    VALUES (%s, %s)
                """, (message_id, now))
                conn.commit()
                return False
            except psycopg2.IntegrityError:
                conn.rollback()
                return True
```

表结构:
```sql
CREATE TABLE IF NOT EXISTS channel_message_dedup (
    message_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedup_created_at
ON channel_message_dedup(created_at);
```

TTL 清理: 在 `cleanup_expired()` 方法中定期删除 `created_at < cutoff` 的记录。

### 4. 速率限制

**方案选择**: 在 `WeComAdapter` 中增加基于内存的滑动窗口限流器，消费已有 `rate_limit` 配置。

**Rationale**:
- 配置已经存在（`settings.channels.wecom.rate_limit`），只是未消费
- 限流粒度：按 `user_id` 每分钟限制
- 使用内存字典 + 滑动窗口，因为限流不需要跨 worker 严格一致（稍微宽松是可接受的）
- 如果将来需要严格限流，可以迁移到 Redis

**实现细节**:
```python
class WeComAdapter:
    def __init__(...):
        ...
        self._rate_limiter = {}  # user_id -> deque[timestamp]
        self._rate_limit_enabled = config.rate_limit.enabled
        self._rate_limit_max = config.rate_limit.max_per_minute

    def _check_rate_limit(self, user_id: str) -> bool:
        if not self._rate_limit_enabled:
            return True
        now = time.time()
        window = self._rate_limiter.setdefault(user_id, deque())
        # 移除 60 秒前的记录
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= self._rate_limit_max:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return False
        window.append(now)
        return True
```

在 `send_long_message()` 和消息接收处理流程中调用 `_check_rate_limit()`。

### 5. Metadata 序列化规范化

**方案选择**: 将所有 `str(metadata)` 替换为 `json.dumps(metadata, ensure_ascii=False)`，读取时使用 `json.loads()`。

**Rationale**:
- `str(dict)` 产生 Python 字面量字符串（单引号），不是标准 JSON（双引号），其他语言/工具无法解析
- `json.dumps()` 是标准做法
- 需要同步修改写入和读取两处

**涉及的修改点**:
- `session.py:164`: `str(metadata)` -> `json.dumps(metadata, ensure_ascii=False)`
- `session.py:237`: `str(context_data)` -> `json.dumps(context_data, ensure_ascii=False)`
- `session.py:241`: `str(metadata)` -> `json.dumps(metadata, ensure_ascii=False)`
- `session.py:295-296`: `str(attachments)` / `str(metadata)` -> `json.dumps(...)`

### 6. ToUserName 校验

**方案选择**: 在 `callback.py` 的 POST 处理中，解析 XML 后校验 `ToUserName` 是否等于本应用的 `corp_id` 或 `agent_id`。

**Rationale**:
- 企业微信回调消息中 `<ToUserName>` 字段表示消息的接收方（即应用的 CorpID）
- 校验此字段可以防止其他应用/企业的消息被误路由到本应用
- 校验失败时返回 403，记录 warning 日志

**实现细节**:
```python
# callback.py wecom_callback_post() 中
root = ET.fromstring(decrypted_xml)
to_user_name = root.findtext("ToUserName", "")
if to_user_name and to_user_name != adapter.corp_id:
    logger.warning(f"ToUserName mismatch: expected {adapter.corp_id}, got {to_user_name}")
    return PlainTextResponse("Invalid receiver", status_code=403)
```

## Risks / Trade-offs

1. **DB 去重表无限增长**
   - **风险**: `channel_message_dedup` 表如果不清理，会持续增长
   - **缓解**: 在 `cleanup_expired()` 中定期删除过期记录；表只有两列，占用极小

2. **@前缀清洗误伤**
   - **风险**: 如果用户正常消息以 `@某人 ` 开头（非 @应用），也会被清洗
   - **缓解**: 在企业微信自建应用的群聊中，用户发消息给应用时必须 @应用，正常 `@同事 ` 的消息不会推送到应用回调。因此清洗 `@\S+\s+` 是安全的。

3. **速率限制跨 worker 不一致**
   - **风险**: 内存限流器在多 worker 下每个 worker 独立计数，实际限制为 `N * max_per_minute`
   - **缓解**: 这在企业微信场景下是可接受的（稍微宽松），因为企业微信本身也有平台级频率限制。如需严格限流，后续可迁移到 Redis 或 DB 计数。

4. **metadata 序列化变更的兼容性**
   - **风险**: 已有数据库中的 `str(dict)` 格式记录与新写入的 JSON 格式混存
   - **缓解**: 读取时尝试 `json.loads()`，失败时 fallback 到 `ast.literal_eval()` 或原样返回字符串。
