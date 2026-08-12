# LLM 计费接入改造设计

> 状态：🔧 部分完成（Phase 1-3 代码完成，Phase 4 文档同步完成，待补集成测试）
> 开发计划：[plan-llm-billing-integration.md](../../plans/plan-llm-billing-integration.md)
> 关联文档：[tenant-credit-billing-design.md](./tenant-credit-billing-design.md)

## 1. 背景与目标

### 1.1 背景

项目当前 `chat_records` 表的计费仅覆盖四类链路：

| 链路 | 计费落点 |
|---|---|
| 主链路对话 LLM（含多模态） | `session_record.py:298` -> `ChatRecordDB.create` |
| 视频生成（按秒） | `video_agent/service.py:762` |
| 报告生成（含 summarizer.py 的 LLM 调用，被 generator 聚合） | `reports/generator.py:405` |
| 客户端 LLM 代理（×5） | `client_binding_db.py:317`（独立表） |

其余 LLM 相关调用 -- Embedding、ASR、视频提示词 LLM、background_runner 半夜任务（长期记忆摘要、工作成果复盘、上下文压缩扫描）、对话内后台 LLM（情感/分类/案件匹配/内容生成/数据分析）-- **均未计费**，造成租户成本漏算。

### 1.2 目标

将上述未计费调用全部接入 `chat_records` 计费体系，按「一次完整交互」或「一次后台任务执行」聚合，扣减租户 `credit_balance`。

## 2. 调用点分类

### 2.1 对话内调用（累加到主 chat_records）

对话主链路的一次交互内，存在多个非主循环 LLM/embedding/ASR 调用，按一次完整交互聚合计费：

| 调用点 | 文件 | 累加方式 |
|---|---|---|
| 上下文压缩（gateway 路径） | `mid_term.py:913` | `record_background_llm_usage` |
| 情感分析 | `sentiment_service.py:49` | `record_background_llm_usage` |
| 文本分类 | `classification_service.py:50` | `record_background_llm_usage` |
| 案件匹配 | `case_matching_service.py:126` | `record_background_llm_usage` |
| 内容生成工具 | `content_generate_tool.py:105` | `record_background_llm_usage` |
| 数据分析（迷你 Agent 循环） | `analysis_agent.py:44/93` | `record_background_llm_usage` |
| 酒店检索 embedding | `hotel_search_tool.py:74`、`hotel_retriever.py:51` | `add_embedding_usage` |
| 景点检索 embedding | `attraction_search_tool.py:68`、`attraction_retriever.py:51` | `add_embedding_usage` |
| 微信语音 ASR | `channel_routes.py:2095` | `add_asr_usage` |

### 2.2 独立 chat_records（source_type 区分）

不属于某次对话交互的离线/后台调用，独立写一条 chat_records：

| 调用点 | 文件 | source_type |
|---|---|---|
| 视频提示词 LLM（qwen-vl） | `video_agent/service.py`（4 处调用点） | `video_prompt` |
| 知识库文档向量化 + 摘要 | `knowledge/service.py:89/263` | `knowledge_embedding` |
| 长期记忆摘要（02:00） | `memory_summarizer.py:264` | `background_llm` |
| 工作成果复盘（02:30） | `work_outcome_review.py:315` | `background_llm` |
| 上下文压缩后台扫描 | `mid_term.py:208/913`（在 `run_background_compression_scan` 调用） | `background_llm` |

## 3. 数据库变更

### 3.1 `chat_records` 表新增 3 字段

```sql
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS embedding_tokens INT DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS asr_calls INT DEFAULT 0;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS usage_breakdown JSONB;
```

- `embedding_tokens`：本条记录内 embedding 调用消耗的 token 数（按 `text-embedding-v3` 计费）
- `asr_calls`：本条记录内 ASR 调用次数（阿里云 NLS 按次计费）
- `usage_breakdown`：详细分项明细 JSON，结构如下：

```json
{
  "chat": {
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "cached_input_tokens": 200,
    "total_tokens": 1500,
    "model": "deepseek-v4-pro",
    "credit": 0.15
  },
  "embedding": {
    "tokens": 800,
    "calls": 2,
    "model": "text-embedding-v3",
    "credit": 0.0006
  },
  "asr": {
    "calls": 1,
    "model": "aliyun-nls-asr",
    "credit": 0.06
  }
}
```

### 3.2 `token_cost_prices` 表新增 2 字段

```sql
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS embedding_price_per_m NUMERIC(10,4);
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS asr_price_per_call NUMERIC(10,4);
```

种子数据：

