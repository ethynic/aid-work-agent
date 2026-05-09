# Excel 智能处理工具设计文档

> 版本: v1.1 | 创建日期: 2026-05-09 | 状态: 已完成（P0+P1+P2 全部交付）

## 1. 背景与动机

### 1.1 现状分析

项目中 Excel 相关功能分散在 **5 个位置**，职责重叠，能力有限：

| 位置 | 功能 | 问题 |
|------|------|------|
| `src/skills/excel-data-assistant/` | 数据分析、清洗、聚合、图表 | Skill 脚本通过 subprocess 调用，无法直接操作文件对象 |
| `src/tools/file/excel_reader.py` | 读取 Excel 文件内容 | 仅读取，格式化为文本，无修改能力 |
| `src/knowledge/parsers/excel_parser.py` | 知识库 Excel 解析 | 仅文本提取，非业务用途 |
| `src/skills/quote-export/` | 报价导出 | 特定业务场景，不可复用 |
| `src/tools/word/` (md_to_word) | 无 | Word 工具已有，Excel 没有 |

**核心缺失能力**：
- 无法根据模板生成 Excel 报告（填充占位符）
- 无法将表格数据/CSV 导出为格式化的 Excel
- 无法将 Excel 转为 Markdown 供大模型理解
- 无法对 Excel 进行增删改操作（单元格、行、列、Sheet）
- 没有统一的 LLM 路由入口，用户需求需要精确匹配到具体 Skill

### 1.2 为什么做成 Tool 而非 Skill

参考 Word 工具的演进路径（从 Skill 迁移为 Tool），Excel 也应该走同一条路：

| 维度 | Skill 模式 | Tool 模式（目标） |
|------|-----------|-----------------|
| 调用方式 | 外部 Agent 需要精确匹配技能名，调用 `use_skill` → `skill_execute` | 外部 Agent 只需调用 `excel_process`，传 context + file_paths |
| 参数结构 | 需要外部 Agent 构造精确的 JSON 参数（如 `--operations`） | 内部 LLM Router 自动解析用户意图，生成操作参数 |
| 能力组合 | 每个脚本是独立的，无法组合（如"读取+分析+导出"需要多次调用） | Pipeline 机制支持多步骤串联（如 `read,analyze,export`） |
| 文件传递 | 脚本间通过临时文件和 JSON 传递，需要外部 Agent 串联 | PipelineContext 自动传递中间结果 |
| 新增能力 | 每增加一个功能需要新建脚本 + 更新 SKILL.md | 新增子模块 + 在 Router prompt 中添加规则即可 |

---

## 2. 设计目标

### 2.1 核心能力

一个 LLM 驱动的 Excel 处理工具，覆盖以下场景：

| # | 能力 | 说明 |
|---|------|------|
| 1 | **Excel → Markdown** | 将 Excel 内容转为 Markdown 表格，供大模型理解 |
| 2 | **Markdown/CSV/JSON → Excel** | 将结构化数据导出为格式化的 Excel 文件 |
| 3 | **模板填充** | 加载 .xlsx 模板，替换占位符 `{{var}}` 为实际数据 |
| 4 | **数据读取** | 读取指定 Sheet、范围，返回结构化数据 |
| 5 | **数据修改** | 写入单元格、插入/删除行列、合并单元格 |
| 6 | **格式化** | 设置字体、边框、背景色、数字格式、列宽行高 |
| 7 | **图表生成** | 根据数据创建柱状图、折线图、饼图等 |
| 8 | **数据分析** | 统计摘要、透视分析、异常检测 |
| 9 | **多文件合并** | 将多个 Excel/CSV 文件合并为一个 |
| 10 | **文件格式转换** | CSV ↔ Excel、JSON ↔ Excel 互转 |

### 2.2 非目标

- 不做 Excel 公式计算引擎（不替代 Excel 本身的计算能力）
- 不做 .xls（Excel 97-2003）的完整支持，仅提示用户转换为 .xlsx
- 不做 VBA 宏/XLSM 的读取或生成
- 不做实时协作编辑

---

## 3. 技术选型

### 3.1 库选择

| 库 | 版本 | 用途 | License |
|---|------|------|---------|
| **openpyxl** >= 3.1.0 | 已有依赖 | 核心：读写 xlsx，样式，图表 | MIT |
| **markitdown** | 新增 | Excel → Markdown 转换（供 LLM 理解） | MIT |
| **pandas** >= 2.0.0 | 已有依赖 | 数据分析、聚合、透视计算 | BSD-3 |
| **chardet** >= 5.0.0 | 已有依赖 | CSV 编码检测 | LGPL |

**不引入的库**：
- **XlsxWriter**：与 openpyxl 功能重叠，且不能读/改已有文件。openpyxl 已满足创建新文件需求
- **xlrd**：仅支持 .xls，优先提示用户转换
- **tablib**：pandas + openpyxl 已覆盖格式转换需求
- **python-calamine**：读性能优化，当前场景不需要
- **xltpl/xlsxtpl**：需要用户在单元格中写 Jinja2 语法（`{% for %}`），对非技术用户门槛太高。本项目自研简化模板引擎，仅用 `{{变量名}}` 一种语法

### 3.2 架构模式

**复用 Word 工具的"LLM 内部路由 + Pipeline"模式**：

```
外部 Agent (大模型)
    │
    │ 调用 excel_process 工具，传 {context, file_paths}
    ▼
ExcelProcessTool (BaseTool 子类)
    │
    │ 调用内部 LLM 路由器
    ▼
ExcelRouter (使用 LLMGateway.chat)
    │
    │ 返回 {task: "read,export", params: {...}, reason: "..."}
    ▼
Pipeline 执行器 (按顺序分发到各 handler)
    │
    ├─ excel_reader.py      → read / analyze
    ├─ excel_writer.py      → export / merge / convert
    ├─ excel_modifier.py    → modify (单元格编辑、行列操作)
    ├─ excel_formatter.py   → format (样式、格式)
    ├─ excel_chart.py       → chart (图表生成)
    ├─ excel_template.py    → fill_template / list_templates
    ├─ excel_to_md.py       → to_md (Excel → Markdown)
    └─ excel_analyzer.py    → analyze (数据统计分析)
```

---

## 4. 详细设计

### 4.1 目录结构

```
src/tools/excel/
├── __init__.py                 # 导出 ExcelProcessTool
├── excel_process_tool.py       # 入口：BaseTool 子类 + Pipeline 执行器
├── excel_router.py             # 内部 LLM 路由器
├── excel_lib.py                # 共享工具函数、常量映射
├── excel_reader.py             # 读取 Excel 数据
├── excel_writer.py             # 导出/创建 Excel
├── excel_modifier.py           # 修改 Excel 内容
├── excel_formatter.py          # 格式化样式
├── excel_chart.py              # 图表生成
├── excel_template.py           # 模板管理（列表 + 填充）
├── excel_to_md.py              # Excel → Markdown
└── excel_analyzer.py           # 数据分析
```

