# chat_lite 切换后的计费模型错配（已知问题）

> **状态**：✅ 已修复（2026-09-15）。交互型调用点改走新增的 `chat_no_thinking`（沿用主链路模型、仅关思考），三条计费路径的模型与实际消耗一致；纯后台批量调用点保留 `chat_lite`（2026-09-12 兜底路径 ②③ 显式传 lite 模型名计费）。遗留边界见 §6。
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

- **兜底路径（小改）** ✅ 已实施（2026-09-12）：路径 ②③ 的 11 处调用点显式传 lite 模型名（用现成 helper `settings.llm.get_lite_model()`，等价于 `get_lite_target()[1]`），`_persist_background_llm_record` / `record_admin_llm_usage` 即可按正确单价计价。涉及文件：`knowledge/parsers/excel_parser.py`、`services/classification_service.py`、`services/case_matching_service.py`、`services/sentiment_service.py`、`services/data_analysis/schema_extractor.py` ×2、`tools/browser/orchestrator.py`、`tools/pdf/pdf_router.py`、`tools/word/word_router.py`、`skills/followup_manager.py`、`api/agent_definition_sections.py`、`api/page_metadata.py`。2026-09-12 全量计费审计（[llm-billing-audit-20260912.md](llm-billing-audit-20260912.md)）发现遗漏的第 12 处：`memory/mid_term.py` 上下文压缩摘要 fallback（走 `gateway.chat_lite` 但按摘要配置主模型计价），已同步修复。注意：该修复只对路径 ②（兜底独立落库）与路径 ③（管理后台独立落库）生效；对话内走路径 ① 累加时 model 参数被忽略，仍按主模型计价，需等下方中等改动落地。
- **对话内累加路径（中等改动）**（未实施，已被下方方案 2 取代）：`add_llm_usage` 增加 model 维度（per-call 快照带 model），`usage_breakdown` 按模型分桶累计，`save()` 分模型查档计价。涉及 `SessionRecordService` 结构与分段计价逻辑，需回归全部计费测试。
- 统一整改时建议同步处理 `billing_audit.md` §3.6 的「工具入口 vs 渠道入口」核对，避免引入双计。

## 4.1 最终修复方案：交互型调用点改走 `chat_no_thinking`（2026-09-15 已实施）

不再做「add_llm_usage 分模型分桶」中等改造，改为**让实际消耗模型与计费模型一致**：新增 `LLMGateway.chat_no_thinking()`（`src/llm/gateway.py`），沿用主链路模型（走完整链路含 failover），仅注入关思考参数（deepseek `thinking={"type": "disabled"}` / qwen `enable_thinking=False` / zhipu `reasoning_effort="low"`，与 `chat_lite` 同源）。路径 ① 的 `record.model` 本就取 `agent.llm.get_model_name()`（主模型），实际模型 = 计费模型，错配消除，无需改 `SessionRecordService`。

**调用点分流原则**（依据 [llm-summary-cost-benchmark.md](../research/llm-summary-cost-benchmark.md)）：

| 处理 | 调用点 | 理由 |
|------|--------|------|
| 换 `chat_no_thinking`（12 文件 13 处） | `tools/pdf/pdf_router.py`、`tools/word/word_router.py`、`tools/browser/orchestrator.py`、`services/classification_service.py`、`services/sentiment_service.py`、`services/case_matching_service.py`、`services/data_analysis/schema_extractor.py` ×2、`skills/followup-tracking-1.0.0/scripts/followup_manager.py`、`api/agent_definition_sections.py`、`api/page_metadata.py`、`knowledge/parsers/excel_parser.py`、`services/recruiting_match_service.py`、`services/overlay_heal_service.py` | 对话内交互型，延迟敏感（deepseek 关思考 1.2s vs qwen 2.6s），每轮可多次触发；同步把计费 model 参数回改为主模型名（`gateway.get_model_name()`） |
| 保留 `chat_lite` | `memory/mid_term.py`、`knowledge/service.py`（摘要）、`reports/summarizer.py` ×2、`reports/work_outcome_review.py`、`recap/tasks/external_push.py` ×2、`api/subagent.py`（数字员工空态摘要） | 纯后台批量/一次性文案，无会话上下文，qwen lite 成本优（2.09 vs 4.92 元/千次），计费已按 lite 单价显式传参 |

**代价与收益**：
- 租户账单不变（此前就按主模型价计收），改的是平台实际成本：交互型小任务从 qwen（2.09 元/千次）换到 deepseek 关思考（4.92 元/千次，约 2.4 倍），单次绝对值约 +0.003 元；换取对话内工具路由延迟每轮降约 1s
- `chat_no_thinking` 不传 model，子智能体配置了自定义 `model_code` 时 gateway 自动用该模型，与 `record.model`（`agent.llm.get_model_name()`）同源

**验证**：定向测试 471 passed（gateway lite/no_thinking、计费、recruiting、overlay_heal、followup、pdf/word router、browser lifecycle 等 15 个测试文件）。

## 5. 关联

- 上游改造：2026-09-11「max_tokens 过小 + 思考烧穿」整改（A 类 11 处切 chat_lite、B 类 3 处放大 max_tokens、C 类 mid_term/travel-quote llm_client 关思考）
- 审计规范：[billing_audit.md](../../.claude/rules/billing_audit.md)
- 同类先例：`session_record.py` P2-1 修复（工具路由/技能脚本必须显式传 model，否则兜底误用 mid_term 摘要模型单价）

## 6. 遗留边界（已知，不在本次范围）

1. ~~**子智能体自定义模型 + 工具内 gateway 来源不统一**~~（✅ 已解决，2026-09-15）：新增 `src/tools/context.py::resolve_llm_gateway()` 统一工具内 gateway 获取——优先取工具执行上下文中的调用方智能体 gateway（含子智能体 model_code 覆盖，且与 `record.model` 同源），无上下文时兜底到调用方自建实例。已接入：pdf_router / word_router / excel_router / ppt planner / browser orchestrator（决策调用 + 计费模型名）/ data_analysis schema_extractor / knowledge excel_parser。travel-quote skill 脚本为独立进程（上下文不传播），维持现状。
2. **failover 切换**：主链路 failover（deepseek → qwen → zhipu）触发时，实际响应来自 fallback 模型而计费仍按 `record.model` 单价——主循环自身既有行为，非本次引入。
