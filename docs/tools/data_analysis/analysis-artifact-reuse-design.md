# 数据分析产物复用设计（analyze_data 跨调用）

> 状态：✅ 已实现（Phase 1，2026-09-23 开发+独立测试+CR 完成）
> 本文档整合自已删除的开发计划 `plans/plan-analysis-artifact-reuse.md`，按当前代码实际实现撰写。
> 起因与量化数据见事故复盘 [analysis-agent-cost-392-credits-incident.md](../../incidents/analysis-agent-cost-392-credits-incident.md) §5.2-C。

## 1. 背景与目标

2026-09-20 生产 392 积分事故（trace tr_4c3a88d134ea4235）的核心根因之一：同一会话内
6 次 `analyze_data` 各自新建 AnalysisAgent，4 次重复「load 18 张表 → 逐表 calculate/
aggregate → merge」，单次重复合并即 ~200 万 prompt tokens——`DataAnalyzer._store` 虽然
已把中间变量持久化为 `storage/tenants/{tid}/temp/{var}.csv`，但新子代理不知道它们存在。

目标：同一会话内多次 `analyze_data` 之间复用中间产物（合并宽表、聚合结果等），
消除重复的数据加工链路。对 392 积分类任务，预期削减 3 个重复子任务 ≈ 600 万
prompt tokens（同类任务成本降 50%+）。

## 2. 架构总览（四件套）

```text
analyze_data #1：AnalysisAgent 加工 → _store 落盘 CSV → record_artifact 注册
                                          ↓（storage/tenants/{tid}/temp/analysis_registry_{session_id}.json）
analyze_data #2：run() 注入「本会话已有分析产物」清单 → load_output(var) 直接装入
                  _variables → 跳过整段重复加工
主代理侧：结果携带 reusable_outputs → description 引导下次 requirement 注明复用 var
```

| 组件 | 位置 | 职责 |
|------|------|------|
| 产物注册表 | `src/tools/data_analysis/analysis_artifacts.py` | 会话级 JSON 注册表读写（容错） |
| `load_output` 工具 | `analysis_agent.py:1039-1093` + schema | 子代理按 var 名加载历史产物 |
| 写入钩子 + 启动注入 | `analysis_agent.py` | 中间产物自动注册；新子代理注入可用清单 |
| 主代理引导 | `smart_analysis_tool.py` | session_id 解析、description 引导、reusable_outputs |

## 3. 组件设计

### 3.1 产物注册表（analysis_artifacts.py）

- 存储：`storage/tenants/{tid}/temp/analysis_registry_{session_id}.json`，按会话一个文件，
  与产物 CSV 同目录、同租户隔离；同会话内 analyze_data 串行执行，无写竞争
  （并行假设被打破时靠唯一临时文件名 + `os.replace` 原子替换兜底）。
- 条目：`{var, description, method, rows, columns[:15]}`；同 var 覆盖更新；上限 50 条
  FIFO 淘汰（`REGISTRY_LIMIT`）；注入清单上限 10 条（`INJECT_LIMIT`）。
- 接口：`record_artifact(...)` / `load_artifacts(...)`（时间倒序，剔除 CSV 已丢失条目，
  避免子代理 load_output 必然失败）。
- 容错原则：注册表读写全部 try/except，失败仅 log warning，**绝不影响分析主流程**；
  tenant/session 任一为空时不读不写（无法界定复用范围，宁可不复用）。

### 3.2 load_output 工具（子代理侧）

- Schema：`analysis_tools_schema.py:190-212`，参数 `output_var`；注册于 `ANALYSIS_TOOLS`
  与 `ALLOWED_METHODS`。description 明确：清单覆盖当前需求时必须优先复用，禁止重新
  load_table 再做相同加载/合并。
- Handler `_handle_load_output`（`analysis_agent.py:1039-1093`）：
  1. `is_valid_var_name` 校验（`[\w\-]{1,64}` fullmatch，防路径穿越）；
  2. realpath 校验必须在租户 temp 目录内；
  3. 读 `{var}.csv` 装入 `analyzer._variables[var]`；
  4. 返回 rows/columns/前 3 行预览 + hint；
  5. 未命中时返回已注册 var 清单引导重试（无所需数据再走正常 search/load 原始表）。

