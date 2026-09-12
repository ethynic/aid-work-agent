# LLM 计费审计报告（2026-09-12）

> 按 [.claude/rules/billing_audit.md](../../.claude/rules/billing_audit.md) §3 checklist 执行的全量审计。上次全量审计为 2026-08-14（18 处缺口，见 [llm-billing-gap-audit.md](llm-billing-gap-audit.md)），本报告为季度复审 + 修复记录。

## 审计范围

| 类别 | 扫描命中 | 核对方式 |
|------|---------|---------|
| LLM 调用（gateway.chat / chat_with_tools / chat_lite） | 21 处主扫描 + 补充交叉扫描约 20 处 | 逐点读函数完整上下文（前后 50 行），按「已计费五条件」核对 |
| Embedding（TextEmbeddingV3Client / embed_sync / embed_batch） | 12 处 | 逐点核对 |
| ASR（SpeechToTextTool） | 3 处 | 工具内部计费 + 外层双计检查 |
| 视频生成（VideoChatService / calculate_video_credit_cost） | 1 处 | 预扣 + 退款闭环核对 |
| 同步/异步陷阱 | 全部调用点 | 确认 await / asyncio.run，无 coroutine 漏执行 |

## 已正确计费调用点（按五条件归类）

| 条件 | 代表调用点 |
|------|-----------|
| A（record_background_llm_usage） | travel-quote 3 个 excel parser、excel_router、write_tool、ppt planner、analysis_agent、classification/case_matching/sentiment/recruiting_match、overlay_heal、followup_manager（历史 P1 已修复）、recap external_push |
| B（record.add_llm_usage 等累加） | agent.py 主循环 4 处 chat_with_tools（通路①）、hotel/attraction_search_tool、hybrid_retriever（条件式） |
| C（ChatRecordDB.create 独立落库） | client_routes（ClientUsageLogDB ×10 同事务）、video_gen 预扣、knowledge_embedding 入库 |
| D（专用计费函数） | admin_subagent / agent_definition_sections / page_metadata（record_admin_llm_usage）、memory_summarizer、reports summarizer、knowledge service 摘要、video_prompt |
| E（install_usage_recorder） | association enrichment 全链（ui/providers/profile_extractor/leadership_search），确认为唯一入口，无绕过通路 |

**2026-08-14 的 18 处历史缺口已全部修复**（除本报告新发现的 4 处残留）。

## 计费缺失（本次发现，已全部修复）

| 调用点 | 场景 | 优先级 | 修复 |
|--------|------|--------|------|
| `hotel_retriever.py` / `attraction_retriever.py` 的 `import_hotel` / `import_attraction` 入库向量化 | 被 4 个 API 调用（Excel 批量导入 travel_quote.py:1499/1687 + 单条导入 :2108/2142），完全漏计；批量导入一次几十条。同文件 `search_by_vector` 与 `_update_chunk_embedding` 均已计费，唯独导入路径漏 | **P1** | reset_usage -> embed -> `record_admin_embedding_usage(source_type=background_embedding)`，批量导入每条独立落账（reset 后 embed 防计数器累积双记） |
| `memory/mid_term.py:946` 上下文压缩摘要 fallback | 走 `gateway.chat_lite` 但计费 `model=model_cfg`（摘要配置主模型名），按主模型单价落账多收租户；536f3613 修复 11 处时遗漏此处 | P2 | model 改为 `settings.llm.get_lite_model()`，`_actual_model` 同步修正 |
| `api/subagent.py` 数字员工问候语 | LLM 成功但 JSON 解析失败 data=None 时不落账，token 已耗漏计 | P2 | 计费移到 chat_lite 成功后的 else 分支，解析失败也记账（对齐 overlay_heal 模式） |
| `knowledge/api.py:440` search_documents | 独立搜索 API 无会话 record，HybridRetriever 条件式计费静默跳过 | P2 | service.search_documents 补兜底：无 record 时 `record_admin_embedding_usage` 独立落库；有 record 时 retriever 已累加，互斥无双计 |

## 潜在风险（未启用/存量，不阻塞）

| 项 | 说明 |
|----|------|
| ASR 计费依赖隐式时序 | channel_routes 的 wecom_kf ASR 在 start_record 之前调用（record 恒 None），与工具内部计费互斥；若未来把调用移到 start_record 之后会双计，代码注释已写明约定 |
| chat_lite 对话内路径① | `add_llm_usage` 无模型维度，对话内 lite 调用仍按主模型单价累加，待分桶计价统一整改（见 chat-lite-billing-model-mismatch.md） |
| attraction_retriever 假异步（存量） | `import_attraction`（async def）内 `_embed` 同步阻塞，调用方多经 to_thread/独立 loop，未列入本次修复 |

## 修复进度

- [x] P1 travel-quote 导入路径漏计修复
- [x] P2 mid_term lite 单价错配修复
- [x] P2 subagent 问候语解析失败漏计修复
- [x] P2 search_documents 兜底计费修复
- [x] 计费回归测试 39 passed（test_billing_background_llm / test_billing_embedding_integration / mid_term 摘要）+ 容器 import 终检 + 独立 CodeReview 通过

## 维护提示

- 新增 chat_lite 调用点必须显式传 `model=settings.llm.get_lite_model()`（沿用 536f3613 + 本次 mid_term 模式）
- 新增知识库写入/向量化入口必须核对 billing_audit.md §3.2 + §3.5
- 下次定期审计：2026-12 或大版本前
