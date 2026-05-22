# Phase 2.1 短期记忆修复 — 实施计划

> 版本: v1.0 | 创建: 2026-04-28 | 状态: 待审核

## 前置状态确认

Phase 1 部分任务已完成：
- **任务 1.1 统一配置来源** — ✅ Agent 已从 `settings.memory.short_term` 读取配置（`agent.py:118-121`）
- **任务 1.2 消除双实例** — ⚠️ DialogManager 未接入生产（dead code），不影响运行，暂不处理
- **任务 1.3 MemoryManager 接入** — ✅ Agent 已使用 `MemoryManager`（`agent.py:118`）
- **任务 1.4 主动清理机制** — ❌ 未实现，`cleanup_interval` 配置存在但无消费方，作为本次附带任务一并完成

---

## 现状问题分析

### P1: 恢复历史会话时 Agent 上下文为空

**现象**：用户在历史会话中发新消息，Agent 完全不知道之前的对话内容。

**原因链**：
1. `Agent.__init__` 创建空的 `MemoryManager` → 内部 `ShortTermMemory._cache` 为空 `dict`
2. `process_message()` 直接 `self.memory.add()` + `_build_messages()`，从空缓存开始
3. 虽然 `main.py:882-895` 将消息持久化到 `chat_messages` 表，但 **没有任何代码在处理新消息前从 DB 加载历史到 memory**
4. 前端通过 `GET /api/sessions/{id}/messages` 独立加载显示历史，与 Agent 的 memory 完全独立

**影响**：在历史会话中继续聊天 = 在旧会话中开始全新对话，Agent 无法理解上下文。

### P1.1: 附件信息在历史恢复后丢失

**现状**：
- 用户上传文件时，附件信息被嵌入到 `enhanced_input` 文本中（`agent.py:1384`），格式为 `[Attachments]\n- file.pdf (file, mime)\n\n**📎 Uploaded files available:**\n- File: file.pdf\n  Full path: /path/to/file`
- 这个 `enhanced_input` 作为 `content` 存入 DB（`main.py:885`）
- `metadata.attachments` 也保存了文件信息（`main.py:881`）
- 所以从 DB 加载历史消息时，`content` 字段**已经包含**附件路径文本

**结论**：只要正确恢复 `content` 字段，附件信息自然恢复。无需额外处理。

### P3: max_messages 配置值（已解决但需确认）

**现状**：
- `config.yaml` 已设为 `max_messages: 100`，与代码默认值一致
- Agent 从 `settings.memory.short_term.max_messages` 读取（`agent.py:119`）

**结论**：已解决，无需修改。

---

## 实施任务

### 任务 1: ShortTermMemory 新增 `load_history()` 方法

**目标**：支持批量加载历史消息到空 session。

**修改文件**: `src/memory/short_term.py`

**实现方案**:

```python
def load_history(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
    """
    批量加载历史消息到指定 session（仅在 session 为空时生效）。
    
    用于会话恢复场景：Agent 首次处理某 session 的消息前，
    从 DB 加载历史消息到 ShortTermMemory。
    
    Args:
        session_id: 会话 ID
        messages: 历史消息列表，每条消息需包含 role 和 content 字段
                  顺序应为时间正序（ASC）
    
    注意：
    - 仅在 session 不存在或为空时加载，已有数据的 session 不覆盖
    - 加载量受 max_messages 限制，超出部分自动丢弃最早的消息
    """
    # 如果 session 已存在且有数据，跳过加载
    if session_id in self._cache and len(self._cache[session_id]) > 0:
        return
    
    # 创建 deque 并加载消息
    dq = deque(maxlen=self.max_messages)
    for msg in messages:
        if "role" in msg and "content" in msg:
            if "timestamp" not in msg:
                msg["timestamp"] = datetime.now().isoformat()
            dq.append(msg)
    
    self._cache[session_id] = dq
    self._timestamps[session_id] = datetime.now()
```

**设计决策说明**:
- 使用 `deque(maxlen)` 自动裁剪：如果历史消息超过 `max_messages`，最早的自动丢弃
- 加载时更新 `_timestamps`，防止刚加载就被 TTL 清理
- `load_history` 是幂等操作：重复调用在已有数据时安全跳过
- 保留原始消息的完整字段（包括 `tool_calls`、`_skill_summary` 等），确保 `_build_messages()` 正常工作

---

### 任务 2: MemoryManager 代理 `load_history()` 方法

**目标**：通过 MemoryManager 统一入口暴露历史加载能力。

**修改文件**: `src/memory/manager.py`

**实现方案**:

在 `MemoryManager` 中新增方法：

```python
def load_history(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
    """批量加载历史消息到短期记忆（仅在 session 为空时生效）"""
    self.short_term.load_history(session_id, messages)
```