### 4.2 工具注册

```python
# src/tools/excel/excel_process_tool.py

class ExcelProcessInput(BaseModel):
    context: Optional[str] = Field(
        default=None,
        description="用户的原始需求描述，包含所有相关内容"
    )
    file_paths: Optional[List[str]] = Field(
        default=None,
        description="用户上传的附件文件路径列表"
    )

class ExcelProcessTool(BaseTool):
    name = "excel_process"
    description = TOOL_DESCRIPTION
    InputModel = ExcelProcessInput
```

**TOOL_DESCRIPTION**（对外部 Agent 的说明）：

```
Excel电子表格处理工具。所有与Excel(.xlsx)相关的操作都通过本工具处理。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求导出、生成、创建Excel文件
- 用户要求读取、分析、修改Excel文件
- 用户要求将数据(表格、CSV、JSON)转为Excel
- 用户要求将Excel转为其他格式(Markdown、CSV)
- 用户要求基于模板填充数据生成Excel报告
- 用户要求对Excel数据进行统计分析、生成图表
- 用户上传了.xlsx/.csv文件并要求处理
不要自己生成文件内容，一律交给本工具。

调用方式：
- 将用户的原始需求描述和相关内容放在 context 中
- 如果需要将对话中的表格数据转为Excel，context 中必须包含表格数据（Markdown表格或JSON数组）
- 用户上传的附件路径放在 file_paths 中
工具会自动判断并执行合适的操作。
```

### 4.3 内部路由器

#### 操作类型定义

```python
class TaskType:
    READ = "read"               # 读取 Excel 数据
    ANALYZE = "analyze"         # 数据统计分析
    TO_MD = "to_md"             # Excel → Markdown
    EXPORT = "export"           # 创建/导出 Excel（从数据生成新文件）
    MODIFY = "modify"           # 修改已有 Excel
    FORMAT = "format"           # 格式化样式
    CHART = "chart"             # 生成图表
    FILL_TEMPLATE = "fill_template"  # 模板填充
    LIST_TEMPLATES = "list_templates"  # 列出可用模板
    MERGE = "merge"             # 合并多个文件
    CONVERT = "convert"         # 格式转换（CSV↔Excel、JSON↔Excel）

    ALL = {READ, ANALYZE, TO_MD, EXPORT, MODIFY, FORMAT, CHART,
           FILL_TEMPLATE, LIST_TEMPLATES, MERGE, CONVERT}
```

#### 路由 Prompt（草案）

```
你是 Excel 电子表格处理工具的内部路由器。根据用户的上下文和附件，判断需要执行的操作并提取参数。

## 可用操作

1. **read** — 读取 Excel 文件的数据内容
   - 触发：用户想看Excel里有什么数据、查看某个Sheet
   - 参数：{sheet_name: "可选", range: "可选,如A1:D10", include_formulas: false}

2. **analyze** — 对数据进行统计分析
   - 触发：用户要求分析数据、统计摘要、找异常
   - 参数：{sheet_name: "可选", analysis_type: "summary|anomaly|pivot", group_by: "可选", aggregations: "可选"}

3. **to_md** — 将 Excel 转为 Markdown 表格
   - 触发：用户要求查看Excel内容（给大模型看的场景）、将Excel转为文本
   - 参数：{sheet_name: "可选", max_rows: 50}

4. **export** — 从数据创建新的 Excel 文件
   - 触发：用户要求导出数据为Excel、把表格数据存为Excel、创建报表
   - 参数：{data_type: "markdown|csv|json|table", data: "数据内容或已在context中",
           file_name: "输出文件名", sheet_name: "可选", auto_format: true}

5. **modify** — 修改已有 Excel 文件内容
   - 触发：用户要求修改单元格、插入行列、删除行列、合并单元格
   - 参数：{operations: [{type, ...具体参数}], output_name: "可选"}

6. **format** — 设置 Excel 格式样式
   - 触发：用户要求设置字体、边框、颜色、列宽、数字格式
   - 参数：{format_operations: [{type, ...具体参数}], output_name: "可选"}

7. **chart** — 生成图表
   - 触发：用户要求创建图表、画柱状图/折线图/饼图
   - 参数：{chart_type: "bar|line|pie|scatter", x_column: "", y_columns: [],
           title: "可选", sheet_name: "可选"}

8. **fill_template** — 使用模板填充数据
   - 触发：用户要求基于模板生成Excel、按模板填写数据
   - 模板来源有两种（路由器必须区分）：
     a) 系统模板：用户从已有模板列表中选择 → template_name 参数
     b) 用户上传的临时模板：附件中包含 .xlsx 且上下文明确提到"模板"、"按这个格式"等 → template_file 参数（附件路径）
   - 模板有两种填充模式（工具自动检测，路由器无需指定）：
     - 占位符模式：模板中有 {{变量}} 等 Jinja2 语法，由 xltpl 引擎处理
     - 结构感知模式：模板中无占位符但有表头-数据区域结构，由 openpyxl 引擎处理
   - 参数：{template_name: "系统模板名（二选一）", template_file: "用户上传的模板文件路径（二选一）", variables: {key: value}, output_name: "可选"}
   - variables 中：字符串/数字值为单值替换；列表值为行循环数据（占位符模式）或数据行填充（结构感知模式）

9. **list_templates** — 列出可用模板
   - 触发：用户问有哪些模板可用、想选择一个系统模板
   - 参数：{}

10. **merge** — 合并多个文件
    - 触发：用户要求合并多个Excel/CSV文件
    - 参数：{output_name: "可选", merge_mode: "rows|columns|sheets"}

11. **convert** — 格式转换
    - 触发：用户要求CSV转Excel、Excel转CSV、JSON转Excel
    - 参数：{source_format: "csv|json|excel", target_format: "csv|json|excel", output_name: "可选"}

## 决策规则

按优先级从高到低匹配：
- 有附件且上下文提到"模板"、"按这个格式"、"照着这个填"等 → fill_template（template_file = 附件路径）
- 要求基于系统模板生成（无附件模板，提到模板名或要求选择） → fill_template（template_name = 模板名）或先 list_templates
- 有附件且要求转为Markdown理解 → to_md
- 要求生成图表 → chart（可能需要先 read 获取列信息）
- 要求修改已有Excel → modify
- 要求设置样式格式 → format
- 要求创建新Excel / 导出数据 → export
- 要求格式转换 → convert
- 要求合并文件 → merge
- 要求分析统计 → analyze
- 要求查看数据内容 → read
- 仅有附件无明确指令 → to_md（默认将内容转为可理解格式）

## 输出格式

返回严格 JSON，不要包含其他文本：
{
  "task": "操作名（可逗号分隔多个，如 read,chart）",
  "params": { ... 操作对应参数 ... },
  "reason": "判断依据"
}

## fill_template 参数示例

场景一：用户选择系统模板
用户："用月度报告模板生成4月份报告"
→ params: {template_name: "monthly_report", variables: {month: "4月", ...}}

场景二：用户上传临时模板
用户上传 attachment.xlsx 后说："按这个模板填一下，公司名是XX科技"
→ params: {template_file: "/path/to/attachment.xlsx", variables: {公司名: "XX科技"}}

场景三：用户不确定有什么模板
用户："有哪些报告模板？"
→ task: "list_templates"
```

