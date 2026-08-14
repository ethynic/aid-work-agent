# LLM 调用计费缺失审计

> **审计日期**：2026-08-14
> **审计方式**：全量代码走查 `grep -rn` LLM 调用点 + 逐点读函数上下文核实
> **结论**：项目 LLM 调用**未全部实现计费**，存在 **13 处真实缺口** + **2 处潜在风险**。

## 1. 计费架构（先厘清机制）

主计费载体是 `chat_records` 表：`SessionRecordService.add_llm_usage()` 累加 token → `calculate_credit_cost()` 计算信用点费用 → `ChatRecordDB.create()` 落库并扣减租户余额。

LLM token 用量共有三条计费通路：

| 通路 | 机制 | 覆盖范围 |
|------|------|---------|
| ① agent 主循环 | `agent.py:2700/3733` 的 `chat_with_tools` 后显式 `_record.add_llm_usage()` | 仅覆盖主循环**自身**的 LLM 调用 |
| ② 后台/工具服务显式计费 | `record_background_llm_usage()`（session_record.py）或独立 `_record_background_llm_billing()`（memory_summarizer / work_outcome_review） | 已接入的服务 |
| ③ `gateway.chat()` 内 `record_response_usage()` | 依赖 ContextVar `_recorder`，仅调用方预先 `install_usage_recorder()` 才生效 | 目前**仅** association_enrichment_ui 显式安装，其余场景均为 no-op |

**关键陷阱**：`gateway.chat()` 内部的 `record_response_usage` 是**装饰性**的，默认是 no-op，不能作为"已计费"的依据。真正落账靠通路①②显式调用。

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
| reports/summarizer → generator | `_write_billing_chat_record` 落 chat_records |
| video_agent/prompt_engine | `_record_prompt_llm_usage` → `ChatRecordDB.create`（source_type=video_prompt） |
| api/client_routes `/llm/chat` | `ClientUsageLogDB.record_llm_usage` 落 `client_usage_logs`（×5 扣减租户余额） |
| knowledge/service 上传向量化 + 文档摘要 | `_record_knowledge_embedding_billing` |
| travel-quote 检索 embedding（attraction/hotel retriever + search tool） | `add_embedding_usage` |

## 3. ❌ 计费缺失（13 处真实缺口）

这些调用点每次调用都会消耗 token，但**完全不在 `chat_records`（或任何计费表）中**。

### 3.1 主循环工具内部发起的 LLM 调用（9 处）

运行在 agent 主循环的会话上下文内，但工具自身未计费；主循环的 `add_llm_usage`（通路①）**不覆盖**这些额外调用。

| 文件:行 | 场景 | 调用形式 |
|---------|------|---------|
| `src/tools/excel/excel_router.py:148` | Excel 内容生成路由 | `gateway.chat` 裸调用 |
| `src/tools/pdf/pdf_router.py:137` | PDF 生成路由 | `gateway.chat` 裸调用 |
| `src/tools/word/word_router.py:131` | Word 生成路由 | `gateway.chat` 裸调用 |
| `src/tools/ppt/planner.py:93` | PPT 规划器 | `gateway.chat` 裸调用 |
| `src/tools/file/write_tool.py:341` | 写文件内容生成 | `llm_gateway.chat` 裸调用 |
| `src/tools/browser/orchestrator.py:205` | 浏览器自动化决策 | `llm_gateway.chat` 裸调用 |
| `src/services/data_analysis/schema_extractor.py:142/189` | 数据模式提取/关系推断 | `gateway.chat` 裸调用（API 路由 + 主循环工具两条路径均缺失） |
| `src/skills/followup-tracking-1.0.0/scripts/followup_manager.py:364` | 跟进评估 | `llm_gateway.chat` 裸调用 |
| `src/skills/travel-quote/scripts/{attraction,hotel,vehicle}_excel_parser.py` | 行程 Excel 解析（×3） | `gateway.chat` 裸调用 |

### 3.2 API 路由直接调用（3 处）

管理后台 API，无会话上下文，裸 `llm_gateway.chat`，无任何 usage 记录。

| 文件:行 | 场景 |
|---------|------|
| `src/api/agent_definition_sections.py:115` | 优化章节描述 |
| `src/api/admin_subagent.py:504` | AI 增强子智能体描述 |
| `src/api/page_metadata.py:144` | 业务页面推荐 |

### 3.3 对话内 RAG 检索 embedding（1 处）

`src/knowledge/retriever/hybrid_retriever.py:99` — query 向量化 `embedding_client.embed(query)` 未接 `add_embedding_usage`。

三个调用方**全部缺失**：
- `src/tools/knowledge/knowledge_base_tool.py:107`（对话内知识库检索主路径）
- `src/tools/data_analysis/analysis_agent.py:688`
- `src/knowledge/service.py:677`

每次对话内检索都产生 embedding token 消耗（`text-embedding-v3`），完全未计入。

## 4. ⚠️ 存疑 / 潜在风险（2 处）

| 调用点 | 问题 | 现状 |
|--------|------|------|
| `src/core/intent_engine.py:297` | LLM 意图识别裸调用无计费 | `recognize()` 当前**无任何外部调用点**（grep 确认），是未启用的死路径。若未来在主循环启用需补计费 |
| `gateway.stream_chat()`（gateway.py:314 / failover.py:526） | 流式接口只 yield chunk，**不收集 usage**，无法计费 | 当前无业务调用点。若未来启用流式接口（SSE）必须补 usage 汇总逻辑 |

## 5. 修复建议

1. **3.1 主循环工具内 LLM 调用**：在各工具调用 `gateway.chat` 后补 `record_background_llm_usage(response.get("usage"), ...)`，复用 session_record.py 的现有兜底（有会话上下文时累加到当前 record，无会话时独立落 chat_records）。
2. **3.2 API 路由**：用独立 `ChatRecordDB.create(source_type=...)` 落账，或参考 `_persist_background_llm_record` 的写法按调用方落账（管理后台操作应归属到对应管理员的 tenant_id/user_id）。
3. **3.3 RAG 检索 embedding**：在 `hybrid_retriever.retrieve()` 或三个调用方处，读取 `embedding_client.last_usage_tokens` 后调用 `record.add_embedding_usage()`（与 travel-quote 检索的现有写法一致）。
4. **gateway 层兜底**：可在 `gateway.chat()` 中，将 `record_response_usage` 的失效路径改为"无 recorder 时也尝试累加到 `SessionRecordManager.get_current_record()`"，统一兜底所有漏网调用。但需注意区分会话内调用与后台调用，避免双计。
5. 修复完成后，建议对 `chat_records` 的 `total_token_count` 做一轮基线核对，量化当前漏记规模。

## 关联

- 计费表结构与落库逻辑：`src/services/session_record.py`、`src/services/billing.py`、`src/db/models.py`（ChatRecordDB）
- LLM 网关：`src/llm/gateway.py`、`src/llm/failover.py`
- 后台计费兜底函数：`src/services/session_record.py::record_background_llm_usage`