---

### 任务 3: Agent.process_message() 增加历史加载逻辑

**目标**：处理新消息前自动从 DB 加载该 session 的历史到 memory。

**修改文件**: `src/core/agent.py`

**插入位置**: `process_message()` 方法中，在 `logger.info(f"Processing message...")` 之后、`self.memory.add()` 之前（约 `agent.py:1305` 行之后）。

**实现方案**:

```python
# 在 process_message() 中，约 line 1305 之后插入:

# 恢复历史会话上下文：如果该 session 的 memory 为空，从 DB 加载历史消息
if self.memory.get_message_count(session_id) == 0:
    try:
        from src.db.models import MessageDB
        db_messages = MessageDB.list_by_session(session_id, limit=self.memory.short_term.max_messages)
        if db_messages:
            # 将 DB 消息转换为 memory 格式
            history_messages = []
            for msg in db_messages:
                history_messages.append({
                    "role": msg["role"],
                    "content": msg["content"] or "",
                    "timestamp": msg.get("created_at", ""),
                })
            self.memory.load_history(session_id, history_messages)
            logger.info(f"Loaded {len(history_messages)} history messages for session {session_id}")
    except Exception as e:
        logger.warning(f"Failed to load history for session {session_id}: {e}")
        # 加载失败不影响正常流程
```

**设计决策说明**:
- **惰性加载**：只在 memory 为空时加载，避免每次请求都查 DB
- **加载量限制**：只加载最近 `max_messages` 条（默认 100），与短期记忆窗口一致
- **错误容忍**：加载失败只记录 warning，不阻塞正常消息处理
- **消息格式转换**：只取 `role`、`content`、`timestamp` 三个字段。不恢复 `tool_calls` 等中间状态，原因如下：
  - DB 中只存了 user 和 assistant 消息（`main.py:882-895` 只存这两种 role）
  - tool 消息、tool_calls 等中间执行细节不持久化到 `chat_messages`
  - 加载的历史只需提供对话上下文，不需要完整的工具调用链
- **附件信息保留**：DB 中的 `content` 字段已经包含附件路径文本，无需额外处理

---

### 任务 4: MessageDB.list_by_session 增加 role 过滤支持

**目标**：只加载有上下文价值的消息（user 和 assistant），排除 system 消息。

**修改文件**: `src/db/models.py`

**分析**：
- `chat_messages` 表中，`main.py:882-895` 只存入 `role="user"` 和 `role="assistant"` 的消息
- 但不排除其他路径可能存入 system 消息
- 为安全起见，增加 role 过滤参数，只加载 user 和 assistant 消息

**实现方案**:

修改 `MessageDB.list_by_session` 方法签名，增加可选的 `roles` 参数：

```python
@staticmethod
def list_by_session(session_id: str, limit: int = 100, roles: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """获取会话的所有消息"""
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if roles:
            placeholders = ",".join([placeholder] * len(roles))
            cursor.execute(f"""
                SELECT * FROM chat_messages
                WHERE session_id = {placeholder} AND role IN ({placeholders})
                ORDER BY created_at ASC
                LIMIT {placeholder}
            """, (session_id, *roles, limit))
        else:
            cursor.execute(f"""
                SELECT * FROM chat_messages
                WHERE session_id = {placeholder}
                ORDER BY created_at ASC
                LIMIT {placeholder}
            """, (session_id, limit))
        # ... 后续处理不变
```

Agent 调用时传入 `roles=["user", "assistant"]`。

---

### 任务 5: 过期会话主动清理机制

**目标**：实现配置中 `cleanup_interval: 300` 秒的定时清理，不依赖惰性清理。

**修改文件**: `src/main.py`

**实现方案**:

在 FastAPI app 的 `lifespan` 中启动后台清理任务：

```python
import asyncio

async def memory_cleanup_task():
    """定期清理过期的短期记忆会话"""
    from src.core.agent import master_agent
    while True:
        try:
            await asyncio.sleep(settings.memory.cleanup_interval)
            cleaned = master_agent.memory.cleanup_expired()
            if cleaned > 0:
                logger.info(f"[MemoryCleanup] Cleaned {cleaned} expired sessions")
        except Exception as e:
            logger.error(f"[MemoryCleanup] Error: {e}")
```

在 `lifespan` 中启动：
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 现有启动逻辑 ...
    cleanup_task = asyncio.create_task(memory_cleanup_task())
    yield
    cleanup_task.cancel()
    # ... 现有关闭逻辑 ...