### 3.3 注册表写入钩子

`_handle_data_method` 在 `analyzer._store` 成功后调用 `_register_artifact`（
`analysis_agent.py:399-412`）。仅 `DATA_PROCESSING_METHODS`（query/aggregate/merge/
pivot/calculate/compare/trend/extract_hierarchy）的中间产物入注册表；
`to_table`/`to_chart` 是最终交付物，只进 `self._artifacts`，不入注册表——复用的对象是
"中间加工结果"，最终交付物由主代理经对话上下文直接可见。钩子整体 try/except，
失败仅 warning。

### 3.4 子代理启动注入

`run()` 首条 user message 由 `_build_user_message` 构建：用户需求 → 已加载数据表 →
产物清单段（注册表非空才追加，最近 ≤10 条，每条 `- \`{var}\`: {rows} 行（列: 前8列） — {description}`）。
措辞硬性：「必须优先调用 load_output(output_var) 加载复用，禁止重新 load_table 原始表
再做一遍相同的加载、清洗和合并」。`ANALYSIS_SYSTEM_PROMPT` 阶段二第 4 条同义规则双保险。

### 3.5 主代理侧（SmartDataAnalysisTool）

- **session_id 解析**：工具执行上下文 `context.session_id` **优先**于 LLM 参数 kwargs
  （`smart_analysis_tool.py:67-71`）。理由：session_id 经 analyze_data 参数暴露给 LLM，
  可幻觉/被注入，且是注册表路径成分，不可信值会导致复用错位或路径异常；无上下文的
  调用方（后台任务）才用参数值。
- **description 引导**：同一数据源的多个分析子问题尽量合并为一次调用；先前调用返回的
  `reusable_outputs` 中的产物可直接复用，在 requirement 中注明要复用的 output_var。
- **reusable_outputs**：`_build_result` 仅当本次运行新增注册条目时携带
  `result["reusable_outputs"] = [{output_var, description, rows}]`（无新产物不携带，
  避免结果膨胀）。

## 4. 安全设计

session_id 是 LLM 可控参数且参与注册表路径拼接，防线（Phase 1 独立验证曾发现 P0
穿越漏洞并 PoC 证实，以下为修复后形态）：

1. `SESSION_ID_PATTERN = [\w\-]{1,128}` fullmatch（re 的 `$` 锚允许尾随换行，
   必须 fullmatch）；
2. `_registry_path` realpath 包含检查：拦截一切逃逸租户 temp 目录的构造（含符号链接外指）；
3. `load_output` 双重防线：var 名 fullmatch + realpath 前缀检查；
4. `load_artifacts` 对条目 var 再校验（注册表文件本身可能被外部篡改）。

## 5. 验证记录（2026-09-23）

- 开发+自测完成；独立验证发现 P0 session_id 路径穿越（跨租户读写注册表，已 PoC）→
  修复（上述 §4 双防线 + context 优先）并补 6 个安全测试；复核：8 项修复全过、
  两侧穿越 PoC 闭合、254/254 定向测试通过。
- 测试基线：`tests/unit/tools/test_analysis_artifacts.py` 27 用例（var 名校验 / 注册表
  roundtrip·覆盖·FIFO·会话与租户隔离·穿越拒绝·损坏 JSON / load_output 全分支 /
  agent 集成：注册触发、失败不破坏分析、注入有无、reusable_outputs 携带、
  session_id 回退）。
- P2 修复：`$` 锚尾换行绕过、条目 var 过滤、tmp 文件名并发唯一化、提示词措辞对齐。

## 6. 边界与遗留

- 复用范围 = 同租户 + 同会话（session_id 维度），不做跨会话复用。
- 遗留（不在本任务修）：`DataAnalyzer._store` 对 output_var 无校验（存量同类穿越面），
  建议另立任务加固。
- 内层上下文压缩、token 硬上限等事故报告 §5.2-A/B/D 的其他优化项与本文档无关，
  状态见事故复盘。
