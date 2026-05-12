## 1. 群聊 @前缀清洗

- [x] 1.1 在 `src/channels/wecom/adapter.py` 的 `parse_message()` 中，text 分支增加 `@前缀清洗逻辑`
- [x] 1.2 使用正则 `^@\S+\s+` 匹配并去除前缀
- [x] 1.3 在清洗前后分别记录 debug 日志，便于排查
- [ ] 1.4 单元测试: 验证 `@应用 消息` → `消息`, `@应用   消息` → `消息`, `普通消息` 不变

## 2. Subscribe 欢迎消息

- [x] 2.1 在 `src/channels/callback.py` 的 event 处理分支中，增加 `event_type == "subscribe"` 判断
- [x] 2.2 发送欢迎消息: 调用 `adapter.send_text(welcome, message.user_id)`
- [x] 2.3 欢迎消息文案从配置读取（fallback 到默认文案），配置路径: `settings.channels.wecom.welcome_message`
- [x] 2.4 使用 `asyncio.create_task()` 异步发送，不阻塞回调响应
- [ ] 2.5 单元测试: 验证 subscribe 事件触发欢迎消息发送

## 3. 多 Worker 消息去重

- [x] 3.1 在 `src/channels/idempotency.py` 中，重构 `MessageDeduplicator` 为基于 PostgreSQL 的实现
- [x] 3.2 新增 `channel_message_dedup` 表（message_id TEXT PRIMARY KEY, created_at REAL）
- [x] 3.3 `is_duplicate()` 使用 INSERT ... ON CONFLICT（或 try/except IntegrityError）判断重复
- [x] 3.4 新增 `cleanup_expired()` 方法，删除 TTL 外的记录
- [x] 3.5 在 `src/main.py` lifespan 中启动定时清理任务（复用现有 memory cleanup task 或新增）
- [ ] 3.6 单元测试: 验证分布式去重（跨连接去重有效）

## 4. 速率限制配置消费

- [x] 4.1 在 `src/channels/wecom/adapter.py` 的 `__init__` 中初始化 `_rate_limiter`（`Dict[str, deque]`）
- [x] 4.2 实现 `_check_rate_limit(user_id)` 滑动窗口方法
- [x] 4.3 在 `send_long_message()` 开头调用 `_check_rate_limit()`，超限则记录 warning 并返回 False
- [x] 4.4 在 `wecom_callback_post()` 中收到消息后也调用 `_check_rate_limit()`，超限则返回 "success" 但跳过处理
- [ ] 4.5 单元测试: 验证限流阈值生效

## 5. Metadata 序列化规范化

- [x] 5.1 在 `src/channels/session.py` 中，将所有 `str(metadata)` 替换为 `json.dumps(metadata, ensure_ascii=False)`
- [x] 5.2 将所有 `str(context_data)` 替换为 `json.dumps(context_data, ensure_ascii=False)`
- [x] 5.3 将所有 `str(attachments)` 替换为 `json.dumps(attachments, ensure_ascii=False)`
- [x] 5.4 读取处增加 `json.loads()` 解析，失败时 fallback 到原样返回
- [ ] 5.5 单元测试: 验证写入和读取的 round-trip

## 6. ToUserName 校验

- [x] 6.1 在 `src/channels/callback.py` 的 `wecom_callback_post()` 中，解密后解析 `ToUserName`
- [x] 6.2 如果 `ToUserName` 非空且不等于 `adapter.corp_id`，返回 403
- [x] 6.3 记录 warning 日志，包含期望和实际值
- [ ] 6.4 单元测试: 验证正确和错误的 ToUserName

## 7. 集成测试与回归

- [ ] 7.1 运行现有渠道相关单元测试，确保无回归
- [ ] 7.2 手动测试: 私聊文本消息正常收发
- [ ] 7.3 手动测试: 群聊 @应用 消息前缀已清洗
- [ ] 7.4 手动测试: 新用户首次进入收到欢迎消息
- [ ] 7.5 手动测试: 超过速率限制后消息被节流
- [x] 7.6 验证 `npm run build` 通过（前端无变更，但需确认）
