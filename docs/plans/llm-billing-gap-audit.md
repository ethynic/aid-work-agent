# LLM 调用计费缺失审计

> **审计日期**：2026-08-14
> **复核日期**：2026-08-14（逐点读源码核实，所有缺口均真实存在）
> **交叉核对日期**：2026-08-14（与上次计划 `plan-llm-billing-integration.md` 对比，发现本次审计仍有 5 处遗漏，已补入 §3.4）
> **审计方式**：全量代码走查 `grep -rn` LLM 调用点 + 逐点读函数上下文核实
> **结论**：项目 LLM 调用**未全部实现计费**，存在 **18 处真实缺口**（原 13 + 交叉核对新增 5）+ **2 处潜在风险** + **1 处连带 P1 bug**（followup_manager 同步调用 async 函数导致 LLM 实际从未执行）。

## 1. 计费架构（先厘清机制）

主计费载体是 `chat_records` 表：`SessionRecordService.add_llm_usage()` 累加 token -> `calculate_credit_cost()` 计算信用点费用 -> `ChatRecordDB.create()` 落库并扣减租户余额。

LLM token 用量共有三条计费通路：

| 通路 | 机制 | 覆盖范围 |
|------|------|---------|
| ① agent 主循环 | `agent.py:2700/3733` 的 `chat_with_tools` 后显式 `_record.add_llm_usage()` | 仅覆盖主循环**自身**的 LLM 调用 |
| ② 后台/工具服务显式计费 | `record_background_llm_usage()`（session_record.py）或独立 `_record_background_llm_billing()`（memory_summarizer / work_outcome_review） | 已接入的服务 |
| ③ `gateway.chat()` 内 `record_response_usage()` | 依赖 ContextVar `_recorder`（`src/services/llm_usage_meter.py:30`），仅调用方预先 `install_usage_recorder()` 才生效 | 目前**仅** `association_enrichment_ui` 显式安装，其余场景均为 no-op |

**关键陷阱**：`gateway.chat()` 内部的 `record_response_usage`（`src/llm/gateway.py:296-297` -> `src/services/llm_usage_meter.py:105-106`）是**装饰性**的，默认是 no-op，不能作为"已计费"的依据。真正落账靠通路①②显式调用。

**核实补注**：原文档说"`record_response_usage` 依赖 ContextVar `_recorder`"准确；机制实现位于 `src/services/llm_usage_meter.py`，调用入口为 `gateway.py:296`。`install_usage_recorder` 全仓仅 `association_enrichment_ui.py:440` 一处调用，证明确实为装饰性 no-op。

## 2. ✅ 已正确计费调用点（14 处）

| 调用点 | 计费方式 |
|--------|---------|
| agent 主循环 `chat_with_tools`（agent.py:2700/3733） | 显式 `add_llm_usage` |
| memory/mid_term 上下文压缩（mid_term.py:914/944） | `record_background_llm_usage` |
| memory/memory_summarizer | 独立 `_record_background_llm_billing` |
| reports/work_outcome_review | 独立 `_record_background_llm_billing` |
| services/case_matching_service | `record_background_llm_usage` |
| services/classification_service | `record_background_llm_usage` |
| services/sentiment_service | `record_background_llm_usage` |
| tools/llm/content_generate_tool | `record_background_llm_usage` |
| tools/data_analysis/analysis_agent | `record_background_llm_usage` |
| services/association_enrichment_providers / profile_extractor | `install_usage_recorder` 聚合到 run 级计数 |
| reports/summarizer -> generator | `_write_billing_chat_record` 落 chat_records |
| video_agent/prompt_engine | `_record_prompt_llm_usage` -> `ChatRecordDB.create`（source_type=video_prompt） |
| api/client_routes `/llm/chat` | `ClientUsageLogDB.record_llm_usage` 落 `client_usage_logs`（×5 扣减租户余额） |
| knowledge/service 上传向量化 + 文档摘要 | `_record_knowledge_embedding_billing` |
| travel-quote 检索 embedding（attraction/hotel retriever + search tool） | `add_embedding_usage` |

**核实补注**：上述 14 处全部经 grep 验证存在计费调用，归类准确。

## 3. ❌ 计费缺失（18 处真实缺口 = 原 13 + 交叉核对新增 5）

这些调用点每次调用都会消耗 token，但**完全不在 `chat_records`（或任何计费表）中**。

> §3.1-3.3 为本次审计初轮发现（13 处），§3.4 为与上次计划交叉核对后新发现（5 处）。

### 3.1 主循环工具内部发起的 LLM 调用（9 处）

运行在 agent 主循环的会话上下文内，但工具自身未计费；主循环的 `add_llm_usage`（通路①）**不覆盖**这些额外调用。

| 文件:行 | 场景 | 调用形式 | 核实结论 |
|---------|------|---------|---------|
| `src/tools/excel/excel_router.py:148` | Excel 内容生成路由 | `gateway.chat` 裸调用 | ✅ 真实缺失。`ExcelRouter.route()` 是 `async def`，调用正确，但无任何计费调用 |
| `src/tools/pdf/pdf_router.py:137` | PDF 生成路由 | `gateway.chat` 裸调用 | ✅ 真实缺失。`PdfRouter.route()` 同上 |
| `src/tools/word/word_router.py:131` | Word 生成路由 | `gateway.chat` 裸调用 | ✅ 真实缺失。`WordRouter.route()` 同上 |
| `src/tools/ppt/planner.py:93` | PPT 规划器 | `gateway.chat` 裸调用 | ✅ 真实缺失。`PPTPlanner._call_llm()` 同上 |
| `src/tools/file/write_tool.py:341` | 写文件内容生成 | `llm_gateway.chat` 裸调用 | ✅ 真实缺失。`_generate_content()` 是 `async def`，max_tokens=65536，单次消耗大 |
| `src/tools/browser/orchestrator.py:205` | 浏览器自动化决策 | `llm_gateway.chat` 裸调用 | ✅ 真实缺失。每步决策一次调用，循环多步累计消耗大 |
| `src/services/data_analysis/schema_extractor.py:142/189` | 数据模式提取/关系推断 | `gateway.chat` 裸调用（API 路由 + 主循环工具两条路径均缺失） | ✅ 真实缺失。两处均在 `async def` 中，两条调用路径：API 路由 `src/api/data_analysis.py:468/577/855`、主循环工具 `src/tools/data_analysis/upload_data_tool.py:85` |
| `src/skills/followup-tracking-1.0.0/scripts/followup_manager.py:364` | 跟进评估 | `llm_gateway.chat` 裸调用 | ✅ 真实缺失。**附带 P1 bug**：`_evaluate_with_llm()` 是同步 `def`，但 `llm_gateway.chat()` 是 `async def`，`result = llm_gateway.chat(...)` 返回 coroutine 对象，`result.get("content", "")` 必抛 `AttributeError`，被 `except Exception` 捕获后返回默认评分。**LLM 实际从未执行**，故当前并无 token 消耗。修复计费时必须同时修复 async 调用（详见 §5.1.8） |
| `src/skills/travel-quote/scripts/{attraction,hotel,vehicle}_excel_parser.py` | 行程 Excel 解析（×3 文件） | `gateway.chat` 裸调用 | ✅ 真实缺失。3 个文件各 1 处：`attraction_excel_parser.py:212`、`hotel_excel_parser.py:249`、`vehicle_excel_parser.py:151`，均在 `async def parse_sheet()` 中。max_tokens 高达 16384，单次解析消耗大 |