### 4.4 各子模块详细设计

#### 4.4.1 excel_reader.py — 数据读取

```python
def read_sheet(file_path: str, sheet_name: str = None,
               cell_range: str = None, include_formulas: bool = False) -> Dict:
    """
    读取 Excel 文件数据。

    返回：
    {
        "file_name": "report.xlsx",
        "sheets": ["Sheet1", "Sheet2"],
        "active_sheet": "Sheet1",
        "headers": ["Name", "Amount", "Date"],
        "rows": [["张三", 1000, "2026-01-01"], ...],
        "row_count": 100,
        "column_count": 5,
        "merged_cells": ["A1:C1", ...],
        "formulas": {"B5": "=SUM(B2:B4)"}  # only if include_formulas=True
    }
    """
```

**核心能力**：
- 支持指定 Sheet（默认第一个）
- 支持指定范围（如 `A1:D10`）
- 支持读取公式（`data_only=False`）或公式结果值（`data_only=True`，默认）
- 自动识别表头行（第一个非空行）
- 返回合并单元格信息
- 支持 `.xlsx` 和 `.csv`（自动检测）

#### 4.4.2 excel_writer.py — 创建/导出 Excel

```python
def create_excel(data: Any, data_type: str = "markdown",
                 file_name: str = None, sheet_name: str = "Sheet1",
                 auto_format: bool = True) -> Dict:
    """
    从结构化数据创建 Excel 文件。

    data_type 支持：
    - "markdown": Markdown 表格文本 → 解析为数据
    - "csv": CSV 文本 → 解析为数据
    - "json": JSON 数组 → 直接使用
    - "table": [[row1], [row2], ...] 二维数组
    - "dict_list": [{key: val, ...}, ...] 字典列表

    auto_format=True 时自动应用：
    - 表头加粗蓝色背景
    - 自动列宽（考虑中文宽度）
    - 边框
    - 数字/日期格式自动检测

    返回：
    {
        "success": true,
        "file_path": "/tmp/output.xlsx",
        "file_name": "output.xlsx",
        "sheet_name": "Sheet1",
        "row_count": 50,
        "download_url": "/api/download/output.xlsx"
    }
    """

def write_multi_sheet(sheets_data: Dict[str, Any], file_name: str = None) -> Dict:
    """
    创建多 Sheet 的 Excel 文件。

    sheets_data: {"销售数据": markdown_str, "汇总": json_arr, ...}
    """

def append_to_excel(file_path: str, data: Any, sheet_name: str = None,
                    start_row: int = None) -> Dict:
    """
    向已有 Excel 追加数据。
    """
```

#### 4.4.3 excel_modifier.py — 内容修改

```python
def batch_modify(file_path: str, operations: List[Dict],
                 output_name: str = None) -> Dict:
    """
    批量修改 Excel 内容。

    支持的操作类型（operations 列表中每项的 type 字段）：

    1. write_cell — 写入单元格
       {type: "write_cell", sheet: "Sheet1", cell: "B3", value: 100}

    2. write_range — 批量写入区域
       {type: "write_range", sheet: "Sheet1", start: "A1",
        data: [[h1,h2],[v1,v2]]}

    3. insert_rows — 插入行
       {type: "insert_rows", sheet: "Sheet1", position: 5, count: 3}

    4. delete_rows — 删除行
       {type: "delete_rows", sheet: "Sheet1", start: 5, count: 3}

    5. insert_columns — 插入列
       {type: "insert_columns", sheet: "Sheet1", position: 3, count: 2}

    6. delete_columns — 删除列
       {type: "delete_columns", sheet: "Sheet1", start: 3, count: 2}

    7. rename_sheet — 重命名 Sheet
       {type: "rename_sheet", old_name: "Sheet1", new_name: "销售数据"}

    8. add_sheet — 添加新 Sheet
       {type: "add_sheet", name: "新Sheet", data: [[...]]}

    9. delete_sheet — 删除 Sheet
       {type: "delete_sheet", name: "Sheet2"}

    10. merge_cells — 合并单元格
        {type: "merge_cells", sheet: "Sheet1", range: "A1:D1"}

    11. unmerge_cells — 取消合并
        {type: "unmerge_cells", sheet: "Sheet1", range: "A1:D1"}

    12. sort_data — 排序
        {type: "sort_data", sheet: "Sheet1", column: "B",
         order: "asc", has_header: true}
    """
```

#### 4.4.4 excel_formatter.py — 格式化样式

```python
def batch_format(file_path: str, format_operations: List[Dict],
                 output_name: str = None) -> Dict:
    """
    批量设置 Excel 格式。

    支持的操作类型：

    1. set_font — 设置字体
       {type: "set_font", range: "A1:D1", font_name: "微软雅黑",
        size: 12, bold: true, color: "FFFFFF"}
       支持 scope: "header" | range | sheet

    2. set_fill — 设置背景色
       {type: "set_fill", range: "A1:D1", color: "4472C4"}

    3. set_border — 设置边框
       {type: "set_border", range: "A1:D10", style: "thin",
        color: "000000"}

    4. set_alignment — 设置对齐
       {type: "set_alignment", range: "A1:D10",
        horizontal: "center", vertical: "center", wrap_text: true}

    5. set_number_format — 设置数字格式
       {type: "set_number_format", range: "B2:B100",
        format: "#,##0.00"}  // 或 "0%" / "yyyy-mm-dd"

    6. set_column_width — 设置列宽
       {type: "set_column_width", columns: ["A","B","C"],
        widths: [15, 20, 30]}  // 或 "auto" 自动适配

    7. set_row_height — 设置行高
       {type: "set_row_height", rows: [1], height: 30}

    8. freeze_panes — 冻结窗格
       {type: "freeze_panes", sheet: "Sheet1", cell: "A2"}

    9. auto_filter — 添加自动筛选
       {type: "auto_filter", sheet: "Sheet1", range: "A1:D100"}
    """
```

#### 4.4.5 excel_chart.py — 图表生成

