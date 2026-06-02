# AI Agent 平台可观测性设计调研报告

> 版本: v1.0 | 创建: 2026-05-29 | 状态: 调研完成

## 1. 调研范围与目标

本文档为企业 AI Agent 平台可观测性系统的前期调研，覆盖六个主题：

1. 分布式追踪（Distributed Tracing）
2. LLM 响应质量评估（Response Quality Evaluation）
3. 实时监控仪表盘（Real-time Monitoring Dashboard）
4. 结构化告警（Structured Alerting）
5. 幻觉检测（Hallucination Detection）
6. 行业参考（Dify / FastGPT / Langfuse / LangSmith / Coze）

**技术栈约束**：Python/FastAPI + PostgreSQL + Vue.js，避免引入 OpenTelemetry 全栈等重型方案。

---

## 2. 分布式追踪

### 2.1 行业标准数据模型（参考 Langfuse）

Langfuse 是当前 LLM 可观测性的事实标准，其数据模型清晰且被广泛验证。Langfuse V3 将追踪数据从 PostgreSQL 迁移到 ClickHouse，但其数据模型设计完全适用于 PostgreSQL。

**核心层级关系**：

```
Trace（一次完整请求/工作流）
  ├── Observation: Span（一个工作单元，有起止时间）
  │     ├── Observation: Generation（LLM 调用，含 model/tokens/usage）
  │     ├── Observation: Event（时间点事件，无时长）
  │     └── Observation: 嵌套 Span（子智能体调用等）
  └── Scores（评估分数，关联到 Trace 或特定 Observation）
```

**各层级应采集的数据**：

| 层级 | 关键字段 | 说明 |
|------|---------|------|
| **Trace** | `trace_id`, `session_id`, `user_id`, `tenant_id`, `input`, `output`, `metadata`, `tags`, `timestamp`, `duration_ms` | 一次完整对话请求 |
| **Span** | `span_id`, `parent_span_id`, `trace_id`, `name`, `start_time`, `end_time`, `input`, `output`, `metadata`, `level`, `status` | 工具调用、子智能体委派、RAG 检索 |
| **Generation** | 在 Span 基础上增加: `model`, `model_parameters`(temperature等), `prompt`, `completion`, `usage`(input_tokens, output_tokens, cached_tokens), `cost` | LLM API 调用 |
| **Event** | `event_id`, `trace_id`, `name`, `timestamp`, `level`, `metadata` | 状态变更、错误事件、里程碑 |

**适合本项目的 Observation 类型映射**：

```
用户请求 → Trace
  ├── Agent 推理循环 → Span (name="agent_loop", type="agent")
  │     ├── LLM 调用 → Generation (model="qwen-plus", usage={...})
  │     ├── 工具调用 → Span (name="search_knowledge_base", type="tool")
  │     ├── 子智能体委派 → Span (name="delegate_to_subagent", type="agent")
  │     │     ├── 子 Agent LLM 调用 → Generation
  │     │     └── 子 Agent 工具调用 → Span
  │     └── RAG 检索 → Span (name="knowledge_retrieval", type="retriever")
  └── 质量评估 → Span (name="quality_check", type="evaluator")
```

### 2.2 轻量级实现方案

**不需要 OpenTelemetry 的理由**：
- 本项目是单体 FastAPI 应用，不存在微服务间跨进程调用
- Agent loop 内的所有步骤都在同一进程内执行
- OpenTelemetry 引入的 SDK、Collector、Exporter 组件过重
- 我们需要的是 LLM 语义追踪，不是通用分布式追踪

**推荐方案：基于 PostgreSQL 的轻量追踪**：

```python
# 数据模型设计（PostgreSQL 表）

# 表1: traces（一次完整请求）
CREATE TABLE obs_traces (
    trace_id TEXT PRIMARY KEY,
    session_id TEXT,
    tenant_id TEXT,
    user_id TEXT,
    subagent_id TEXT,           -- 关联的数字员工 ID
    input TEXT,                 -- 用户输入（脱敏后）
    output TEXT,                -- 最终输出（脱敏后）
    metadata JSONB,             -- 扩展元数据
    tags TEXT[],                -- 标签（用于筛选）
    total_tokens INTEGER DEFAULT 0,
    total_cost NUMERIC(10,6) DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running', -- running/completed/failed
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

# 表2: obs_spans（追踪内的各个步骤）
CREATE TABLE obs_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES obs_traces(trace_id),
    parent_span_id TEXT,        -- 父 Span（嵌套关系）
    span_type TEXT NOT NULL,    -- span/generation/event
    name TEXT NOT NULL,         -- 步骤名称
    input TEXT,                 -- 输入（JSON，脱敏后）
    output TEXT,                -- 输出（JSON，脱敏后）
    metadata JSONB,             -- 模型参数、工具参数等
    model TEXT,                 -- LLM 模型名（generation 专用）
    model_params JSONB,         -- temperature 等
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    cost NUMERIC(10,6) DEFAULT 0,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

# 表3: obs_scores（评估分数，关联到 trace 或 span）
CREATE TABLE obs_scores (
    id SERIAL PRIMARY KEY,
    trace_id TEXT REFERENCES obs_traces(trace_id),
    span_id TEXT REFERENCES obs_spans(span_id),
    score_name TEXT NOT NULL,   -- faithfulness, relevance, etc.
    score_type TEXT NOT NULL,   -- numeric/categorical/boolean
    score_value NUMERIC,
    score_string TEXT,          -- 分类值或文本
    source TEXT NOT NULL,       -- api/eval/annotation/user_feedback
    comment TEXT,               -- 评估理由
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**追踪数据采集方式（异步写入，零侵入）**：

```python
# 基于 ContextVar 的追踪上下文传播
import contextvars

