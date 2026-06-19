# 代码审查：LLM 调用、智能体对话、工具调用

> 审查范围：`src/llm/`、`src/core/agent.py`、`src/tools/`、`src/core/skill_executor.py`
> 审查日期：2026-06-19
> 原则：逻辑复杂且经过多轮测试，原则上不改动，仅记录有十足把握确认的问题。

---

## 严重程度说明

- 🔴 **必须修复**：存在明确 bug，会导致数据错误、崩溃或安全漏洞
- 🟡 **建议修复**：边界情况下的异常行为，影响可靠性或可维护性
- 🔵 **改进建议**：代码质量、性能或风格优化，非阻塞性

---

## 🔴 必须修复

### 1. `skill_executor.py` — `execute_skill_python` 全局修改 `sys.stdout`/`sys.stderr`

**文件**：`src/core/skill_executor.py`，`execute_skill_python` 方法

**问题**：直接替换 `sys.stdout` 和 `sys.stderr` 是进程级全局操作。FastAPI 通过 `asyncio` 并发处理多个请求，若两个会话同时执行 Python skill，会导致：
- 会话 A 的 stdout 输出被会话 B 的 `StringIO` 捕获
- 会话 B 执行完毕后恢复 `sys.stdout`，此时会话 A 的后续输出写入已关闭的 `StringIO`

**修复方向**：不修改全局 `sys.stdout`，改用 `contextlib.redirect_stdout` + `redirect_stderr`（同样有并发问题，但更明确），或直接在子进程中执行 Python 代码（与 `_execute_command` 一致）。

---

### 2. `tool/executor.py` — 日志打印完整工具参数，可能泄露敏感数据

**文件**：`src/tools/executor.py`，`execute` 方法

```python
logger.info(f"执行工具: {tool_name}, 参数: {parameters}")
```

**问题**：工具参数可能包含密码、API key、邮件正文等敏感内容（如 `send_email` 工具的 `body` 参数、`smtp_login` 的 `password`）。`logger.info` 级别默认输出到控制台和日志文件，存在敏感信息泄露风险。

**修复方向**：将参数日志降级为 `logger.debug`，或对已知敏感字段（`password`、`api_key`、`token`）做脱敏处理。

---

## 🟡 建议修复

### 3. `key_pool.py` — 快速路径依赖 `Semaphore._value` 私有属性

**文件**：`src/llm/key_pool.py`，`_KeySlot.available` 属性

```python
@property
def available(self) -> bool:
    return self.semaphore._value > 0  # type: ignore[attr-defined]
```

**问题**：
1. `_value` 是 CPython 实现细节，非公开 API，未来 Python 版本可能改变
2. 已有 `type: ignore` 注释表明作者知道这不是标准接口

**修复方向**：用 `asyncio.Semaphore` 的 `_locked()` 方法（Python 3.13+）替代，或维护一个独立的计数器跟踪可用槽位。

---

### 4. `failover.py` — `_wait_any_slot` 用 `next(iter(done))` 取已完成任务

**文件**：`src/llm/failover.py`，`_wait_any_slot` 方法

```python
completed_task = next(iter(done))
```

**问题**：`done` 是一个 `set`，`iter(set)` 的顺序不确定。虽然任意一个已完成任务都表示有 slot 可用，但如果有多个同时完成，取到的不一定是"最先完成"的那个。在当前场景下不影响正确性（任何可用 slot 都行），但在极端时序下可能导致 Key 选择不均匀。

---

### 5. `gateway.py` — 每次调用创建新的 `httpx.AsyncClient`，无连接复用

**文件**：`src/llm/providers/qwen.py`、`zhipu.py`、`deepseek.py`

```python
async with httpx.AsyncClient(timeout=300.0) as client:
    response = await client.post(...)
```

**问题**：Agent 主循环可能连续多轮调用 LLM（最多 20 轮），每次都创建新的 `httpx.AsyncClient`，无法复用 TCP 连接。对于同一个 provider + api_key 的连续调用，每次都要重新建立 TLS 连接（约 100-300ms 额外延迟）。

**修复方向**：在 Provider 层缓存 `httpx.AsyncClient` 实例（按 api_key 分），Provider 销毁时关闭 client。或在 `_build_provider` 中不做缓存，但让 Provider 自身持有 long-lived client。

---

### 6. `provider` 错误处理不一致 — `raise RuntimeError(...)` vs `raise ... from None`

**文件**：`src/llm/providers/qwen.py`、`zhipu.py` vs `src/llm/providers/deepseek.py`

```python
# qwen.py / zhipu.py:
raise RuntimeError(f"通义千问API请求失败: {e.response.text}")

# deepseek.py:
raise RuntimeError(f"DeepSeek API请求失败: {e.response.text}") from None
```