```python
def create_chart(file_path: str, chart_type: str,
                 x_column: str, y_columns: List[str],
                 title: str = None, sheet_name: str = None,
                 output_name: str = None, **kwargs) -> Dict:
    """
    在 Excel 中创建图表。

    chart_type: bar | line | pie | scatter | area

    额外参数（kwargs）：
    - group_by: 分组列名（簇状柱形图场景）
    - style: 图表样式编号 (1-48)
    - width: 图表宽度 (默认 20)
    - height: 图表高度 (默认 12)
    - x_title: X轴标题
    - y_title: Y轴标题
    - show_legend: 是否显示图例 (默认 true)
    - show_labels: 是否显示数据标签 (默认 false)

    返回：
    {
        "success": true,
        "file_path": "/tmp/chart_output.xlsx",
        "chart_type": "bar",
        "chart_title": "月度销售统计",
        "data_source": "Sheet1!A1:D13"
    }
    """
```

**设计决策**：图表直接嵌入到 Excel 文件中（而非生成图片），保持 Excel 原生可编辑。

#### 4.4.6 excel_template.py — 模板管理

**模板来源分为两种**：

| 来源 | 标识 | 模板位置 | 生命周期 |
|------|------|----------|----------|
| **系统模板** | `template_name` | `storage/excel_templates/` | 持久化，所有用户共享 |
| **用户上传模板** | `template_file` | 用户附件临时路径 | 单次会话有效 |

##### 模板语法 — 只有一种：`{{变量名}}`

用户只需学习一条规则：在 Excel 单元格中写 `{{变量名}}`，系统根据变量值的类型自动决定行为：

| 变量值类型 | 行为 | 示例 |
|-----------|------|------|
| 字符串/数字 | 直接替换单元格文本 | `{{公司名}}` → `XX科技有限公司` |
| 字典列表（多行数据） | 从当前行开始向下展开为多行 | `{{产品明细}}` 见下方详解 |

**多行数据（字典列表）的处理规则**：

当 `{{变量名}}` 对应的值是一个 `[dict, dict, ...]` 列表时：
1. 检测当前行的所有 `{{变量名}}`，收集其中值是列表的那些变量
2. 同一行的列表变量必须等长（如 `{{产品名}}` 和 `{{数量}}` 都是 3 条数据）
3. 将当前行作为**模板行**复制 N 份（N = 列表长度）
4. 每份中对应的 `{{变量名}}` 替换为列表中对应项的值
5. 模板行的格式（字体、边框、对齐、行高、合并单元格）自动复制到每一行

**一行多字段 — 两种配置方式**：

**方式 A：每个字段一个 `{{变量名}}`（推荐）**

用户在模板的一行中，每个单元格写一个变量名：

```
| A          | B        | C        | D        |
|------------|----------|----------|----------|
| 产品名     | 数量     | 单价     | 金额     |   ← 表头（普通文本，不需要 {{}}）
| {{名称}}   | {{数量}} | {{单价}} | {{金额}} |   ← 数据行模板
| 合计       |          |          |          |   ← 汇总行
```

variables：
```json
{
    "名称": ["产品A", "产品B", "产品C"],
    "数量": [100, 200, 50],
    "单价": [50, 40, 80],
    "金额": [5000, 8000, 4000]
}
```

所有列表长度 = 3，模板行复制 3 份，每份填入对应索引的值。结果：

```
| 产品名     | 数量     | 单价     | 金额     |
| 产品A      | 100      | 50       | 5000     |   ← 格式与模板行一致
| 产品B      | 200      | 40       | 8000     |
| 产品C      | 50       | 80       | 4000     |
| 合计       |          |          |          |   ← 汇总行自动下移
```

**方式 B：一个 `{{变量名}}` 对应字典列表（值是对象数组）**

用户在一个或多个单元格写变量名，变量值是字典列表，字典的 key 与同列的**表头文本**匹配：

```
| A        | B    | C    | D     |
|----------|------|------|-------|
| 产品名   | 数量 | 单价 | 金额  |    ← 表头
| {{产品}} |      |      |       |    ← 只需在一个单元格写 {{变量名}}
```

变量名可以写在任意一个数据单元格中，只需写一次。
variables：
```json
{
    "产品": [
        {"产品名": "产品A", "数量": 100, "单价": 50, "金额": 5000},
        {"产品名": "产品B", "数量": 200, "单价": 40, "金额": 8000}
    ]
}
```

系统检测到值是字典列表后，取每个字典的 key 与**该列对应的表头文本**匹配，填入正确的列：

```
| 产品名   | 数量 | 单价 | 金额  |
| 产品A    | 100  | 50   | 5000  |
| 产品B    | 200  | 40   | 8000  |
```

**两种方式对比**：

| 维度 | 方式 A（每列一个变量） | 方式 B（字典列表） |
|------|----------------------|-------------------|
| 模板写法 | 每个数据单元格都写 `{{变量名}}` | 只需一个单元格写 `{{变量名}}` |
| 数据格式 | 每个变量是独立数组 | 一个变量是字典数组 |
| 适合场景 | 列之间独立（如姓名和电话分开提供） | 列之间关联（如一条产品有多个字段） |
| 列映射 | 变量名 → 单元格（位置对应） | 字典 key → 表头文本（语义对应） |
| **推荐** | 模板中每列都有独立变量时 | Agent 内部路由器构造数据时 |

**注意**：两种方式可以混用。同一模板中，部分列用方式 A，部分用方式 B，互不冲突。
系统按以下优先级处理每个 `{{变量名}}`：
1. 值是字符串/数字 → 直接替换
2. 值是简单列表 `["A", "B", "C"]` → 按索引填入当前列（方式 A）
3. 值是字典列表 `[{...}, {...}]` → 按表头文本匹配填入所有列（方式 B）

##### 完整模板示例

模板文件 `报价单.xlsx`：

```
Row 1: | {{公司名}}报价单                                              |     |          |      |
Row 2: | 客户: {{客户名}}           日期: {{日期}}                     |     |          |      |
Row 3: | 序号  | 产品名称    | 规格     | 数量   | 单价   | 金额      |     |          |      |
Row 4: | {{序号}} | {{产品名称}} | {{规格}} | {{数量}} | {{单价}} | {{金额}} |     |          |      |
Row 5: |      | 合计         |          |        |        | {{总金额}} |     |          |      |
```

variables：
```json
{
    "公司名": "YY科技有限公司",
    "客户名": "ABC贸易公司",
    "日期": "2026-05-09",
    "序号": [1, 2, 3],
    "产品名称": ["电机A", "电机B", "控制器"],
    "规格": ["220V", "380V", "V2.0"],
    "数量": [50, 30, 10],
    "单价": [200, 350, 500],
    "金额": [10000, 10500, 5000],
    "总金额": 25500
}
```

