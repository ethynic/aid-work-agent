# 线上故障复盘：数据分析智能体 max_tokens 截断致空结论、主智能体误判"知识库无数据"

> 日期：2026-08-26
> 环境：生产环境 `aid_work_agent` / `aid_work_logs`（124.222.3.254:5433）
> 影响：数据分析智能体对复杂分析（2026-07 vs 2025-07 各品类销售毛利同比）返回空结论，主智能体据空结论误回复"知识库中未能检索到2025年7月（去年同期）的销售毛利数据"，而数据实际存在
> 根因：`src/tools/data_analysis/analysis_agent.py` 硬编码 `max_tokens=4000`，deepseek-v4-pro 推理模型烧穿预算返回空 `content`；原逻辑把空 `content`（无 `tool_calls`）当最终总结返回，`success=True` + `conclusion=""` 误导主智能体归因为"无数据"
> 关联代码：`src/tools/data_analysis/analysis_agent.py`、`src/tools/data_analysis/smart_analysis_tool.py`、`src/llm/providers/deepseek.py`

---

## 一、故障现象

用户使用「数据分析智能体」分析「2026年7月各品类销售毛利与去年同期（2025年7月）的同比对比情况」，智能体回复：

> "知识库中未能检索到2025年7月（去年同期）的销售毛利数据，因此无法计算同比变化。这可能是因为：知识库中仅存有近期的销售记录，未包含去年同期的历史数据。"

用户确认知识库中**实际存在** 2025-07 的销售毛利数据。

## 二、排查过程

### 2.1 定位失败 trace（aid_work_logs 库）

从 `obs_traces` / `obs_spans` 定位到用户请求对应的分析 `analysis_7cd94613`（2026-08-26 15:13，数据集 10540 行 × 9 列）：

| 序号 | prompt_tokens | completion_tokens | 耗时 | output |
|------|--------------|-------------------|------|--------|
| 1 | 6104 | 211 | 2972ms | （工具调用） |
| 2 | 6660 | 159 | 3247ms | （工具调用） |
| 3 | 7235 | 166 | 2948ms | （工具调用） |
| 4 | 8213 | 184 | 3111ms | （工具调用） |
| 5 | 9975 | **4000** | **69746ms** | **空** |

第 5 次（最终总结调用）`completion_tokens` **恰好等于 4000**（命中 `max_tokens` 上限）、耗时 69.7s、`content` 为空。

### 2.2 失败模式是系统性的，非单次偶发

同日/前一日多个分析 trace 呈现完全相同的特征（`completion=4000` + 空 output）：

| trace | 时间 | 数据集 | completion | 耗时 |
|-------|------|--------|-----------|------|
| analysis_5c1858b2 | 08-26 10:34 | 10540 行 | 4000 | 72377ms |
| analysis_53b02afc | 08-26 10:14 | 21559 行 | 4000 | 62240ms |
| analysis_f217d17e | 08-26 10:12 | 21559 行 | 4000 | 69306ms |
| analysis_543cc03e | 08-25 11:01 | 10540 行 | 4000 | （同模式） |

### 2.3 成功对照：数据确实存在

同一数据集、两分钟后的各渠道同比分析 `analysis_39311b7d`（08-26 15:15）**成功**：

- 14 次 LLM 调用，最终总结仅需 `completion=283`、耗时 5.1s
- 输出"已完成2026年7月各渠道毛利分析…"

证明 `search_data_tables` 能检索到 2025-07 数据、可加载、可计算同比。**失败不是检索不到，而是生成不出来。**

### 2.4 token 速率佐证：4000 token 消耗在推理链上

| 调用 | tokens | 耗时 | 速率 |
|------|--------|------|------|
| 失败总结 | 4000 | 69.7s | ~57 tok/s |
| 成功总结 | 283 | 5.1s | ~55 tok/s |

两者生成速率相同，但失败调用 `content` 为空——**4000 token 预算全部消耗在模型推理（reasoning）上，content 一个字没生成**，是 deepseek-v4 推理模型烧穿小 `max_tokens` 的典型特征。