current_trace = contextvars.ContextVar('current_trace', default=None)
current_span = contextvars.ContextVar('current_span', default=None)

class TraceContext:
    """追踪上下文管理器"""

    @staticmethod
    def start_trace(session_id, tenant_id, user_id, input_msg, **kwargs):
        trace_id = f"tr_{uuid4().hex[:16]}"
        trace_data = {
            "trace_id": trace_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "input": input_msg,
            "metadata": kwargs,
            "status": "running",
            "spans": [],  # 内存中暂存 spans
        }
        current_trace.set(trace_data)
        return trace_data

    @staticmethod
    def start_span(name, span_type="span", parent_id=None, **kwargs):
        span_id = f"sp_{uuid4().hex[:16]}"
        trace = current_trace.get()
        parent = current_span.get()

        span_data = {
            "span_id": span_id,
            "trace_id": trace["trace_id"] if trace else None,
            "parent_span_id": parent["span_id"] if parent else parent_id,
            "span_type": span_type,
            "name": name,
            "metadata": kwargs,
            "start_time": time.time(),
            "status": "running",
        }
        current_span.set(span_data)
        if trace:
            trace["spans"].append(span_data)
        return span_data

    @staticmethod
    def end_span(output=None, usage=None, error=None):
        span = current_span.get()
        if span:
            span["end_time"] = time.time()
            span["duration_ms"] = int((span["end_time"] - span["start_time"]) * 1000)
            span["output"] = output
            span["status"] = "failed" if error else "completed"
            if usage:
                span.update(usage)
            current_span.set(None)  # 清除当前 span

    @staticmethod
    def end_trace(output=None, error=None):
        trace = current_trace.get()
        if trace:
            trace["output"] = output
            trace["status"] = "failed" if error else "completed"
            trace["duration_ms"] = ...
            # 异步写入 PostgreSQL（不阻塞主流程）
            _persist_trace(trace)
            current_trace.set(None)
```

### 2.3 追踪数据在 Agent Loop 中的埋点位置

```
main.py SSE handler
  ├── TraceContext.start_trace(session_id, tenant_id, user_id, message)
  │
  ├── agent.process_message()
  │     │
  │     ├── Agent Loop iteration N:
  │     │     ├── span = TraceContext.start_span("llm_call", span_type="generation", model=model_name)
  │     │     │     response = await llm.chat_with_tools(...)
  │     │     │     TraceContext.end_span(output=response, usage={tokens...})
  │     │     │
  │     │     ├── for tool_call in tool_calls:
  │     │     │     span = TraceContext.start_span(f"tool:{tool_name}", span_type="tool")
  │     │     │     result = await tool.execute(**args)
  │     │     │     TraceContext.end_span(output=result)
  │     │     │
  │     │     └── [如果触发子智能体]
  │     │           span = TraceContext.start_span(f"subagent:{name}", span_type="agent")
  │     │           result = await SubagentExecutor.execute(...)
  │     │           TraceContext.end_span(output=result)
  │     │
  │     └── return full_response
  │
  └── TraceContext.end_trace(output=full_response)
```

**与现有 `chat_records` 的关系**：
- `chat_records` 保留计费和审计用途（已有 token 统计、工具调用链）
- `obs_traces` + `obs_spans` 提供更细粒度的追踪能力（可按 span 级别查看延迟、错误）
- 两者通过 `session_id` 关联
- 后续可以考虑合并，但初期并行运行风险更低

---

## 3. LLM 响应质量评估

### 3.1 核心评估指标

根据 RAGAS、TruLens、DeepEval 三大开源框架的共识，RAG 系统的质量评估围绕以下指标：

| 指标 | 评估对象 | 方法 | 说明 |
|------|---------|------|------|
| **Faithfulness（忠实度）** | 回答 vs 检索上下文 | 逐句验证回答中的每个声明是否能在上下文中找到依据 | 最核心的 RAG 质量指标 |
| **Context Relevance（上下文相关性）** | 检索的上下文 vs 用户问题 | 判断检索到的每个 chunk 是否与问题相关 | 评估检索质量 |
| **Answer Relevance（回答相关性）** | 最终回答 vs 用户问题 | 判断回答是否真正回答了用户的问题 | 评估生成质量 |
| **Correctness（正确性）** | 回答 vs 标准答案 | 需要有 ground truth 参考答案 | 离线评估用 |
| **Hallucination Rate（幻觉率）** | 回答 vs 上下文 | 检测回答中无依据的声明占比 | 生产监控用 |
| **Toxicity（毒性）** | 回答内容 | 检测有害/不当内容 | 安全合规 |

**TruLens 的 RAG Triad 模型**（Context Relevance + Groundedness + Answer Relevance）是行业验证最充分的评估框架。三者共同达标可确保 RAG 系统无幻觉。

### 3.2 LLM-as-Judge 实现方案

**核心思路**：用一个 LLM 来评估另一个 LLM 的输出质量。使用评分规则（rubric）定义"好"的标准。

**Prompt 模板设计（实用版）**：

```
你是一个AI回答质量评估专家。请根据以下标准对回答进行评分。

