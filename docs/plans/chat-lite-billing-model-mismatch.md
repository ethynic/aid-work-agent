# chat_lite 切换后的计费模型错配（已知问题）

> **状态**：部分修复（2026-09-12 兜底路径 ②③ 已显式传 lite 模型名；对话内累加路径 ① 仍按主模型计价，短期接受，待后续统一整改）
> **发现时间**：2026-09-11
> **性质**：计费不精确（方向为多收租户，非漏收），非功能性故障

## 1. 背景

2026-09-11 排查「知识库文档摘要为空」线上问题（deepseek 思考 token 烧穿 `max_tokens=500` 致 content 为空）后，按「小任务不需要思考」原则将 11 处 `max_tokens <= 4096` 的 LLM 调用点从 `gateway.chat()`（主链路，思考开启）切换为 `gateway.chat_lite()`（lite 模型，关思考）。受影响调用点：

| 调用点 | 任务 | 计费路径 |
|--------|------|---------|
| `knowledge/parsers/excel_parser.py` | Excel 布局判定 | record_background_llm_usage（不传 model） |
| `api/agent_definition_sections.py` | 分段文案优化 | record_admin_llm_usage |
| `api/page_metadata.py` | 页面推荐 | record_admin_llm_usage |
| `services/classification_service.py` | 文本分类 | record_background_llm_usage（不传 model） |
| `services/case_matching_service.py` | 相似度匹配 | record_background_llm_usage（不传 model） |
| `services/sentiment_service.py` | 情绪分析 | record_background_llm_usage（不传 model） |
| `services/data_analysis/schema_extractor.py` ×2 | schema 抽取/关系推断 | record_background_llm_usage（传 get_model_name()） |
| `tools/browser/orchestrator.py` | 浏览器操作决策 | record_background_llm_usage（传 get_model_name()） |
| `tools/pdf/pdf_router.py` | 路由选择 | record_background_llm_usage（传 get_model_name()） |
| `tools/word/word_router.py` | 路由选择 | record_background_llm_usage（传 get_model_name()） |
| `skills/followup_manager.py` | 跟进评分 | record_background_llm_usage（传 get_model_name()） |

## 2. 问题：lite token 按主模型单价计费

`chat_lite` 实际调用 **lite 模型**（生产配置 `qwen3.8-flash`），但计费链路在三条路径上都按**主模型**（生产配置 `deepseek-v4-flash`）单价计价：

### 路径 ①：对话内累加（有 SessionRecordService）

`record_background_llm_usage` -> `record.add_llm_usage(usage)`（`session_record.py:628`）只累加 token 数，**不带模型维度**；`save()` 时整条 record 按 `self.model`（主模型）单价统一计价（`session_record.py:366-369` 的 `calculate_llm_credit_cost_with_breakdown(usage_calls, model=self.model)`）。

### 路径 ②：兜底独立落库（无 SessionRecordService，如 background_runner / 独立进程）

`_persist_background_llm_record`（`session_record.py:684`）按显式 `model` 参数查单价。但 A 类调用点传的是 `gateway.get_model_name()`——该函数（`gateway.py:576`）返回**主链路模型名**，感知不到 `chat_lite` 实际用的 lite 模型。不传 `model` 的调用点回退到 mid_term 摘要模型（生产同为 deepseek 系），同样错配。

### 路径 ③：管理后台独立落库

`record_admin_llm_usage`（`session_record.py:941-946`）未传 model 时内部 fallback 到 `llm_gateway.get_model_name()`（主模型）。

## 3. 影响量化

生产 `token_cost_prices` 表单价（元/百万 token）：

| 模型 | input | output |
|------|-------|--------|
| deepseek-v4-flash（主模型，计费所用） | 2.0 | 8.0 |
| qwen3.8-flash（lite 模型，实际消耗） | 0.8 | 2.7 |

lite token 被按主模型单价计费 -> **多收租户**（输出端价差约 3 倍：8.0 vs 2.7）。

单次量级：这些调用点都是小任务（completion 通常几百~2000 token），单次误差约 `(8.0-2.7) × tokens/1e6 ≈ 0.003~0.01 元`。但分类/情绪/路由类调用在每轮对话中可能触发多次，长期累积不可忽略。对账核查时 `usage_breakdown.chat.model` 与实际调用模型不一致可作为识别特征。

## 4. 修复方向

- **兜底路径（小改）** ✅ 已实施（2026-09-12）：路径 ②③ 的 11 处调用点显式传 lite 模型名（用现成 helper `settings.llm.get_lite_model()`，等价于 `get_lite_target()[1]`），`_persist_background_llm_record` / `record_admin_llm_usage` 即可按正确单价计价。涉及文件：`knowledge/parsers/excel_parser.py`、`services/classification_service.py`、`services/case_matching_service.py`、`services/sentiment_service.py`、`services/data_analysis/schema_extractor.py` ×2、`tools/browser/orchestrator.py`、`tools/pdf/pdf_router.py`、`tools/word/word_router.py`、`skills/followup_manager.py`、`api/agent_definition_sections.py`、`api/page_metadata.py`。注意：该修复只对路径 ②（兜底独立落库）与路径 ③（管理后台独立落库）生效；对话内走路径 ① 累加时 model 参数被忽略，仍按主模型计价，需等下方中等改动落地。
- **对话内累加路径（中等改动）**（未实施）：`add_llm_usage` 增加 model 维度（per-call 快照带 model），`usage_breakdown` 按模型分桶累计，`save()` 分模型查档计价。涉及 `SessionRecordService` 结构与分段计价逻辑，需回归全部计费测试。
- 统一整改时建议同步处理 `billing_audit.md` §3.6 的「工具入口 vs 渠道入口」核对，避免引入双计。

## 5. 关联

- 上游改造：2026-09-11「max_tokens 过小 + 思考烧穿」整改（A 类 11 处切 chat_lite、B 类 3 处放大 max_tokens、C 类 mid_term/travel-quote llm_client 关思考）
- 审计规范：[billing_audit.md](../../.claude/rules/billing_audit.md)
- 同类先例：`session_record.py` P2-1 修复（工具路由/技能脚本必须显式传 model，否则兜底误用 mid_term 摘要模型单价）