**计数说明**：本节按文件计 9 处，实际代码位点 11 处（schema_extractor 2 行 + travel-quote 3 文件）。原文档计数方式保留。

### 3.2 API 路由直接调用（3 处）

管理后台 API，无会话上下文，裸 `llm_gateway.chat`，无任何 usage 记录。

| 文件:行 | 场景 | 核实结论 |
|---------|------|---------|
| `src/api/agent_definition_sections.py:115` | 优化章节描述 | ✅ 真实缺失。`optimize_section()` 是 `async def` 路由，调用正确，无计费 |
| `src/api/admin_subagent.py:504` | AI 增强子智能体描述 | ✅ 真实缺失。同上 |
| `src/api/page_metadata.py:144` | 业务页面推荐 | ✅ 真实缺失。同上 |

**核实补注**：3 处 API 路由均通过 `_require_admin(request)` 鉴权，可从 `request.state.tenant_id` / `request.state.user_id`（由 `TenantContextMiddleware` 注入）拿到计费归属。

### 3.3 对话内 RAG 检索 embedding（1 处）

`src/knowledge/retriever/hybrid_retriever.py:99` - query 向量化 `embedding_client.embed(query)` 未接 `add_embedding_usage`。

三个调用方**全部缺失**：
- `src/tools/knowledge/knowledge_base_tool.py:107`（对话内知识库检索主路径）
- `src/tools/data_analysis/analysis_agent.py:688`
- `src/knowledge/service.py:677`

每次对话内检索都产生 embedding token 消耗（`text-embedding-v3`），完全未计入。

**核实补注**：
- 三个调用方均未在 `retriever.retrieve()` 之后读取 `embedding_client.last_usage_tokens` 并调用 `record.add_embedding_usage()`
- `embedding_client.last_usage_tokens` 字段存在（`embedding_client.py:52`），每次 `embed()` 调用后会累加
- 已有的参考实现：`src/skills/travel-quote/scripts/attraction_retriever.py:74-84`、`hotel_retriever.py`、`src/tools/knowledge/attraction_search_tool.py:95`、`hotel_search_tool.py:160` 共 4 处均使用相同模式（`client.reset_usage()` -> `_embed(query)` -> 读 `client.last_usage_tokens` -> `record.add_embedding_usage(tokens, model=client.model)`）
- 修复位置选择：**在 `hybrid_retriever.retrieve()` 内部修复一处即可覆盖三个调用方**，避免三处重复补丁

### 3.4 交叉核对发现的额外缺口（5 处）

> 本节为 2026-08-14 与上次计划 `plan-llm-billing-integration.md` 交叉核对后**新发现**的遗漏。本次审计 §3.1-3.3 仅按"工具内 LLM 调用 / API 路由 LLM / 对话内 RAG embedding"三类穷举，未覆盖以下 5 处。详见 §6 遗漏原因分析。

### 3.4.1 数据 schema 管理路径 embedding 计费缺失（3 处）

数据 schema 的 CRUD（创建/更新/重新向量化）路径调用了 `embedding_client.embed_batch` 或 `retriever._embed`，但**均未接 `add_embedding_usage` 或 `_record_knowledge_embedding_billing`**。

| 文件:行 | 场景 | 调用形式 | 核实结论 |
|---------|------|---------|---------|
| `src/services/data_analysis/schema_saver.py:78` | `save_schema_to_knowledge` 保存 schema 时向量化 | `embedding_client.embed_batch([schema_text])` 裸调用 | ✅ 真实缺失。两条调用路径均无计费：API 路由 `api/data_analysis.py:30` import、主循环工具 `tools/data_analysis/upload_data_tool.py:68` import |
| `src/api/data_analysis.py:742` | `update_schema` 路由更新 schema 后重新向量化 | `embedding_client.embed_batch([schema_text])` 裸调用 | ✅ 真实缺失。API 路由直接调用，无 SessionRecordService 也无独立落库 |
| `src/api/travel_quote.py:216` | `_update_chunk_embedding` 更新景点/酒店 chunk 文本后重新向量化 | `retriever._embed(text)` 裸调用 | ✅ 真实缺失。被 `travel_quote.py:300/549` 两处 API 路由调用（更新景点/酒店知识库 chunk）。`retriever._embed` 内部会累加 `last_usage_tokens`，但调用方未读取也未调 `add_embedding_usage` |

### 3.4.2 ASR 工具入口计费缺失（1 处，关键漏洞）

| 文件 | 场景 | 核实结论 |
|------|------|---------|
| `src/tools/asr/speech_to_text_tool.py`（`SpeechToTextTool.execute` 全函数） | agent 主循环调用 `speech_to_text` 工具时，工具内部 `_call_aliyun_asr` 实际调用阿里云 NLS，但**工具自身完全无 `add_asr_usage` 调用** | ✅ 真实缺失。上次计划 P2.4 仅在 `channel_routes.py:2174` 的微信语音消息路径补了 `record_service.add_asr_usage(calls=1)`，**只覆盖渠道侧路径**。当 agent 主循环通过工具调用 ASR（如用户在对话中上传音频文件让 agent 转文字）时，ASR 调用完全不计费。`channel_routes.py:415` 的 `_transcribe_voice_with_asr` 内部也调 `SpeechToTextTool.execute`，但计费在 `channel_routes.py:2174` 外层补，工具自身仍无计费--这意味着任何"非 channel_routes 路径"调用 `SpeechToTextTool.execute` 都会漏计 |

### 3.4.3 离线运维脚本 embedding 计费缺失（1 处，低优先级）

| 文件:行 | 场景 | 核实结论 |
|---------|------|---------|
| `src/skills/travel-quote/scripts/backfill_hotel_info_name.py:107` | 酒店 info name 回填运维脚本 | ✅ 真实缺失。`retriever._embed(new_text)` 裸调用。低频运维路径，但应记录归属租户便于审计 |

### 3.4.4 排除项（核实后确认无需计费）

| 文件:行 | 场景 | 排除原因 |
|---------|------|---------|
| `src/tools/browser/semantic/natural_matcher.py:156` | 浏览器语义匹配 `_embedding_similarity` 调 `_get_embedding` | 使用本地 `sentence-transformers` 模型（`paraphrase-multilingual-MiniLM-L12-v2`），**不调用云端 embedding API**，不产生 token 消费 |

## 4. ⚠️ 存疑 / 潜在风险（2 处）

| 调用点 | 问题 | 现状 |
|--------|------|------|
| `src/core/intent_engine.py:297` | LLM 意图识别裸调用无计费 | `recognize()` 当前**无任何外部调用点**（`grep -rn "recognize\b"` 仅命中 `intent_engine.py:169` 自身定义；`planner.py:13` 仅 import 类型 + `planner.py:61` 调用 `intent_engine.get_intent_info(intent)` 不触发 LLM），是未启用的死路径。若未来在主循环启用需补计费 |
| `gateway.stream_chat()`（gateway.py:314 / failover.py:526） | 流式接口只 yield chunk，**不收集 usage**，无法计费 | `grep -rn "stream_chat"` 仅命中 `gateway.py`/`failover.py`/`providers/*.py` 中的定义与内部转发，无任何业务调用点。若未来启用流式接口（SSE）必须补 usage 汇总逻辑 |