结果：Row 4 展开为 3 行数据行（格式与模板行一致），Row 5 汇总行自动下移到 Row 7。

```python
def list_templates() -> Dict:
    """列出可用的系统 Excel 模板。"""

def detect_variables(file_path: str) -> Dict:
    """
    扫描 Excel 文件，提取所有 {{变量名}} 占位符。

    返回：
    {
        "variables": {
            "公司名": {"cells": ["A1"], "type": "single"},
            "序号": {"cells": ["A4"], "type": "unknown"},  // 运行时根据值类型决定
            "产品名称": {"cells": ["B4"], "type": "unknown"}
        },
        "row_template": {
            "row": 4,                    // 包含列表变量的行号
            "columns": ["A","B","C","D","E","F"],
            "headers": ["序号","产品名称","规格","数量","单价","金额"]  // 对应表头文本
        },
        "variable_count": 9,
        "has_row_variables": true         // 是否有需要展开多行的变量
    }
    """

def fill_template(template_name: str = None, template_file: str = None,
                  variables: Dict[str, Any] = None,
                  output_name: str = None) -> Dict:
    """
    使用数据填充 Excel 模板。

    模板来源（二选一）：
    - template_name: 从系统模板目录加载
    - template_file: 使用用户上传的文件路径

    填充逻辑：
    1. 扫描所有单元格，收集 {{变量名}} 占位符及其位置
    2. 按 variables 中的值类型分类：
       - 字符串/数字 → 单值替换
       - 列表 → 标记为行展开变量
    3. 对行展开变量：
       a. 找到所有包含列表变量的行（模板行）
       b. 验证同一行的列表变量长度一致
       c. 在模板行位置插入 (N-1) 行（N=列表长度），复制模板行格式
       d. 逐行填入对应的列表值
    4. 对单值变量：直接替换单元格文本
    5. 处理汇总行自动下移（insert_rows 会自然推开下方行）

    返回：
    {
        "success": true,
        "file_path": "/tmp/filled_report.xlsx",
        "template_source": "user_upload",
        "variables_replaced": 9,
        "rows_inserted": 2,            // 额外插入的行数（3行数据 - 1行模板 = 2）
        "unmatched_variables": []       // 模板中有但 variables 中没有的变量
    }
    """
```

##### 行展开的核心实现

```python
from copy import copy

def _expand_row_variables(ws, row_idx, row_vars, variables):
    """
    处理一行的列表变量展开。

    row_vars: 该行中所有值是列表的变量信息 [{"name":"序号","col":1}, ...]
    variables: 完整的变量字典

    步骤：
    1. 获取列表长度 N（所有列表变量必须等长）
    2. 先保存模板行的所有单元格样式和合并范围
    3. ws.insert_rows(row_idx + 1, amount=N - 1)  // 插入 N-1 行
    4. 对每行 i (0 ~ N-1)：
       a. 目标行 = row_idx + i
       b. 从保存的模板样式复制到目标行：dst._style = copy(src._style)
       c. 复制行高：ws.row_dimensions[目标行].height = 模板行高度
       d. 复制合并单元格范围（偏移到新行）
       e. 对每个列表变量：ws.cell(目标行, col).value = values[i]
    5. 原模板行的单元格文本已被 insert_rows 推到 row_idx + N - 1
       → 替换最后一个展开行的值
    """
```

**格式复制要点**：
- `dst._style = copy(src._style)` — 一次性复制所有样式（字体、边框、背景、数字格式、对齐），这是 openpyxl 内部最高效的方式
- 行高通过 `ws.row_dimensions` 复制
- 合并单元格：unmerge 模板行 → 在新行中 re-merge（openpyxl 的 insert_rows 不自动处理合并范围）
- 如果模板行是 Excel Table 的一部分，手动更新 `table.ref` 范围

**模板存放规则**：
- 系统模板目录：`storage/excel_templates/`
- 系统模板命名：`<template_name>.xlsx`（如 `monthly_report.xlsx`）
- 系统模板元数据：可选的 `<template_name>.json`（描述变量、用途等）
- 用户上传模板：直接使用附件路径，不复制到模板目录

**无占位符时的 fallback**：

如果用户上传的模板没有 `{{...}}` 但有明确的表头+数据区域结构，
系统进入**结构感知模式**（见 7.3 模式二），自动检测表头行，
将 variables 中的列表数据按表头文本映射填入数据区域。
此模式不需要用户在模板中写任何占位符。

#### 4.4.7 excel_to_md.py — Excel → Markdown

```python
def excel_to_markdown(file_path: str, sheet_name: str = None,
                      max_rows: int = 100) -> Dict:
    """
    将 Excel 转为 Markdown 表格文本，供大模型理解。

    使用 markitdown 库进行转换。

    参数：
    - file_path: Excel 文件路径
    - sheet_name: 指定 Sheet（默认全部）
    - max_rows: 每个 Sheet 最大行数（防止超长输出）

    返回：
    {
        "success": true,
        "markdown": "## Sheet1\n\n| Name | Amount |\n|---|---|\n| ... |",
        "sheet_count": 2,
        "total_rows": 50,
        "truncated": false
    }
    """
```

#### 4.4.8 excel_analyzer.py — 数据分析

```python
def analyze_data(file_path: str, sheet_name: str = None,
                 analysis_type: str = "summary",
                 **kwargs) -> Dict:
    """
    对 Excel 数据进行统计分析。

    analysis_type:
    - "summary": 基础统计摘要（每列的类型、空值率、唯一值、数值列的 min/max/mean/median）
    - "correlation": 相关性分析（数值列之间的相关系数矩阵）
    - "distribution": 分布分析（直方图数据、分位数）
    - "anomaly": 异常值检测（基于 IQR 或 Z-Score）
    - "pivot": 透视分析（需指定 group_by 和 aggregations）

    透视分析参数：
    - group_by: 分组列名
    - aggregations: [{column: "amount", function: "sum"}, ...]
    - sort_by: 排序列
    - sort_order: "asc" | "desc"

    返回：
    {
        "success": true,
        "analysis_type": "summary",
        "result": {
            "row_count": 1000,
            "column_count": 8,
            "columns": [
                {"name": "amount", "type": "float", "null_rate": 0.02,
                 "min": 100, "max": 50000, "mean": 5600, "median": 3200}
            ],
            "suggestions": ["amount 列有 5% 的异常值（超出 Q3+1.5*IQR）"]
        }
    }
    """
```

#### 4.4.9 excel_lib.py — 共享工具