```

**注意**：使用 `asyncio.create_task` 而非 APScheduler，因为项目是 FastAPI 单进程架构，不需要额外依赖。

---

## 涉及文件汇总

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `src/memory/short_term.py` | 修改 | 新增 `load_history()` 方法 |
| `src/memory/manager.py` | 修改 | 新增 `load_history()` 代理方法 |
| `src/core/agent.py` | 修改 | `process_message()` 中增加历史加载逻辑 |
| `src/db/models.py` | 修改 | `MessageDB.list_by_session()` 增加 `roles` 参数 |
| `src/main.py` | 修改 | 新增定时清理后台任务 |

---

## 数据流图

```
用户发送消息到历史会话
    │
    ▼
Agent.process_message(user_input, session_id)
    │
    ├─ memory.get_message_count(session_id) == 0 ?
    │   │
    │   ├─ YES: 从 DB 加载历史
    │   │   │
    │   │   ▼
    │   │   MessageDB.list_by_session(session_id, limit=100, roles=["user","assistant"])
    │   │   │
    │   │   ▼
    │   │   转换为 [{role, content, timestamp}, ...]
    │   │   │
    │   │   ▼
    │   │   memory.load_history(session_id, messages)
    │   │   │
    │   │   ▼
    │   │   ShortTermMemory._cache[session_id] 被填充
    │   │   │
    │   │   ▼
    │   │   logger.info("Loaded N history messages")
    │   │
    │   └─ NO: 跳过，继续正常流程
    │
    ▼
memory.add(session_id, "user", enhanced_input)  ← 追加新消息
    │
    ▼
_build_messages(session_id)  ← 从 memory 中获取完整上下文（历史 + 新消息）
    │
    ▼
调用 LLM...
```

---

## 验收测试

### 手动测试场景

1. **新会话正常对话**：
   - 创建新会话，发送消息 → 正常回复
   - 确认 memory 中有消息，历史加载逻辑不触发（memory 非空）

2. **恢复历史会话**：
   - 在会话 A 中进行多轮对话
   - 切换到会话 B 进行对话
   - 切换回会话 A，发送新消息
   - **预期**：Agent 能理解会话 A 之前的对话内容

3. **附件信息保留**：
   - 在会话中上传文件，确认回复中引用了文件
   - 切换到其他会话后切回
   - 发送消息引用之前上传的文件
   - **预期**：Agent 能识别之前上传的文件路径信息

4. **重启服务后恢复**：
   - 进行对话后重启后端服务
   - 重新连接，在历史会话中发送消息
   - **预期**：Agent 仍能感知之前的对话内容（因为从 DB 加载）

5. **长对话截断**：
   - 会话中有超过 100 条历史消息
   - 恢复该会话后发送新消息
   - **预期**：只加载最近 100 条消息，最早的被自动丢弃

### 单元测试

| 测试用例 | 文件 | 验证内容 |
|----------|------|----------|
| `test_load_history_empty_session` | `tests/unit/test_short_term.py` | 空 session 加载历史后能通过 get_context 获取 |
| `test_load_history_non_empty_skip` | `tests/unit/test_short_term.py` | 已有数据的 session 加载历史被跳过 |
| `test_load_history_truncation` | `tests/unit/test_short_term.py` | 超过 max_messages 的历史被自动截断 |
| `test_load_history_format` | `tests/unit/test_short_term.py` | 加载的消息保留 role/content/timestamp |
| `test_memory_manager_load_history` | `tests/unit/test_memory.py` | MemoryManager.load_history 正确代理到 ShortTermMemory |

---

## 风险和注意事项

1. **DB 查询性能**：每次进入空 session 时查询一次 DB。在高并发场景下，可在 `load_history` 中加去重标记避免并发重复加载（当前 `get_message_count == 0` 检查在单线程 SSE 模型下足够安全）。

2. **历史消息中不含 tool_calls**：DB 只存 user/assistant 的 content，不含中间工具调用链。恢复后 Agent 只能看到对话文本，无法看到之前的工具执行过程。这是可接受的——人类也不记得每一步的细节，关键是理解之前的对话内容。

3. **deque 被替换为 list 的问题**：`SkillCompleteTool._compress_skill_context` 将 deque 替换为 list（`skill_complete_tool.py:121`），导致 `maxlen` 失效。这不影响本次改动，但应记录为技术债。

4. **TTL 与历史加载的交互**：加载历史时更新 `_timestamps`，确保刚加载的 session 不会立即被清理。但如果用户超过 TTL 时长未操作，下次发消息时 memory 已被清理，会重新从 DB 加载——这是正确的行为。

5. **定时清理任务的并发安全**：`cleanup_expired()` 遍历 `_cache` 并删除过期 session。在 asyncio 单线程模型中，与 `process_message` 不会并发执行（SSE handler 是 async 的，但 Agent 运行在 ThreadPoolExecutor 中）。`dict` 的删除操作在 CPython 中是线程安全的（GIL），但仍建议后续考虑加锁。