**核实补注**：两处风险点确认仍为未启用状态，文档说法准确。

## 5. 修复方案（完善版）

### 5.1 3.1 节工具内 LLM 调用（9 处文件 / 11 处代码位）

**统一修复模式**：在每个工具的 `gateway.chat` 调用后，立即补一行 `record_background_llm_usage(response.get("usage"))`。`record_background_llm_usage`（`src/services/session_record.py:462`）已实现兜底：
- 当前线程有 `SessionRecordService`（主循环内调用） -> 累加到当前 record
- 无 `SessionRecordService`（background_runner 调度） -> 独立落 `chat_records`（source_type=background_llm）

**逐处修复要点**：

#### 5.1.1 `src/tools/excel/excel_router.py:148`
```python
response = await gateway.chat(...)
# 补：
from src.services.session_record import record_background_llm_usage
record_background_llm_usage(response.get("usage") if isinstance(response, dict) else None)
```

#### 5.1.2 `src/tools/pdf/pdf_router.py:137` / 5.1.3 `src/tools/word/word_router.py:131` / 5.1.4 `src/tools/ppt/planner.py:93`
同 5.1.1 模式，在 `gateway.chat` 调用后补 `record_background_llm_usage`。

#### 5.1.5 `src/tools/file/write_tool.py:341`
同 5.1.1 模式。注意此处 `max_tokens=65536`，单次消耗大，修复优先级高。

#### 5.1.6 `src/tools/browser/orchestrator.py:205`
同 5.1.1 模式。浏览器自动化多步循环，累计消耗大。

#### 5.1.7 `src/services/data_analysis/schema_extractor.py:142/189`
两处均在 `async def` 中，分别补 `record_background_llm_usage`。注意两条调用路径：
- API 路由（`api/data_analysis.py:468/577/855`）：无 SessionRecordService，会走兜底独立落库分支（source_type=background_llm），但 `tenant_id`/`user_id` 默认 None，**无法归属租户**。建议在 `extract_schema`/`infer_relations` 方法签名增加 `tenant_id`/`user_id` 可选参数，由 API 路由从 `request.state` 透传，再传入 `record_background_llm_usage(..., tenant_id=..., user_id=..., source="schema_extract")`
- 主循环工具（`upload_data_tool.py:85`）：有 SessionRecordService，累加到当前 record，无需额外参数

#### 5.1.8 `src/skills/followup-tracking-1.0.0/scripts/followup_manager.py:364`（附带 P1 bug 修复）

**P1 bug 修复**：`_evaluate_with_llm` 是同步 `def`，但 `llm_gateway.chat` 是 `async def`，必须用 `asyncio.run()` 包裹：

```python
def _evaluate_with_llm(record: dict) -> tuple:
    import asyncio
    from src.llm.gateway import llm_gateway
    from src.services.session_record import record_background_llm_usage

    try:
        result = asyncio.run(llm_gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        ))
        # 补计费：
        record_background_llm_usage(
            result.get("usage") if isinstance(result, dict) else None,
            source="followup_evaluate",
        )
        content = result.get("content", "")
        # ... 后续解析不变
    except Exception as e:
        ...
```

**注意**：`asyncio.run()` 在已有事件循环的上下文（如 FastAPI 路由内）会抛 `RuntimeError`。但 `followup_manager` 是命令行脚本（`python scripts/followup_manager.py evaluate-quality`），主线程无事件循环，`asyncio.run` 安全。

**计费兜底**：`followup_manager` 是独立进程，无 `SessionRecordService`，`record_background_llm_usage` 会走兜底独立落库分支。但当前 `record_background_llm_usage` 的兜底分支**不接受 `tenant_id`/`user_id`**（默认 None），会导致计费无法归属。修复时需要：
1. `cmd_evaluate_quality` / `cmd_batch_evaluate` 从 `bs_customer_followup_records` 查询时同时取 `tenant_id`/`user_id` 字段
2. 透传给 `_evaluate_with_llm`
3. 再传入 `record_background_llm_usage(..., tenant_id=..., user_id=..., source="followup_evaluate")`

#### 5.1.9 `src/skills/travel-quote/scripts/{attraction,hotel,vehicle}_excel_parser.py`（3 文件）
3 处均在 `async def parse_sheet()` 中，分别补 `record_background_llm_usage`。这三个 skill 在主循环内调用，会累加到当前 record。

### 5.2 3.2 节 API 路由（3 处）

**统一修复模式**：用独立 `ChatRecordDB.create(source_type="admin_llm_ops")` 落账，从 `request.state` 取 `tenant_id`/`user_id`：

```python
from src.db.models import ChatRecordDB
from src.services.billing import calculate_credit_cost

async def optimize_section(request: Request, ...):
    ...
    result = await llm_gateway.chat(...)

    # 补计费：
    try:
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        credit_cost = calculate_credit_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=settings.llm.model,
        )
        ChatRecordDB.create(
            session_id=f"admin_llm_ops_{request.state.user_id}_{int(time.time())}",
            tenant_id=request.state.tenant_id,
            user_id=request.state.user_id,
            user_message=f"[管理后台] {section_key} 优化",
            assistant_message=None,
            total_token_count=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=settings.llm.model,
            provider=settings.llm.provider,
            source_type="admin_llm_ops",
            credit_cost=credit_cost,
            status="completed",
        )
    except Exception as billing_err:
        logger.error(f"管理后台 LLM 计费落库失败: {billing_err}", exc_info=True)
```

3 处修复点：
- `src/api/agent_definition_sections.py:115` -> `optimize_section`
- `src/api/admin_subagent.py:504` -> AI 增强子智能体描述
- `src/api/page_metadata.py:144` -> `recommend_pages`

**建议**：把上述计费逻辑封装为 `src/services/session_record.py::record_admin_llm_usage(response, request, source_label)` 工具函数，避免三处重复代码。

### 5.3 3.3 节 RAG 检索 embedding（1 处修复覆盖 3 个调用方）

**修复位置**：`src/knowledge/retriever/hybrid_retriever.py:99`，在 `embedding_client.embed(query)` 之后立即补计费。**一处修复覆盖全部三个调用方**：

```python
async def retrieve(self, query: str, top_k: int = 10, ...):
    # 1. 向量检索（语义相似度，权重更高）
    query_embedding = await self.embedding_client.embed(query)

    # 补：累加 embedding usage 到当前 SessionRecordService
    if getattr(self.embedding_client, "last_usage_tokens", 0) > 0:
        try:
            from src.services.session_record import SessionRecordManager
            record = SessionRecordManager.get_current_record()
            if record:
                record.add_embedding_usage(
                    self.embedding_client.last_usage_tokens,
                    model=getattr(self.embedding_client, "model", "text-embedding-v3"),
                )
        except Exception:
            logger.debug("Failed to record embedding usage", exc_info=True)

    raw_vector_results = await self.vector_db.search(...)
    # ... 后续不变
```