```python
# 常量映射
FONT_MAP = {
    "微软雅黑": "Microsoft YaHei",
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    # ...
}

NUMBER_FORMATS = {
    "千分位": "#,##0.00",
    "百分比": "0.00%",
    "整数": "#,##0",
    "日期": "yyyy-mm-dd",
    "货币": "¥#,##0.00",
    # ...
}

class ExcelFileHandler:
    """文件操作工具类"""

    @staticmethod
    def copy_and_open(file_path: str) -> openpyxl.Workbook:
        """复制文件到临时目录并打开，不修改原文件"""

    @staticmethod
    def save_and_register(wb: openpyxl.Workbook, file_name: str) -> Dict:
        """保存 Workbook 到临时目录并注册下载"""

    @staticmethod
    def resolve_path(file_path: str) -> str:
        """解析文件路径（相对路径 → 绝对路径）"""

    @staticmethod
    def detect_file_type(file_path: str) -> str:
        """检测文件类型（xlsx/csv/json）"""

def parse_range(range_str: str) -> Tuple:
    """解析 Excel 范围字符串 (A1:D10 → ((1,1),(4,10)))"""

def auto_column_width(ws, min_width=8, max_width=50, sample_rows=100):
    """自动调整列宽（考虑中文字符宽度）"""

def apply_table_style(ws, header_range, data_range, style="default"):
    """应用表格样式（表头加粗蓝色背景、斑马纹等）"""
```

### 4.5 Pipeline 上下文传递

```python
class PipelineContext:
    """Pipeline 步骤间传递上下文"""
    def __init__(self, file_paths: List[str], context: str):
        self.file_paths: List[str] = list(file_paths)
        self.original_file_paths: List[str] = list(file_paths)
        self.context: Optional[str] = context
        self.read_data: Optional[Dict] = None      # read 操作的结果
        self.analysis_result: Optional[Dict] = None  # analyze 操作的结果
        self.markdown_content: Optional[str] = None  # to_md 操作的结果
        self.results: List[Dict] = []
```

**上下文更新规则**：

| 操作 | 更新行为 |
|------|----------|
| read | `ctx.read_data = result` |
| analyze | `ctx.analysis_result = result` |
| to_md | `ctx.markdown_content = result["markdown"]` |
| export | `ctx.file_paths = [result["file_path"]]` |
| modify | `ctx.file_paths = [result["file_path"]]` |
| format | `ctx.file_paths = [result["file_path"]]` |
| chart | `ctx.file_paths = [result["file_path"]]` |
| fill_template | `ctx.file_paths = [result["file_path"]]` |
| merge | `ctx.file_paths = [result["file_path"]]` |
| convert | `ctx.file_paths = [result["file_path"]]` |

### 4.6 Pipeline 组合示例

用户可能的组合需求和对应的 Pipeline：

| 用户需求 | Pipeline | 说明 |
|----------|----------|------|
| "看看这个Excel里有什么" | `to_md` | 转为 Markdown 展示给用户 |
| "分析这个Excel的数据" | `to_md,analyze` | 先读取再分析 |
| "把这些数据做成图表" | `read,chart` | 先读取列信息，再生成图表 |
| "把这个表格导出为Excel并加上格式" | `export,format` | 先导出，再格式化 |
| "修改这个Excel然后生成图表" | `modify,chart` | 先修改数据，再基于修改后的数据画图 |
| "基于模板生成月度报告" | `fill_template` | 系统模板填充 |
| "用户上传模板 + 填数据" | `to_md,fill_template` | 先理解模板内容再填充 |
| "用户上传模板直接说填什么" | `fill_template` | template_file 参数直接用附件路径 |
| "把这几个CSV合并成一个Excel" | `merge` | 多文件合并 |
| "把这个CSV转成带格式的Excel" | `convert,format` | 先转换格式，再应用样式 |

---

## 5. 注册与集成

### 5.1 Agent 注册

在 `src/core/agent.py` 的 `_register_builtin_tools()` 中添加：

```python
# Excel 处理工具
from src.tools.excel import ExcelProcessTool
self.tool_registry.register(ExcelProcessTool())
```

### 5.2 Agent Tool Schema

在 `AGENT_TOOLS` 列表中添加：

```python
{
    "type": "function",
    "function": {
        "name": "excel_process",
        "description": "Excel电子表格处理工具...",
        "parameters": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": "用户的原始需求描述，包含所有相关内容"
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "用户上传的附件文件路径列表"
                }
            }
        }
    }
}
```

### 5.3 依赖安装

```bash
pip install markitdown  # 新增：Excel → Markdown
```

现有依赖无需变更：`openpyxl`, `pandas`, `chardet` 已在 requirements.txt 中。
模板引擎自研实现（基于 openpyxl），不引入额外依赖。

---

## 6. 与现有功能的关系

### 6.1 替代关系

| 现有功能 | 处置方式 |
|----------|----------|
| `src/skills/excel-data-assistant/` | **已删除**。全部功能已迁移至 Excel Tool（export 替代 md_to_excel，analyze 替代 analyze_data，chart 替代 aggregate_chart，excel_lib 复用了 excel_utils 的编码检测和列宽计算逻辑） |
| `src/tools/file/excel_reader.py` | **保留**。用于通用文件读取（FileReaderTool 链路），不冲突 |
| `src/knowledge/parsers/excel_parser.py` | **不动**。知识库专用，独立链路 |
| `src/skills/quote-export/` | **保留**。业务特定逻辑，不通用化 |

### 6.2 复用关系

Excel Tool 开发时已参考 `excel_utils.py` 中的编码检测 fallback 策略和 CJK 宽度计算逻辑，直接在 `excel_lib.py` 和 `excel_reader.py` 中重新实现，无需保留 Skill 目录。

---

## 7. 模板系统设计

### 7.1 设计理念

**一条规则**：用户在 Excel 单元格中写 `{{变量名}}`，系统根据值的类型自动处理：

| 值类型 | 处理方式 | 用户需要知道的 |
|--------|---------|--------------|
| 字符串/数字 | 直接替换文本 | 写 `{{公司名}}` |
| 列表 `["A","B","C"]` | 按位置展开为多行 | 每个数据列写一个 `{{变量名}}`，值给数组 |
| 字典列表 `[{...},{...}]` | 按表头匹配展开为多行 | 只需一个单元格写 `{{变量名}}` |

不需要学 Jinja2 的 `{% for %}` / `{% endfor %}`，不需要写在注释里，不需要任何编程概念。

### 7.2 两种模板来源

| 来源 | 触发场景 | 模板定位方式 | 使用者 |
|------|----------|-------------|--------|
| **系统模板** | 用户说"用XX模板"、要求查看可用模板、选择一个模板 | `template_name` 从 `storage/excel_templates/` 加载 | 所有用户共享 |
| **用户上传模板** | 用户上传附件 .xlsx 并说"按这个模板"、"照这个格式填"、"用这个文件当模板" | `template_file` 直接使用附件路径 | 仅当前会话 |

