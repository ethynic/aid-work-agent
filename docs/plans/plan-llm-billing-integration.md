# LLM 计费接入改造开发计划

> 设计文档：[llm-billing-integration-design.md](../system/saas/llm-billing-integration-design.md)
> 状态：🔧 部分完成（Phase 1-4 文档同步完成，待补集成测试）

## Phase 1：基础设施（数据库 + 计费函数）✅ 已完成

- [x] P1.1 `chat_records` 加 3 字段（`init-postgres.sql` + `db_update.sql`）
- [x] P1.2 `token_cost_prices` 加 2 字段 + 种子数据
- [x] P1.3 `billing.py` 新增 `calculate_embedding_credit_cost` / `calculate_asr_credit_cost`
- [x] P1.4 `settings.py` 加 `embedding_usage_factor` / `asr_usage_factor`
- [x] P1.5 `ChatRecordDB.create` 扩展参数与 SQL
- [x] P1.6 `SessionRecordService` 扩展（`add_embedding_usage` / `add_asr_usage` / `save` 改造）
- [x] P1.7 `enums.py` 补充 `source_type` 枚举

**验收**：`tests/unit/test_credit_billing.py` 扩展，覆盖新计费函数（50 个用例通过）

## Phase 2：对话内计费接入 ✅ 已完成

- [x] P2.1 6 个对话内后台 LLM 调用点 `record_background_llm_usage`
  - `mid_term.py:913`（双路径覆盖）
  - `sentiment_service.py:49`
  - `classification_service.py:50`
  - `case_matching_service.py:126`
  - `content_generate_tool.py:105`
  - `analysis_agent.py:44/93`
- [x] P2.2 `embedding_client.py` 透出 usage（`last_usage_tokens` / `reset_usage` / `embed_sync`）
- [x] P2.3 4 个对话内 embedding 调用点改用 `TextEmbeddingV3Client.embed_sync` + `add_embedding_usage`
  - `hotel_search_tool.py`、`attraction_search_tool.py`
  - `hotel_retriever.py`、`attraction_retriever.py`
- [x] P2.4 ASR 调用点 `add_asr_usage`（`channel_routes.py:2095`）
- [x] P2.5 视频提示词 LLM 独立 `ChatRecordDB.create`（4 处调用点）

## Phase 3：离线/后台计费接入 ✅ 已完成

- [x] P3.1 `knowledge/service.py` 文档上传向量化 + 摘要独立 `ChatRecordDB.create(source_type='knowledge_embedding')`
- [x] P3.2 `memory_summarizer.py` 独立 `ChatRecordDB.create(source_type='background_llm')`
- [x] P3.3 `work_outcome_review.py` 独立 `ChatRecordDB.create(source_type='background_llm')`
- [x] P3.4 `mid_term.py` 双路径覆盖（`record_background_llm_usage` 在无 SessionRecordService 时降级独立落库）

## Phase 4：文档与登记 ✅ 已完成

- [x] P4.1 设计文档 `docs/system/saas/llm-billing-integration-design.md`
- [x] P4.2 开发计划 `docs/plans/plan-llm-billing-integration.md`（本文件）
- [x] P4.3 `docs/ideas.md` 系统功能区 #42 条目补充 + 状态 🔧 部分完成
- [x] P4.4 更新 `docs/system/saas/tenant-credit-billing-design.md` 关联引用

## 测试方案

### 单元测试（`tests/unit/`）

- `test_credit_billing.py` 扩展：`calculate_embedding_credit_cost` / `calculate_asr_credit_cost`（已通过 50 个用例）

### 集成测试（`tests/integration/`，待补）

- `test_billing_embedding_integration.py`：模拟文档上传向量化，验证 `chat_records(source_type='knowledge_embedding')` 写入
- `test_billing_background_llm.py`：模拟 background_runner 任务，验证 `chat_records(source_type='background_llm')` 写入

### 回归测试（待执行）

跑现有 `test_credit_billing.py` / `test_billing_balance_api.py` / `test_billing_recharges_api.py` / `test_billing_daily_detail_api.py`，确保不破坏。

## 上线步骤

1. **Phase 1**（基础设施）已上线，不影响现有计费
2. **Phase 2**（对话内）灰度，监控 `chat_records` 新字段写入
3. **Phase 3**（后台）上线，监控 background_runner 任务计费
4. **Phase 4** 文档同步

## 关键决策记录

### 决策 1：用显式累加而非全局 recorder

**背景**：原计划在 `agent.py` 主循环开始时 `install_usage_recorder`，让对话内所有 `gateway.chat` 自动累加。

**问题**：
- 主链路 `agent.py:2704` 已经手动调用 `_record.add_llm_usage`
- 若全局 recorder 也累加，主链路 token 会重复计算
- 破坏现有主链计费

**决策**：用显式累加策略，每个后台 LLM 调用点显式调用 `record_background_llm_usage(usage)`。这样：
- 主链路 token 不受影响（仍由 `agent.py:_record.add_llm_usage` 主导）
- 后台 LLM token 显式累加，无重复计算风险
- 6 个调用点各加 2 行代码（import + 调用）

### 决策 2：`record_background_llm_usage` 双路径

**背景**：`mid_term.py:913` 在对话内压缩时走 gateway 路径，在 background_runner 扫描时也走同一路径。

**问题**：
- 对话内场景：`SessionRecordManager.get_current_record()` 有值，累加到主 chat_records
- background_runner 场景：无 HTTP 上下文，`get_current_record()` 恒 None，usage 被丢弃

**决策**：`record_background_llm_usage` 函数本身做双路径判断：
- 有 SessionRecordService -> `add_llm_usage`（累加到主记录）
- 无 SessionRecordService -> 独立 `ChatRecordDB.create(source_type=background_llm)`（兜底落库）

避免 `mid_term.py` 调用方需要判断场景，统一入口。

### 决策 3：视频提示词 LLM 独立计费

**背景**：用户可能多次调整视频提示词后放弃创建视频。

**问题**：若把提示词 LLM 计费并入视频生成计费，放弃创建视频时提示词 LLM 用量丢失。

**决策**：4 处视频提示词 LLM 调用点（refine draft / refine submit / agile / continue_with_video）各自独立 `ChatRecordDB.create(source_type=video_prompt)`，不与最终视频生成计费合并。