**注意**：
- `add_embedding_usage` 仅在当前线程有 `SessionRecordService` 时累加，无则丢弃。三个调用方都在主循环或 API 路由内有 SessionRecordService（除 `knowledge/service.py:677` 是知识库后台检索，可能无 SessionRecordService -- 此时 embedding 消耗已包含在 `_record_knowledge_embedding_billing` 的离线计费中，**不重复计费**，需在修复后验证不双计）
- 与 travel-quote retriever 的写法完全一致，已验证可行

### 5.4 gateway 层兜底（可选，建议审慎）

原文档建议："在 `gateway.chat()` 中将 `record_response_usage` 的失效路径改为'无 recorder 时也尝试累加到 `SessionRecordManager.get_current_record()`'，统一兜底所有漏网调用"。

**复核结论**：**不建议**作为短期修复手段，原因：
1. `record_response_usage` 当前是 ContextVar 驱动的装饰性 no-op，若改为自动累加到 SessionRecordService，会让所有调用 `gateway.chat` 的代码都自动计费 -- 包括已显式计费的代码（如 `record_background_llm_usage` 调用方），导致**双计**
2. `record_background_llm_usage` 内部已调用 `record.add_llm_usage`，再加自动累加会重复
3. 不同调用场景的 `source_type` 归属不同（admin_llm_ops / background_llm / video_prompt 等），自动累加无法区分
4. 修复 13 处显式缺口后，剩余遗漏面已经很窄，不值得引入双计风险

**替代方案**：保留 `record_response_usage` 的装饰性语义；在 5.1-5.3 显式修复完成后，给 `gateway.chat` 加一行 `logger.debug` 打印未安装 recorder 的调用栈，便于后续审计漏网点：

```python
# src/llm/gateway.py:296 附近
from src.services.llm_usage_meter import record_response_usage, _recorder
if _recorder.get() is None:
    logger.debug(f"[billing-gap] gateway.chat called without usage recorder; "
                 f"usage={result.get('usage')}, stack via logger.trace")
record_response_usage(result)
```

### 5.5 第 4 节潜在风险的处理

- **`intent_engine.recognize`**：当前无调用点，**不修**。在函数 docstring 顶部加注释 `# TODO(billing): 启用前需补 record_background_llm_usage`，避免未来启用时遗漏
- **`gateway.stream_chat`**：当前无业务调用点，**不修**。在 `gateway.py:314` 函数 docstring 顶部加注释 `# TODO(billing): 启用流式前需在 chunk 累积 loop 末尾收集 usage 并调用 record_background_llm_usage`

### 5.6 §3.4 交叉核对新增缺口的修复方案

#### 5.6.1 `schema_saver.save_schema_to_knowledge` + `api/data_analysis.update_schema`（2 处 embedding）

`schema_saver.py:78` 被 API 路由 + 主循环工具两条路径调用，统一在 `save_schema_to_knowledge` 内部补计费即可覆盖两条路径：

```python
# src/services/data_analysis/schema_saver.py
async def save_schema_to_knowledge(...):
    ...
    embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
    embedding_client.reset_usage()  # 补
    embeddings = await embedding_client.embed_batch([schema_text])

    # 补计费：
    emb_tokens = getattr(embedding_client, "last_usage_tokens", 0)
    if emb_tokens > 0:
        try:
            from src.services.session_record import (
                SessionRecordManager, record_background_llm_usage
            )
            record = SessionRecordManager.get_current_record()
            if record:
                record.add_embedding_usage(emb_tokens, model=embedding_client.model)
            else:
                # API 路由无 SessionRecordService，独立落库
                # 复用 _record_knowledge_embedding_billing 模式
                from src.knowledge.service import _record_knowledge_embedding_billing  # 或抽公共函数
                ...
        except Exception:
            logger.debug("Failed to record schema embedding usage", exc_info=True)
```

`api/data_analysis.py:742` 的 `update_schema` 直接调 `embed_batch`，建议复用同一计费模式（或抽出 `record_admin_embedding_usage(embedding_client, request, source_label)` 工具函数）。

#### 5.6.2 `api/travel_quote._update_chunk_embedding`（1 处 embedding）

`_update_chunk_embedding` 是同步 `def`，被两处 API 路由调用。修复模式：

```python
def _update_chunk_embedding(doc_id: int, chunk_index: int, text: str) -> None:
    ...
    retriever = AttractionRetriever()
    client = retriever._get_embedding_client()
    client.reset_usage()
    embedding = retriever._embed(text)
    # 补计费（API 路由无 SessionRecordService，独立落库）：
    emb_tokens = client.last_usage_tokens
    if emb_tokens > 0:
        # 用 record_admin_llm_usage 同款模式落 chat_records（source_type=admin_embedding_ops）
        ...
```

#### 5.6.3 `speech_to_text_tool.SpeechToTextTool.execute`（1 处 ASR，关键漏洞）

**修复位置**：在 `SpeechToTextTool.execute` 的 `_call_aliyun_asr` 调用**成功后**（`status_code == 20000000` 分支内）补 `add_asr_usage`：

```python
# src/tools/asr/speech_to_text_tool.py:345 附近
if status_code == 20000000:
    text = result_json.get("result", "")
    # 补计费：ASR 调用成功后累加到当前 SessionRecordService
    try:
        from src.services.session_record import SessionRecordManager
        record = SessionRecordManager.get_current_record()
        if record:
            record.add_asr_usage(calls=1)
    except Exception:
        logger.debug("Failed to record ASR usage", exc_info=True)
    return {"success": True, "text": text, ...}
```

**关键考虑**：
- ASR 工具被 `channel_routes.py:415`（渠道侧已在外层 `add_asr_usage`）和 agent 主循环（无外层计费）两条路径调用
- 若在工具内部统一计费，`channel_routes.py:2174` 的外层 `add_asr_usage(calls=1)` **必须删除**，否则双计
- 修复后验证：渠道侧语音消息单次 ASR 只累加 1 次（不是 2 次），agent 工具调用 ASR 也累加 1 次

#### 5.6.4 `backfill_hotel_info_name.py`（1 处 embedding，低优先级）

运维脚本，单次运行。建议在该脚本调用 `retriever._embed` 后用独立 `ChatRecordDB.create(source_type='background_embedding')` 落账，归属租户从脚本参数或环境变量传入。低优先级，可与 §5.6.1-5.6.3 一起修。

### 5.7 修复优先级与排期建议

| 优先级 | 缺口 | 修复理由 |
|--------|------|---------|
| P0 | 5.1.5 `write_tool` / 5.1.6 `browser orchestrator` / 5.1.9 `travel-quote 3 个 excel_parser` | 单次 token 消耗大（max_tokens 65536/16384），且为高频对话路径 |
| P0 | 5.1.8 `followup_manager` | 既有计费缺口又有 P1 bug（LLM 实际未执行），修复一举两得 |
| P0 | 5.6.3 `speech_to_text_tool` ASR | 工具入口计费完全缺失，agent 调用 ASR 100% 漏计；修复时需同步删除 `channel_routes.py:2174` 外层计费避免双计 |
| P1 | 5.1.1-5.1.4 `excel/pdf/word/ppt router+planner` / 5.1.7 `schema_extractor` | 文档生成路由高频，schema 提取影响数据分析准确性 |
| P1 | 5.3 `hybrid_retriever embedding` / 5.6.1 `schema_saver embedding` / 5.6.2 `_update_chunk_embedding` | 对话内 RAG 检索 + 数据 schema 管理主路径，每次都消耗 |
| P2 | 5.2 `3 处 API 路由` / 5.6.4 `backfill_hotel_info_name` | 管理后台低频操作 / 运维脚本，应归属租户便于审计 |

