# Excel 工具重构设计 — 移除数据分析和图表生成能力，增强读取功能

> 版本: v2.0 | 创建日期: 2026-06-09 | 状态: 📋 待开发
> 关联文档: [Excel 工具设计文档](excel_tool_design.md) | [智能数据分析工具设计](../../system/digital-employee/smart-data-analysis-tool-design.md)

---

## 1. 背景与动机

### 1.1 现状

Excel 工具（`src/tools/excel/`）当前包含 11 种操作类型，其中 `analyze` 和 `chart` 属于数据分析类能力：
- `analyze`：5 种预设数据分析（summary、correlation、distribution、anomaly、pivot）
- `chart`：在 Excel 中嵌入柱状图/折线图/饼图/散点图/面积图

数据分析工具（`src/tools/data_analysis/`，即 `SmartDataAnalysisTool`）已上线，提供更强大的分析和可视化能力：
- LLM 驱动的多步骤分析（最多 50 轮迭代）
- 14 种内部分析方法（query、aggregate、merge、pivot、calculate、compare、trend 等）
- 多表关联、层次聚合、趋势分析
- 结构化表格输出 + matplotlib 图表生成（bar、line、pie、scatter、stacked_bar、grouped_bar）

### 1.2 问题

Excel 工具的 `analyze` 和 `chart` 都属于数据分析范畴，与数据分析工具功能重叠：

| 维度 | Excel 工具 analyze/chart | 数据分析工具 |
|------|-------------------------|-------------|
| 分析类型 | 5 种固定分析模式 | 14 种灵活方法 + LLM 编排 |
| 图表类型 | openpyxl 内嵌图表（5 种） | matplotlib 独立图片（6 种），更灵活 |
| 数据源 | 单文件单表 | 多文件、多表、数据库 |
| 多表关联 | 不支持 | merge 操作 |
| 输出 | JSON 结果 + Excel 内嵌图表 | 结构化表格 + PNG 图表 + 自然语言总结 |
| 灵活性 | 固定参数 | 自然语言驱动 |

Excel 工具应该聚焦**电子表格操作**（读写、格式化、模板、转换），数据分析类能力（统计分析、图表生成）统一由数据分析工具承接。

### 1.3 目标

1. **移除 Excel 工具的 `analyze` 和 `chart` 操作**，将数据分析和图表生成需求统一交由 `SmartDataAnalysisTool` 处理
2. **增强 Excel 工具的 `read` 操作**，将 `src/tools/file/excel_reader.py`（FileReaderTool 的 ExcelReader）的读取能力复制到 Excel 工具中，使 Excel 工具具备更丰富的文档级读取能力（文档元信息、全 Sheet 文本格式化、行数统计等）
3. FileReaderTool 的 `ExcelReader` 保持不变（双保险），两条读取链路各自独立

---

## 2. 变更范围

### 2.1 需要修改的文件

| 文件 | 变更内容 |
|------|---------|
| `src/tools/excel/excel_process_tool.py` | 移除 `ANALYZE`/`CHART` 常量、`_handle_analyze`/`_handle_chart` 方法、`PipelineContext.analysis_result`、相关路由和合并逻辑、更新 TOOL_DESCRIPTION、增强 `_handle_read` |
| `src/tools/excel/excel_router.py` | 移除 analyze/chart 操作定义和判断规则，移除 `VALID_TASKS` 中的 "analyze"/"chart" |
| `src/tools/excel/excel_reader.py` | 新增 `read_excel_document()` 函数 |
| `docs/tools/excel/excel_tool_design.md` | 同步更新设计文档 |

### 2.2 需要删除的文件

| 文件 | 说明 |
|------|------|
| `src/tools/excel/excel_analyzer.py` | 数据分析子模块，由 SmartDataAnalysisTool 替代 |
| `src/tools/excel/excel_chart.py` | 图表生成子模块，由 SmartDataAnalysisTool 的 to_chart 替代 |

### 2.3 不修改的文件

| 文件 | 说明 |
|------|------|
| `src/tools/file/excel_reader.py` | FileReaderTool 的 ExcelReader 保持不变，双保险 |
| `src/tools/file/file_reader_tool.py` | FileReaderTool 调用链不变 |
| `src/tools/data_analysis/` | 数据分析工具不动 |