【评估维度】: {metric_name}
【评分规则】:
- 5分: {level_5_description}
- 4分: {level_4_description}
- 3分: {level_3_description}
- 2分: {level_2_description}
- 1分: {level_1_description}

【用户问题】: {question}
【检索到的上下文】: {context}
【AI回答】: {answer}

请按以下格式输出：
思考过程: <逐步分析回答中的每个要点是否在上下文中有依据>
评分: <1-5的整数>
理由: <一句话说明评分理由>
```

**五大核心评估 Prompt**：

#### (1) Faithfulness 评分

```
评估维度: 回答忠实度
评分规则:
- 5分: 回答中所有事实声明都能在上下文中找到明确依据，无任何编造
- 4分: 绝大部分声明有依据，存在少量合理推断
- 3分: 主要声明有依据，但有1-2个声明缺乏上下文支持
- 2分: 多个声明无法在上下文中找到依据，存在明显编造
- 1分: 回答大部分内容与上下文无关或矛盾
```

#### (2) Answer Relevance 评分

```
评估维度: 回答相关性
评分规则:
- 5分: 完全针对用户问题回答，内容精准无冗余
- 4分: 较好地回答了问题，有少量无关内容
- 3分: 部分回答了问题，但偏离了核心需求
- 2分: 回答与问题关联度低，未触及核心需求
- 1分: 回答与问题完全无关
```

#### (3) Context Relevance 评分

```
评估维度: 检索上下文相关性
评分规则:
- 5分: 所有检索片段都与问题高度相关
- 4分: 大部分片段相关，有1个不相关
- 3分: 约半数片段相关
- 2分: 只有少量片段相关
- 1分: 检索结果与问题完全无关
```

#### (4) Completeness 评分

```
评估维度: 回答完整性
评分规则:
- 5分: 全面覆盖问题所有方面，信息充分
- 4分: 覆盖主要方面，细节略欠
- 3分: 回答了部分问题，有明显遗漏
- 2分: 只回答了问题的很小部分
- 1分: 几乎没有提供有价值的信息
```

#### (5) Conciseness 评分

```
评估维度: 回答简洁性
评分规则:
- 5分: 信息密度高，无冗余
- 4分: 基本简洁，有少量重复
- 3分: 存在较多冗余信息
- 2分: 内容冗长，大量重复
- 1分: 极度冗长，核心信息被淹没
```

**输出格式约束**（结构化解析）：

```python
# LLM Judge 返回格式
judge_response_schema = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"]
}
```

### 3.3 LLM-as-Judge 已知偏差与缓解

根据 2025 年 ScienceDirect 上的调研论文，LLM-as-Judge 存在以下已知偏差：

| 偏差类型 | 表现 | 缓解策略 |
|---------|------|---------|
| **Position Bias** | 多个候选时偏向第一个 | 随机打乱候选顺序 |
| **Length Bias** | 偏向更长的回答 | 在 prompt 中明确说明"简洁回答同样高分" |
| **Verbosity Preference** | 偏好冗长的回答 | 增加 Conciseness 维度独立评估 |
| **Self-Preference** | 偏好自己生成的风格 | 使用不同模型做 judge（如用 qwen-max 评估 qwen-plus） |

### 3.4 评估执行策略

**生产环境**：
- 采样评估：只对 5-10% 的对话执行 LLM Judge（控制成本）
- 单维度评估：每次只评估 1-2 个核心维度（faithfulness + answer relevance）
- 使用较小/便宜的模型做 judge（如 qwen-turbo）
- 结果写入 `obs_scores` 表

**离线/测试环境**：
- 全量评估：对所有对话执行完整 5 维度评估
- 使用强模型做 judge（如 qwen-max）
- 生成评估报告，对比不同版本的表现

```python
class QualityEvaluator:
    """LLM 响应质量评估器"""

    def __init__(self, llm_gateway, sample_rate=0.1):
        self.llm = llm_gateway
        self.sample_rate = sample_rate

    async def evaluate_if_needed(self, question, context, answer, trace_id):
        """根据采样率决定是否评估"""
        import random
        if random.random() > self.sample_rate:
            return None

        scores = {}
        for metric in ["faithfulness", "answer_relevance"]:
            result = await self._run_judge(metric, question, context, answer)
            if result:
                scores[metric] = result
                # 写入 obs_scores 表
                await self._persist_score(trace_id, metric, result)

        return scores

    async def _run_judge(self, metric, question, context, answer):
        prompt = self._build_judge_prompt(metric, question, context, answer)
        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model="qwen-turbo",  # 用便宜模型做 judge
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        return self._parse_judge_response(response)
```

### 3.5 开源工具对比

| 工具 | 核心能力 | 适用场景 | 依赖 |
|------|---------|---------|------|
| **RAGAS** | RAG 专项评估（faithfulness, context_precision/recall, answer_relevance） | 离线评估、回归测试 | 需要 LLM API |
| **TruLens** | 生产监控 + RAG Triad（context_relevance, groundedness, answer_relevance） | 实时监控、仪表盘 | 需要 LLM API |
| **DeepEval** | 通用 LLM 评估 + 自定义指标 | 测试驱动开发 | 需要 LLM API |
| **LettuceDetect** (2025新) | Token 级别幻觉检测 | RAG 幻觉检测 | 本地模型，无需 API |

**推荐策略**：不直接集成这些框架，而是借鉴其指标设计和 prompt 模板，用本项目的 LLM Gateway 实现自有的 `QualityEvaluator`。原因：
1. 这些框架都依赖 OpenAI SDK 或 LangChain，与本项目架构不兼容
2. 直接使用本项目的 LLM Gateway 可以利用已有的 failover 和密钥管理
3. 自有实现可以精确控制成本（采样率、judge 模型选择）

---

## 4. 实时监控仪表盘

### 4.1 核心监控指标

**AI Agent 平台最重要的指标**：

| 类别 | 指标 | 采集来源 | 告警阈值建议 |
|------|------|---------|-------------|
| **性能** | 请求延迟 P50/P95/P99 | `obs_traces.duration_ms` | P95 > 30s |
| **性能** | LLM 调用延迟 | `obs_spans` (generation) | > 15s |
| **性能** | 工具调用延迟 | `obs_spans` (tool) | > 10s |
| **质量** | Faithfulness 均分 | `obs_scores` | < 3.5 |
| **质量** | 幻觉检出率 | `obs_scores` | > 15% |
| **成本** | Token 消耗趋势 | `chat_records` | 日环比增长 > 50% |
| **成本** | 单次对话平均 token | `chat_records` | > 10000 tokens |
| **可用性** | 请求成功率 | `obs_traces.status` | < 95% |
| **可用性** | Agent 循环超限率 | `obs_traces` (agent_iterations >= 20) | > 5% |
| **可用性** | LLM API 错误率 | `obs_spans` (generation status=failed) | > 5% |
| **负载** | 并发会话数 | WebSocket 连接数 | > 200 |
| **负载** | 队列深度 | ThreadPoolExecutor 队列 | > 50 |

### 4.2 WebSocket 实时推送方案

本项目已有 SSE 流式输出机制。实时监控仪表盘可以复用类似模式：

**方案：FastAPI WebSocket + Redis Pub/Sub（可选）**

```python
# 监控 WebSocket 端点
@router.websocket("/ws/monitor")
async def monitor_websocket(websocket: WebSocket):
    await websocket.accept()

    # 方案A：轮询模式（简单，无需 Redis）
    interval = 5  # 5秒刷新
    while True:
        metrics = await _collect_realtime_metrics()
        await websocket.send_json(metrics)
        await asyncio.sleep(interval)