### 5.8 验证清单

修复完成后，按以下步骤验证：

1. **基线对比**：修复前先跑一轮回归对话（覆盖 Excel/PDF/Word/PPT 生成、浏览器、知识库检索、数据分析），记录 `chat_records.total_token_count` 基线
2. **修复后回归**：同样对话再跑一轮，对比 `total_token_count` 是否提升（提升幅度 ≈ 工具内 LLM 调用消耗）
3. **不双计验证**：检查 `usage_breakdown` JSON 字段，确认 `chat.calls` 与实际 LLM 调用次数一致，未出现翻倍
4. **租户归属验证**：管理后台触发 3 处 API 路由，确认 `chat_records.tenant_id`/`user_id` 不为 NULL
5. **followup_manager 端到端验证**：执行 `python scripts/followup_manager.py evaluate-quality --record-id XXX`，确认 LLM 真正调用（评分非默认 5 分），且 `chat_records` 落库（source_type=background_llm）
6. **embedding 计费验证**：对话内触发知识库检索，确认 `usage_breakdown.embedding.tokens > 0`；离线上传文档向量化，确认不双计（`source_type=knowledge_embedding` 单独落账，对话内 `embedding` 累加到 `usage_breakdown`）
7. **ASR 不双计验证**（§5.6.3 修复后）：渠道侧发送一条微信语音消息，确认 `chat_records.asr_calls = 1`（不是 2）；对话内让 agent 调用 `speech_to_text` 工具转写一段音频，确认 `asr_calls = 1`
8. **数据 schema 管理路径验证**（§5.6.1-5.6.2 修复后）：管理后台创建/更新数据 schema，确认 `chat_records` 落账（source_type=admin_embedding_ops 或累加到 usage_breakdown.embedding）；更新景点/酒店知识库 chunk，确认同样落账

## 6. usage_breakdown 分项单价追溯增强

> **背景**：`token_cost_prices` 是 DB 表（非 yaml 配置），运营可随时在管理后台调整各类模型的输入/输出/缓存/embedding/ASR/视频单价。当前 `chat_records` 只记录 token 用量与总积分消耗，**没有分项单价和分项积分**，导致历史记录无法追溯"写入时实际使用了哪个单价"，对账困难。
>
> **目标**：在 `chat_records.usage_breakdown`（jsonb）中为 6 个分项计费分别记录**单价 + 分项积分**，使每条记录都能独立对账，不再依赖当前 `token_cost_prices` 表的快照。
>
> **6 个分项**：未命中缓存输入 token / 命中缓存输入 token / 输出 token / 向量模型 token / ASR 次数 / 视频模型时长。

### 6.1 现状分析

#### 6.1.1 chat_records 计费相关字段（`deploy/init-postgres.sql:96-112`）

| 字段 | 类型 | 含义 |
|------|------|------|
| `total_token_count` | INTEGER | 总 token 数（prompt + completion） |
| `prompt_tokens` | INTEGER | 输入 token（含缓存命中部分） |
| `cached_input_tokens` | INTEGER | 命中缓存的输入 token |
| `completion_tokens` | INTEGER | 输出 token |
| `embedding_tokens` | INTEGER | 向量模型 token |
| `asr_calls` | INTEGER | ASR 调用次数 |
| `credit_cost` | NUMERIC(12,2) | **总**积分消耗（6 分项之和） |
| `usage_breakdown` | JSONB | 分项明细（当前结构见下） |
| `source_type` | TEXT | 来源（chat / wecom / background_llm / video_gen / video_prompt / knowledge_embedding 等） |

#### 6.1.2 usage_breakdown 当前结构（`session_record.py:357-370` + `knowledge/service.py:155-170`）

```json
{
  "chat": {
    "prompt_tokens": 100,
    "cached_input_tokens": 20,
    "completion_tokens": 50,
    "total_tokens": 150,
    "model": "qwen-plus",
    "credit": 0.17
  },
  "embedding": {"tokens": 1000, "calls": 1, "model": "text-embedding-v3", "credit": 0.0005},
  "asr": {"calls": 1, "model": "aliyun-nls-asr", "credit": 0.01}
}
```

**问题**：
- `chat.credit` 是 3 个分项（未命中缓存输入 + 命中缓存输入 + 输出）的合计，**未拆分**
- 所有分项**未记录写入时的单价**，单价后续若调整，无法核对历史积分是否正确
- **视频生成**（source_type=video_gen）写 `chat_records` 时**完全未传 usage_breakdown**（`video_agent/service.py:847-863`），仅靠 `execution_details` 字段存元数据，无法用统一 SQL 对账

#### 6.1.3 单价来源

| 单价 | 存储位置 | 字段 | 单位 |
|------|---------|------|------|
| 未命中缓存输入 | `token_cost_prices` | `input_price_per_m` | 元/百万 token |
| 命中缓存输入 | `token_cost_prices` | `cached_input_price_per_m` | 元/百万 token |
| 输出 | `token_cost_prices` | `output_price_per_m` | 元/百万 token |
| 向量模型 | `token_cost_prices` | `embedding_price_per_m` | 元/百万 token |
| ASR | `token_cost_prices` | `asr_price_per_call` | 元/次 |
| 视频 | `token_cost_prices` | `price_per_second` / `price_per_second_by_resolution` | 元/秒 |

`token_cost_prices` 是 DB 表，运营可在管理后台调整。`calculate_credit_cost`（`billing.py:27`）等函数每次调用都通过 `TokenCostPriceDB.get_by_model_name(model)` 实时读取单价。

#### 6.1.4 系数字段

`configs/config.yaml:268-272` 配置了 4 个系数（元 -> 积分的换算系数）：

| 系数 | 默认值 | 用途 |
|------|--------|------|
| `billing.usage_factor` | 100 | LLM token 计费系数 |
| `billing.video_gen_usage_factor` | 33 | 视频模型计费系数 |
| `billing.embedding_usage_factor` | 100 | 向量模型计费系数 |
| `billing.asr_usage_factor` | 100 | ASR 计费系数 |

公式：`分项积分 = 用量 × 单价 × 系数`（token 类还需 `/ 1_000_000`）。系数**也需要记录到 usage_breakdown**，因为运营可能调整。

### 6.2 设计方案

#### 6.2.1 完整 usage_breakdown 结构（增强后）

```json
{
  "chat": {
    "model": "qwen-plus",
    "prompt_tokens": 100,
    "cached_input_tokens": 20,
    "completion_tokens": 50,
    "total_tokens": 150,
    "non_cached_input_tokens": 80,
    "unit_prices": {
      "input_per_m": 0.8,
      "cached_input_per_m": 0.16,
      "output_per_m": 2.0
    },
    "usage_factor": 100,
    "credits": {
      "non_cached_input": 0.064,
      "cached_input": 0.0032,
      "output": 0.1
    },
    "credit": 0.17
  },
  "embedding": {
    "model": "text-embedding-v3",
    "tokens": 1000,
    "calls": 1,
    "unit_price_per_m": 0.5,
    "usage_factor": 100,
    "credit": 0.0005
  },
  "asr": {
    "model": "aliyun-nls-asr",
    "calls": 1,
    "unit_price_per_call": 0.01,
    "usage_factor": 100,
    "credit": 0.01
  },
  "video": {
    "model": "wan2.7-r2v",
    "duration_seconds": 5.0,
    "resolution": "720P",
    "unit_price_per_second": 0.6,
    "usage_factor": 33,
    "credit": 0.99
  }
}
```

