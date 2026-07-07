# 可观测性与质量保障 — 方案设计文档

> 关联文档：[企业级 2B 智能体平台基础设施建设差距分析](../research/enterprise-agent-infrastructure-gap-analysis.md) §2.1
> 关联调研：[AI Agent 平台可观测性设计调研](../research/observability-design-research.md)
> 开发计划：[可观测性与质量保障开发计划](./observability-dev-plan.md)
> 前置重构：[AsyncGenerator 迁移](./async-generator-migration-design.md)（已完成，事件流已结构化）
> 设计日期：2026-05-29
> 更新日期：2026-07-07（§三 架构更新为方案 C：TraceCollector 下沉到 Agent.process_message）
> 状态：🔧 Phase 1 已完成（含方案 C）；Phase 2-4 待开发

---

## 一、背景与目标

### 1.1 为什么可观测性排第一

企业采购 AI 平台最大的顾虑是 **"AI 回答靠谱吗？出了问题怎么排查？成本花在哪了？"**。没有可观测性，企业不敢把 AI 放到关键业务流程中。可观测性是安全、运维、质量优化的基础——它让看不见的问题变得可见。

### 1.2 现状评估

| 能力 | 现状 | 评价 |
|------|------|------|
| LLM 调用日志 | JSONL 格式（`log/llm/llm_invoke_logs_YYYYMMDD.jsonl`），含 request_id | 基础可用 |
| Agent 迭代日志 | JSONL 格式（`log/agent/agent_session_logs_YYYYMMDD.jsonl`） | 基础可用 |
| Token 统计 | `chat_records` 表，按对话/租户/月汇总 | 良好 |
| 错误日志 | `log_error` 表 + 前端 ErrorLogs 页面 | 基础可用 |
| LLM 故障告警 | FailoverGateway 已有熔断器 + 冷却 + Webhook 通知 | 良好 |
| **请求链路追踪** | ❌ 无 trace_id 传播 | 无法追踪一次请求的完整路径 |
| **回复质量评估** | ❌ 无 | 不知道 AI 回答好不好 |
| **幻觉检测** | ❌ 无 | 无法发现 AI 编造内容 |
| **实时监控仪表盘** | ❌ 静态卡片 | 运维无法实时发现问题 |
| **业务告警** | 仅 LLM 层面有告警 | 不覆盖质量/成本/业务层 |

### 1.3 设计目标

1. **全链路追踪**：每次用户请求生成唯一 `trace_id`，贯穿渠道适配 → Agent Loop → LLM 调用 → 工具调用 → 知识库检索 → 子智能体委派 → 响应构建
2. **自动质量评估**：异步 Pipeline 对回复进行多维度评分（忠实度、相关性、完整性）
3. **幻觉检测**：轻量级多层检测，< 50ms，嵌入 Agent Loop 不影响响应延迟
4. **实时监控仪表盘**：WebSocket 推送的实时仪表盘，覆盖性能/质量/成本/可用性
5. **结构化告警**：统一告警框架，覆盖 LLM 可用性、延迟、质量、成本四个维度

### 1.4 设计原则

- **不引入重型框架**：不使用 OpenTelemetry 全栈、ClickHouse，基于 PostgreSQL 自建
- **零侵入埋点**：基于 ContextVar 的追踪上下文，Agent Loop 内纯内存操作零 I/O，不阻塞主流程
- **异步持久化**：追踪数据和评分写入在请求完成后异步执行，不影响用户感知延迟
- **渐进式建设**：分四个阶段交付，每阶段可独立上线、独立产生价值
- **复用现有能力**：`chat_records` 保留计费和审计用途，`FailoverGateway` 告警机制保留并扩展

---

## 二、核心数据模型

### 2.1 双数据库架构

追踪数据使用独立数据库，与业务库分离：

| 数据库 | 用途 | 数据库实例 | 配置环境变量 |
|--------|------|-----------|-------------|
| **业务库** | 业务数据（用户、会话、知识库、SaaS 等） | 腾讯云轻量数据库 / 线上 PG | `DATABASE_URL` |
| **追踪库** | 可观测性数据（obs_traces、obs_spans、obs_scores） | CVM 自托管 PG + TimescaleDB | `LOGS_DATABASE_URL` |

**追踪库特点**：
- 启用 TimescaleDB 扩展，自动压缩（90%+ 存储缩减）、自动过期（90 天 DROP chunk）
- 开发环境：同一 PG 实例，数据库名 `aid-work-logs2`，手动启用扩展
- 生产环境：独立 CVM 实例（2C4G），通过 `LOGS_DATABASE_URL` 配置

**环境变量配置**：

```env
# .env

# 业务库（现有，不改动）
DATABASE_URL=postgresql://aid_user:Aid_2026@124.222.3.254:5433/aid_work_agent

# 追踪库（新增）
# 开发环境：同一实例，不同数据库
LOGS_DATABASE_URL=postgresql://aid_user:Aid_2026@124.222.3.254:5433/aid_work_logs2
# 生产环境：独立 CVM 实例
# LOGS_DATABASE_URL=postgresql://aid_user:Aid_2026@logs-db.internal:5432/aid_work_logs
```

**连接管理**：在 `src/db/database.py` 中新增 `init_logs_pool()` / `get_logs_connection()` 方法，与现有业务库连接池独立，使用相同的 psycopg2 连接池机制。

### 2.2 数据模型概览

借鉴 Langfuse 的 Trace → Span → Generation → Score 层级模型，适配本项目架构：

```
Trace（一次完整用户请求）
  ├── Span: agent_loop       （Agent 推理循环）
  │     ├── Generation: llm_call       （LLM API 调用）
  │     ├── Span: tool:{name}          （工具调用）
  │     ├── Span: knowledge_retrieval  （知识库检索）
  │     └── Span: subagent:{name}      （子智能体委派）
  │           ├── Generation: llm_call
  │           └── Span: tool:{name}
  └── Scores                 （评估分数：忠实度、相关性、完整性...）
```

### 2.3 数据库表设计

> 所有表建在追踪库（`LOGS_DATABASE_URL`）中。使用 TimescaleDB Hypertable 实现自动压缩和过期。

#### TimescaleDB 初始化