# 方案B：事件驱动模式（需要 Redis Pub/Sub，更实时）
@router.websocket("/ws/monitor")
async def monitor_websocket(websocket: WebSocket):
    await websocket.accept()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("monitor:metrics")

    async for message in pubsub.listen():
        if message["type"] == "message":
            await websocket.send_json(json.loads(message["data"]))
```

**推荐方案A（轮询）**，理由：
- 系统并发用户数百级，不需要毫秒级实时
- 5 秒刷新间隔对监控仪表盘足够
- 不增加 Redis 依赖
- PostgreSQL 聚合查询在数据量 < 100 万条时性能足够

### 4.3 成本追踪与 Token 优化

本项目已有完善的 `chat_records` token 统计机制。以下是优化策略：

**Token 优化手段**：

| 策略 | 预期节省 | 实现难度 |
|------|---------|---------|
| **Prompt 缓存利用** | 20-40% input tokens | 已支持（cached_tokens 统计已实现） |
| **上下文窗口裁剪** | 30-50% input tokens | 中等（需要实现智能截断） |
| **对话历史压缩** | 20-40% input tokens | 中等（摘要压缩旧消息） |
| **模型降级路由** | 40-60% 成本 | 低（简单问题用小模型） |
| **采样评估替代全量** | 90% judge 成本 | 低（已设计） |

**成本估算公式**（需要维护模型价格表）：

```python
MODEL_PRICING = {
    "qwen-turbo": {"input": 0.0003, "output": 0.0006},   # 元/千token
    "qwen-plus":  {"input": 0.0008, "output": 0.002},
    "qwen-max":   {"input": 0.002,  "output": 0.006},
    "glm-4-flash":{"input": 0.0001, "output": 0.0001},
    "glm-4":      {"input": 0.0001, "output": 0.0001},
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int,
                   cached_tokens: int = 0) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
    # 缓存 token 通常有 50% 折扣
    input_cost = (input_tokens - cached_tokens) * pricing["input"] / 1000
    cached_cost = cached_tokens * pricing["input"] * 0.5 / 1000
    output_cost = output_tokens * pricing["output"] / 1000
    return input_cost + cached_cost + output_cost