> `summary_llm`（离线文档摘要，`knowledge/service.py:155-170`）同步升级为与 `chat` 相同的结构。

#### 6.2.2 6 个分项字段定义

| 分项 | 单价字段 | 单位 | 用量字段 | 分项积分字段 | 计算公式 |
|------|---------|------|---------|-------------|---------|
| 未命中缓存输入 | `chat.unit_prices.input_per_m` | 元/百万 token | `chat.non_cached_input_tokens` | `chat.credits.non_cached_input` | `tokens × input_per_m / 1e6 × usage_factor` |
| 命中缓存输入 | `chat.unit_prices.cached_input_per_m` | 元/百万 token | `chat.cached_input_tokens` | `chat.credits.cached_input` | `tokens × cached_input_per_m / 1e6 × usage_factor` |
| 输出 | `chat.unit_prices.output_per_m` | 元/百万 token | `chat.completion_tokens` | `chat.credits.output` | `tokens × output_per_m / 1e6 × usage_factor` |
| 向量模型 | `embedding.unit_price_per_m` | 元/百万 token | `embedding.tokens` | `embedding.credit` | `tokens × unit_price_per_m / 1e6 × usage_factor` |
| ASR | `asr.unit_price_per_call` | 元/次 | `asr.calls` | `asr.credit` | `calls × unit_price_per_call × usage_factor` |
| 视频模型 | `video.unit_price_per_second` | 元/秒 | `video.duration_seconds` | `video.credit` | `seconds × unit_price_per_second × usage_factor` |

#### 6.2.3 关键设计决策

1. **单价单位保持原始表单位**（per_m / per_call / per_second），不换算为"元/token"--便于与 `token_cost_prices` 表直接对照核对
2. **分项积分保留 6 位小数**（`round(..., 6)`），总积分仍按 `math.ceil(x × 100) / 100` 取 2 位小数，避免分项求和与总积分的舍入误差引起对账告警
3. **系数 `usage_factor` 写入每个分项**，防止后续系数调整后历史记录无法复算
4. **不新增表字段**：`usage_breakdown` 是 jsonb 自由结构，无需 ALTER TABLE；旧记录无新字段时按 NULL/0 兜底
5. **视频新增 `usage_breakdown.video`**：当前视频记录完全无 breakdown，本次补齐

### 6.3 实施步骤

#### 6.3.1 `src/services/billing.py` 新增带 breakdown 的计算函数

为 4 个计费函数各新增一个 `*_with_breakdown` 版本，返回 `(credit_cost, breakdown_dict)`：

```python
# src/services/billing.py
def calculate_credit_cost_with_breakdown(
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
    cached_input_tokens: int = 0,
    usage_factor_override: Optional[int] = None,
) -> tuple[float, dict]:
    """返回 (credit_cost, breakdown)，breakdown 含分项单价与分项积分"""
    tcp = TokenCostPriceDB.get_by_model_name(model) or {}
    input_price = tcp.get("input_price_per_m") or 0.0
    cached_input_price = tcp.get("cached_input_price_per_m")  # 可能为 None
    output_price = tcp.get("output_price_per_m") or 0.0

    non_cached_input_tokens = max(prompt_tokens - cached_input_tokens, 0)
    non_cached_input_cost = non_cached_input_tokens * input_price / 1_000_000
    cached_input_cost = (
        cached_input_tokens * cached_input_price / 1_000_000
        if cached_input_price is not None else 0.0
    )
    output_cost = completion_tokens * output_price / 1_000_000
    token_cost = non_cached_input_cost + cached_input_cost + output_cost

    usage_factor = usage_factor_override or settings.billing.usage_factor
    credit_cost = math.ceil(token_cost * usage_factor * 100) / 100

    breakdown = {
        "non_cached_input_tokens": non_cached_input_tokens,
        "unit_prices": {
            "input_per_m": input_price,
            "cached_input_per_m": cached_input_price,
            "output_per_m": output_price,
        },
        "usage_factor": usage_factor,
        "credits": {
            "non_cached_input": round(non_cached_input_cost * usage_factor, 6),
            "cached_input": round(cached_input_cost * usage_factor, 6),
            "output": round(output_cost * usage_factor, 6),
        },
    }
    return credit_cost, breakdown
```

同样为 `calculate_embedding_credit_cost` / `calculate_asr_credit_cost` / `calculate_video_credit_cost` 新增 `*_with_breakdown` 版本，返回结构对齐 §6.2.2。

**保留原函数**：现有 `calculate_credit_cost` 等不改签名（被 `client_routes.py`、`video_agent/service.py` 等多处调用），内部改为调用 `*_with_breakdown` 后丢弃 breakdown，避免重复实现。

#### 6.3.2 `src/services/session_record.py` `_finalize_record` 写入分项单价

`session_record.py:330-370` 的计费计算改为调用 `*_with_breakdown`，并把 breakdown 合并到 `usage_breakdown`：

```python
# session_record.py:330 附近
chat_credit_cost, chat_bd = calculate_credit_cost_with_breakdown(
    prompt_tokens=self.prompt_tokens,
    completion_tokens=self.completion_tokens,
    model=self.model,
    cached_input_tokens=self.cached_input_tokens,
)
embedding_credit_cost, emb_bd = calculate_embedding_credit_cost_with_breakdown(
    embedding_tokens=self.embedding_tokens,
)
asr_credit_cost, asr_bd = calculate_asr_credit_cost_with_breakdown(asr_calls=self.asr_calls)

credit_cost = round(chat_credit_cost + embedding_credit_cost + asr_credit_cost, 2)

usage_breakdown: Dict[str, Any] = dict(self.usage_breakdown)
usage_breakdown.setdefault("chat", {
    "prompt_tokens": self.prompt_tokens,
    "completion_tokens": self.completion_tokens,
    "cached_input_tokens": self.cached_input_tokens,
    "total_tokens": self.total_token_count,
    "non_cached_input_tokens": chat_bd["non_cached_input_tokens"],
    "model": self.model,
    "unit_prices": chat_bd["unit_prices"],
    "usage_factor": chat_bd["usage_factor"],
    "credits": chat_bd["credits"],
    "credit": round(chat_credit_cost, 2),
})
if self.embedding_tokens > 0 and "embedding" in usage_breakdown:
    usage_breakdown["embedding"].update({
        "unit_price_per_m": emb_bd["unit_price_per_m"],
        "usage_factor": emb_bd["usage_factor"],
    })
    usage_breakdown["embedding"]["credit"] = round(embedding_credit_cost, 2)
if self.asr_calls > 0 and "asr" in usage_breakdown:
    usage_breakdown["asr"].update({
        "unit_price_per_call": asr_bd["unit_price_per_call"],
        "usage_factor": asr_bd["usage_factor"],
    })
    usage_breakdown["asr"]["credit"] = round(asr_credit_cost, 2)
```

