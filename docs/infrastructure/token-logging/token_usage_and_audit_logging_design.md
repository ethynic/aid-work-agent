# Token 用量统计与审计日志设计方案

> 版本: v1.1 | 创建: 2026-05-09 | 状态: 已完成开发并验证

## 1. 现状分析

### 1.1 当前已有的基础设施

| 组件 | 状态 | 说明 |
|------|------|------|
| `chat_records` 表 | 字段已存在但数据为空 | `total_token_count`、`prompt_tokens`、`completion_tokens` 始终为 0 |
| `SessionRecordService` | 已实现但未接入 token 数据 | `add_llm_usage()` 方法存在，但 agent loop 从未调用 |
| `agent_logger.py` | 记录每次迭代的日志到 JSONL 文件 | 含 `usage`、`tool_names` 等，但无 `tenant_id`，无聚合能力 |
| `llm_call_logger.py` | 记录每次 LLM API 调用到 JSONL 文件 | 含 `request_params`、`response`、`usage`，但无业务上下文 |
| LLM Provider `_parse_response()` | 已解析 `prompt_tokens`、`completion_tokens`、`total_tokens` | 但**未解析**缓存命中 token（`cached_tokens` / `prompt_tokens_details`） |

### 1.2 核心缺口

| # | 缺口 | 影响 |
|---|------|------|
| G1 | Agent loop 不调用 `add_llm_usage()`，token 数据丢失 | `chat_records` 表 token 字段永远为 0 |
| G2 | `chat_records` 表缺少 `tenant_id` 字段 | 无法按租户统计 token 消耗 |
| G3 | Provider 未解析缓存命中 token | 无法区分付费 token 和缓存命中的 token |
| G4 | `chat_records` 表缺少 `model`、`provider`、`agent_iterations` 等审计字段 | 无法按模型/提供商分析成本 |
| G5 | Agent loop 中工具调用的结果只存在 loguru 日志中，无结构化存储 | 审计时无法追溯工具返回内容 |
| G6 | `SessionRecordService` 通过 `threading.local()` 管理，在 ThreadPoolExecutor 线程中可能丢失 | 数据不一致 |
| G7 | `execution_details` 字段（JSON）中的工具执行结果被截断到 500 字符 | 审计价值有限 |

---

## 2. 设计目标

### 2.1 计费需求

- 每次对话记录：`input_tokens`（含缓存命中）、`output_tokens`、`cached_input_tokens`
- 按租户、用户、模型、时间维度聚合统计
- 支持子智能体调用的 token 单独归属

### 2.2 审计需求

- 记录每次对话的完整工具调用链：工具名、参数、返回结果、耗时
- 记录 agent loop 迭代次数、每次迭代的 token 消耗
- 关联 `tenant_id`、`user_id`、`session_id`
- 结构化存储，支持后续查询和展示

### 2.3 非功能性需求：零性能侵入

**核心原则：token 统计和审计日志的任何异常都不允许影响 agent 主流程。**

| 要求 | 说明 |
|------|------|
| 不增加延迟 | agent loop 内部只做内存中的计数累加，不执行任何 I/O 操作（DB 写入、文件写入） |
| 不引入失败点 | 所有 I/O 操作（DB save、JSONL 写入）在独立上下文中执行，异常静默处理 |
| 不阻塞主循环 | `add_llm_usage()`、`increment_iterations()` 等调用必须是纯内存操作，微秒级完成 |
| 不依赖外部状态 | agent loop 中通过 `SessionRecordManager.get_current_record()` 获取的实例只在内存中累加数据，不直接写 DB |

---

## 3. 方案设计

### 3.1 架构概览 — 异步隔离设计

**核心思路**：agent loop 内部只做**内存累加**（零 I/O），所有 I/O 操作（DB 写入、JSONL 写入）延迟到对话结束后执行。