```

---

## 5. 结构化告警

### 5.1 告警规则设计模式

**AI 系统告警与传统的 CPU/内存告警不同，需要关注"业务质量"而不仅是"系统健康"**：

```python
class AlertRule:
    """告警规则定义"""
    name: str
    metric: str               # 观测指标名
    condition: str            # gt/lt/eq/ne/gte/lte
    threshold: float          # 阈值
    window_minutes: int       # 统计窗口
    evaluation_interval: int  # 评估间隔（秒）
    severity: str             # critical/warning/info
    channels: List[str]       # 通知渠道: [email, wecom, webhook]
    message_template: str     # 告警消息模板
    cooldown_minutes: int     # 冷却时间（避免重复告警）
```

**推荐的告警规则集**：

| 规则 | 指标 | 条件 | 窗口 | 级别 |
|------|------|------|------|------|
| LLM 服务不可用 | `llm_error_rate` | > 50% | 5min | critical |
| LLM 延迟飙升 | `llm_p95_latency` | > 30s | 10min | warning |
| Token 消耗异常 | `hourly_token_usage` | 较昨日同期增长 > 100% | 1h | warning |
| 响应质量下降 | `avg_faithfulness_score` | < 3.0 | 30min | warning |
| 幻觉率偏高 | `hallucination_rate` | > 20% | 30min | warning |
| Agent 循环超限 | `agent_loop_limit_rate` | > 10% | 15min | warning |
| 请求成功率低 | `request_success_rate` | < 90% | 5min | critical |
| 数据库连接异常 | `db_connection_errors` | > 5 | 5min | critical |

### 5.2 多渠道告警路由

```python
class AlertRouter:
    """告警路由策略"""

    ROUTING_RULES = {
        "critical": {
            "channels": ["wecom", "phone_call"],  # 企业微信 + 电话
            "repeat_interval": 15,                 # 15分钟重复一次直到确认
            "escalate_after": 30,                   # 30分钟未确认则升级
        },
        "warning": {
            "channels": ["wecom"],                  # 仅企业微信
            "repeat_interval": 60,                  # 1小时重复一次
            "auto_resolve_after": 120,              # 2小时自动恢复
        },
        "info": {
            "channels": ["webhook"],                # 仅 Webhook（记录到系统）
            "repeat_interval": 0,                   # 不重复
            "auto_resolve_after": 60,
        },
    }
```

### 5.3 避免告警疲劳

**核心原则**：

1. **分级告警**：只对 critical/warning 发送外部通知，info 级别只记录
2. **冷却机制**：同一规则触发后，在冷却期内不重复发送
3. **自适应阈值**：基于历史数据动态调整阈值（如 token 消耗在工作时间 vs 非工作时间差异大）
4. **告警聚合**：5分钟内同一服务的多个告警合并为一条
5. **自动恢复**：指标恢复正常后自动发送恢复通知
6. **静默期**：支持设定维护窗口期，期间不发送告警

```python
class AlertManager:
    """告警管理器（避免告警疲劳）"""

    def __init__(self):
        self._last_alert_time = {}  # rule_name -> last_trigger_time
        self._active_alerts = {}    # alert_id -> alert_state
        self._silence_windows = []  # 静默窗口列表

    async def evaluate_and_notify(self, rule: AlertRule, current_value: float):
        # 检查静默窗口
        if self._in_silence_window():
            return

        # 检查冷却期
        last_time = self._last_alert_time.get(rule.name, 0)
        if time.time() - last_time < rule.cooldown_minutes * 60:
            return

        # 评估条件
        if not self._evaluate_condition(rule, current_value):
            # 检查是否需要发送恢复通知
            if rule.name in self._active_alerts:
                await self._send_recovery(rule, current_value)
                del self._active_alerts[rule.name]
            return

        # 发送告警
        await self._send_alert(rule, current_value)
        self._last_alert_time[rule.name] = time.time()
        self._active_alerts[rule.name] = {
            "rule": rule,
            "triggered_at": time.time(),
            "value": current_value
        }
