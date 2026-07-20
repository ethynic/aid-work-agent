# 智能数据分析工具（SmartDataAnalysisTool）— 开发计划

> 对应设计文档：[智能数据分析工具设计](./smart-data-analysis-tool-design.md)
> 关联功能索引：[docs/ideas_finished.md](../../ideas_finished.md) 数字员工 / 子智能体 #9
> 关联开发计划：[数据源导入功能开发计划](./data-source-import-dev-plan.md)（9a）
> 创建日期：2026-06-05（2026-07-20 按规范补登记）
> 状态：✅ 已完成开发

---

## 范围说明

实现设计文档中的统一智能分析工具：对外是注册到主智能体的 `analyze_data` 工具，对内是 `AnalysisAgent` 迷你 Agent 循环（LLM function calling 编排 `DataAnalyzer` 的 pandas/numpy 方法），一次调用完成「检索匹配表 → 加载数据 → 编排分析 → 输出结论与产物」。

不在本计划范围内：数据源导入（9a）、聊天附件数据分析（9b）、工具返回结构优化（9c）——均另有条目。

## 交付物（实际实现）

| # | 文件 | 说明 |
|---|------|------|
| 1 | `src/tools/data_analysis/smart_analysis_tool.py` | `SmartDataAnalysisTool`（`analyze_data`），对外工具接口 |
| 2 | `src/tools/data_analysis/analysis_agent.py` | `AnalysisAgent` 内部迷你 Agent 循环 |
| 3 | `src/tools/data_analysis/analysis_tools_schema.py` | 内部 function calling 的方法 schema 定义 |
| 4 | `src/tools/data_analysis/data_analyzer.py` | `DataAnalyzer` pandas/numpy 执行引擎 + DataFrame 缓存 |
| 5 | `src/core/agent.py` | `Agent._register_builtin_tools()` 注册 `SmartDataAnalysisTool` |
| 6 | `tests/unit/tools/test_smart_analysis_tool.py` | 工具单测（mock AnalysisAgent），19 项 |
| 7 | `tests/unit/tools/test_data_analyzer.py` | 执行引擎单测，130 项 |
| 8 | `tests/integration/test_data_analysis_integration.py` | 真实 LLM 端到端集成脚本（15 个分析场景） |

## 关键设计变更（相对原设计）

- `tables_metadata` 由必填改为可选：不传时由 AnalysisAgent 内部 `list_data_tables` / `search_data_tables` 自动检索匹配数据表，主智能体只需传自然语言需求。
- 工具返回结构采用 `conclusion + artifacts + analysis_meta` 三层结构（详见 9c [返回结构优化设计](./data-analysis-tool-result-redesign.md)）。

## 验收标准

- [x] 工具注册到主智能体并可被 function calling 调用
- [x] 内部 Agent 循环可自动检索表、加载数据、编排 query/aggregate/merge/pivot/calculate/compare/trend 分析
- [x] 表格产物自动导出 Excel 文件并可下载
- [x] 图表产物中文渲染正常（容器环境乱码已修复 2026-07-13）
- [x] 单元测试通过（19 + 130 项）
- [x] 集成测试脚本覆盖 15 个真实 LLM 分析场景

## 开发记录

| 日期 | 提交 | 内容 |
|------|------|------|
| 2026-06-09 | 12925ae | 添加智能数据分析工具模块 + 单元测试 |
| 2026-06-12 | 6237db1 | 上下文失效修复 + 工具消息事务持久化 |
| 2026-06-12 | 121f5b9 | 表格自动导出 Excel，修复 ready_for_download |
| 2026-07-10 | bd8bb3d | 修复孤儿元数据导致数据源不存在报错 |
| 2026-07-13 | e2a813a | 修复容器环境图表中文乱码 |
| 2026-07-20 | — | 修复 3 个过期单测（tables_metadata 改可选后未同步），补登记本计划 |