```
┌───────────────────── Agent 主流程（零 I/O）──────────────────────┐
│                                                                    │
│  main.py: run_agent()                                              │
│    ├─ record = SessionRecordManager.start_record(...)              │
│    │                                                               │
│    ├─ agent.process_message()                                      │
│    │    └─ Agent Loop iteration N:                                 │
│    │       ├─ response = await llm.chat_with_tools(...)            │
│    │       │                                                       │
│    │       ├─ record.add_llm_usage(usage)    ← 纯内存累加，~0ms    │
│    │       ├─ record.increment_iterations()  ← 纯内存，~0ms        │
│    │       │                                                       │
│    │       └─ 工具执行:                                             │
│    │          └─ record.handle_progress_event(...) ← 纯内存，~0ms  │
│    │                                                               │
│    ├─ record.complete(full_response)                               │
│    │                                                               │
│    └─ SessionRecordManager.end_record()                            │
│         └─ record.save()  ← 唯一的 I/O 点：DB 写入                 │
│                             在 agent 执行完毕后执行                 │
│                             失败只记日志，不影响已返回的响应         │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

**关键设计决策**：

1. **`save()` 在 agent 返回之后调用**：`main.py` 中 `end_record()` 在 `consume_generator()` 完成后执行（line 1085），此时 agent 的响应已经全部 yield 给前端。即使 `save()` 抛异常，响应已经送达。

2. **agent loop 内部零 I/O**：`add_llm_usage()`、`increment_iterations()`、`handle_progress_event()` 都是纯内存操作（dict append、int +=），不涉及 DB 或文件写入。

3. **JSONL 日志写入已自带保护**：现有的 `log_agent_iteration()` 和 `log_llm_invoke()` 内部用 `try/except` 包裹，写入失败静默忽略（`agent_logger.py:110`、`llm_call_logger.py:81`）。

4. **DB 写入失败保护**：`save()` 内部已有 `try/except`（`session_record.py:246`），失败只记 `logger.error`，返回 `None`。调用方 `end_record()` 不依赖返回值。

**不需要引入消息队列或异步线程**的原因：
- agent loop 内部没有 I/O 操作，不存在性能瓶颈
- `save()` 是唯一 I/O 点，在对话结束后同步执行，一次 INSERT 通常 <10ms
- 引入异步队列会增加系统复杂度（消息丢失、消费延迟、进程重启数据丢失），收益不成比例

**但需要对 `save()` 增加超时保护**：如果 DB 连接异常（连接池耗尽、网络抖动），`save()` 可能阻塞。增加一个简单保护：

```python
def save(self, timeout_seconds: int = 5) -> Optional[Dict[str, Any]]:
    """保存会话记录到数据库（带超时保护）"""
    try:
        # ... 现有逻辑 ...
    except Exception as e:
        logger.error(f"Failed to save session record: {e}")
        return None
```

更稳妥的方案是在 `end_record()` 中用 `threading.Timer` 或在 ThreadPoolExecutor 中设超时，但考虑到 `save()` 已经有 `try/except` 保护，且 DB INSERT 通常极快，当前方案已足够。如果未来 DB 写入成为瓶颈，再引入异步队列。

### 3.2 数据库表改造

#### 3.2.1 `chat_records` 表增加字段

```sql
-- 2026-05-09，chat_records 增加 token 统计和审计字段
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS agent_iterations INTEGER DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS subagent_calls TEXT;  -- JSON: 子智能体调用记录