```sql
INSERT INTO token_cost_prices (model_name, embedding_price_per_m)
VALUES ('text-embedding-v3', 0.7)
ON CONFLICT (model_name) DO UPDATE SET embedding_price_per_m = EXCLUDED.embedding_price_per_m;

INSERT INTO token_cost_prices (model_name, asr_price_per_call)
VALUES ('aliyun-nls-asr', 0.06)
ON CONFLICT (model_name) DO UPDATE SET asr_price_per_call = EXCLUDED.asr_price_per_call;
```

单价参考阿里云 2026-08 官方价格，最终需运营确认。

## 4. 计费函数

### 4.1 `calculate_embedding_credit_cost`

```python
def calculate_embedding_credit_cost(
    embedding_tokens: int,
    model: str = "text-embedding-v3",
    usage_factor_override: Optional[int] = None,
) -> float:
```

公式：`credit = ceil(embedding_tokens × 单价 / 1e6 × usage_factor × 100) / 100`

`usage_factor` 默认读 `settings.billing.embedding_usage_factor`（默认 100）。

### 4.2 `calculate_asr_credit_cost`

```python
def calculate_asr_credit_cost(
    asr_calls: int,
    model: str = "aliyun-nls-asr",
    usage_factor_override: Optional[int] = None,
) -> float:
```

公式：`credit = ceil(asr_calls × 单价 × usage_factor × 100) / 100`

阿里云 NLS 按次计费（响应不返回音频时长），故 `asr_calls` 即调用次数。

### 4.3 合并积分

`SessionRecordService.save()` 计算总积分：

```python
credit_cost = round(
    chat_credit_cost
    + embedding_credit_cost
    + asr_credit_cost,
    2,
)
```

单价缺失时 `credit_cost = 0`，不阻断对话。

## 5. SessionRecordService 扩展

### 5.1 新增字段

```python
self.embedding_tokens: int = 0
self.asr_calls: int = 0
self.usage_breakdown: Dict[str, Any] = {}
```

### 5.2 新增方法

```python
def add_embedding_usage(self, tokens: int, model: str = "text-embedding-v3") -> None:
    """累加 embedding 调用 token 用量"""

def add_asr_usage(self, calls: int = 1, model: str = "aliyun-nls-asr") -> None:
    """累加 ASR 调用次数"""
```

### 5.3 模块级工具函数

```python
def record_background_llm_usage(usage: Optional[Dict[str, int]]) -> None:
    """对话内后台 LLM 调用 usage 累加到当前 SessionRecordService

    若当前线程无 SessionRecordService（background_runner 调度场景），
    降级为独立 ChatRecordDB.create(source_type=background_llm)。
    """
```

## 6. Embedding 调用统一入口

### 6.1 `TextEmbeddingV3Client` 扩展

- 新增 `last_usage_tokens: int` 实例属性，累加每次调用 `resp.usage.tokens`
- 新增 `reset_usage()` 方法，重置计数器
- 新增 `embed_sync(text)` 同步版本，供同步代码路径使用（替代裸 `dashscope.TextEmbedding.call`）

### 6.2 4 个绕过 client 的调用点改用 `TextEmbeddingV3Client`

| 文件 | 改造 |
|---|---|
| `hotel_search_tool.py:_embed` | 改用 `client.embed_sync(text)` |
| `attraction_search_tool.py:_embed` | 同上 |
| `hotel_retriever.py:_embed` | 同上 |
| `attraction_retriever.py:_embed` | 同上 |

调用前后：

```python
client = self._get_embedding_client()
client.reset_usage()
embedding = self._embed(query)
if client.last_usage_tokens > 0:
    record = SessionRecordManager.get_current_record()
    if record:
        record.add_embedding_usage(client.last_usage_tokens, model=client.model)
```

## 7. 独立计费落点

### 7.1 视频提示词 LLM（`source_type=video_prompt`）

用户可能多次调整提示词后放弃创建视频，需独立计费。`VideoChatService._record_prompt_llm_usage` 在 4 处调用点（`handle_user_message` 的 refine draft、refine submit、agile、`continue_with_video`）独立写 chat_records：

```python
ChatRecordDB.create(
    session_id=session_id,
    tenant_id=tenant_id,
    user_id=user_id,
    user_message=user_input[:500],
    source_type="video_prompt",
    credit_cost=calculate_credit_cost(...),
    ...
)
```

### 7.2 知识库文档处理（`source_type=knowledge_embedding`）

`KnowledgeBaseService._record_knowledge_embedding_billing` 在 `upload_document` 成功后调用，合并 embedding + 摘要 LLM 两部分用量：

```python
ChatRecordDB.create(
    session_id=f"knowledge_embedding_{doc_id}",
    source_type="knowledge_embedding",
    embedding_tokens=embedding_tokens,
    credit_cost=embedding_credit + llm_credit,
    usage_breakdown={"embedding": {...}, "summary_llm": {...}},
    ...
)
```

### 7.3 background_runner 任务（`source_type=background_llm`）