```

---

## 6. 幻觉检测

### 6.1 实用方法对比

根据 2025-2026 年的研究和实践，幻觉检测有以下几种方法：

| 方法 | 原理 | 延迟 | 准确性 | 是否需要额外 LLM 调用 |
|------|------|------|--------|----------------------|
| **关键词重叠法** | 检查回答中的关键词是否出现在上下文中 | < 5ms | 中等 | 否 |
| **NLI（自然语言推理）** | 用 NLI 模型判断回答与上下文是否矛盾 | ~50ms | 较高 | 否（本地模型） |
| **LLM-as-Judge** | 用另一个 LLM 判断回答是否基于上下文 | 1-3s | 高 | 是 |
| **实体验证法** | 提取回答中的命名实体，验证是否出现在上下文中 | < 10ms | 中等 | 否 |
| **数值矛盾检测** | 检查回答中的数值是否与上下文矛盾 | < 5ms | 较高（对数值类问题） | 否 |
| **Token 分类法** (LettuceDetect) | 在 token 级别标注每个声明是否有依据 | ~100ms | 高 | 否（本地模型） |

**关键发现**（来自 arXiv 2025 论文）：**纯语义嵌入相似度方法在幻觉检测上表现很差**，不能可靠地检测幻觉。需要结合多种方法。

### 6.2 推荐方案：多层混合检测

参考 [hallucination-detector](https://github.com/Emmimal/hallucination-detector/) 项目的设计，实现轻量级的多层幻觉检测。该项目在 FastAPI 下运行，纯 Python 实现，< 50ms 延迟：

**五层检测架构**：

```
用户问题 + 检索上下文 + LLM 回答
  │
  ├── 第1层: 信心评分 (ConfidenceScorer)
  │     检测语言过度自信（"一定"、"绝对"、"肯定"等标记）
  │     方法: 正则匹配 + 词频统计
  │     延迟: < 1ms
  │
  ├── 第2层: 忠实度评分 (FaithfulnessScorer)
  │     将回答拆分为声明句，检查每个声明的关键词是否在上下文中
  │     方法: 句子分割 + 关键词重叠度（阈值 40%）
  │     延迟: ~2ms
  │
  ├── 第3层: 矛盾检测 (ContradictionDetector)
  │     检测数值矛盾、否定翻转、时间矛盾
  │     方法: 正则提取数值 + 否定词模式匹配
  │     延迟: ~1ms
  │
  ├── 第4层: 实体幻觉检测 (EntityHallucinationDetector)
  │     提取回答中的人名、机构名、引用，验证是否在上下文中
  │     方法: spaCy NER 或正则回退
  │     延迟: < 10ms（正则）/ ~45ms（spaCy）
  │
  ├── 第5层: 回答漂移监控 (AnswerDriftMonitor)
  │     对同一问题，检测回答是否随时间变化（指纹对比）
  │     方法: SQLite 持久化指纹 + 相似度对比
  │     延迟: ~3ms
  │
  └── 综合质量评分 (QualityScore)
        加权公式: 0.40 × 忠实度 + 0.30 × 一致性 + 0.20 × 信心度 + 0.10 × 延迟分 - 0.20 × 漂移惩罚
        路由决策:
        - ACCEPT (>= 0.75): 直接输出
        - HEALED_ACCEPT: 尝试修复后输出
        - FALLBACK (< 0.50): 降级回复
        - DISCARD: 安全拒绝
```

**质量评分公式**：

```
quality_score = 0.40 × faithfulness
              + 0.30 × consistency      (有矛盾则为 0.0)
              + 0.20 × confidence       (校准后的信心度)
              + 0.10 × latency_score    (延迟惩罚，非线性)
              - 0.20 × drift_penalty    (漂移扣分，最后应用)
```

### 6.3 自修复策略

当检测到幻觉时，可以尝试修复而非简单拒绝：

| 策略 | 触发条件 | 修复方法 |
|------|---------|---------|
| **矛盾修补** (contradiction_patch) | 数值/时间矛盾 | 用上下文中的正确值替换回答中的错误值 |
| **实体清洗** (entity_scrub) | 虚构实体 | 移除包含虚构实体的句子，附加透明说明 |
| **基于上下文重写** (grounding_rewrite) | 忠实度 < 0.30 | 从上下文中提取最相关的句子重建回答 |

### 6.4 适用于本项目的实现建议

```python
class HallucinationGuard:
    """幻觉检测守卫 - 嵌入 Agent Loop"""

    def __init__(self, config=None):
        self.confidence_scorer = ConfidenceScorer()
        self.faithfulness_scorer = FaithfulnessScorer()
        self.contradiction_detector = ContradictionDetector()
        self.entity_detector = EntityHallucinationDetector(use_spacy=False)  # 先用正则
        # self.drift_monitor = AnswerDriftMonitor(db_path="data/drift.db")  # Phase 2

    async def inspect(self, question: str, context_chunks: list, answer: str) -> dict:
        """检测回答是否存在幻觉（< 50ms）"""
        context_text = "\n".join(context_chunks)

        report = {
            "confidence": self.confidence_scorer.score(answer),
            "faithfulness": self.faithfulness_scorer.score(answer, context_text),
            "contradictions": self.contradiction_detector.detect(answer, context_text),
            "hallucinated_entities": self.entity_detector.detect(answer, context_text),
        }

        # 综合判定
        report["is_hallucinating"] = (
            report["faithfulness"] < 0.50
            or len(report["contradictions"]) > 0
            or len(report["hallucinated_entities"]) > 0
        )

        report["quality_score"] = QualityScore.compute(report)

        return report