#### 6.3.3 `src/video_agent/service.py` 视频计费路径写入 `usage_breakdown.video`

`video_agent/service.py:847-863` 的 `_write_chat_record` 增加从 `execution_details` 提取单价/时长/分辨率，构造 `usage_breakdown["video"]`：

```python
# video_agent/service.py:_write_chat_record
cost_per_second = execution_details.get("cost_per_second_yuan", 0.0)
duration_seconds = execution_details.get("duration_seconds", 0.0)
resolution = execution_details.get("resolution")
video_breakdown = {
    "model": model,
    "duration_seconds": duration_seconds,
    "resolution": resolution,
    "unit_price_per_second": cost_per_second,
    "usage_factor": settings.billing.video_gen_usage_factor,
    "credit": round(credit_cost, 2),
}

record = ChatRecordDB.create(
    session_id=session_id,
    tenant_id=tenant_id,
    user_id=user_id,
    model=model,
    provider=provider,
    execution_details=execution_details,
    usage_breakdown={"video": video_breakdown},
    status=status,
    source_type="video_gen",
    credit_cost=credit_cost,
)
```

**视频提示词**（`source_type=video_prompt`，`service.py:713-749`）同步用 `calculate_credit_cost_with_breakdown` 写入 `usage_breakdown.chat`（与对话 LLM 路径一致）。

#### 6.3.4 `src/knowledge/service.py` 离线向量化路径同步升级

`knowledge/service.py:155-170` 的离线计费写入（`summary_llm` + `embedding`），同步加 `unit_prices` / `unit_price_per_m` / `usage_factor` / `credits` 字段，与主路径格式对齐，避免对账 SQL 出现两套结构。

#### 6.3.5 管理后台 API 路由（§5.2 修复时一并升级）

§5.2 的 3 处管理后台 LLM 路由（`agent_definition_sections.py:115` / `admin_subagent.py:504` / `page_metadata.py:144`）落 `ChatRecordDB.create(source_type=admin_llm_ops)` 时，**同步调用 `calculate_credit_cost_with_breakdown` 写入 `usage_breakdown.chat`**，避免新增"无单价"的 admin_llm_ops 记录。

#### 6.3.6 数据库变更

**无需 ALTER TABLE**（`usage_breakdown` 是 jsonb 自由结构）。

在 `deploy/db_update.sql` 末尾追加注释条目，登记结构升级：

```sql
-- 2026-08-14，chat_records.usage_breakdown 结构升级：6 个分项（chat/embedding/asr/video）
--   增加 unit_prices / unit_price_per_m / unit_price_per_call / unit_price_per_second 单价字段，
--   chat 增加 credits 嵌套对象（non_cached_input / cached_input / output 三分项积分），
--   各分项增加 usage_factor 系数字段，video 分项为新增（原视频记录无 usage_breakdown）。
--   旧记录无这些字段，读取时按 NULL/0 兜底；不涉及表结构变更。
```

### 6.4 兼容性

| 场景 | 处理 |
|------|------|
| 旧 `chat_records` 记录（无 unit_prices） | 读取时按 NULL 处理，对账 SQL 用 `COALESCE(usage_breakdown->'chat'->'unit_prices'->>'input_per_m', '0')` 兜底 |
| 新记录写入 | 必须填齐 6 个分项的单价和分项积分，否则视为实现缺陷 |
| 单价后续调整 | 历史记录保持写入时的单价快照，新记录反映新单价 |
| 系数后续调整 | 历史记录保持写入时的 `usage_factor`，新记录反映新系数 |
| 对账 SQL | 分项求和与 `credit_cost` 字段容差 < 0.01（因 `math.ceil(x × 100) / 100` 取整） |

### 6.5 验证清单

1. **结构验证**：写入新记录后，`SELECT usage_breakdown FROM chat_records WHERE id = ?` 包含 6 个分项的单价和分项积分字段
2. **单价快照验证**：在管理后台调整 `token_cost_prices` 的 `qwen-plus.input_price_per_m` 后，新记录的 `usage_breakdown.chat.unit_prices.input_per_m` 反映新值，旧记录保持旧值
3. **分项求和验证**：`credits.non_cached_input + credits.cached_input + credits.output` ≈ `chat.credit`（容差 < 0.01）
4. **总积分验证**：`chat.credit + embedding.credit + asr.credit + video.credit` ≈ `chat_records.credit_cost`（容差 < 0.01）
5. **视频记录验证**：视频生成成功后，`SELECT usage_breakdown->'video' FROM chat_records WHERE source_type='video_gen'` 返回含 `duration_seconds / resolution / unit_price_per_second / usage_factor / credit` 的完整对象
6. **离线向量化验证**：上传文档向量化后，`usage_breakdown.summary_llm` / `usage_breakdown.embedding` 含单价字段，与主路径结构一致
7. **管理后台 LLM 验证**：触发 3 处管理后台 LLM 路由后，`source_type=admin_llm_ops` 记录的 `usage_breakdown.chat` 含单价字段
8. **旧记录兼容验证**：查询旧记录的 `usage_breakdown` 不报错，对账 SQL 用 COALESCE 兜底后能正常返回 0 或旧 credit

### 6.6 排期建议

| 优先级 | 内容 | 理由 |
|--------|------|------|
| P0 | §6.3.1 billing.py 新增 `*_with_breakdown` 函数 + §6.3.2 session_record.py 升级 | 主路径写入，影响所有新 `chat_records` 记录 |
| P1 | §6.3.3 视频计费路径补 `usage_breakdown.video` + §6.3.4 离线向量化同步升级 | 补齐缺失分项，统一格式 |
| P1 | §6.3.5 管理后台 LLM 路由（与 §5.2 修复同步） | 避免新增无单价记录 |
| P2 | §6.3.6 db_update.sql 注释登记 | 文档登记，无功能影响 |

**建议与 §5 修复方案同批次实施**：§5.1-5.6 修复计费缺口时会改写计费调用点，此时一并接入 `*_with_breakdown` 函数，避免两次改动计费代码。

### 6.7 关联代码

| 模块 | 文件 | 说明 |
|------|------|------|
| 计费核心 | `src/services/billing.py:27-230` | `calculate_credit_cost` 等 4 个函数，新增 `*_with_breakdown` 版本 |
| 主路径写入 | `src/services/session_record.py:330-385` | `_finalize_record` 写入 `usage_breakdown` |
| 离线向量化写入 | `src/knowledge/service.py:155-170` | 同步升级 `summary_llm` / `embedding` 结构 |
| 视频生成写入 | `src/video_agent/service.py:847-863` | 补 `usage_breakdown.video` |
| 视频提示词写入 | `src/video_agent/service.py:713-749` | 用 `*_with_breakdown` 写 `usage_breakdown.chat` |
| 管理后台 LLM | `src/api/agent_definition_sections.py:115` / `admin_subagent.py:504` / `page_metadata.py:144` | 与 §5.2 同步升级 |
| 落库模型 | `src/db/models.py:1030-1064` | `ChatRecordDB.create`（无字段变更，仅 jsonb 内容增强） |
| 单价表 | `token_cost_prices`（DB） + `deploy/init-postgres.sql:239-297` | 单价来源，运营可后台调整 |
| 系数配置 | `configs/config.yaml:268-272` + `src/config/settings.py:358-361` | 4 个 usage_factor 系数 |