background_runner 调度线程无 HTTP 上下文，无法复用 `SessionRecordManager`，独立 `ChatRecordDB.create`：

| 任务 | 文件 | 辅助函数 |
|---|---|---|
| 长期记忆摘要（02:00） | `memory_summarizer.py` | `_record_background_llm_billing` |
| 工作成果复盘（02:30） | `work_outcome_review.py` | `_record_background_llm_billing` |
| 上下文压缩后台扫描 | `mid_term.py:run_background_compression_scan` | `record_background_llm_usage`（无 SessionRecordService 时降级独立落库） |

## 8. 枚举登记

`ChatRecordSourceType`（`src/saas/models/enums.py`）补充：

```python
VIDEO_GEN = "video_gen"              # 已用未登记，本次补登记
VIDEO_PROMPT = "video_prompt"
KNOWLEDGE_EMBEDDING = "knowledge_embedding"
BACKGROUND_LLM = "background_llm"
```

## 9. 测试方案

### 9.1 单元测试（`tests/unit/`）

- `test_credit_billing.py` 扩展：`calculate_embedding_credit_cost` / `calculate_asr_credit_cost` 各 10 个用例（已通过）

### 9.2 集成测试（`tests/integration/`）

- `test_billing_embedding_integration.py`：模拟文档上传向量化，验证 `chat_records(source_type='knowledge_embedding')` 写入
- `test_billing_background_llm.py`：模拟 background_runner 任务，验证 `chat_records(source_type='background_llm')` 写入

### 9.3 回归测试

跑现有 `test_credit_billing.py` / `test_billing_balance_api.py` / `test_billing_recharges_api.py` / `test_billing_daily_detail_api.py`，确保不破坏。

## 10. 风险与上线

### 10.1 风险

1. **重复计算风险**：主链路 token 统计由 `agent.py:_record.add_llm_usage` 主导，本次改造用 `record_background_llm_usage` 仅覆盖非主循环 LLM 调用，不会重复累加主链路 token。
2. **background_runner DB 连接**：APScheduler 线程内 `ChatRecordDB.create` 复用主连接池，需验证连接归还正常。
3. **单价配置**：embedding/ASR 单价需运营确认（阿里云实时价格）。

### 10.2 上线步骤

1. Phase 1（基础设施）：数据库 ALTER + 计费函数 + enum 登记（已上线）
2. Phase 2（对话内计费）：embedding/ASR/视频提示词 LLM 接入
3. Phase 3（后台计费）：background_runner 三个任务接入
4. Phase 4：文档同步

## 11. 关键文件清单

| 文件 | 改动 |
|---|---|
| `deploy/init-postgres.sql` | `chat_records` 表加 3 字段、`token_cost_prices` 表加 2 字段 + 种子 |
| `deploy/db_update.sql` | 末尾追加幂等 ALTER + 种子 INSERT |
| `src/services/billing.py` | 新增 `calculate_embedding_credit_cost` / `calculate_asr_credit_cost` |
| `src/config/settings.py` | `billing` 配置加 `embedding_usage_factor` / `asr_usage_factor` |
| `src/db/models.py` | `ChatRecordDB.create` 扩展参数与 SQL |
| `src/services/session_record.py` | 扩展 `__init__` / `add_embedding_usage` / `add_asr_usage` / `save` / `record_background_llm_usage` |
| `src/saas/models/enums.py` | `ChatRecordSourceType` 补充枚举 |
| `src/knowledge/embedding/embedding_client.py` | `last_usage_tokens` / `reset_usage` / `embed_sync` |
| `src/tools/knowledge/hotel_search_tool.py` / `attraction_search_tool.py` | 改用 `TextEmbeddingV3Client.embed_sync` + `add_embedding_usage` |
| `src/skills/travel-quote/scripts/hotel_retriever.py` / `attraction_retriever.py` | 同上 |
| `src/knowledge/service.py` | 文档上传向量化 + 摘要独立 `ChatRecordDB.create` |
| `src/saas/api/channel_routes.py` | ASR 调用后 `add_asr_usage` |
| `src/video_agent/service.py` | 视频提示词 LLM 独立 `ChatRecordDB.create` |
| `src/video_agent/prompt_engine.py` | `last_usage` 属性 |
| `src/memory/memory_summarizer.py` | 独立 `ChatRecordDB.create` |
| `src/reports/work_outcome_review.py` | 独立 `ChatRecordDB.create` |
| `src/memory/mid_term.py` | `record_background_llm_usage` 双路径覆盖 |
| `src/services/sentiment_service.py` / `classification_service.py` / `case_matching_service.py` | `record_background_llm_usage` |
| `src/tools/llm/content_generate_tool.py` / `data_analysis/analysis_agent.py` | `record_background_llm_usage` |