### 2.5 环境核对

- 用户数据（租户 `tenant_128a10da9e2c`，2 个文档）在 **`aid_work_agent`** 库；trace 在 **`aid_work_logs`** 库。
- 本机 WSL 的 `aid-agent-api` 容器连的是 **`aid_work_agent2`（开发库，无用户数据）**——排查需直连 124.222.3.254:5433 的 4 个库核对，不能在本地容器复现。
- **生产容器代码老于 2026-08-24**：8/24 提交的 DeepSeek `thinking: {"type": "disabled"}` 关思考修复（commit `a850755c`）**未部署到生产**，失败在 8/25~8/26 仍持续。

## 三、根因

1. **直接根因**：`analysis_agent.py:102` 硬编码 `max_tokens=4000`、`model="deepseek-v4-pro"`。复杂分析（9 品类同比对比）最终总结调用时，deepseek-v4-pro 推理模型的 4000 token 预算被思维链耗尽，`content` 为空。
2. **放大因素**：原循环逻辑（`analysis_agent.py` 空总结路径）在「无 `tool_calls` 且 `content` 为空」时直接 `summary = content`（空串）`break`，返回 `success=True` + `conclusion=""`。
3. **误判归因**：主智能体（LLM）收到 `success=True` + `conclusion=""`，幻觉推断"知识库中无 2025-07 数据"，而非如实报告"分析未完成"。

## 四、修复

| # | 修复 | 位置 |
|---|------|------|
| 1 | `max_tokens` 4000 → 16384（与网关默认 `DEFAULT_MAX_TOKENS` 一致） | `analysis_agent.py:104` |
| 2 | 空总结兜底：无工具调用且 `content` 为空时重试一次；仍空返回 `success=False` + 明确 error，绝不再把空串当结论 | `analysis_agent.py:141-164` |
| 3 | 主智能体防线：`execute` 出口若 `success=True` 但 `conclusion` 为空，降级为失败并同步 `conclusion` 为错误文案 | `smart_analysis_tool.py:89-96` |

配套：更新 `test_smart_analysis_tool.py` 过时 mock（旧字段 `summary/charts/steps` → 真实 `conclusion/artifacts/analysis_meta`），新增空总结重试成功、重试仍空降级失败、`execute` 空结论降级失败 3 个用例。

**测试结果**：`tests/unit/tools/test_analysis_agent.py` + `test_smart_analysis_tool.py` 共 **59 passed**，容器 import 安全检查通过。

## 五、部署要求

生产容器代码老于 8/24，**必须更新并重启生产容器**才会生效。本次修复与 8/24 的 `deepseek.py` thinking 关思考修复一起部署，两者共同杜绝"推理烧穿预算→空结论"。

## 六、经验教训

1. **推理模型会静默烧穿小 `max_tokens` 返回空 `content`**——调用 DeepSeek v4 系模型时，`max_tokens` 上限必须留足（本项目网关默认 16384），且任何调用点都要对"空 content"做兜底，不能把空串当有效输出。
2. **工具返回 `success=True` + 空结论会诱导主智能体幻觉归因**——LLM 主智能体会对空结果编造"合理"原因（如"无数据"）。工具必须显式区分"成功但无内容"（失败）与"真无数据"（成功）。
3. **生产环境代码版本滞后导致已修复问题仍复现**——修复提交 ≠ 线上生效。排查时要先核对生产容器实际运行版本（`git log`），避免在本地/开发环境浪费时间复现。
4. **排查 trace 看 `completion_tokens` 是否恰等于上限**——这是"max_tokens 截断"的强信号，配合耗时与 content 是否为空可快速区分"截断"与"真实输出短"。
5. **环境隔离**：生产（`aid_work_agent`/`aid_work_logs`）与开发（`aid_work_agent2`/`aid_work_logs2`）库严格分离，排查用户数据必须在生产库核对。