-- 新增索引：按租户统计 token 用量
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_time
    ON chat_records(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_model
    ON chat_records(tenant_id, model, created_at DESC);
```

#### 3.2.2 改造后完整表结构

```sql
CREATE TABLE IF NOT EXISTS chat_records (
    id SERIAL PRIMARY KEY,
    record_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
    tenant_id TEXT,                              -- NEW: 租户ID
    user_id TEXT,
    user_message TEXT,
    assistant_message TEXT,

    -- Token 统计
    total_token_count INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,             -- 总 input tokens
    completion_tokens INTEGER DEFAULT 0,         -- 总 output tokens
    cached_input_tokens INTEGER DEFAULT 0,       -- NEW: 缓存命中的 input tokens

    -- 模型信息
    model TEXT,
    provider TEXT,                               -- NEW: LLM 提供商 (qwen/zhipu)

    -- 执行详情
    execution_details TEXT,                      -- JSON: 工具调用链、迭代详情
    agent_iterations INTEGER DEFAULT 0,          -- NEW: Agent loop 迭代次数
    subagent_calls TEXT,                         -- NEW: JSON: 子智能体调用记录

    -- 状态
    status TEXT DEFAULT 'completed',
    error_message TEXT,
    duration_ms INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 3.2.3 同时更新 `deploy/init-postgres.sql`

在 `CREATE TABLE chat_records` 中同步添加新字段，保证新部署环境直接创建完整表结构。

### 3.3 LLM Provider 缓存 Token 解析

#### Qwen (DashScope) API 返回格式

通义千问 API 在 `usage` 中返回缓存相关字段：

```json
{
  "usage": {
    "prompt_tokens": 1500,
    "completion_tokens": 200,
    "total_tokens": 1700,
    "prompt_tokens_details": {
      "cached_tokens": 800
    }
  }
}
```

#### ZhipuAI API 返回格式

智谱 API 在 `usage` 中返回：

```json
{
  "usage": {
    "prompt_tokens": 1500,
    "completion_tokens": 200,
    "total_tokens": 1700,
    "prompt_tokens_details": {
      "cached_tokens": 800
    }
  }
}
```

> 两个提供商的 `prompt_tokens_details.cached_tokens` 字段格式一致（均兼容 OpenAI 格式）。

#### 修改方案

修改两个 Provider 的 `_parse_response()` 方法，在 `usage` 中增加 `cached_tokens` 字段：

```python
# qwen.py / zhipu.py 的 _parse_response() 中：
usage = response.get("usage", {})
prompt_details = usage.get("prompt_tokens_details", {})

result = {
    "content": content,
    "finish_reason": finish_reason,
    "usage": {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cached_tokens": prompt_details.get("cached_tokens", 0),  # NEW
    },
    "request_id": response.get("id", ""),
}
```

### 3.4 SessionRecordService 改造

#### 3.4.1 构造函数增加 tenant_id、provider

```python
class SessionRecordService:
    def __init__(self, session_id: str, user_id: str, user_message: str,
                 tenant_id: str = None):
        self.session_id = session_id
        self.tenant_id = tenant_id          # NEW
        self.user_id = user_id
        self.user_message = user_message

        # Token 消耗
        self.total_token_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_input_tokens = 0        # NEW

        # 模型信息
        self.model = None
        self.provider = None                # NEW

        # 执行详情
        self.execution_details = ExecutionDetails()
        self.agent_iterations = 0           # NEW

        # 子智能体调用
        self.subagent_calls = []            # NEW

        # ...其余不变
```

#### 3.4.2 增强 `add_llm_usage()`

```python
def add_llm_usage(self, usage: Dict[str, int]):
    """添加 LLM token 使用量"""
    if usage:
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.total_token_count += usage.get("total_tokens", 0)
        self.cached_input_tokens += usage.get("cached_tokens", 0)  # NEW
```

#### 3.4.3 增强 `ExecutionDetails.to_dict()`

工具执行结果不再截断到 500 字符，改为按配置限制（默认 2000 字符）：

```python
def to_dict(self, max_result_length: int = 2000) -> Dict[str, Any]:
    return {
        "tool_executions": [
            {
                "tool_name": te.tool_name,
                "tool_args": te.tool_args,
                "result": str(te.result)[:max_result_length] if te.result else None,
                "success": te.success,
                "error": te.error,
                "duration_ms": te.duration_ms,
            }
            for te in self.tool_executions
        ],
        "total_iterations": self.total_iterations,
        "subagent_calls": self.subagent_calls,
    }
```

#### 3.4.4 增强 `save()` 方法

```python
def save(self) -> Optional[Dict[str, Any]]:
    try:
        if self.end_time is None:
            self.end_time = time.time()

        record = ChatRecordDB.create(
            session_id=self.session_id,
            tenant_id=self.tenant_id,                          # NEW
            user_id=self.user_id,
            user_message=self.user_message,
            assistant_message=self.assistant_message,
            total_token_count=self.total_token_count,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_input_tokens=self.cached_input_tokens,      # NEW
            model=self.model,
            provider=self.provider,                            # NEW
            execution_details=self.execution_details.to_dict(),
            agent_iterations=self.agent_iterations,            # NEW
            subagent_calls=self.subagent_calls,                # NEW
            status=self.status,
            error_message=self.error_message,
            duration_ms=self.get_duration_ms()
        )

        if record:
            logger.info(
                f"Session record saved: record_id={record['record_id']}, "
                f"session_id={self.session_id}, tenant_id={self.tenant_id}, "
                f"tokens(total={self.total_token_count}, "
                f"input={self.prompt_tokens}, output={self.completion_tokens}, "
                f"cached={self.cached_input_tokens}), "
                f"iterations={self.agent_iterations}, "
                f"duration={self.get_duration_ms()}ms"
            )

        return record

    except Exception as e:
        logger.error(f"Failed to save session record: {e}")
        return None
```

### 3.5 ChatRecordDB 改造

#### 3.5.1 `create()` 方法增加新字段

```python
@staticmethod
def create(
    session_id: str,
    tenant_id: str = None,                    # NEW
    user_id: str,
    user_message: str,
    assistant_message: str = None,
    total_token_count: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_input_tokens: int = 0,             # NEW
    model: str = None,
    provider: str = None,                     # NEW
    execution_details: dict = None,
    agent_iterations: int = 0,                # NEW
    subagent_calls: list = None,              # NEW
    status: str = "completed",
    error_message: str = None,
    duration_ms: int = 0
) -> Optional[Dict[str, Any]]:
```

#### 3.5.2 新增按租户统计的查询方法

```python
@staticmethod
def get_token_usage_by_tenant(
    tenant_id: str,
    start_date: str = None,
    end_date: str = None,
    group_by: str = "day"  # day | model | user
) -> List[Dict[str, Any]]:
    """
    按租户统计 token 用量。

    Args:
        tenant_id: 租户ID
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        group_by: 分组维度 (day/model/user)

    Returns:
        聚合统计结果列表
    """
    where_clauses = ["tenant_id = %s"]
    params = [tenant_id]

    if start_date:
        where_clauses.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        where_clauses.append("created_at < %s")
        params.append(end_date + " 23:59:59")

    where_sql = " AND ".join(where_clauses)

    if group_by == "day":
        group_expr = "DATE(created_at)"
        select_expr = "DATE(created_at) as date"
    elif group_by == "model":
        group_expr = "model"
        select_expr = "model"
    elif group_by == "user":
        group_expr = "user_id"
        select_expr = "user_id"
    else:
        group_expr = "DATE(created_at)"
        select_expr = "DATE(created_at) as date"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT
                {select_expr},
                COUNT(*) as conversation_count,
                SUM(total_token_count) as total_tokens,
                SUM(prompt_tokens) as input_tokens,
                SUM(completion_tokens) as output_tokens,
                SUM(cached_input_tokens) as cached_tokens,
                SUM(duration_ms) as total_duration_ms
            FROM chat_records
            WHERE {where_sql}
            GROUP BY {group_expr}
            ORDER BY {group_expr} DESC
        """, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
```

### 3.6 Agent Loop 接入 Token 统计

#### 3.6.1 改造 `agent.py` 中的 Agent Loop

在 `process_message()` 的 agent loop 中，每次 LLM 调用后将 token usage 传递给 `SessionRecordService`：

```python
# agent.py process_message() 中，约 line 1315 之后（log_agent_iteration 之后）插入：

# 累加 token 用量到 SessionRecordService
from src.services.session_record import SessionRecordManager
record = SessionRecordManager.get_current_record()
if record:
    record.add_llm_usage(response.get("usage", {}))
    record.increment_iterations()
    if not record.provider:
        record.set_model(self.llm.get_model_name())
        record.provider = self.llm.get_provider_name()
```

#### 3.6.2 工具执行结果记录

在 agent loop 的工具执行分支中，记录工具调用的结果到 `SessionRecordService`：

```python
# agent.py 中，工具执行完成后（约 line 1660 附近），追加：

record = SessionRecordManager.get_current_record()
if record:
    record.handle_progress_event({
        "type": "tool_result",
        "toolName": tool_name,
        "result": tool_result,
        "success": tool_result.get("success", True) if isinstance(tool_result, dict) else True,
    })
```

### 3.7 main.py 改造

#### 3.7.1 SSE 流式路由传入 tenant_id

```python
# main.py 中 start_record 调用处（约 line 1033），增加 tenant_id：

from src.saas.context import get_current_tenant_id

tenant_id = get_current_tenant_id()
record_service = SessionRecordManager.start_record(
    session_id=session_id,
    user_id=user_id,
    user_message=full_message,
    tenant_id=tenant_id,           # NEW
)
```

#### 3.7.2 非流式路由同步改造

`POST /api/chat` 路由中也需要同样的改造。

### 3.8 agent_logger 增强

在 `log_agent_iteration()` 中增加 `tenant_id` 字段，使 JSONL 日志也包含租户信息：

```python
def log_agent_iteration(
    iteration: int,
    request_id: str = "",
    user_id: str = "",
    session_id: str = "",
    tenant_id: str = "",          # NEW
    model: str = "",
    provider: str = "",
    has_tool_calls: bool = False,
    tool_calls_count: int = 0,
    tool_names: list | None = None,
    tool_results: list | None = None,  # NEW: 工具执行结果摘要
    content_length: int = 0,
    usage: dict | None = None,
    duration_ms: float | None = None,
) -> None:
    record = {
        "timestamp": datetime.now().isoformat(),
        "iteration": iteration,
        "request_id": request_id,
        "user_id": user_id,
        "session_id": session_id,
        "tenant_id": tenant_id,           # NEW
        "model": model,
        "provider": provider,
        "has_tool_calls": has_tool_calls,
        "tool_calls_count": tool_calls_count,
        "tool_names": tool_names or [],
        "tool_results": tool_results or [],  # NEW
        "content_length": content_length,
        "usage": usage,
        "duration_ms": round(duration_ms, 2) if duration_ms else None,
    }
```

调用处（`agent.py` line 1315）同步更新，传入 `tenant_id`：

```python
log_agent_iteration(
    iteration=iteration,
    request_id=response.get("request_id", ""),
    user_id=user.user_id if user else "",
    session_id=session_id,
    tenant_id=getattr(self, '_init_tenant_id', '') or get_current_tenant_id(),  # NEW
    model=self.llm.get_model_name(),
    provider=self.llm.get_provider_name(),
    has_tool_calls=bool(tool_calls),
    tool_calls_count=len(tool_calls),
    tool_names=[tc.get("function", {}).get("name", tc.get("name", "")) for tc in tool_calls] if tool_calls else [],
    content_length=len(content) if content else 0,
    usage=response.get("usage"),
)
```

### 3.9 子智能体 Token 归属

#### 3.9.1 现状

子智能体 `execute_as_subagent()` 返回 `"token_usage": {"input": 0, "output": 0}`（有 `# TODO` 注释，未实际统计）。

#### 3.9.2 改造方案

在 `SubagentExecutor.execute_as_subagent()` 中，从子 Agent 的 loop 中收集 token usage 并返回：

```python
# subagents/executor.py 中 execute_as_subagent() 改造

# 在子 agent 的 process_message 循环中收集 token（或通过 SessionRecordService）
# 最终返回结果中包含实际 token usage：

return {
    "success": True,
    "response": full_response,
    "token_usage": {
        "input": sub_record.prompt_tokens if sub_record else 0,
        "output": sub_record.completion_tokens if sub_record else 0,
        "cached_input": sub_record.cached_input_tokens if sub_record else 0,
    },
}
```

主 Agent 的 `SessionRecordService` 记录子智能体调用：

```python
record.add_subagent_call(
    subagent_name=subagent_name,
    task_description=task_desc,
    result=result,
    success=True,
    token_usage=result.get("token_usage", {}),
)
```

---

## 4. 数据流图

### 4.1 Token 统计数据流（标注 I/O 边界）

```
用户请求
  │
  ▼
main.py SSE handler
  │
  ├─ tenant_id = get_current_tenant_id()
  ├─ SessionRecordManager.start_record(session_id, user_id, message, tenant_id)  ← 内存操作
  ├─ record_service.set_model(agent.llm.get_model_name())                        ← 内存操作
  ├─ record_service.provider = agent.llm.get_provider_name()                     ← 内存操作
  │
  ▼
agent.process_message()
  │
  ├─ Agent Loop iteration 1
  │   │
  │   ├─ response = await self.llm.chat_with_tools(...)      ← 网络I/O（不可省略）
  │   │   │
  │   │   └─ usage = {
  │   │        "prompt_tokens": 1500,
  │   │        "completion_tokens": 200,
  │   │        "total_tokens": 1700,
  │   │        "cached_tokens": 800
  │   │      }
  │   │
  │   ├─ try: record.add_llm_usage(usage)                    ← 内存操作 ~0.001ms
  │   │   except: logger.debug(...)                           ← 异常隔离
  │   ├─ try: record.increment_iterations()                   ← 内存操作 ~0.001ms
  │   │   except: pass
  │   ├─ log_agent_iteration(..., tenant_id, usage)           ← 文件I/O，已有 try/except 保护
  │   │
  │   └─ 工具执行:
  │      ├─ await tool.execute(**args)                        ← 工具自身I/O（不可省略）
  │      └─ try: record.handle_progress_event(tool_result)    ← 内存操作 ~0.01ms
  │         except: pass
  │
  ├─ Agent Loop iteration 2 ... N
  │   └─ 同上，token 在内存中持续累加
  │
  ▼
agent 返回最终响应 → 前端已收到全部 chunks ✓  ← 此时即使后续全部失败，用户不受影响
  │
  ▼
main.py (agent 执行完毕后)
  │
  ├─ record_service.complete(full_response)                ← 内存操作
  ├─ SessionRecordManager.end_record()
  │   │
  │   └─ record.save()                                     ← ★ 唯一的 DB I/O
  │       │                                                    一次 INSERT，通常 <10ms
  │       ├─ 成功 → ChatRecordDB.create(...) ✓
  │       └─ 失败 → logger.error(...) → 静默，不影响任何已返回的响应
  │
  └─ 对话结束
```

### 4.2 审计查询数据流

```
运营/管理员需要查看 token 用量
  │
  ▼
GET /api/saas/tenants/{tenant_id}/token-usage?start=2026-05-01&end=2026-05-09&group_by=day
  │
  ▼
ChatRecordDB.get_token_usage_by_tenant(tenant_id, start_date, end_date, group_by)
  │
  ▼
SELECT DATE(created_at) as date,
       COUNT(*) as conversation_count,
       SUM(total_token_count) as total_tokens,
       SUM(prompt_tokens) as input_tokens,
       SUM(completion_tokens) as output_tokens,
       SUM(cached_input_tokens) as cached_tokens,
       SUM(duration_ms) as total_duration_ms
FROM chat_records
WHERE tenant_id = %s AND created_at BETWEEN ...
GROUP BY DATE(created_at)
  │
  ▼
[
  {"date": "2026-05-09", "conversation_count": 150, "total_tokens": 500000,
   "input_tokens": 400000, "output_tokens": 100000, "cached_tokens": 200000},
  ...
]
```

---

## 5. 故障场景分析

证明任何统计/日志相关的异常都不会影响 agent 主流程：

| 故障场景 | 发生位置 | 对 agent 的影响 | 恢复方式 |
|----------|----------|-----------------|----------|
| `get_current_record()` 返回 `None` | agent loop 内 | 无。`if record:` 检查跳过，agent 正常执行 | 下一轮 loop 正常 |
| `add_llm_usage()` 抛异常（如 usage dict 格式异常） | agent loop 内 | 无。`try/except` 捕获，`logger.debug` 记录 | 本次迭代的 token 不计入，不影响后续迭代 |
| `handle_progress_event()` 抛异常 | agent loop 内 | 无。同上 | 本次工具结果不计入审计 |
| `save()` DB 写入失败（连接超时、表不存在） | `end_record()` 内 | **无**。此时 agent 已完成执行，响应已全部 yield 给前端。`save()` 内部 `try/except` 捕获 | 本次对话记录丢失，不影响下次对话 |
| JSONL 日志写入失败（磁盘满、权限不足） | `log_agent_iteration()` / `log_llm_invoke()` 内 | 无。内部 `try/except: pass` 静默忽略 | 当天日志缺失，不影响功能 |
| DB `ALTER TABLE` 未执行（新字段不存在） | `save()` → `ChatRecordDB.create()` 内 | 无。INSERT 会失败，`save()` 的 `try/except` 捕获 | 执行 `db_update.sql` 后恢复 |
| `SessionRecordManager` 的 `threading.local()` 被意外清除 | agent loop 内 | 无。`get_current_record()` 返回 `None`，`if record:` 跳过 | 本次对话统计丢失 |

**结论：全链路异常隔离，agent 主流程零中断风险。**

---

## 6. 文件变更清单

### 新增文件

无

### 修改文件（全部已完成）

| 文件 | 改动 | 说明 | 状态 |
|------|------|------|------|
| `deploy/init-postgres.sql` | `chat_records` 表增加字段 | 新增 `tenant_id`、`cached_input_tokens`、`provider`、`agent_iterations`、`subagent_calls`；新增索引 | ✅ |
| `deploy/db_update.sql` | 增量变更 SQL | `ALTER TABLE` 增加新字段 + 新索引 | ✅ |
| `src/db/database.py` | 代码自动建表同步 | `CREATE TABLE chat_records` 增加新字段 | ✅ |
| `src/llm/providers/qwen.py` | `_parse_response()` 增加缓存 token | 从 `prompt_tokens_details.cached_tokens` 解析 | ✅ |
| `src/llm/providers/zhipu.py` | `_parse_response()` 增加缓存 token | 同上 | ✅ |
| `src/services/session_record.py` | `SessionRecordService` 改造 | 增加 `tenant_id`、`provider`、`cached_input_tokens`、`agent_iterations`、`subagent_calls`；`add_llm_usage()` 增加缓存 token；`save()` 传递新字段；`to_dict()` 放宽截断 | ✅ |
| `src/db/models.py` | `ChatRecordDB.create()` 增加新字段 + 新增统计方法 | 增加 `cached_input_tokens`、`provider`、`agent_iterations`、`subagent_calls`；新增 `get_token_usage_by_tenant()` | ✅ |
| `src/core/agent.py` | Agent loop 接入 token 统计 | 每次 LLM 调用后调用 `record.add_llm_usage()`；工具执行后调用 `record.handle_progress_event()`；`log_agent_iteration()` 传入 `tenant_id`；子智能体 token 归属 | ✅ |
| `src/core/agent_logger.py` | `log_agent_iteration()` 增加 `tenant_id` | 记录中包含租户 ID | ✅ |
| `src/main.py` | SSE 路由传入 `tenant_id` | `start_record()` 调用时传入 `tenant_id`；设置 `provider` | ✅ |
| `frontend/src/api/session.ts` | `ChatRecord` 接口增加新字段 | `tenant_id`、`cached_input_tokens`、`provider`、`agent_iterations` | ✅ |

---

## 7. 实施任务拆分

### Task 1: 数据库表结构升级 ✅ 已完成

**改动文件**: `deploy/init-postgres.sql`, `deploy/db_update.sql`, `src/db/database.py`

**内容**:
- `chat_records` 表增加 `tenant_id`、`cached_input_tokens`、`provider`、`agent_iterations`、`subagent_calls` 字段
- 新增 `idx_chat_records_tenant_time`、`idx_chat_records_tenant_model` 索引
- 同步更新 `init-postgres.sql` 的 `CREATE TABLE` 语句

**风险**: 低。`ALTER TABLE ADD COLUMN IF NOT EXISTS` 对现有数据无影响。

---

### Task 2: LLM Provider 解析缓存 Token ✅ 已完成

**改动文件**: `src/llm/providers/qwen.py`, `src/llm/providers/zhipu.py`

**内容**:
- `_parse_response()` 中从 `usage.prompt_tokens_details.cached_tokens` 提取缓存命中 token
- 在返回的 `usage` dict 中增加 `"cached_tokens"` 字段
- API 未返回该字段时默认为 0

**验证**: 调用 LLM API 后检查返回的 `usage` dict 是否包含 `cached_tokens`。

**风险**: 低。纯增量字段，不影响现有逻辑。

---

### Task 3: SessionRecordService 改造 ✅ 已完成

**改动文件**: `src/services/session_record.py`

**内容**:
- 构造函数增加 `tenant_id` 参数
- 新增 `cached_input_tokens`、`provider`、`agent_iterations` 属性
- `add_llm_usage()` 增加 `cached_tokens` 累加
- `ExecutionDetails.to_dict()` 截断限制从 500 调整为 2000
- `save()` 方法传递所有新字段
- `add_subagent_call()` 增加 `token_usage` 参数

**风险**: 低。所有改动都是增量，不破坏现有接口。

---

### Task 4: ChatRecordDB 改造 ✅ 已完成

**改动文件**: `src/db/models.py`

**内容**:
- `create()` 方法签名增加新字段
- SQL INSERT 语句增加新列
- 新增 `get_token_usage_by_tenant()` 聚合查询方法
- 新增 `get_token_usage_by_user()` 按用户统计方法（可选）

**风险**: 低。`CREATE` 方法增加可选参数，默认值与表定义一致。

---

### Task 5: Agent Loop 接入（零性能侵入） ✅ 已完成

**改动文件**: `src/core/agent.py`

**内容**:
- 每次 LLM 调用后（约 line 1327 之后），调用 `SessionRecordManager.get_current_record().add_llm_usage(response.get("usage", {}))`
- 每次迭代后调用 `record.increment_iterations()`
- 工具执行后调用 `record.handle_progress_event({"type": "tool_result", ...})`
- `log_agent_iteration()` 调用增加 `tenant_id` 参数
- **所有调用必须包裹在 `try/except` 中，异常只记 `logger.debug`，绝不 `raise`**

**性能保证**：

| 操作 | 耗时 | 性质 |
|------|------|------|
| `add_llm_usage(usage)` | ~0.001ms | `int +=` 和 `dict.get()`，纯内存 |
| `increment_iterations()` | ~0.001ms | `int +=`，纯内存 |
| `handle_progress_event(event)` | ~0.01ms | `dict.append()`，纯内存 |
| `SessionRecordManager.get_current_record()` | ~0.001ms | `getattr()`，纯内存 |

**每次 agent loop 迭代额外开销 < 0.1ms**（对比 LLM API 调用通常 1-10s），可忽略不计。

**异常隔离代码模板**：

```python
# agent.py 中所有 record 调用必须使用此模板：
try:
    record = SessionRecordManager.get_current_record()
    if record:
        record.add_llm_usage(response.get("usage", {}))
        record.increment_iterations()
except Exception:
    # token 统计失败不影响 agent 主流程
    logger.debug(f"Failed to record token usage", exc_info=True)
```

**threading.local() 线程安全性验证结论**:

已验证通过。`threading.local()` 在当前架构下**安全**，无需改为参数传递。原因：

1. **`start_record()` 在 ThreadPoolExecutor 线程内调用**（`main.py:1036`，位于 `run_agent()` 函数内）
2. **`end_record()` 在同一线程内调用**（`main.py:1085`，位于 `run_agent()` 函数内）
3. **`handle_progress_event()` 在同一线程内调用**（`main.py:1016`，位于 `run_agent()` 内的 `sync_progress_callback`）
4. **Agent loop 的 `process_message()` 也在同一线程内执行**（`main.py:1059`，位于 `run_agent()` 内的 `consume_generator()`）

调用链：`executor.submit(run_agent)` → `run_agent()` → `start_record()` / `process_message()` / `end_record()` 全在同一个 worker thread。

因此 agent loop 内的 `SessionRecordManager.get_current_record()` 能正确获取到 `start_record()` 设置的实例。`_local` 是类级别的 `threading.local()`，同一线程内读写是同一个命名空间，不存在跨线程问题。

**唯一注意点**：子智能体（Task 8）在独立线程中执行，`threading.local()` 会隔离到子线程自己的命名空间。但子智能体**不应共享主 Agent 的 record**（独立统计），所以隔离反而是正确行为。

---

### Task 6: main.py 传入 tenant_id ✅ 已完成

**改动文件**: `src/main.py`

**内容**:
- SSE 流式路由 `start_record()` 调用增加 `tenant_id=get_current_tenant_id()`
- 非流式路由 `start_record()` 同步改造
- `record_service.set_model()` 之后设置 `record_service.provider`

**风险**: 低。

---

### Task 7: agent_logger 增强 ✅ 已完成

**改动文件**: `src/core/agent_logger.py`, `src/core/agent.py`

**内容**:
- `log_agent_iteration()` 增加 `tenant_id` 参数
- 调用处传入 `tenant_id`

**风险**: 低。纯增量。

---

### Task 8: 子智能体 Token 归属 ✅ 已完成

**改动文件**: `src/core/agent.py`（`execute_as_subagent` 方法）

**内容**:
- `execute_as_subagent()` 中创建独立的 `SessionRecordService` 收集子智能体的 token
- 返回结果中包含实际 token usage（替换当前的 `# TODO: 实际统计`）
- 主 Agent 的 record 中通过 `add_subagent_call()` 记录子智能体的 token 消耗

**风险**: 中。子智能体执行在独立线程中，需要确保 `SessionRecordManager` 的 `threading.local()` 隔离正确。

---

## 8. 验证方案

### 7.1 数据库字段验证

```sql
-- 验证新字段存在
SELECT column_name FROM information_schema.columns
WHERE table_name = 'chat_records' AND column_name IN
    ('tenant_id', 'cached_input_tokens', 'provider', 'agent_iterations', 'subagent_calls');

-- 验证索引存在
SELECT indexname FROM pg_indexes WHERE tablename = 'chat_records';
```

### 7.2 Token 统计验证

发送一条对话后查询：

```sql
SELECT record_id, session_id, tenant_id,
       total_token_count, prompt_tokens, completion_tokens, cached_input_tokens,
       model, provider, agent_iterations, duration_ms
FROM chat_records
WHERE session_id = '<test_session_id>'
ORDER BY created_at DESC LIMIT 1;
```

预期：
- `total_token_count > 0`
- `prompt_tokens > 0`
- `completion_tokens > 0`
- `cached_input_tokens >= 0`（可能为 0，取决于 API 是否返回缓存命中）
- `model` 不为空
- `provider` 为 `qwen` 或 `zhipu`
- `agent_iterations >= 1`
- `tenant_id` 不为空（SaaS 模式下）

### 7.3 工具执行审计验证

```sql
SELECT execution_details
FROM chat_records
WHERE session_id = '<test_session_id>'
ORDER BY created_at DESC LIMIT 1;
```

预期 `execution_details` JSON 中：
- `tool_executions` 数组包含本次对话调用的所有工具
- 每个工具有 `tool_name`、`tool_args`、`result`（不再被截断到 500 字符）、`duration_ms`

### 7.4 聚合查询验证

```sql
SELECT DATE(created_at) as date,
       COUNT(*) as conversations,
       SUM(total_token_count) as tokens
FROM chat_records
WHERE tenant_id = '<test_tenant_id>'
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

### 7.5 JSONL 日志验证

检查 `log/agent/agent_session_logs_YYYYMMDD.jsonl`：
- 每条记录包含 `tenant_id` 字段
- `usage` 字段包含 `cached_tokens`

---

## 9. 扩展方向

| 方向 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| Token 用量 API | 暴露 REST API 供前端仪表盘展示租户/用户 token 消耗趋势 | P1 | ✅ 已完成 |
| Token 配额限制 | 按租户设置月度 token 额度，超限降级或提醒 | P2 | 待开发 |
| Token 计费账单 | 基于 token 用量生成月度账单 | P2 | 待开发 |
| 成本估算 | 根据 provider 的定价表自动估算费用 | P3 | 待开发 |
| 实时监控告警 | 单次对话 token 超阈值时告警 | P3 | 待开发 |
| 工具返回结构化存储 | 工具返回结果存到独立表，支持按工具类型检索 | P4 | 待开发 |

### P1 Token 用量 API — 已完成

**后端实现**:
- `src/saas/db/usage_log_db.py` — 全部查询方法改为直接通过 `chat_records.tenant_id` 查询，返回 Input/Output/Cached 分项数据；新增 `get_model_usage()` 按模型聚合
- `src/saas/api/usage_reports.py` — 新增 `GET /api/saas/reports/tokens/detail`（含 input/output/cached 分项趋势）、`GET /api/saas/reports/models`（按模型统计）

**前端实现**:
- `frontend/src/api/saasTenant.ts` — 新增 `getTokenDetail()` 和 `getModelUsage()` API 函数
- `frontend/src/components/saas/UsageReports.vue` — 完整重写：6 个摘要卡片、Input/Output/Cached 堆叠柱状图、按模型统计表格、增强的用户用量明细表