---

## 3. 详细设计

### 3.1 移除 analyze 和 chart 操作

#### 3.1.1 TaskType 常量清理

```python
class TaskType:
    """有效操作类型常量"""
    READ = "read"
    # ANALYZE = "analyze"  ← 删除
    TO_MD = "to_md"
    EXPORT = "export"
    MODIFY = "modify"
    FORMAT = "format"
    # CHART = "chart"      ← 删除
    FILL_TEMPLATE = "fill_template"
    LIST_TEMPLATES = "list_templates"
    MERGE = "merge"
    CONVERT = "convert"

    ALL = {READ, TO_MD, EXPORT, MODIFY, FORMAT,
           FILL_TEMPLATE, LIST_TEMPLATES, MERGE, CONVERT}
```

操作类型从 11 种缩减为 8 种。

#### 3.1.2 PipelineContext 清理

删除 `self.analysis_result` 字段。

#### 3.1.3 _get_handler 清理

删除 `"analyze"` 和 `"chart"` 两个 handler 条目。

#### 3.1.4 _update_context 清理

删除 `elif op == "analyze"` 分支。`chart` 操作的更新逻辑（将输出文件路径写回 ctx.file_paths）也随之删除。

#### 3.1.5 _merge_results 清理

删除 `elif op == "analyze"` 和 `chart` 相关的结果合并分支。

#### 3.1.6 删除方法

- 删除 `_handle_analyze` 方法（整个方法体）
- 删除 `_handle_chart` 方法（整个方法体）

#### 3.1.7 路由器 Prompt 更新

`ROUTING_PROMPT_PREFIX` 中：
- 删除 analyze 操作定义（第 2 条）
- 删除 chart 操作定义（原第 7 条）
- 删除判断规则中的 "要求分析统计 → analyze" 和 "要求生成图表 → chart"
- 其余操作编号顺延

`VALID_TASKS` 中删除 `"analyze"` 和 `"chart"`。

### 3.2 增强 read 操作

#### 3.2.1 背景

当前 Excel 工具的 `excel_reader.py` 提供 `read_sheet()` 函数，返回结构化的 headers/rows 数据。但缺少：
- 文档级元信息（文件大小、作者、创建时间等）
- 全 Sheet 文本格式化输出（固定列宽对齐的纯文本）
- 行数统计

`src/tools/file/excel_reader.py`（FileReaderTool 的 ExcelReader）具备这些能力，但属于不同的调用链路。

#### 3.2.2 方案

在 `src/tools/excel/excel_reader.py` 中新增 `read_excel_document()` 函数，从 FileReaderTool 的 `ExcelReader` 中复制核心逻辑：

```python
def read_excel_document(file_path: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """
    读取 Excel 文档的完整内容（增强版），返回结构化数据 + 文本格式化内容 + 文档元信息。

    与 read_sheet() 的区别：
    - read_sheet(): 读取指定 Sheet/范围的结构化数据（headers, rows, merged_cells, formulas）
    - read_excel_document(): 读取文档级信息（全 Sheet 文本、文档元信息、行数统计）
    """
```

#### 3.2.3 _handle_read 增强

`_handle_read` 方法增加对 `read_excel_document` 的调用支持。根据路由参数决定使用哪种读取模式：

| 路由参数 | 使用的函数 | 说明 |
|---------|-----------|------|
| 有 `range` 或 `include_formulas` | `read_sheet()` | 精确范围读取、公式读取 |
| 默认 | `read_excel_document()` | 完整文档信息 |

### 3.3 TOOL_DESCRIPTION 变更 — 大模型路由分流

Excel 工具的 `TOOL_DESCRIPTION` 是主智能体判断何时调用该工具的唯一依据。移除 analyze/chart 后，必须同步更新描述。

#### 3.3.1 变更后 TOOL_DESCRIPTION