**路由器识别规则**：

```
判断模板来源的优先级：
1. file_paths 中有 .xlsx 附件 且 context 中包含"模板"相关关键词
   → template_file = file_paths[0]（用户上传模板）

2. context 中提到具体模板名（如"月度报告模板"、"报价单模板"）
   但没有 .xlsx 附件作为模板
   → template_name = 匹配的系统模板名

3. context 中只提到"模板"但没有指定哪个，也无附件
   → 先 list_templates 让用户选择
```

**关键区分**：用户上传一个 Excel 文件不等于模板。路由器必须结合上下文判断：
- "帮我看看这个Excel" → `to_md`（用户上传的是数据文件）
- "按这个Excel的格式填数据" → `fill_template(template_file=...)`（用户上传的是模板）
- "用这个模板生成报告" → `fill_template(template_file=...)`（用户上传的是模板）

### 7.3 用户上传模板的完整流程

```
用户上传 report_template.xlsx，说"用这个模板，填上以下数据..."

1. 外部 Agent 调用 excel_process(context="用这个模板填数据...",
                                  file_paths=["/tmp/report_template.xlsx"])

2. 内部路由器判断：
   - 有 .xlsx 附件 ✓
   - context 提到"模板" ✓
   → task: "fill_template"
   → params: {template_file: "/tmp/report_template.xlsx", variables: {...}}

3. fill_template 执行：
   a. 打开用户上传的模板文件（复制到临时目录，不修改原文件）
   b. 扫描所有单元格，收集 {{变量名}} 占位符及其位置
   c. 按 variables 中的值类型分类：字符串→直接替换，列表→标记为行展开
   d. 对行展开变量：复制模板行 N 次，每次填入对应数据
   e. 对单值变量：直接替换单元格文本
   f. 报告结果：替换了几个变量、插入了多少行
```

### 7.4 模板语法 — 行展开详解

#### 场景一：每列一个 `{{变量名}}`（推荐用于模板设计）

模板：

| A | B | C | D |
|---|---|---|---|
| **产品名** | **数量** | **单价** | **金额** |
| `{{名称}}` | `{{数量}}` | `{{单价}}` | `{{金额}}` |
| **合计** | | | `{{总金额}}` |

variables：
```json
{
    "名称": ["产品A", "产品B", "产品C"],
    "数量": [100, 200, 50],
    "单价": [50, 40, 80],
    "金额": [5000, 8000, 4000],
    "总金额": 17000
}
```

结果：

| A | B | C | D |
|---|---|---|---|
| **产品名** | **数量** | **单价** | **金额** |
| 产品A | 100 | 50 | 5000 |
| 产品B | 200 | 40 | 8000 |
| 产品C | 50 | 80 | 4000 |
| **合计** | | | 17000 |

**要点**：
- 同一行的列表变量长度必须一致（此处都是 3）
- 模板行的格式（字体、边框、行高）自动复制到每个展开行
- 汇总行被 `insert_rows` 自然下推

#### 场景二：一个 `{{变量名}}` 对应字典列表（适合 Agent 内部构造）

模板（只需在一个单元格写变量名）：

| A | B | C | D |
|---|---|---|---|
| **产品名** | **数量** | **单价** | **金额** |
| `{{产品}}` | | | |
| **合计** | | | |

variables：
```json
{
    "产品": [
        {"产品名": "产品A", "数量": 100, "单价": 50, "金额": 5000},
        {"产品名": "产品B", "数量": 200, "单价": 40, "金额": 8000}
    ]
}
```

系统检测到值是字典列表 → 取每个字典的 key 与**同列的表头文本**匹配 → 填入对应列。

结果：

| A | B | C | D |
|---|---|---|---|
| **产品名** | **数量** | **单价** | **金额** |
| 产品A | 100 | 50 | 5000 |
| 产品B | 200 | 40 | 8000 |
| **合计** | | | |

#### 场景三：无占位符的结构感知模式（fallback）

用户上传了一个没有任何 `{{...}}` 的 Excel，但有表头和空数据区域。
系统自动检测表头行（样式特征：加粗/背景色），识别数据区域，将 variables 中的列表数据按表头文本映射填入。

此模式不需要用户在模板中写任何东西，但识别准确率依赖启发式（表头样式检测），不如 `{{变量名}}` 可靠。

### 7.5 模板 JSON 元数据（仅系统模板）

```json
// storage/excel_templates/monthly_report.json
{
    "name": "月度业务报告",
    "description": "标准月度业务报告模板",
    "variables": [
        {"name": "month", "description": "报告月份", "example": "2026年4月"},
        {"name": "department", "description": "部门名称", "example": "销售部"},
        {"name": "名称", "description": "产品名称列表", "type": "array"},
        {"name": "数量", "description": "产品数量列表", "type": "array"}
    ]
}
```

用户上传模板不需要元数据文件——占位符从文件内容自动检测。

### 7.6 模板填充示例

**示例一：系统模板**

用户：`"用月度报告模板生成4月份销售部的报告，产品A卖了100件共5000元，产品B卖了200件共8000元"`

内部路由器输出：
```json
{
    "task": "fill_template",
    "params": {
        "template_name": "monthly_report",
        "variables": {
            "month": "2026年4月",
            "department": "销售部",
            "名称": ["产品A", "产品B"],
            "数量": [100, 200],
            "金额": [5000, 8000]
        }
    }
}
```

**示例二：用户上传模板（有占位符）**

用户上传 `合同模板.xlsx`（含 `{{甲方}}`、`{{乙方}}`、`{{合同金额}}`），说：
`"用这个模板填一下，甲方是XX科技，乙方是YY贸易，合同金额50万"`

内部路由器输出：
```json
{
    "task": "fill_template",
    "params": {
        "template_file": "/tmp/uploads/合同模板.xlsx",
        "variables": {
            "甲方": "XX科技有限公司",
            "乙方": "YY贸易有限公司",
            "合同金额": "500,000"
        }
    }
}
```

**示例三：用户上传模板（无占位符，结构感知）**

用户上传 `报价单.xlsx`（无 `{{}}`，但有表头），说：
`"按这个格式填数据，公司是YY科技，产品A卖了50个单价200，产品B卖了30个单价350"`

内部路由器输出：
```json
{
    "task": "fill_template",
    "params": {
        "template_file": "/tmp/uploads/报价单.xlsx",
        "variables": {
            "items": [
                {"产品名": "产品A", "数量": 50, "单价": 200, "金额": 10000},
                {"产品名": "产品B", "数量": 30, "单价": 350, "金额": 10500}
            ]
        }
    }
}
```

fill_template 扫描后未找到 `{{}}` → 进入结构感知模式 → 检测表头 → 按表头文本映射填入数据。