```

**嵌入位置**：在 `agent.py` 的 `process_message()` 中，LLM 生成最终回答后、返回给用户前：

```python
# agent.py process_message() 末尾，full_response 生成后
if self._hallucination_guard and context_chunks:
    report = self._hallucination_guard.inspect(
        question=user_message,
        context_chunks=context_chunks,
        answer=full_response
    )
    if report["is_hallucinating"]:
        logger.warning(
            f"Hallucination detected: faithfulness={report['faithfulness']:.2f}, "
            f"contradictions={len(report['contradictions'])}, "
            f"entities={len(report['hallucinated_entities'])}"
        )
        # 写入追踪系统
        TraceContext.add_score("hallucination_detected", 1.0, source="auto")
        TraceContext.add_score("faithfulness", report["faithfulness"], source="auto")
```

---

## 7. 行业参考对比

### 7.1 平台可观测性功能矩阵

| 功能 | Langfuse | LangSmith | Dify | FastGPT | Coze |
|------|----------|-----------|------|---------|------|
| **开源** | 是 | 否 | 是 | 部分 | 否 |
| **自部署** | 支持 | 不支持 | 支持 | 支持 | 不支持 |
| **请求追踪** | 强 | 强 | 通过集成 | 基础 | 有限 |
| **Span 级追踪** | 支持（多类型） | 支持 | 支持 | 不支持 | 不支持 |
| **LLM-as-Judge** | 内置 | 内置 | 通过集成 | 不支持 | 不支持 |
| **评分数据模型** | 完善（numeric/categorical/boolean/text） | 完善 | 无 | 无 | 无 |
| **Prompt 管理** | 支持 | 支持 | 可视化 | 可视化 | 可视化 |
| **数据集/实验** | 支持 | 支持 | 不支持 | 不支持 | 不支持 |
| **实时仪表盘** | 支持 | 支持 | 基础 | 基础 | 基础 |
| **成本追踪** | 支持 | 支持 | 基础 | 不支持 | 不支持 |
| **框架绑定** | 无（框架无关） | LangChain 深度绑定 | 自有 | 自有 | 字节跳动生态 |
| **存储** | ClickHouse（V3） | 自有 | PostgreSQL | MongoDB | 自有 |

### 7.2 各平台架构特点

**Langfuse**：
- 数据模型：Trace → Observations (Span/Generation/Event) → Scores
- V3 从 PostgreSQL 迁移到 ClickHouse 以提升查询性能
- 通过 SDK/API 接收追踪数据，支持 OTLP 协议
- LLM-as-Judge 评估器自动运行，支持数据集实验对比
- 最适合参考的架构设计

**LangSmith**：
- LangChain 生态的核心可观测性工具
- 通过 LangChain Callback 自动采集追踪数据
- 不支持自部署，仅 SaaS
- 评估系统集成度最高

**Dify**：
- "Beehive" 模块化架构，工作流引擎在可视化画布上执行节点
- 工作流执行自动生成层级 Span：根 Span（工作流运行）→ 子 Span（LLM 调用/工具调用/条件分支）
- 通过集成 Langfuse/LangSmith 获得高级可观测性
- 自身基础日志能力有限

**FastGPT**：
- 专注 LLM 原生应用构建
- 基础的对话日志和工作流调试
- 可观测性能力较基础

**Coze（字节跳动）**：
- 云端平台，不开放自部署
- 可观测性功能有限，聚焦于应用构建和发布

### 7.3 对本项目的启示

1. **借鉴 Langfuse 数据模型**：Trace → Span → Generation → Score 的层级关系已被行业验证，直接采用
2. **不依赖外部追踪平台**：自建轻量追踪系统，数据存 PostgreSQL，避免引入 ClickHouse 等新依赖
3. **参考 Dify 的工作流追踪**：将 Agent Loop 的每一步视为 Span，LLM 调用标记为 Generation
4. **整合 LLM-as-Judge**：在 `obs_scores` 表中存储评估结果，支持后续的离线分析和回归测试
5. **渐进式实现**：先做追踪和基础指标，再做质量评估和幻觉检测

---

## 8. 实施优先级建议

| Phase | 内容 | 优先级 | 预计工期 |
|-------|------|--------|---------|
| **P1** | 分布式追踪（obs_traces + obs_spans 表 + ContextVar 追踪） | 高 | 1-2 周 |
| **P1** | 实时监控 API（聚合查询 + WebSocket 推送） | 高 | 1 周 |
| **P2** | LLM-as-Judge 质量评估（采样评估 + obs_scores） | 中 | 1-2 周 |
| **P2** | 前端监控仪表盘（Vue.js + WebSocket） | 中 | 1-2 周 |
| **P3** | 幻觉检测（多层检测 + 自修复） | 中 | 2 周 |
| **P3** | 结构化告警（规则引擎 + 多渠道路由） | 中 | 1 周 |
| **P4** | 成本优化（模型价格表 + 成本估算 + 优化建议） | 低 | 1 周 |
| **P4** | 漂移监控（AnswerDriftMonitor + SQLite） | 低 | 3 天 |

---

## 9. 参考资料

### 分布式追踪
- [Langfuse Data Model](https://langfuse.com/docs/observability/data-model) — Langfuse 核心 Trace/Observation 数据模型
- [Langfuse Observation Types](https://langfuse.com/docs/observability/features/observation-types) — Span/Generation/Event 类型定义
- [Langfuse V3 架构演进](https://langfuse.com/blog/2026-03-10-simplify-langfuse-for-scale) — 从 PostgreSQL 到 ClickHouse 的迁移
- [Langfuse x ClickHouse](https://clickhouse.com/blog/langfuse-llm-analytics) — Langfuse 使用 ClickHouse 的经验

### LLM 响应质量评估
- [LLM-as-a-Judge 实用指南](https://towardsdatascience.com/llm-as-a-judge-a-practical-guide/) — 评分规则设计和最佳实践
- [Hamel Husain: LLM Judge 实战经验](https://hamel.dev/blog/posts/llm-judge/) — 30+ AI 实施项目的经验总结
- [Arize: LLM-as-a-Judge 入门](https://arize.com/llm-as-a-judge/) — 预置评估器和自定义规则
- [Pydantic: LLM-as-a-Judge with Pydantic Evals](https://pydantic.dev/articles/llm-as-a-judge) — Pydantic 生态的评估工具
- [LLM-as-a-Judge 学术调研](https://www.sciencedirect.com/science/article/pii/S2666675825004564) — 偏差分析和缓解策略

### RAG 评估框架
- [RAGAS vs TruLens vs DeepEval 对比](https://atlan.com/know/llm-evaluation-frameworks-compared/) — 三大框架横向对比
- [TruLens RAG Triad](https://www.trulens.org/getting_started/core_concepts/rag_triad/) — Context Relevance + Groundedness + Answer Relevance
- [Cleanlab: 幻觉检测基准测试](https://cleanlab.ai/blog/rag-tlm-hallucination-benchmarking/) — 多种检测方法的精度/召回率对比

### 幻觉检测
- [hallucination-detector](https://github.com/Emmimal/hallucination-detector/) — 纯 Python RAG 幻觉检测 + 自修复（< 50ms）
- [RAG 幻觉自修复实践](https://towardsdatascience.com/rag-hallucinates-i-built-a-self-healing-layer-that-fixes-it-in-real-time/) — 五层检测 + 三种修复策略
- [LettuceDetect](https://arxiv.org/html/2502.17125v1) — Token 级别幻觉检测模型
- [嵌入相似度幻觉检测的局限性](https://arxiv.org/html/2512.15068v1) — 证明纯嵌入方法不可靠

### 可观测性平台对比
- [Laminar vs Langfuse vs LangSmith](https://laminar.sh/blog/2026-01-29-laminar-vs-langfuse-vs-langsmith-llm-observability-compared) — 2026 年三大平台对比
- [Dify + LangSmith/Langfuse 集成](https://dify.ai/blog/dify-integrates-langsmith-langfuse) — Dify 的可观测性架构
- [Langfuse: Dify 集成指南](https://langfuse.com/integrations/no-code/dify) — Langfuse 接入 Dify 的方式
- [2025 LLM 可观测性工具 Top 8](https://langwatch.ai/blog/top-10-llm-observability-tools-complete-guide-for-2025) — 工具全景

### Token 成本追踪
- [LiteLLM + PostgreSQL Token 追踪](https://medium.com/@hithasrinivas4/tracking-llm-usage-spend-with-litellm-postgresql-and-litellm-ui-8ca9e6773f17) — PostgreSQL 方案
- [LLM Token 优化案例: 83% reduction](https://www.zenml.io/llmops-database/optimizing-llm-token-usage-with-production-monitoring-in-natural-language-to-sql-system) — 生产监控优化 token 用量

### 告警最佳实践
- [Alert Fatigue Reduction with AI](https://oneuptime.com/blog/post/2026-03-05-alert-fatigue-ai-on-call/view) — AI 驱动的告警降噪
- [Multi-Channel Alerting](https://swiftask.ai/ai-integration/piped/intelligent-multi-channel-alerting) — 多渠道智能告警路由