```python
TOOL_DESCRIPTION = """Excel电子表格处理工具。处理Excel(.xlsx/.csv)文件的读取、创建、修改、格式化、模板填充、格式转换等操作。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求导出、生成、创建Excel文件
- 用户要求读取、修改、格式化Excel文件
- 用户要求将数据(表格、CSV、JSON)转为Excel
- 用户要求将Excel转为其他格式(Markdown、CSV)
- 用户要求基于模板填充数据生成Excel报告
- 用户上传了.xlsx/.csv文件并要求处理

🚫 以下场景不要调用本工具，请使用对应专用工具：
- 用户要求对数据进行**统计分析、趋势分析、对比分析、异常检测**等 → 调用 analyze_data 工具
- 用户要求**生成图表**（柱状图、折线图、饼图等） → 调用 analyze_data 工具
- 用户要求从多个数据源关联分析 → 调用 analyze_data 工具

调用方式（重要）：
- context 参数必须包含**完整的表格数据**，不能只传用户意图描述
- 如果需要将数据导出为Excel，context 中必须包含完整的 Markdown表格 或 JSON数组 数据
- 如果当前对话中已有表格数据（由其他工具生成或用户提供），必须将其完整放入 context 中
- 如果还没有表格数据，Agent 应先通过其他方式准备好数据，再调用本工具
- 用户上传的附件路径放在 file_paths 中
工具会自动判断并执行合适的操作。"""
```

**关键变更点**：
1. 触发规则中移除"分析"和"生成图表"相关的条目
2. 🚫 不触发规则中明确列出：统计分析、图表生成都走 `analyze_data`
3. Excel 工具定位清晰：纯粹的电子表格操作工具

#### 3.3.2 analyze_data 工具描述确认

`SmartDataAnalysisTool.description` 当前为：
```
"智能数据分析工具。接收用户的分析需求，自动搜索匹配相关数据表，
编排分析步骤（查询、聚合、关联、对比、趋势、透视、计算、可视化等），
一次性完成分析并返回结果摘要和图表文件。"
```

该描述已覆盖数据分析和图表生成场景，**无需修改**。

---

## 4. 设计文档更新

`docs/tools/excel/excel_tool_design.md` 需要同步更新：

| 章节 | 变更 |
|------|------|
| 2.1 核心能力表格 | 移除"数据分析"和"图表生成"两项 |
| 3.2 架构图 | 移除 `excel_analyzer.py → analyze` 和 `excel_chart.py → chart` |
| 4.1 目录结构 | 移除 `excel_analyzer.py` 和 `excel_chart.py` |
| 4.2 TOOL_DESCRIPTION | 替换为 §3.3.1 的新版描述 |
| 4.3 TaskType | 移除 `ANALYZE` 和 `CHART` |
| 4.3 路由 Prompt | 移除 analyze 和 chart 操作定义 |
| 4.4.5 excel_chart.py 章节 | 整节删除 |
| 4.4.8 excel_analyzer.py 章节 | 整节删除 |
| 4.4.1 excel_reader.py 章节 | 新增 `read_excel_document()` 说明 |
| 4.5 Pipeline 上下文 | 移除 `analysis_result` 字段 |
| 4.6 Pipeline 组合示例 | 移除含 analyze/chart 的组合 |
| 9 开发优先级 | P1 表格中移除 excel_analyzer 和 excel_chart 行 |

---

## 5. 影响分析

| 变更 | 影响 | 处理方式 |
|------|------|---------|
| 移除 analyze | 用户说"分析这个 Excel"时不再走 Excel 工具 | 由主智能体路由到 `analyze_data` |
| 移除 chart | 用户说"画个图表"时不再走 Excel 工具 | 由主智能体路由到 `analyze_data`（to_chart） |
| read 增强 | Excel 工具的 read 操作返回更丰富的信息 | 向后兼容 |
| 删除 excel_analyzer.py | 无外部直接调用 | 无影响 |
| 删除 excel_chart.py | 无外部直接调用 | 无影响 |

---

## 6. 开发计划

详见 [Excel 工具重构开发计划](excel-tool-refactor-dev-plan.md)。

---

## 7. 验证标准

1. Excel 工具不再接受 `analyze` 和 `chart` 操作，调用返回错误
2. 路由器不再将"分析"和"图表"意图路由到 Excel 工具
3. Excel 工具的 read 操作能返回文档元信息（file_size, author, created 等）和全 Sheet 文本
4. FileReaderTool 的 ExcelReader 不受影响，正常工作
5. SmartDataAnalysisTool 能正常处理 Excel 文件分析和图表生成
6. 现有测试全部通过（除了依赖 analyze/chart 的测试需删除或更新）