## 7. 上次计划遗漏原因分析（交叉核对结论）

上次计划 `docs/plans/plan-llm-billing-integration.md`（提交 `a76b9b1` / `e183508`）已上线且测试反馈符合预期，但本次审计仍发现 18 处缺口。根因有 7 条：

### 7.1 审计方法论的盲区：链路类型枚举 ≠ 全量调用点扫描

上次设计文档 §1.1 用「链路类型枚举」法--人工列出"未计费链路"（embedding/ASR/视频提示词/background_runner/对话内后台 LLM），然后按类型找调用点。该方法**受限于审计人员的预设视野**，无法发现"未预想到的链路类型"。

本次审计虽改用 `grep -rn gateway.chat` 全量扫描 LLM 调用点，但仍按"工具内/API 路由/RAG embedding"三类归类，未对 embedding 调用做同等全量扫描，导致 §3.4.1-3.4.3 的 4 处 embedding 遗漏。**正解**：审计时应对所有计费相关入口（`gateway.chat` / `embedding_client.embed*` / `TextEmbedding.call` / `_call_aliyun_asr`）做并行全量扫描。

### 7.2 服务层 vs 工具层边界盲区

上次计划 P2.1 覆盖了 `src/services/*` 的 6 个 LLM 调用点（mid_term/sentiment/classification/case_matching/content_generate/analysis_agent），但完全漏掉 `src/tools/*` 工具层的 9 处 LLM 调用（excel_router/pdf_router/word_router/ppt planner/write_tool/browser orchestrator/schema_extractor/followup_manager/travel-quote excel_parser）。

工具层 LLM 调用（路由器/规划器/内容生成器）是"工具内部决策"性质，容易被忽视。本次审计虽补全，但 §3.4.2 显示仍漏了 `speech_to_text_tool`（工具内 ASR 调用）。

### 7.3 管理后台 API 直接调用 LLM/embedding 场景未纳入设计

上次设计文档 §1.1 列出的"未计费链路"全是"对话/后台"场景，没有"管理后台 API 直接调用 LLM"这一类别。本次审计 §3.2 补了 3 处管理后台 LLM 路由，但 §3.4.1-3.4.2 又漏了 3 处管理后台 embedding 路由（`update_schema` / `_update_chunk_embedding` / `backfill_hotel_info_name`）。

管理后台 AI 增强（提示词优化、智能体描述增强、页面推荐、schema 重新向量化）是上次设计完全未考虑的场景维度。

### 7.4 travel-quote 子目录局部覆盖偏差

上次 P2.3 只覆盖 travel-quote 的 4 个 retriever/search_tool（embedding 计费），漏掉同目录的 3 个 excel_parser（LLM 计费）。本次审计 §3.1 补了 3 个 excel_parser，但 §3.4.3 又漏了同目录的 backfill 脚本。

travel-quote 是个子目录密集型 skill，每个文件都可能是独立计费点，需要逐文件扫描而非按"已知文件"枚举。

### 7.5 ASR 工具入口单一假设

上次 P2.4 假设 ASR 只通过 `channel_routes.py:2095` 微信语音消息路径调用，在该路径外层补了 `add_asr_usage(calls=1)`。但 `SpeechToTextTool` 是 BaseTool，agent 主循环可通过工具调用它（如用户在对话中上传音频文件让 agent 转文字），此路径**完全无计费**。

**根因**：上次设计把"渠道侧语音消息"等同于"ASR 调用入口"，未识别工具入口的多态性。本次审计 §3.4.2 补全。

### 7.6 embedding 计费视角的盲区：以"绕过 client"为改造目标

上次 P2.2-P2.3 的改造目标是"让 4 个绕过 `TextEmbeddingV3Client` 的调用点改用 client"。但 `hybrid_retriever` / `schema_saver` / `update_schema` / `_update_chunk_embedding` **已经用了 client**，只是没接 `add_embedding_usage`，不在改造范围内。

**根因**：改造目标设定为"统一 embedding 入口"，而非"全量 embedding 计费覆盖"。这两个目标重叠但不等价--已用 client 但未计费的调用点被忽略。

### 7.7 数据 schema 管理路径完全未审计

`schema_saver.save_schema_to_knowledge` + `api/data_analysis.update_schema` + `api/travel_quote._update_chunk_embedding` 是数据 schema 管理（CRUD）的 embedding 调用路径，上次完全未审计。本次审计 §3.4.1-3.4.2 补全。

该路径属于"管理后台低频但产生 token 消耗"场景，与 §3.2 的"管理后台 LLM 路由"同属一类盲区。

### 7.8 改进建议：建立计费审计 checklist

为避免下次审计再次遗漏，建议建立"计费审计 checklist"作为开发规范的一部分（可加入 `.claude/rules/` 或 `docs/system/`）：

1. **全量入口扫描**：`grep -rn "gateway\.chat\b\|llm_gateway\.chat\b\|_gateway\.chat\b" src/` -- LLM 调用
2. **全量 embedding 扫描**：`grep -rn "embedding_client\.embed\|TextEmbeddingV3Client\|\.embed_batch\|\.embed_sync\|retriever\._embed" src/` -- embedding 调用
3. **全量 ASR 扫描**：`grep -rn "SpeechToTextTool\|_call_aliyun_asr\|nls-gateway" src/` -- ASR 调用
4. **全量视频扫描**：`grep -rn "calculate_video_credit_cost\|video_agent" src/` -- 视频生成
5. **逐点对照**：每个调用点必须满足以下之一才算"已计费"：
   - 紧邻调用处有 `record_background_llm_usage` / `add_embedding_usage` / `add_asr_usage`
   - 紧邻调用处有 `ChatRecordDB.create`（独立落库）
   - 紧邻调用处有 `_record_knowledge_embedding_billing` / `_record_background_llm_billing` / `_record_prompt_llm_usage` 等专用计费函数
   - 调用处于 `install_usage_recorder` 上下文内（association_enrichment 模式）
6. **工具入口 vs 渠道入口分离**：BaseTool 的 `execute` 方法被多入口调用时（agent 主循环 / channel_routes / API 路由），计费应在工具内部统一补，外层不得重复补
7. **同步/异步核对**：所有 `gateway.chat` 是 `async def`，调用方必须是 `async def` 且 `await`，否则 LLM 实际未执行（followup_manager P1 bug）

## 关联

- 计费表结构与落库逻辑：`src/services/session_record.py`、`src/services/billing.py`、`src/db/models.py`（ChatRecordDB）
- LLM 网关：`src/llm/gateway.py`、`src/llm/failover.py`
- usage recorder 机制：`src/services/llm_usage_meter.py`（ContextVar `_recorder`）
- 后台计费兜底函数：`src/services/session_record.py::record_background_llm_usage`
- 已有 embedding 计费参考实现：`src/skills/travel-quote/scripts/attraction_retriever.py:74-84`
- 已有管理后台计费参考实现：`src/api/client_routes.py` 的 `/llm/chat` 路由（用 `ClientUsageLogDB.record_llm_usage`，不同于 chat_records，但模式可借鉴）