**问题**：`from None` 抑制原始异常链，影响调试。三个 provider 行为不一致，`is_retryable_error` 依赖 `RuntimeError` 的字符串匹配来判断错误类型，抑制异常链会让日志中缺少原始 HTTP 响应信息。

**修复方向**：统一使用 `raise RuntimeError(...) from e` 或统一 `from None`，保持一致。

---

### 7. `failover.py` — 流式 failover 仅在连接阶段生效

**文件**：`src/llm/failover.py`，`stream_with_failover` 方法

```python
first_chunk = await stream_iter.__anext__()
# ... 如果第一个 chunk 成功，就认为连接成功
yield first_chunk
async for chunk in stream_iter:
    yield chunk
```

**问题**：如果流在传输中途断开（如网络波动），无法自动切换到备用 provider，已输出的内容会丢失。这是架构限制而非 bug，但应在文档中说明。

---

## 🔵 改进建议

### 8. `gateway.py` — 过多冗余日志

**文件**：`src/llm/gateway.py`

`_call_with_pool`、`_stream_with_pool`、`chat`、`chat_with_tools` 等方法都添加了 `start_time` + `logger.info` 计时日志，如：

```python
chat_start = time.time()
logger.info(f"[LLM] chat() called, provider=...")
# ...
chat_duration = time.time() - chat_start
logger.info(f"[LLM] chat() completed, duration=...")
```

**问题**：Agent 主循环内已有完整的调用计时，gateway 层的日志与 agent 层重复，且 `logger.info` 级别在生产环境会产生大量输出。

**修复方向**：将 gateway 层日志降级为 `logger.debug`，或只保留关键节点的 info 日志。

---

### 9. `failover.py` — 冗余的 `import time`

**文件**：`src/llm/failover.py`

文件顶部已 `import time`，但 `call_with_failover` 方法内再次 `import time`（实际上没有，但 `gateway.py` 中存在）。

**文件**：`src/llm/gateway.py`，`_call_with_pool`、`chat`、`chat_with_tools` 方法

```python
async def _call_with_pool(self, fn_name: str, **kwargs) -> Any:
    import time  # 冗余，顶部已有
    call_start = time.time()
```

**修复方向**：移除方法内的 `import time`。

---

### 10. `tool/executor.py` — `execute_batch` 遇到第一个失败就停止

**文件**：`src/tools/executor.py`，`execute_batch` 方法

```python
if not result.get("success", False):
    break
```

**问题**：批量执行时，第一个工具失败就放弃后续所有任务。某些场景下可能需要全部执行或按条件继续。当前行为可能是有意设计，建议在方法文档中明确说明此策略。

---

### 11. `base.py` — `_format_messages` 对 `assistant + tool_calls` 时 content 的处理

**文件**：`src/llm/providers/base.py`，`_format_messages`

```python
elif role == "assistant" and "tool_calls" in msg:
    assistant_msg = {
        "role": "assistant",
        "content": str(content) if not isinstance(content, str) else content,
        "tool_calls": msg.get("tool_calls", [])
    }
```

**问题**：当 assistant 消息同时包含 `tool_calls` 和 list 类型的 content（如多模态内容）时，`str(content)` 会把 list 转成字符串表示。OpenAI API 要求此时 content 为 `null` 或字符串，list 格式不适用。当前代码的 `str()` 处理是合理的 fallback，但可能导致意外的字符串输出。

---

### 12. `key_pool.py` — `_get_slot` 快速路径注释不准确

**文件**：`src/llm/key_pool.py`，`_get_slot` 方法

```python
# 在锁外获取信号量（此时 _value > 0，不会阻塞）
acquired = await slot.acquire(timeout=self._queue_timeout)
```

**问题**：注释说"不会阻塞"，但在锁释放到 `slot.acquire()` 之间，其他协程可能已经占用了该 slot，导致 `wait_for` 需要等待。实际行为是"很可能不阻塞"而非"不会阻塞"，注释应更正。

---

## 总结

| 严重程度 | 数量 | 主要关注点 |
|---------|------|-----------|
| 🔴 必须修复 | 2 | sys.stdout 全局污染、敏感信息日志泄露 |
| 🟡 建议修复 | 5 | 私有属性依赖、连接复用、异常链一致性 |
| 🔵 改进建议 | 5 | 日志优化、注释准确性 |

**优先修复**：
1. `execute_skill_python` 的 `sys.stdout` 问题 — 并发场景下必然出错
2. 工具参数日志脱敏 — 安全合规要求

**可暂缓**：
- httpx 连接复用（性能优化，非功能问题）
- 日志级别调整（运维层面可过滤）