**示例四：先查看再填充**

用户上传模板但不确定里面有什么：`"看看这个模板里有哪些要填的"`

内部路由器输出：
```json
{
    "task": "to_md",
    "params": {}
}
```

Agent 将模板内容展示给用户后，用户说明要填什么，再触发 `fill_template`。

### 7.7 与 Word 模板的对比

| 维度 | Word 模板 | Excel 模板（本项目） |
|------|----------|---------------------|
| 占位符语法 | `{{var}}` 和 `[var]` | `{{var}}`（统一用一种） |
| 行循环 | 不支持 | 自动：变量值是列表 → 展开为多行 |
| 多列配置 | 不涉及 | 每列一个 `{{变量名}}` 或一个字典列表 |
| 无占位符填充 | 不支持 | 支持（结构感知模式） |
| 格式保留 | replace_text_cross_run | `dst._style = copy(src._style)` |
| 表格数据 | 扁平文本替换 | 行展开 + 格式复制 |

---

## 8. 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/tools/excel/__init__.py` | 模块导出 |
| `src/tools/excel/excel_process_tool.py` | 入口 + Pipeline |
| `src/tools/excel/excel_router.py` | 内部 LLM 路由器 |
| `src/tools/excel/excel_lib.py` | 共享工具函数 |
| `src/tools/excel/excel_reader.py` | 数据读取 |
| `src/tools/excel/excel_writer.py` | 创建/导出 |
| `src/tools/excel/excel_modifier.py` | 内容修改 |
| `src/tools/excel/excel_formatter.py` | 格式化样式 |
| `src/tools/excel/excel_chart.py` | 图表生成 |
| `src/tools/excel/excel_template.py` | 模板管理 |
| `src/tools/excel/excel_to_md.py` | Excel → Markdown |
| `src/tools/excel/excel_analyzer.py` | 数据分析 |
| `storage/excel_templates/` | 模板存放目录 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/core/agent.py` | AGENT_TOOLS 添加 schema + _register_builtin_tools 注册 |
| `requirements.txt` | 新增 `markitdown` 依赖 |

---

## 9. 开发优先级

### P0（核心 — 第一批交付）✅ 已完成

| 模块 | 能力 | 优先级理由 | 状态 |
|------|------|-----------|------|
| excel_to_md | Excel → Markdown | 最基础：让 Agent 能理解用户上传的 Excel | ✅ |
| excel_writer | 数据导出为 Excel | 高频需求：将对话中的表格/数据导出 | ✅ |
| excel_reader | 读取 Excel 数据 | 基础能力：读取指定范围数据 | ✅ |
| excel_process_tool + excel_router | 入口 + 路由 | 必须先有骨架 | ✅ |

### P1（增强 — 第二批交付）✅ 已完成

| 模块 | 能力 | 优先级理由 | 状态 |
|------|------|-----------|------|
| excel_template | 模板填充 | 企业高频：基于模板生成报告 | ✅ |
| excel_chart | 图表生成 | 数据可视化的核心需求 | ✅ |
| excel_analyzer | 数据分析 | 复用 Skill 已有的分析逻辑 | ✅ |
| excel_formatter | 格式化 | 提升输出质量 | ✅ |

### P2（完善 — 第三批交付）✅ 已完成

| 模块 | 能力 | 优先级理由 | 状态 |
|------|------|-----------|------|
| excel_modifier | 内容修改 | 低频但完整能力所需 | ✅ |
| excel_writer (merge) | 多文件合并 | 特定场景需求 | ✅ |
| excel_writer (convert) | 格式转换 | 可用 pandas 一行代码实现 | ✅ |

---

## 10. 风险与注意事项

### 10.1 性能风险

- **大文件处理**：openpyxl 加载大型 Excel 文件（>10MB）会占用大量内存。对超过 10000 行的文件使用 `read_only` 模式读取，分批处理
- **Markdown 输出过长**：多 Sheet 大文件的 Markdown 可能超过 LLM 上下文窗口。`max_rows` 参数控制每个 Sheet 的输出行数，超出时截断并提示

### 10.2 markitdown 依赖

- markitdown 是微软开源项目，MIT License，但版本迭代较快。建议锁定版本号（如 `markitdown>=0.1.0`）
- 如果 markitdown 不可用，fallback 到 openpyxl 直接读取 + 手动格式化为 Markdown 表格

### 10.3 模板系统复杂度

- **自研模板引擎而非依赖 xltpl**：xltpl 要求用户写 Jinja2 语法（`{% for %}` / `{% endfor %}`），对非技术用户门槛太高。本项目自研简化引擎，用户只需写 `{{变量名}}` 一种语法，系统根据值的类型自动决定行为（字符串替换 or 列表展开为多行）
- **行展开的实现难点**：openpyxl 的 `insert_rows` 不复制格式，需手动用 `dst._style = copy(src._style)` 复制。合并单元格需 unmerge → insert → re-merge。Excel Table 对象的 ref 范围需手动更新。这些是实现时需要特别注意的 openpyxl 已知限制
- **结构感知模式的结构检测准确性**：自动检测表头行和数据区域的启发式算法在某些边界情况下可能出错（如没有明显表头样式的简单表格、多级表头、跨列合并的表头）。此模式作为 fallback，优先推荐用户在模板中使用 `{{变量名}}`
- 系统模板的安全扫描：只允许从 `storage/excel_templates/` 目录加载模板，防止路径遍历
- **用户上传模板的识别准确率**：路由器需要准确区分"用户上传的是模板"还是"用户上传的是数据文件"。关键信号是 context 中的"模板"、"按这个格式"、"照着填"等关键词。`fill_template` 会兜底：既无占位符又无表头结构的文件会返回错误提示
- **同一行列表变量长度不一致**：如果用户提供的 variables 中同一行的列表变量长度不同（如 `名称` 有 3 项但 `数量` 有 2 项），系统应报错提示具体哪个变量长度不匹配，而非静默截断

### 10.4 与现有 Skill 的关系

- Excel Tool 作为主入口后，`excel-data-assistant` Skill 的 `md_to_excel` 功能被 Tool 的 `export` 操作替代
- Skill 的 `clean_data`、`aggregate_chart` 可保留为独立技能，或未来提取到 Tool 的 `analyze` 操作中
- ~~excel-data-assistant Skill 已删除~~，全部功能由 Excel Tool 承接，无功能重复

### 10.5 编码处理

- CSV 文件的编码检测是已知的棘手问题（chardet 不总是准确）
- 沿用现有 Skill 的多级 fallback 策略：chardet → utf-8-sig → utf-8 → gbk → gb2312 → latin-1
- 对于明确指定编码的用户请求，优先使用用户指定的编码
