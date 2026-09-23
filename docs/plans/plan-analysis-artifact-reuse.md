# 数据分析产物复用（analysis artifact reuse）开发计划

> 关联事故复盘：[analysis-agent-cost-392-credits-incident.md](../incidents/analysis-agent-cost-392-credits-incident.md) §5.2-C
> 关联调研结论：6 次 analyze_data 中 4 次重复「load 18 张表 → 逐表 calculate/aggregate → merge」，
> 单次重复合并即 ~200 万 prompt tokens；根因是每次 analyze_data 新建 AnalysisAgent，
> 子代理无法发现/加载上一次调用已持久化的中间产物（CSV 落在 `storage/tenants/{tid}/temp` 但不可见）。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 产物注册表 + load_output 工具 + 子代理注入 + 主代理引导 | ✅ 完成（2026-09-23） | 开发+自测完成；独立验证发现 P0 session_id 路径穿越（跨租户读写注册表，已 PoC）→ 修复（session_id 校验 + realpath 包含检查 + context 优先）并补 6 个安全测试；复核报告：8 项修复全过、两侧穿越 PoC 闭合、254/254 定向测试通过、无新引入问题。待部署 |
| Phase 2 | 生产部署 + 真实任务 token 对比验收 | 📋 待开发 | 部署后复跑「狗粮出口经营分析」类任务对比 analysis_* 子任务 token |

## 1. 目标

同一会话内多次 `analyze_data` 调用之间复用中间分析产物（合并宽表、聚合结果等），
消除重复的「检索 → load 原始表 → describe → 逐表加工 → merge」链路。
对 392 积分类任务，预期削减 3 个重复子任务 ≈ 600 万 prompt tokens（同类任务成本降 50%+）。

## 2. 方案设计

### 2.1 产物注册表（新模块 `src/tools/data_analysis/analysis_artifacts.py`）

- 存储：`storage/tenants/{tid}/temp/analysis_registry_{session_id}.json`（按会话一个文件，
  与产物 CSV 同目录、同租户隔离；同会话内 analyze_data 串行执行，无写竞争）。
- 条目：`{var, description, method, rows, columns(截断), created_at}`；同 var 覆盖更新；上限 50 条（FIFO 淘汰）。
- 接口：`record_artifact(tenant_id, session_id, entry)` / `load_artifacts(tenant_id, session_id, limit)`，
  全部 try/except 包裹，注册表读写失败只 log warning，不影响分析主流程。
- session_id 为空时不读不写（无法界定复用范围，宁可不复用）。

### 2.2 `load_output` 工具（子代理侧）

- Schema 加入 `ANALYSIS_TOOLS` + `ALLOWED_METHODS`；参数 `output_var`。
- Handler：校验 var 名（`^[\w\-]{1,64}$`，防路径穿越）→ 从租户 temp 目录读 `{var}.csv` →
  装入 `analyzer._variables[var]`，返回 rows/columns/前 3 行预览；realpath 校验必须在租户 temp 目录内。
- 未命中时返回已注册 var 清单引导重试。

### 2.3 注册表写入钩子

- `_handle_data_method` 在 `_store` 成功后 `record_artifact(...)`（仅 DATA_PROCESSING_METHODS 中间产物；
  to_table/to_chart 是最终交付物不入注册表）。
- `AnalysisAgent.__init__` 新增 `session_id` 参数。

### 2.4 子代理启动注入可用产物清单

- `run()` 构建 user message 时，若注册表非空，追加「本会话此前分析产物」清单（最近 ≤10 条：
  var、行数、列、描述、时间），并注明优先 `load_output` 复用。
- `ANALYSIS_SYSTEM_PROMPT` 阶段二补规则：清单中已有相同数据的产物时必须优先 load_output，
  禁止重新加载原始表重复合并。

### 2.5 主代理侧引导

- `SmartDataAnalysisTool`：session_id 兜底改为 `kwargs 或 context.session_id`（主循环工具上下文必带）。
- 工具 description / requirement 字段描述补引导：同一数据源的多个子问题尽量合并为一次调用；
  后续分析在 requirement 中注明可复用的 output_var。
- `_build_result` 增加 `reusable_outputs`（本次运行新增的注册条目），主代理可在下一次调用中引用。

## 3. 改动文件

| 文件 | 改动 |
|------|------|
| `src/tools/data_analysis/analysis_artifacts.py` | 新增：注册表读写 |
| `src/tools/data_analysis/analysis_agent.py` | `load_output` handler、注册表写入钩子、user message 注入、session_id 参数、reusable_outputs |
| `src/tools/data_analysis/analysis_tools_schema.py` | `load_output` schema、系统提示词补复用规则 |
| `src/tools/data_analysis/smart_analysis_tool.py` | session_id 兜底、description 引导 |
| `tests/unit/tools/test_analysis_artifacts.py` | 新增测试 |

## 4. 测试计划

- 注册表：round trip / 同 var 覆盖 / 上限淘汰 / session 隔离（不同 session 文件互不可见）/ 租户隔离。
- load_output：正常加载入 `_variables` / 非法 var 名拒绝 / 路径穿越拒绝 / 未命中返回可用清单。
- 注入：注册表非空时 user message 含产物清单；空注册表不追加。
- 钩子容错：record_artifact 抛异常不影响 `_handle_data_method` 返回。
- 回归：现有 `test_analysis_agent.py` / `test_smart_analysis_tool.py` 全部保持通过。

## 5. 验证与交付

- 定向自测：`./scripts/dev_test.sh tests/unit/tools/test_analysis_artifacts.py tests/unit/tools/test_analysis_agent.py tests/unit/tools/test_smart_analysis_tool.py -p no:cacheprovider -q`
- 独立验证智能体：跑定向测试 + 审查 diff（重点：租户/会话边界、路径穿越、异常容错、提示词一致性）。
- 生产验收（部署后）：复跑「狗粮出口经营分析」类任务，对比 trace 的 analysis_* 子任务 token 总量。

## 6. 审查发现与修复记录（2026-09-23 独立验证）

- **P0（已修）**：session_id 经 analyze_data 参数暴露给 LLM 且原实现 kwargs 优先于服务端上下文，未校验直接拼注册表路径，可穿越租户 temp 目录实现跨租户注册表读写（验证者已 PoC）。修复：`SESSION_ID_PATTERN` fullmatch 校验 + `_registry_path` realpath 包含检查 + `smart_analysis_tool` session_id 改 context 优先。
- **P2（已修）**：`$` 锚尾换行绕过（改 fullmatch）；`load_artifacts` 条目 var 未校验（补过滤）；tmp 文件名并发覆盖（pid+uuid 唯一化）；提示词「需求前/后」措辞对齐；ideas.md 说明超长收敛。
- **遗留（不在本任务修）**：`DataAnalyzer._store` 对 output_var 无校验（存量问题，穿越面同类），建议另立任务加固。