```sql
-- 追踪库首次使用时执行（需要超级用户权限）
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

#### obs_traces — 请求追踪

```sql
CREATE TABLE IF NOT EXISTS obs_traces (
    trace_id TEXT PRIMARY KEY,
    session_id TEXT,
    tenant_id TEXT,
    user_id TEXT,
    subagent_id TEXT,
    input TEXT,                   -- 用户输入（脱敏后完整保存）
    output TEXT,                  -- 最终输出（脱敏后完整保存）
    metadata JSONB,               -- 扩展元数据（渠道来源、客户端信息等）
    tags TEXT[],                  -- 标签（用于筛选：error、slow、hallucination 等）
    total_tokens INTEGER DEFAULT 0,
    total_cost NUMERIC(10,6) DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    agent_iterations INTEGER DEFAULT 0,
    tool_calls_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',    -- running / completed / failed
    error_message TEXT,
    source_type TEXT DEFAULT 'chat',  -- chat / wecom / dingtalk / feishu
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 转换为 TimescaleDB Hypertable（按 created_at 按日分区）
SELECT create_hypertable('obs_traces', 'created_at', chunk_time_interval => INTERVAL '1 day', migrate_data => true);

-- 索引
CREATE INDEX idx_obs_traces_tenant_time ON obs_traces(tenant_id, created_at DESC);
CREATE INDEX idx_obs_traces_session ON obs_traces(session_id);
CREATE INDEX idx_obs_traces_status ON obs_traces(status);
CREATE INDEX idx_obs_traces_tags ON obs_traces USING GIN(tags);

-- 7 天后自动压缩（列式压缩，节省 90% 存储）
ALTER TABLE obs_traces SET (timescaledb.compress, timescaledb.compress_segmentby = 'tenant_id');
SELECT add_compression_policy('obs_traces', INTERVAL '7 days');

-- 90 天后自动过期
SELECT add_retention_policy('obs_traces', INTERVAL '90 days');
```

#### obs_spans — 追踪步骤

```sql
CREATE TABLE IF NOT EXISTS obs_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,          -- 父 Span（嵌套关系）
    span_type TEXT NOT NULL,      -- span / generation / event
    name TEXT NOT NULL,           -- 步骤名称（llm_call, tool:search_knowledge_base 等）
    input TEXT,                   -- 输入（JSON 文本，脱敏后完整保存）
    output TEXT,                  -- 输出（JSON 文本，脱敏后完整保存）
    metadata JSONB,               -- 模型参数、工具参数、检索结果数等
    model TEXT,                   -- LLM 模型名（generation 专用）
    model_params JSONB,           -- temperature、top_p 等
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    cost NUMERIC(10,6) DEFAULT 0,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',    -- running / completed / failed
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_obs_spans_trace ON obs_spans(trace_id);
CREATE INDEX idx_obs_spans_parent ON obs_spans(parent_span_id);
CREATE INDEX idx_obs_spans_type_name ON obs_spans(span_type, name);
CREATE INDEX idx_obs_spans_time ON obs_spans(start_time DESC);

-- 转换为 TimescaleDB Hypertable
SELECT create_hypertable('obs_spans', 'created_at', chunk_time_interval => INTERVAL '1 day', migrate_data => true);

-- 7 天后自动压缩
ALTER TABLE obs_spans SET (timescaledb.compress, timescaledb.compress_segmentby = 'trace_id');
SELECT add_compression_policy('obs_spans', INTERVAL '7 days');

-- 90 天后自动过期
SELECT add_retention_policy('obs_spans', INTERVAL '90 days');
```

#### obs_scores — 评估分数

```sql
CREATE TABLE IF NOT EXISTS obs_scores (
    id SERIAL PRIMARY KEY,
    trace_id TEXT,
    span_id TEXT,
    score_name TEXT NOT NULL,     -- faithfulness / answer_relevance / completeness / hallucination
    score_value NUMERIC,          -- 1.0 ~ 5.0 数值评分
    score_source TEXT NOT NULL,   -- auto_eval / user_feedback / manual_annotation
    reasoning TEXT,               -- 评估理由
    metadata JSONB,               -- 评估模型、耗时、原始 prompt 等附加信息
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_obs_scores_trace ON obs_scores(trace_id);
CREATE INDEX idx_obs_scores_name ON obs_scores(score_name);
CREATE INDEX idx_obs_scores_source ON obs_scores(score_source);

-- 转换为 TimescaleDB Hypertable（数据量小，按周分区）
SELECT create_hypertable('obs_scores', 'created_at', chunk_time_interval => INTERVAL '7 days', migrate_data => true);

-- 30 天后自动压缩
ALTER TABLE obs_scores SET (timescaledb.compress, timescaledb.compress_segmentby = 'score_name');
SELECT add_compression_policy('obs_scores', INTERVAL '30 days');

-- 180 天后自动过期
SELECT add_retention_policy('obs_scores', INTERVAL '180 days');
```

### 2.4 与现有表的关系

| 现有表 | 关系 | 说明 |
|--------|------|------|
| `chat_records` | `obs_traces.session_id` = `chat_records.session_id` | 保留 `chat_records` 的计费和审计用途，obs 提供更细粒度追踪 |
| `chat_messages` | `obs_traces.session_id` = `chat_messages.session_id` | **web 端**消息内容存在 `chat_messages`，obs 只存摘要 |
| `channel_messages` | `obs_traces.session_id` = `channel_messages.session_id` | **第三方渠道**消息内容存在 `channel_messages`（与 chat_messages 严格分离，按 source_type 区分） |
| `log_error` | `obs_traces.status = 'failed'` 时关联 | 错误详情仍存在 `log_error`，obs 提供请求级别的上下文 |
| `token_cost_prices` | 计算 `obs_traces.total_cost` 和 `obs_spans.cost` | 复用已有价格表 |

---

## 三、分布式链路追踪

> **架构演进（2026-07-07，方案 C 已落地）**：TraceCollector 接入点已从「SSE handler 调用方旁路」下沉到 `Agent.process_message()` 内部 wrapper，自动从 `SessionRecordService` 读取所有 trace 上下文（含 `source_type`）。Web / wecom / wecom_kf / dingtalk / feishu 等所有渠道**零改造**自动产生 trace。详见 [observability-channel-sessions-design.md](./observability-channel-sessions-design.md)。
>
> 历史背景：AsyncGenerator 迁移（commit `51c41f4`）完成后，事件流已是结构化 dict。原方案在此基础上让每个调用方在 `async for event` 循环中各自接入 TraceCollector —— 由于调用方分散在 `main.py:event_generator`、`channel_routes.py`（4 个渠道）、`scheduler/executor.py` 等多处，该方案导致 4 个渠道全部遗漏 trace。方案 C 把接入点下沉到唯一入口 `Agent.process_message()`，从根上消除「漏接」可能性。

### 3.1 架构：Agent 内部自动收集

TraceCollector 在 `Agent.process_message()` 内部 wrapper 中启动，自动从当前请求绑定的 `SessionRecordService` 解析上下文，无需调用方传任何 trace 相关参数：

```
Agent.process_message() 内部 wrapper
  ├── 从 record_service 解析 trace 上下文（session/tenant/user/source_type/subagent）
  ├── 初始化 TraceCollector（_record 为空时静默跳过：Gradio/CLI/Scheduler）
  └── async for event in self._process_message_impl(...):
        ├── trace_collector.on_event(event)
        └── yield event
  try/except/finally 中处理 on_error / on_complete
LLM 调用信息从已有的 SessionRecordService 获取（token、model、duration）
```

**核心优势**：
- **渠道零改造**：所有现有渠道（Web/wecom/wecom_kf/dingtalk/feishu）和未来新增渠道自动产生 trace，调用方无需任何 trace 相关修改
- **数据来源已结构化**：事件流本身包含 `type`、`timestamp`、`toolName`、`toolArgs`、`result` 等字段
- **LLM 调用数据已有**：`SessionRecordService` 已在每个迭代中收集 token 用量、model、provider、duration
- **LLM 完整上下文**：`llm_call` 事件携带完整 messages 数组，TraceCollector 只保留最后一次（避免重复存储）

### 3.2 事件类型到 Span 的映射

`agent.py` 当前 yield 的事件类型及其追踪语义：

| Agent 事件 type | 追踪映射 | span_type | 说明 |
|-----------------|---------|-----------|------|
| `tool_start` | 创建一个 tool span | `span` | `toolName` = 工具名，`toolArgs` = 输入参数 |
| `tool_result` | 关闭对应的 tool span | `span` | `result` = 输出，`success` = 状态 |
| `llm_call` | 创建/覆盖 generation span | `generation` | 完整 messages、tools schema、response content、usage。**只保留最后一次** |
| `response` | 记录到 trace | — | 最终回复内容（拼接后存 trace.output） |
| `progress` | 记录到 span metadata | — | 进度信息 |
| `clarification` | 创建 event | `event` | 子智能体需要澄清 |
| `cancelled` | 标记 trace 状态 | — | 用户取消 |
| `error` | 标记 trace 状态 | — | 错误 |

**LLM 调用的追踪数据**来自两个来源：
1. **`llm_call` 事件**（新增）：包含完整的 messages 数组（输入上下文）、tools schema、response content
2. **`SessionRecordService`**（已有）：汇总的 token 用量、model、provider
- `prompt_tokens`、`completion_tokens`、`cached_tokens` → `record_service._llm_usage`
- `model`、`provider` → `record_service.model` / `record_service.provider`
- `agent_iterations` → `record_service._llm_call_count`
- `duration_ms` → `record_service.duration_ms`

### 3.3 TraceCollector 实现

TraceCollector 由 `Agent.process_message()` wrapper 实例化，每个事件 yield 前调用 `on_event`，请求结束/出错时由 wrapper 的 `finally` 块调用 `on_complete(_record)` 持久化：

```python
# src/core/trace_collector.py

import time
import uuid
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class SpanRecord:
    """一个工具调用的追踪记录"""
    span_id: str
    name: str
    start_time: float
    tool_args: Optional[str] = None     # 完整的参数 JSON
    end_time: Optional[float] = None
    result: Optional[str] = None        # 完整的返回结果 JSON
    success: bool = True
    duration_ms: int = 0


@dataclass
class TraceRecord:
    """一次完整请求的追踪记录"""
    trace_id: str
    session_id: str
    tenant_id: str
    user_id: str
    subagent_id: Optional[str]
    input: str                          # 用户输入（完整，脱敏后）
    source_type: str = 'chat'
    start_time: float = 0.0
    status: str = 'running'
    output: Optional[str] = None        # 最终输出（完整，脱敏后）
    error_message: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    spans: List[SpanRecord] = field(default_factory=list)
    # LLM 调用数据（从 SessionRecordService 获取）
    total_tokens: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    agent_iterations: int = 0
    duration_ms: int = 0

    def __post_init__(self):
        if not self.start_time:
            self.start_time = time.time()


class TraceCollector:
    """
    追踪数据收集器 — 由 Agent.process_message() wrapper 在请求开始时实例化，
    在事件流（async for event）和 try/except/finally 中旁路调用。

    所有上下文（session/tenant/user/source_type/subagent）从 SessionRecordService
    读取，调用方无需传任何 trace 相关参数。所有 input/output 数据完整保存，不截断。
    """

    def __init__(self, session_id: str, tenant_id: str, user_id: str,
                 input_msg: str, source_type: str = 'chat',
                 subagent_id: str = None):
        self.trace = TraceRecord(
            trace_id=f"tr_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            input=input_msg,
            source_type=source_type,
        )
        self._active_spans: Dict[str, SpanRecord] = {}  # toolName -> SpanRecord
        self._tool_name_counter: Dict[str, int] = {}     # 处理同名工具多次调用

    def on_event(self, event: dict):
        """处理从 agent.process_message() yield 出来的每个事件"""
        event_type = event.get("type")

        if event_type == "tool_start":
            self._handle_tool_start(event)
        elif event_type == "tool_result":
            self._handle_tool_result(event)
        elif event_type == "response":
            data = event.get("data", "")
            if self.trace.output:
                self.trace.output += data
            else:
                self.trace.output = data
        elif event_type == "clarification":
            self.trace.tags.append("clarification")
        elif event_type == "cancelled":
            self.trace.status = "cancelled"
            self.trace.tags.append("cancelled")

    def on_error(self, error: str):
        """Agent 执行出错时调用"""
        self.trace.status = "failed"
        self.trace.error_message = error
        self.trace.tags.append("error")

    def on_complete(self, record_service=None):
        """
        请求完成时调用。从 SessionRecordService 获取 LLM 调用数据，
        然后将完整 trace + spans 异步持久化。
        """
        self.trace.duration_ms = int((time.time() - self.trace.start_time) * 1000)
        if self.trace.status == "running":
            self.trace.status = "completed"

        # 从已有的 record_service 获取 LLM 数据
        if record_service:
            self.trace.total_tokens = (
                getattr(record_service, '_total_prompt_tokens', 0)
                + getattr(record_service, '_total_completion_tokens', 0)
            )
            self.trace.model = getattr(record_service, '_model', None)
            self.trace.provider = getattr(record_service, '_provider', None)
            self.trace.agent_iterations = getattr(record_service, '_llm_call_count', 0)

        # 关闭所有未完成的 span
        now = time.time()
        for span in self.trace.spans:
            if span.end_time is None:
                span.end_time = now
                span.duration_ms = int((now - span.start_time) * 1000)

        # 异步持久化
        from src.core.trace_persist import schedule_persist
        schedule_persist(self.trace)

    def _handle_tool_start(self, event: dict):
        tool_name = event.get("toolName", "unknown")
        tool_args = event.get("toolArgs", {})

        # 处理同名工具多次调用
        count = self._tool_name_counter.get(tool_name, 0) + 1
        self._tool_name_counter[tool_name] = count

        span_id = f"sp_{uuid.uuid4().hex[:16]}"
        span = SpanRecord(
            span_id=span_id,
            name=f"tool:{tool_name}",
            start_time=time.time(),
            tool_args=json.dumps(tool_args, ensure_ascii=False),
        )
        self._active_spans[f"{tool_name}_{count}"] = span
        self.trace.spans.append(span)

    def _handle_tool_result(self, event: dict):
        tool_name = event.get("toolName", "unknown")
        result = event.get("result", {})
        success = event.get("success", True)

        # 找到对应的 active span
        count = self._tool_name_counter.get(tool_name, 1)
        key = f"{tool_name}_{count}"
        span = self._active_spans.get(key)

        if span:
            span.end_time = time.time()
            span.duration_ms = int((span.end_time - span.start_time) * 1000)
            span.result = json.dumps(result, ensure_ascii=False)
            span.success = success
            if not success:
                self.trace.tags.append("tool_error")
            del self._active_spans[key]
```

### 3.4 接入点：Agent.process_message wrapper

TraceCollector 的唯一接入点是 `Agent.process_message()` wrapper（`src/core/agent.py:1769-1834`）。原 `_process_message_impl` 的 yield 逻辑保持不变，外层 wrapper 仅做三件事：解析 record_service → 初始化 TraceCollector → 在事件循环和 try/except/finally 中旁路调用。

```python
# src/core/agent.py（精简示意，以实际代码为准）
async def process_message(self, user_input, session_id, user=None, ...):
    # 1. 从 record_service 解析 trace 上下文
    _record = getattr(self, '_explicit_record_service', None) \
              or SessionRecordManager.get_current_record()

    trace_collector = None
    if _record:
        try:
            trace_collector = TraceCollector(
                session_id=_record.session_id or session_id,
                tenant_id=_record.tenant_id or '',
                user_id=_record.user_id or '',
                input_msg=user_input or _record.user_message,
                source_type=_record.source_type or 'chat',   # ★ 自动来源
                subagent_id=getattr(self, '_subagent_id', None),
            )
            _record.trace_collector = trace_collector
        except Exception as e:
            logger.debug(f"Trace collector init skipped: {e}")

    # 2. 转发事件流，旁路收集
    try:
        async for event in self._process_message_impl(...):
            if trace_collector:
                try: trace_collector.on_event(event)
                except Exception as e: logger.debug(f"Trace on_event failed: {e}")
            yield event
    except Exception as e:
        if trace_collector:
            try: trace_collector.on_error(str(e))
            except Exception as ce: logger.debug(f"Trace on_error failed: {ce}")
        raise
    finally:
        if trace_collector:
            try: trace_collector.on_complete(_record)
            except Exception as e: logger.debug(f"Trace on_complete failed: {e}")
```

**调用方零改造**：

| 调用路径 | 入口 | 接入 trace 的方式 |
|---------|------|------------------|
| Web SSE 聊天 | `main.py:event_generator` → `agent.process_message()` | 自动（`SessionRecordManager.get_current_record()`） |
| 企微会话 / 微信客服 / 钉钉 / 飞书 | `channel_routes.py` → `process_message_sync()` | 自动（`self._explicit_record_service` 由 sync 入口设置） |
| Gradio / CLI / Scheduler | 不调用 `start_record` | `_record` 为空，静默跳过（预期行为） |

`main.py:event_generator` 中**已无任何 TraceCollector 相关代码**（原旁路收集代码已全部删除，仅保留两处注释指向上面的 wrapper）。所有非 SSE 渠道路径（`channel_routes`、未来的新渠道等）自动产生 trace，无需各接一遍。

> 详细方案 C 设计（含 `_explicit_record_service` 在 sync 路径的赋值、record_service 字段映射、子智能体场景的边界处理）见 [observability-channel-sessions-design.md](./observability-channel-sessions-design.md)。

### 3.5 异步持久化

追踪数据在请求完成后异步写入数据库，不阻塞主流程：

```python
# src/core/trace_persist.py

import threading
import queue
import json
from loguru import logger
from src.db.database import get_logs_connection


_persist_queue: queue.Queue = queue.Queue()
_worker_started = False


def schedule_persist(trace: 'TraceRecord'):
    """将 trace 数据放入异步持久化队列"""
    global _worker_started
    if not _worker_started:
        _start_persist_worker()
        _worker_started = True
    _persist_queue.put(trace)


def _start_persist_worker():
    """启动后台持久化线程"""
    def worker():
        while True:
            try:
                trace = _persist_queue.get(timeout=1)
                _do_persist(trace)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Trace persist error: {e}", exc_info=True)

    t = threading.Thread(target=worker, daemon=True, name="trace-persist")
    t.start()


def _do_persist(trace: 'TraceRecord'):
    """将 trace 和 spans 写入数据库"""
    try:
        with get_logs_connection() as conn:
            with conn.cursor() as cur:
                # UPSERT trace
                cur.execute("""
                    INSERT INTO obs_traces
                        (trace_id, session_id, tenant_id, user_id, subagent_id,
                         input, output, metadata, tags,
                         total_tokens, total_cost, duration_ms, agent_iterations,
                         tool_calls_count, status, error_message, source_type,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, 0, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (trace_id) DO UPDATE SET
                        output = EXCLUDED.output,
                        status = EXCLUDED.status,
                        error_message = EXCLUDED.error_message,
                        total_tokens = EXCLUDED.total_tokens,
                        duration_ms = EXCLUDED.duration_ms,
                        agent_iterations = EXCLUDED.agent_iterations,
                        tool_calls_count = EXCLUDED.tool_calls_count,
                        tags = EXCLUDED.tags,
                        updated_at = NOW()
                """, (
                    trace.trace_id, trace.session_id, trace.tenant_id,
                    trace.user_id, trace.subagent_id,
                    trace.input, trace.output,
                    json.dumps({"model": trace.model, "provider": trace.provider},
                               ensure_ascii=False),
                    trace.tags, trace.total_tokens, trace.duration_ms,
                    trace.agent_iterations, len(trace.spans),
                    trace.status, trace.error_message, trace.source_type,
                ))

                # INSERT spans
                for span in trace.spans:
                    cur.execute("""
                        INSERT INTO obs_spans
                            (span_id, trace_id, parent_span_id, span_type, name,
                             input, output, metadata,
                             start_time, end_time, duration_ms, status,
                             error_message, created_at)
                        VALUES (%s, %s, NULL, 'span', %s,
                                %s, %s, %s,
                                to_timestamp(%s), to_timestamp(%s), %s, %s,
                                NULL, NOW())
                        ON CONFLICT (span_id) DO NOTHING
                    """, (
                        span.span_id, trace.trace_id, span.name,
                        span.tool_args, span.result,
                        json.dumps({"success": span.success}, ensure_ascii=False),
                        span.start_time, span.end_time,
                        span.duration_ms,
                        'completed' if span.success else 'failed',
                    ))

                conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist trace {trace.trace_id}: {e}", exc_info=True)
```

### 3.6 脱敏策略

追踪数据在写入前进行脱敏处理：

| 字段 | 脱敏规则 |
|------|---------|
| `input` | 移除手机号（`\d{11}` → `1xx****xxxx`）、邮箱（`*@*` → `***@***`）、身份证号 |
| `output` | 同上 |
| `tool_args` / `result` | 移除 `api_key`、`token`、`password` 等敏感 key |
| `tags` | 不脱敏（由系统生成的标签） |

复用 `src/core/error_log_sink.py` 中已有的 `sanitize_error_info()` 函数。

---

## 四、LLM 响应质量评估

### 4.1 评估指标

采用 RAG Triad + 扩展维度，覆盖 RAG 系统核心质量：

| 指标 | 评估对象 | 分值 | 说明 |
|------|---------|------|------|
| **Faithfulness（忠实度）** | 回答 vs 检索上下文 | 1-5 | 回答中的事实声明是否有上下文依据 |
| **Answer Relevance（回答相关性）** | 回答 vs 用户问题 | 1-5 | 回答是否真正解决了用户问题 |
| **Completeness（完整性）** | 回答覆盖面 | 1-5 | 是否完整回答了问题的所有方面 |
| **Conciseness（简洁性）** | 回答信息密度 | 1-5 | 是否冗余，信息密度是否足够 |

### 4.2 LLM-as-Judge 实现

#### 评分 Prompt 模板

每个维度使用独立的评分 Prompt，基于 1-5 分 rubric 定义标准：

**Faithfulness（忠实度）Prompt**：

```
你是一个AI回答质量评估专家。请根据以下标准对回答进行评分。

【评估维度】: 回答忠实度
【评分规则】:
- 5分: 回答中所有事实声明都能在上下文中找到明确依据，无任何编造
- 4分: 绝大部分声明有依据，存在少量合理推断
- 3分: 主要声明有依据，但有1-2个声明缺乏上下文支持
- 2分: 多个声明无法在上下文中找到依据，存在明显编造
- 1分: 回答大部分内容与上下文无关或矛盾

【用户问题】: {question}
【检索到的上下文】: {context}
【AI回答】: {answer}

请按以下JSON格式输出：
{"reasoning": "逐步分析", "score": 评分整数, "reason": "一句话理由"}
```

Answer Relevance、Completeness、Conciseness 的 Prompt 模板类似，替换评估维度和评分规则即可（详细 Prompt 见调研文档 §3.2）。

#### QualityEvaluator 类

```python
# src/core/quality_evaluator.py

import json
import random
from typing import Optional, Dict, Any
from loguru import logger


class QualityEvaluator:
    """LLM 响应质量评估器"""

    METRICS = {
        "faithfulness": FAITHFULNESS_PROMPT,
        "answer_relevance": ANSWER_RELEVANCE_PROMPT,
        "completeness": COMPLETENESS_PROMPT,
        "conciseness": CONCISENESS_PROMPT,
    }

    # 生产环境默认只评估核心维度
    PRODUCTION_METRICS = ["faithfulness", "answer_relevance"]

    def __init__(self, llm_gateway, sample_rate: float = 0.1,
                 judge_model: str = "qwen-turbo"):
        self.llm = llm_gateway
        self.sample_rate = sample_rate
        self.judge_model = judge_model

    async def evaluate_if_needed(
        self,
        question: str,
        context: str,
        answer: str,
        trace_id: str,
        metrics: list = None
    ) -> Optional[Dict[str, Any]]:
        """根据采样率决定是否评估，返回评估结果或 None"""
        if random.random() > self.sample_rate:
            return None

        eval_metrics = metrics or self.PRODUCTION_METRICS
        scores = {}

        for metric_name in eval_metrics:
            try:
                result = await self._run_judge(metric_name, question, context, answer)
                if result:
                    scores[metric_name] = result
            except Exception as e:
                logger.warning(f"Quality eval failed for {metric_name}: {e}")

        return scores if scores else None

    async def _run_judge(self, metric: str, question: str,
                         context: str, answer: str) -> Optional[Dict]:
        """执行单个维度的 LLM Judge 评估"""
        prompt_template = self.METRICS.get(metric)
        if not prompt_template:
            return None

        prompt = prompt_template.format(
            question=question,
            context=context[:2000],  # 截断过长上下文
            answer=answer[:2000],
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.judge_model,
                temperature=0.0,
            )
            return self._parse_judge_response(response, metric)
        except Exception as e:
            logger.warning(f"Judge call failed: {e}")
            return None

    def _parse_judge_response(self, response: str, metric: str) -> Optional[Dict]:
        """解析 Judge 返回的 JSON 评分"""
        try:
            # 尝试提取 JSON
            text = response if isinstance(response, str) else str(response)
            # 处理 markdown code block 包裹
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            result = json.loads(text.strip())
            score = float(result.get("score", 0))
            if 1 <= score <= 5:
                return {
                    "score_name": metric,
                    "score_value": score,
                    "reasoning": result.get("reasoning", ""),
                    "reason": result.get("reason", ""),
                }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse judge response: {e}")
        return None
```

### 4.3 评估执行策略

| 环境 | 采样率 | 评估维度 | Judge 模型 | 说明 |
|------|--------|---------|-----------|------|
| **生产环境** | 5-10% | faithfulness + answer_relevance | qwen-turbo | 控制成本，每天约额外消耗 50-100 次 LLM 调用 |
| **新智能体上线** | 100%（前 50 条） | 全部 4 维度 | qwen-turbo | 新上线时全量评估，建立质量基线 |
| **离线评估** | 100% | 全部 4 维度 | qwen-max | 定期全量评估，对比不同版本 |
| **手动触发** | 指定 | 指定 | 可选 | 管理员手动评估指定对话 |

### 4.4 评估结果写入

评估完成后异步写入 `obs_scores` 表，并与 `obs_traces` 关联：

```python
async def _persist_score(self, trace_id: str, metric: str, result: dict):
    """异步写入评估分数"""
    try:
        with get_logs_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO obs_scores
                        (trace_id, score_name, score_value, score_source,
                         reasoning, metadata, created_at)
                    VALUES (%s, %s, %s, 'auto_eval', %s, %s, NOW())
                """, (
                    trace_id,
                    metric,
                    result["score_value"],
                    result.get("reason", ""),
                    json.dumps({"judge_model": self.judge_model}, ensure_ascii=False),
                ))
                conn.commit()
    except Exception as e:
        logger.warning(f"Failed to persist score: {e}")
```

---

## 五、幻觉检测

### 5.1 轻量多层检测

采用纯 Python 实现的多层幻觉检测，总延迟 < 50ms，不需要额外 LLM 调用：

```
用户问题 + 检索上下文 + LLM 回答
  │
  ├── 第1层: 信心评分检测 (< 1ms)
  │     检测语言过度自信标记（"一定"、"绝对"、"肯定"、"毫无疑问"）
  │
  ├── 第2层: 忠实度评分 (~2ms)
  │     回答拆分为声明句，检查关键词是否在上下文中出现
  │     阈值: 关键词重叠度 < 40% 则标记
  │
  ├── 第3层: 矛盾检测 (~1ms)
  │     检测数值矛盾、否定翻转、时间矛盾
  │
  ├── 第4层: 实体幻觉检测 (< 10ms)
  │     正则提取人名、机构名、引用，验证是否出现在上下文中
  │
  └── 综合判定
        is_hallucinating = faithfulness < 0.50
                           OR contradictions > 0
                           OR hallucinated_entities > 0
```

### 5.2 HallucinationGuard 实现

```python
# src/core/hallucination_guard.py

import re
from typing import Dict, List, Any
from loguru import logger


class HallucinationGuard:
    """幻觉检测守卫 — 嵌入 Agent Loop，< 50ms 延迟"""

    # 过度自信标记词
    CONFIDENCE_MARKERS = [
        r'一定', r'绝对', r'肯定', r'毫无疑问', r'百分之百',
        r'必然', r'绝对不可能', r'完全正确', r'永远',
    ]

    def __init__(self):
        self._confidence_re = re.compile(
            '|'.join(self.CONFIDENCE_MARKERS)
        )

    def inspect(self, question: str, context_chunks: List[str],
                answer: str) -> Dict[str, Any]:
        """检测回答是否存在幻觉"""
        context_text = "\n".join(context_chunks) if context_chunks else ""

        confidence_score = self._score_confidence(answer)
        faithfulness_score = self._score_faithfulness(answer, context_text)
        contradictions = self._detect_contradictions(answer, context_text)
        hallucinated_entities = self._detect_entity_hallucination(
            answer, context_text
        )

        is_hallucinating = (
            faithfulness_score < 0.50
            or len(contradictions) > 0
            or len(hallucinated_entities) > 0
        )

        return {
            "confidence": confidence_score,
            "faithfulness": faithfulness_score,
            "contradictions": contradictions,
            "hallucinated_entities": hallucinated_entities,
            "is_hallucinating": is_hallucinating,
        }

    def _score_confidence(self, answer: str) -> float:
        """第1层：过度自信评分（标记越多，可信度越低）"""
        matches = self._confidence_re.findall(answer)
        if not matches:
            return 1.0
        # 每个标记扣 0.15，最低 0.2
        return max(0.2, 1.0 - len(matches) * 0.15)

    def _score_faithfulness(self, answer: str, context: str) -> float:
        """第2层：忠实度评分（关键词重叠度）"""
        if not context:
            return 0.5  # 无上下文时无法判断，给中间分

        # 提取回答中的中文词汇（2-4字）
        answer_words = set(re.findall(r'[一-鿿]{2,4}', answer))
        if not answer_words:
            return 1.0

        # 检查这些词在上下文中的出现率
        matched = sum(1 for w in answer_words if w in context)
        return matched / len(answer_words)

    def _detect_contradictions(self, answer: str, context: str) -> List[Dict]:
        """第3层：矛盾检测（数值、否定翻转）"""
        contradictions = []

        # 数值矛盾：提取回答中的数值，检查是否与上下文矛盾
        answer_numbers = re.findall(
            r'(?:金额|价格|数量|人数|比例|百分比|折|减|升|降)[是为约]\s*(\d+\.?\d*)',
            answer
        )
        for num_str in answer_numbers:
            if num_str not in context:
                contradictions.append({
                    "type": "unsupported_number",
                    "value": num_str,
                    "description": f"数值 '{num_str}' 在上下文中未找到依据",
                })

        return contradictions

    def _detect_entity_hallucination(self, answer: str,
                                      context: str) -> List[Dict]:
        """第4层：实体幻觉检测（人名、机构名、引用）"""
        entities = []

        # 引用标记：引号中的内容
        quoted_refs = re.findall(r'[「"《【](.*?)[」"》】]', answer)
        for ref in quoted_refs:
            if len(ref) > 2 and ref not in context:
                entities.append({
                    "type": "quoted_reference",
                    "entity": ref,
                    "description": f"引用内容 '{ref}' 在上下文中未找到",
                })

        return entities
```

### 5.3 集成位置

> **更新（2026-07-07）**：方案 C 后，TraceCollector 接入点已下沉到 `Agent.process_message()` wrapper。幻觉检测的触发位置相应调整为：在 wrapper 的 `finally` 块（`on_complete` 之后）或 `trace_persist._do_persist()` 完成后异步执行，与 `main.py:event_generator` 无关。

推荐方案：在 `trace_persist.py` 的 `_do_persist()` 完成后异步触发幻觉检测，从 `obs_spans` 中提取 `tool_result` 的知识库检索结果作为 context。这样调用方完全不需要改动。

```python
# src/core/trace_persist.py — _do_persist() 末尾（方案 C 后推荐触发点）

# ★ 幻觉检测（异步执行，不阻塞主流程）
if trace.output and trace.source_type != 'scheduler':
    _schedule_hallucination_check(
        trace_id=trace.trace_id,
        question=trace.input,
        answer=trace.output,
        # context_chunks 从 obs_spans 中 span_type='tool' 且 name 含 knowledge 的 tool_result 提取
    )
```

或者在 `Agent.process_message()` wrapper 的 `finally` 块中触发（与 `on_complete` 同作用域）：

```python
# src/core/agent.py:1769-1834 wrapper 的 finally 块
finally:
    if trace_collector:
        trace_collector.on_complete(_record)
        # ★ 幻觉检测（trace_collector 仍在作用域内）
        if trace_collector.trace.output:
            _schedule_hallucination_check(
                trace_collector=trace_collector,
                question=user_input,
                answer=trace_collector.trace.output,
            )
```

**重要**：幻觉检测的结果仅用于记录和告警，不自动修改用户看到的回答。自动修复策略作为后续迭代功能。

---

## 六、追踪查看前端

### 6.1 设计思路

Phase 1 的前端目标很明确：**替代 SSH 翻 JSONL 日志文件**。运维人员通过浏览器就能查看每次对话的完整追踪数据，不需要登录服务器。

浏览路径设计为两级：

```
会话列表 → 某个会话的所有 Trace 列表 → 某条 Trace 的详情（Span 树）
```

- **会话维度**：一个 session 有多条 trace（每次用户发消息 = 一条 trace）
- **Trace 维度**：每次对话的用户问题、AI 回复、工具调用、耗时、Token
- **Span 详情**：每个工具调用的入参、返回值、LLM 调用的完整 request/response

### 6.2 后端 API

#### REST 查询 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/monitor/sessions` | GET | 会话列表（分页，有 trace 的会话） |
| `/api/monitor/sessions/{session_id}/traces` | GET | 某会话下的所有 trace 列表 |
| `/api/monitor/traces` | GET | 全局 trace 列表（分页，支持 status/tags/time_range 筛选） |
| `/api/monitor/traces/{trace_id}` | GET | 追踪详情（含完整 span 列表） |

#### 会话列表 API

```python
# GET /api/monitor/sessions?page=1&page_size=20&tenant_id=xxx

# 返回有追踪数据的会话列表，每个会话附带 trace 统计摘要
{
    "sessions": [
        {
            "session_id": "sess_xxx",
            "title": "帮我查一下订单",
            "tenant_id": "tenant_xxx",
            "user_id": "user_xxx",
            "trace_count": 5,
            "last_trace_at": "2026-06-01T10:30:00",
            "total_tokens": 12500,
            "error_count": 0,
        }
    ],
    "total": 100,
    "page": 1,
    "page_size": 20
}
```

#### 会话下的 Trace 列表 API

```python
# GET /api/monitor/sessions/{session_id}/traces

# 返回该会话下所有 trace，按时间正序排列
# 每条 trace 展示：用户问题、AI 回复摘要、状态、耗时、Token、工具数
{
    "traces": [
        {
            "trace_id": "tr_abc123",
            "input": "帮我查一下最近的订单",
            "output": "您最近有3笔订单...",
            "status": "completed",
            "duration_ms": 3250,
            "total_tokens": 2450,
            "tool_calls_count": 2,
            "tags": [],
            "created_at": "2026-06-01T10:30:00"
        },
        ...
    ]
}
```

#### Trace 详情 API

```python
# GET /api/monitor/traces/{trace_id}

# 返回完整 trace + 所有 spans
{
    "trace": {
        "trace_id": "tr_abc123",
        "session_id": "sess_xxx",
        "input": "...",
        "output": "...",
        "status": "completed",
        "duration_ms": 3250,
        "total_tokens": 2450,
        "model": "qwen-plus",
        "provider": "qwen",
        "agent_iterations": 2,
        "tags": [],
        "created_at": "2026-06-01T10:30:00"
    },
    "spans": [
        {
            "span_id": "sp_xxx",
            "span_type": "generation",
            "name": "llm_call",
            "input": "{完整 request JSON}",
            "output": "{完整 response JSON}",
            "model": "qwen-plus",
            "prompt_tokens": 1200,
            "completion_tokens": 350,
            "duration_ms": 1800,
            "status": "completed",
            "start_time": "2026-06-01T10:30:01"
        },
        {
            "span_id": "sp_yyy",
            "span_type": "span",
            "name": "tool:search_knowledge_base",
            "input": "{\"query\": \"订单查询\"}",
            "output": "{\"results\": [...]}",
            "duration_ms": 200,
            "status": "completed",
            "start_time": "2026-06-01T10:30:03"
        },
        ...
    ]
}
```

### 6.3 前端页面设计

#### 追踪查看入口页（/portal/monitoring）

路径 `/portal/monitoring`，注册到 `PortalLayout` 平台管理侧边栏菜单中。

**页面 1：会话追踪列表**

```
┌──────────────────────────────────────────────────────────┐
│ AppHeader: "追踪查看"                        [搜索框]    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  筛选：[状态 ▼] [时间范围 ▼] [租户 ▼]    [搜索会话标题] │
│                                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 会话标题 | 租户 | Trace数 | Token | 错误 | 最后活跃 │  │
│  │──────────|─────|─────────|───────|──────|─────────│  │
│  │ 订单查询  | A公司 |   5    | 12.5K |  0   | 10:30  │  │
│  │ 邮件发送  | B公司 |   3    |  8.2K |  1   | 09:15  │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  [< 1 2 3 ... 10 >]                                     │
└──────────────────────────────────────────────────────────┘
```

**页面 2：会话内 Trace 列表（点击会话行进入）**

展示一个会话中所有对话轮次，按时间正序。类似聊天记录的列表视图：

```
┌──────────────────────────────────────────────────────────┐
│ ← 返回列表   会话: "帮我查一下订单"      sess_xxx       │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ Trace 1 ──────────────────────────────────────────┐ │
│  │ 👤 用户: "帮我查一下最近的订单"                      │ │
│  │ 🤖 回复: "您最近有3笔订单，分别是..."                 │ │
│  │ ⏱ 3250ms | 📊 2450 tokens | 🔧 2 工具调用          │ │
│  │ 状态: ✅ completed                [查看详情 →]      │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌─ Trace 2 ──────────────────────────────────────────┐ │
│  │ 👤 用户: "第一笔订单的物流信息"                      │ │
│  │ 🤖 回复: "第一笔订单的物流状态为..."                  │ │
│  │ ⏱ 2100ms | 📊 1800 tokens | 🔧 1 工具调用          │ │
│  │ 状态: ✅ completed                [查看详情 →]      │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌─ Trace 3 ──────────────────────────────────────────┐ │
│  │ 👤 用户: "帮我发一封邮件给客户"                      │ │
│  │ 🤖 回复: (空)                                       │ │
│  │ ⏱ 5200ms | 📊 3100 tokens | 🔧 3 工具调用          │ │
│  │ 状态: ❌ failed  标签: [error]     [查看详情 →]      │ │
│  └────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**页面 3：Trace 详情（点击某条 Trace 进入）**

```
┌──────────────────────────────────────────────────────────┐
│ ← 返回会话    Trace 详情: tr_abc123def456                │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  基本信息区:                                             │
│  状态: ✅ completed  耗时: 3250ms  Token: 2450           │
│  用户: user_xxx  会话: session_xxx  来源: wecom          │
│  模型: qwen-plus (qwen)  迭代轮次: 2                    │
│                                                          │
│  ┌─ 用户输入 ────────────────────────────────────────┐  │
│  │ 帮我查一下最近的订单                                │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌─ AI 输出 ─────────────────────────────────────────┐  │
│  │ 您最近有3笔订单，分别是...                          │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  Span 时间线:                                            │
│  ┌────────────────────────────────────────────────────┐ │
│  │ 🔵 llm_call (qwen-plus)        1800ms  ███████   │ │
│  │   prompt_tokens: 1200, completion_tokens: 350       │ │
│  │   [查看完整 request/response]                       │ │
│  │                                                    │ │
│  │ 🟢 tool:search_knowledge_base   200ms  █         │ │
│  │   输入: {"query": "订单查询"}                        │ │
│  │   输出: {"results": ["3个文档片段..."]}              │ │
│  │   [查看完整输入/输出]                                │ │
│  │                                                    │ │
│  │ 🔵 llm_call (qwen-plus)        1200ms  █████     │ │
│  │   prompt_tokens: 1800, completion_tokens: 500       │ │
│  │   [查看完整 request/response]                       │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  (质量评分区域 — Phase 2 后填充)                          │
└──────────────────────────────────────────────────────────┘
```

#### 前端组件拆分

| 组件 | 路径 | 职责 |
|------|------|------|
| `TraceBrowser.vue` | `frontend/src/components/saas/TraceBrowser.vue` | 入口页：会话追踪列表 |
| `SessionTraces.vue` | `frontend/src/components/saas/SessionTraces.vue` | 会话内 Trace 列表 |
| `TraceDetail.vue` | `frontend/src/components/saas/TraceDetail.vue` | Trace 详情（Span 树） |
| `JsonViewer.vue` | `frontend/src/components/ui/JsonViewer.vue` | JSON 格式化查看器（通用组件） |
| `monitor.ts` | `frontend/src/api/monitor.ts` | API 层 |

#### JsonViewer 组件设计

Trace 详情页中有大量 JSON 数据需要查看：LLM 的完整 request/response（含 messages 数组、tools schema）、工具调用的 input/output。这些数据通常很大（几 KB 到几十 KB），需要专门的查看器组件。

**需求**：
- **默认折叠**：只显示第一级 key，值部分折叠为 `{...}` / `[...]` / 字符串预览
- **逐级展开**：点击 key 可展开下一级，支持任意深度嵌套
- **语法高亮**：复用已有的 `highlight.js`，key/string/number/boolean/null 各有颜色区分
- **字符串截断预览**：长字符串默认只显示前 80 字符 + `...`，点击可展开全文
- **数组计数**：折叠时显示数组长度，如 `[3 items]`
- **不引入外部库**：自实现，基于递归组件，依赖已有的 `highlight.js`

**交互示例**：

```
▶ messages: Array[3]
▶ model: "qwen-plus"
▶ temperature: 0.7
▶ tools: Array[5]
▶ max_tokens: 4096
```

展开后：

```
▼ messages: Array[3]
  ▶ [0]: Object {role: "system", content: "你是一个智能助手..."}
  ▼ [1]: Object
      role: "user"
    ▶ content: "帮我查一下最近的订单"（点击可展开全文）
  ▶ [2]: Object {role: "assistant", content: "您最近有3笔订单..."}
▼ model: "qwen-plus"
▼ temperature: 0.7
▶ tools: Array[5]
▼ max_tokens: 4096
```

**组件接口**：

```vue
<!-- JsonViewer.vue -->
<script setup lang="ts">
defineProps<{
  data: any              // 要展示的 JSON 数据（对象或数组）
  maxPreview?: number    // 字符串预览长度，默认 80
  defaultExpand?: number // 默认展开层级，默认 0（全部折叠）
}>()
</script>
```

**实现方式**：递归组件，每个 JSON 节点（Object/Array/Primitive）渲染为一个可折叠行。使用 `highlight.js` 的 `json` 语言对字符串值做语法着色。不需要安装新的 npm 包。

三个页面通过路由切换：
- `/portal/monitoring` → `TraceBrowser.vue`
- `/portal/monitoring/:session_id` → `SessionTraces.vue`
- `/portal/monitoring/trace/:trace_id` → `TraceDetail.vue`

#### 路由注册

在 `frontend/src/main.ts` 的 `/portal` children 中添加：

```javascript
{ path: 'monitoring',              name: 'portal-monitoring',          component: () => import('./components/saas/TraceBrowser.vue') },
{ path: 'monitoring/:session_id',  name: 'portal-session-traces',      component: () => import('./components/saas/SessionTraces.vue') },
{ path: 'monitoring/trace/:trace_id', name: 'portal-trace-detail',     component: () => import('./components/saas/TraceDetail.vue') },
```

在 `PortalLayout.vue` 的 `portalMenuItems` 中添加：

```javascript
{ path: '/portal/monitoring', label: '追踪查看', icon: '🔍' },
```

### 6.4 后续扩展（Phase 3+）

Phase 1 的追踪查看页面是纯事实数据浏览。后续 Phase 在此基础上扩展：

| Phase | 扩展内容 |
|-------|---------|
| Phase 2（质量评估） | Trace 详情页增加质量评分区域（忠实度/相关性/完整性评分） |
| Phase 3（监控仪表盘） | 新增 `/portal/monitoring/dashboard` 子路由，增加概览卡片、趋势图表、WebSocket 实时推送 |
| Phase 4（告警） | 增加告警列表标签页 |

---

## 七、结构化告警

### 7.1 告警规则

| 规则 | 指标 | 条件 | 窗口 | 级别 | 通知渠道 |
|------|------|------|------|------|---------|
| LLM 服务不可用 | `llm_error_rate` | > 50% | 5min | critical | 企业微信 + 邮件 |
| 请求成功率低 | `request_success_rate` | < 90% | 5min | critical | 企业微信 + 邮件 |
| LLM 延迟飙升 | `llm_p95_latency` | > 30s | 10min | warning | 企业微信 |
| Token 消耗异常 | `hourly_token_usage` | 较昨日同期 +100% | 1h | warning | 企业微信 |
| 响应质量下降 | `avg_faithfulness_score` | < 3.0 | 30min | warning | 企业微信 |
| 幻觉率偏高 | `hallucination_rate` | > 20% | 30min | warning | 企业微信 |
| Agent 循环超限 | `agent_loop_limit_rate` | > 10% | 15min | warning | 企业微信 |
| 数据库连接异常 | `db_connection_errors` | > 5 次 | 5min | critical | 企业微信 + 邮件 |

### 7.2 告警管理器

```python
# src/core/alert_manager.py

import time
from typing import Dict, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class AlertRule:
    name: str
    metric: str
    operator: str          # gt / lt / gte / lte
    threshold: float
    window_minutes: int
    severity: str          # critical / warning / info
    channels: list         # ['wecom', 'email', 'webhook']
    message_template: str
    cooldown_minutes: int = 30


class AlertManager:
    """结构化告警管理器"""

    DEFAULT_RULES = [
        AlertRule(
            name="llm_error_rate_high",
            metric="llm_error_rate",
            operator="gt", threshold=0.5,
            window_minutes=5, severity="critical",
            channels=["wecom", "email"],
            message_template="LLM 服务异常：过去 {window} 分钟错误率 {value:.1%}，超过阈值 {threshold:.1%}",
        ),
        AlertRule(
            name="request_success_rate_low",
            metric="request_success_rate",
            operator="lt", threshold=0.9,
            window_minutes=5, severity="critical",
            channels=["wecom", "email"],
            message_template="请求成功率过低：过去 {window} 分钟成功率 {value:.1%}",
        ),
        AlertRule(
            name="llm_latency_high",
            metric="llm_p95_latency",
            operator="gt", threshold=30000,
            window_minutes=10, severity="warning",
            channels=["wecom"],
            message_template="LLM 延迟飙升：P95 = {value:.0f}ms",
        ),
        AlertRule(
            name="quality_score_low",
            metric="avg_faithfulness_score",
            operator="lt", threshold=3.0,
            window_minutes=30, severity="warning",
            channels=["wecom"],
            message_template="回复质量下降：忠实度均分 = {value:.1f}/5",
        ),
        AlertRule(
            name="hallucination_rate_high",
            metric="hallucination_rate",
            operator="gt", threshold=0.2,
            window_minutes=30, severity="warning",
            channels=["wecom"],
            message_template="幻觉率偏高：{value:.1%} 的回复检测到幻觉",
        ),
        AlertRule(
            name="token_usage_spike",
            metric="token_usage_change",
            operator="gt", threshold=1.0,
            window_minutes=60, severity="warning",
            channels=["wecom"],
            message_template="Token 消耗异常：较昨日同期增长 {value:.0%}",
        ),
    ]

    def __init__(self):
        self._last_alert_time: Dict[str, float] = {}
        self._active_alerts: Dict[str, dict] = {}

    async def evaluate(self, rule: AlertRule, current_value: float):
        """评估告警规则并通知"""
        # 检查冷却期
        last_time = self._last_alert_time.get(rule.name, 0)
        if time.time() - last_time < rule.cooldown_minutes * 60:
            return

        # 评估条件
        triggered = self._check_condition(rule, current_value)

        if triggered:
            message = rule.message_template.format(
                window=rule.window_minutes,
                value=current_value,
                threshold=rule.threshold,
            )
            await self._send_alert(rule, message)
            self._last_alert_time[rule.name] = time.time()
            self._active_alerts[rule.name] = {
                "rule": rule.name,
                "triggered_at": time.time(),
                "value": current_value,
            }
        elif rule.name in self._active_alerts:
            # 自动恢复通知
            await self._send_recovery(rule, current_value)
            del self._active_alerts[rule.name]

    def _check_condition(self, rule: AlertRule, value: float) -> bool:
        ops = {
            "gt": lambda v, t: v > t,
            "lt": lambda v, t: v < t,
            "gte": lambda v, t: v >= t,
            "lte": lambda v, t: v <= t,
        }
        return ops.get(rule.operator, lambda v, t: False)(value, rule.threshold)

    async def _send_alert(self, rule: AlertRule, message: str):
        """通过已有通知服务发送告警"""
        try:
            from src.services.notification_service import NotificationService
            service = NotificationService()
            await service.send(
                urgency=rule.severity,
                title=f"[{rule.severity.upper()}] {rule.name}",
                message=message,
                channels=rule.channels,
            )
            logger.info(f"Alert sent: {rule.name}")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    async def _send_recovery(self, rule: AlertRule, current_value: float):
        """发送恢复通知"""
        try:
            from src.services.notification_service import NotificationService
            service = NotificationService()
            await service.send(
                urgency="info",
                title=f"[RESOLVED] {rule.name}",
                message=f"指标已恢复正常，当前值: {current_value}",
                channels=rule.channels,
            )
        except Exception as e:
            logger.error(f"Failed to send recovery: {e}")
```

### 7.3 告警评估触发

告警评估通过定时任务触发，复用已有的调度机制：

```python
# 在定时任务中周期性执行告警评估
async def run_alert_evaluation():
    """每分钟执行一次告警评估"""
    manager = AlertManager()

    for rule in manager.DEFAULT_RULES:
        value = await _get_metric_value(rule.metric, rule.window_minutes)
        if value is not None:
            await manager.evaluate(rule, value)
```

---

## 八、数据生命周期管理

> TimescaleDB 自动管理压缩和过期，无需额外的清理定时任务。

### 8.1 数据保留与压缩策略

| 表 | 未压缩保留 | 压缩策略 | 过期策略 |
|------|-----------|---------|---------|
| `obs_traces` | 7 天（热查询） | 7 天后列式压缩，按 tenant_id 分段（90%+ 缩减） | 90 天后 DROP chunk |
| `obs_spans` | 7 天（热查询） | 7 天后列式压缩，按 trace_id 分段 | 90 天后 DROP chunk |
| `obs_scores` | 30 天（热查询） | 30 天后列式压缩，按 score_name 分段 | 180 天后 DROP chunk |
| JSONL 日志文件 | 7 天 | loguru rotation 已配置 | — |

### 8.2 存储量估算

| 指标 | 估算值 |
|------|--------|
| 日均请求量 | ~1000-5000 条 |
| 日均 spans | ~5000-30000 条（每请求 3-8 个 span） |
| 日均 scores | ~100-500 条（10% 采样 × 2 维度） |
| 90 天未压缩大小 | ~5-10 GB |
| TimescaleDB 压缩后 | **~0.5-1 GB**（90%+ 缩减） |

---

## 九、建设路线图

> **2026-06-01 更新**：Phase 1 调整为"采集+查看事实数据"，包含后端追踪采集 + 前端追踪查看页面。前端追踪查看替代 SSH 翻 JSONL 日志，Phase 1 完成后即可通过浏览器查看每次对话的完整追踪。

### Phase 1：追踪采集 + 事实数据查看（2 周）✅ 已完成

**目标**：每次请求可追踪，可通过浏览器查看追踪数据（与 SSH 翻 JSONL 日志并行）

> **2026-07-07 更新**：Phase 1 全部完成。原 SSE handler 集成方案已升级为方案 C（TraceCollector 下沉到 `Agent.process_message`，渠道零改造），详见 [observability-channel-sessions-design.md](./observability-channel-sessions-design.md)。1.6（单测/e2e）和 1.7（JSONL 双写迁移）已取消：obs 系统每日真实流量运行已事实验证；JSONL 与 obs 永久并行（前者供 SSH 翻日志，后者供前端查看），不做对比、不淘汰。

| 任务 | 交付物 | 状态 |
|------|--------|------|
| 创建 `obs_traces`、`obs_spans`、`obs_scores` 表 | SQL 迁移脚本 | ✅ |
| 实现 `TraceCollector`（事件流旁路收集器） | `src/core/trace_collector.py` | ✅ |
| 实现 `trace_persist`（异步持久化） | `src/core/trace_persist.py` | ✅ |
| ~~在 `main.py` SSE handler 集成 TraceCollector~~ → 升级为方案 C：`Agent.process_message` wrapper | `src/core/agent.py:1769-1834` | ✅ |
| 后端追踪查看 API（会话列表/Trace 列表/Trace 详情） | `src/api/monitor.py` | ✅ |
| 前端追踪查看页面（会话→Trace→Span 详情三级浏览） | `TraceBrowser.vue` + `SessionTraces.vue` + `TraceDetail.vue` | ✅ |

### Phase 2：质量评估 + 幻觉检测（2 周）

**目标**：自动评估每次回复的质量

| 任务 | 交付物 |
|------|--------|
| 实现 `QualityEvaluator` | `src/core/quality_evaluator.py` |
| 实现评分 Prompt 模板（4 维度） | 嵌入代码的 prompt 常量 |
| 评估结果写入 `obs_scores` | 异步写入逻辑 |
| 实现 `HallucinationGuard` | `src/core/hallucination_guard.py` |
| 幻觉检测结果写入 `obs_scores` | 异步写入逻辑 |
| 新智能体上线全量评估逻辑 | 前 N 条全量评估 |
| Trace 详情页增加质量评分展示 | 扩展 `TraceDetail.vue` |

### Phase 3：实时监控仪表盘（2 周）

**目标**：运维实时监控系统运行状态

| 任务 | 交付物 |
|------|--------|
| 概览卡片 + 趋势图表（延迟/Token/质量） | `MonitoringDashboard.vue` |
| WebSocket 实时数据推送 | 后端 WebSocket 端点 |
| 工具调用统计图表 | 嵌入监控页面 |

### Phase 4：结构化告警（1 周）

**目标**：异常自动发现和通知

| 任务 | 交付物 |
|------|--------|
| 实现 `AlertManager` | `src/core/alert_manager.py` |
| 告警规则配置存储 | 数据库表或配置文件 |
| 定时告警评估任务 | 周期性执行 |
| 告警历史记录 UI | 前端告警列表页 |
| 与已有通知服务集成 | 复用 `notification_service.py` |

---

## 十、JSONL 日志迁移计划

### 10.1 现状分析

当前系统有两套 JSONL 文件日志，均为"写后不管"模式，代码中**无任何读取逻辑**，仅用于人工 SSH 上服务器翻查调试：

| 日志文件 | 写入位置 | 记录内容 | 截断策略 |
|---------|---------|---------|---------|
| `log/llm/llm_invoke_logs_YYYYMMDD.jsonl` | Provider 层（qwen.py/zhipu.py/deepseek.py） | request_id、完整的 request/response、usage、duration_ms | **不截断** |
| `log/agent/agent_session_logs_YYYYMMDD.jsonl` | Agent 循环层（agent.py） | iteration、tool_calls、usage、duration_ms | 不截断 |
| `log/agent/skill_execute_YYYYMMDD.jsonl` | Skill 执行层 | stdout/stderr/exit_code | 不截断 |

### 10.2 与全链路追踪的覆盖对比

| 数据项 | JSONL 日志 | obs_traces + obs_spans | 替代可行性 |
|--------|-----------|----------------------|-----------|
| LLM request_id | ✅ | obs_spans.metadata | ✅ 完全替代 |
| LLM 完整 request（messages/tools/params） | ✅ 不截断 | obs_spans.input | ⚠️ 需确保不截断 |
| LLM 完整 response（原始 API 返回） | ✅ 不截断 | obs_spans.output | ⚠️ 需确保不截断 |
| LLM provider/model/usage/duration | ✅ | obs_spans + obs_traces | ✅ 完全替代 |
| Agent 迭代轮次 | ✅ | obs_traces.agent_iterations | ✅ 完全替代 |
| 工具调用名称列表 | ✅（tool_names） | obs_spans（每条工具调用独立 span） | ✅ obs 更详细 |
| 工具调用参数和结果 | ❌ agent_session 不记录 | obs_spans.input/output（完整） | ✅ obs 新增能力 |
| Skill 执行详情（stdout/stderr） | ✅ skill_execute 日志 | obs_spans（需新增 span_type=event） | ⚠️ 需扩展 |

### 10.3 迁移策略：三步淘汰

**Phase 1 上线期间（双写期，约 2 周）**：

- 全链路追踪和 JSONL 日志**同时写入**
- 对比两边数据，验证 obs 数据的完整性：
  - obs_traces 的 `total_tokens` vs `agent_session_logs` 的 `usage`
  - obs_spans 的工具调用数 vs `agent_session_logs` 的 `tool_calls_count`
  - obs_spans 的 LLM span vs `llm_invoke_logs` 的调用记录
- 重点验证 obs_spans 中 LLM 调用的 input/output 是否完整保存（**不截断**）

**Phase 1 验证通过后（开关期）**：

- 新增环境变量 `OBS_DISABLE_JSONL_LOGGING=false`（默认 false，即继续写 JSONL）
- 设为 `true` 后停止 JSONL 写入，仅保留 obs 追踪
- 此阶段保留 JSONL 写入代码，可随时回退

**稳定运行 2 周后（移除期）**：

- 移除 `llm_call_logger.py` 中的 `log_llm_invoke()` 调用（Provider 层）
- 移除 `agent_logger.py` 中的 `log_agent_iteration()` 调用（Agent 层）
- 保留 `llm_call_logger.py` 和 `agent_logger.py` 文件（skill_execute 日志暂保留）
- 更新本文档 §1.2 现状评估表

### 10.4 关键注意事项

1. **LLM 完整 response 必须不截断**：现有 `llm_invoke_logs` 最大的价值是保存了完整的 LLM API 原始返回（含 tool_calls schema、finish_reason 等），用于排查 provider 兼容性问题。obs_spans 的 input/output 字段也必须**不截断**完整保存。
2. **Skill 执行日志暂保留**：`skill_execute_YYYYMMDD.jsonl` 记录了代码执行的 stdout/stderr，当前 obs 模型中没有对应的 span 类型。暂时保留此日志，后续可通过 `span_type=event` 的 span 来替代。
3. **Log 文件清理机制保留**：`admin_error_logs.py` 中有 30 天自动清理 JSONL 文件的逻辑，在 JSONL 日志完全移除前保留此机制。

---

## 十一、与差距分析文档的映射

| 差距分析 §2.1 需求 | 本文档对应章节 | 覆盖状态 | 备注 |
|-------------------|--------------|---------|------|
| 分布式链路追踪 | §三 | ✅ 完整覆盖 | 方案 C：TraceCollector 接入 Agent.process_message wrapper，渠道零改造 |
| 回复质量自动评估 | §四 | ✅ 完整覆盖 | SSE handler 异步触发 |
| 幻觉检测 | §五 | ✅ 完整覆盖 | 持久化旁路触发，不修改 agent.py |
| 实时监控仪表盘 | §六 | ✅ 完整覆盖 | |
| 结构化告警 | §七 | ✅ 完整覆盖 | |
