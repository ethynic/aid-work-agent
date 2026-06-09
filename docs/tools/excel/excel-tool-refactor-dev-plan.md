# Excel 工具重构 — 开发计划

> 创建日期: 2026-06-09 | 状态: 📋 待开发
> 关联设计: [Excel 工具重构设计](excel-tool-refactor-design.md) | [Excel 工具设计文档](excel_tool_design.md)

---

## 总览

| 项目 | 内容 |
|------|------|
| 目标 | 移除 Excel 工具的 analyze 和 chart 操作，增强 read 操作 |
| 涉及文件 | 5 个代码文件 + 1 个文档文件 |
| 预估时间 | 约 1.5 小时 |

### 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/tools/excel/excel_analyzer.py` | **删除** | 数据分析子模块，由 SmartDataAnalysisTool 替代 |
| `src/tools/excel/excel_chart.py` | **删除** | 图表生成子模块，由 SmartDataAnalysisTool 的 to_chart 替代 |
| `src/tools/excel/excel_process_tool.py` | 修改 | 移除 analyze/chart + 更新 TOOL_DESCRIPTION + 增强 _handle_read |
| `src/tools/excel/excel_router.py` | 修改 | 移除 analyze/chart 路由规则 |
| `src/tools/excel/excel_reader.py` | 修改 | 新增 read_excel_document() 函数 |
| `docs/tools/excel/excel_tool_design.md` | 修改 | 同步更新设计文档 |

---

## Step 1: 删除 excel_analyzer.py 和 excel_chart.py

**操作**：删除以下两个文件。

| 文件 | 说明 |
|------|------|
| `src/tools/excel/excel_analyzer.py` | 仅被 `_handle_analyze` 引用 |
| `src/tools/excel/excel_chart.py` | 仅被 `_handle_chart` 引用 |

**状态**：✅ 已完成

---

## Step 2: 修改 excel_process_tool.py — 移除 analyze/chart 相关代码

**文件**：`src/tools/excel/excel_process_tool.py`

**状态**：✅ 已完成

### 2.1 TaskType 类（~行 17-32）

删除两个常量，更新 ALL 集合：

```python
class TaskType:
    """有效操作类型常量"""
    READ = "read"
    TO_MD = "to_md"
    EXPORT = "export"
    MODIFY = "modify"
    FORMAT = "format"
    FILL_TEMPLATE = "fill_template"
    LIST_TEMPLATES = "list_templates"
    MERGE = "merge"
    CONVERT = "convert"

    ALL = {READ, TO_MD, EXPORT, MODIFY, FORMAT,
           FILL_TEMPLATE, LIST_TEMPLATES, MERGE, CONVERT}
```

### 2.2 PipelineContext 类（~行 48-59）

删除字段：

```python
self.analysis_result: Optional[Dict] = None
```

### 2.3 TOOL_DESCRIPTION 常量（~行 62-79）

替换为设计文档 §3.3.1 的新版描述。

### 2.4 _get_handler 方法（~行 149-163）

从 handlers 字典中删除两个条目：

```python
"analyze": self._handle_analyze,
"chart": self._handle_chart,
```

### 2.5 _update_context 方法（~行 176-188）

删除 `elif op == "analyze"` 分支。

从 `if op in ("export", "modify", ...)` 条件中移除 `"chart"`。

### 2.6 _merge_results 方法（~行 190-230）

删除 `elif op == "analyze"` 分支。

删除 `_merge_results` 中 chart 相关的合并逻辑（在 `elif op in ("export", "modify", ...)` 分支内，chart 的结果合并与 export 共享同一分支，移除 chart 后需检查条件列表是否需要调整）。

### 2.7 删除两个 handler 方法

- 删除 `_handle_analyze` 方法（~行 248-261）
- 删除 `_handle_chart` 方法（~行 396-433）

---

## Step 3: 修改 excel_router.py — 移除 analyze/chart 路由规则

**文件**：`src/tools/excel/excel_router.py`

**状态**：✅ 已完成

### 3.1 ROUTING_PROMPT_PREFIX（~行 14-111）

删除两条操作定义：
- 第 2 条 `analyze` — 对数据进行统计分析
- 第 7 条 `chart` — 生成图表

其余操作编号顺延：

| 原编号 | 操作 | 新编号 |
|--------|------|--------|
| 1 | read | 1（不变） |
| 2 | ~~analyze~~ | 删除 |
| 3 | to_md | 2 |
| 4 | export | 3 |
| 5 | modify | 4 |
| 6 | format | 5 |
| 7 | ~~chart~~ | 删除 |
| 8 | fill_template | 6 |
| 9 | list_templates | 7 |
| 10 | merge | 8 |
| 11 | convert | 9 |

删除判断规则中的 `要求分析统计 → analyze` 和 `要求生成图表 → chart`。

### 3.2 VALID_TASKS 集合（~行 122-125）

```python
# 变更后
VALID_TASKS = {
    "read", "to_md", "export", "modify", "format",
    "fill_template", "list_templates", "merge", "convert",
}
```

---

## Step 4: 修改 excel_reader.py — 新增 read_excel_document()

**文件**：`src/tools/excel/excel_reader.py`

**状态**：✅ 已完成

### 4.1 实现方式

从 `src/tools/file/excel_reader.py` 的 `ExcelReader` 类中提取核心逻辑，改为模块级独立函数。

### 4.2 新增函数

在文件末尾追加 `read_excel_document()` 及 3 个内部辅助函数：

```python
def read_excel_document(file_path: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """
    读取 Excel 文档的完整内容，返回结构化数据 + 文本格式化内容 + 文档元信息。

    与 read_sheet() 的区别：
    - read_sheet(): 读取指定 Sheet/范围的结构化数据（headers, rows, merged_cells, formulas）
    - read_excel_document(): 读取文档级信息（全 Sheet 文本、文档元信息、行数统计）
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    if src.suffix.lower() == ".xls":
        return {"success": False, "error": "目前仅支持.xlsx格式，请将.xls文件转换为.xlsx格式"}

    if src.suffix.lower() != ".xlsx":
        return {"success": False, "error": f"不支持的文件格式: {src.suffix}，仅支持.xlsx"}

    try:
        wb = openpyxl.load_workbook(str(src), data_only=True)

        # 1. 收集所有 Sheet 元信息
        sheet_names = wb.sheetnames
        sheet_count = len(sheet_names)
        sheets_info = []
        for name in sheet_names:
            ws = wb[name]
            sheets_info.append({
                "name": name,
                "title": ws.title,
                "max_row": ws.max_row,
                "max_column": ws.max_column,
            })

        # 2. 选择目标 Sheet
        target_sheet = sheet_name if sheet_name and sheet_name in sheet_names else sheet_names[0]
        if target_sheet not in sheet_names:
            wb.close()
            return {"success": False, "error": f"工作表不存在: {target_sheet}，可用: {', '.join(sheet_names)}"}

        sheet_data = _extract_sheet_data_doc(wb[target_sheet])

        # 3. 遍历所有 Sheet，格式化为固定列宽文本
        all_sheets_content = []
        for name in sheet_names:
            ws = wb[name]
            sheet_text = _format_sheet_to_text_doc(ws)
            all_sheets_content.append(f"=== 工作表: {name} ===\n{sheet_text}")

        full_content = "\n\n".join(all_sheets_content)
        total_lines = len(full_content.splitlines())

        # 4. 文档元信息
        doc_info = _extract_document_info_doc(wb, src)

        wb.close()

        return {
            "success": True,
            "message": "成功读取Excel文档",
            "file_path": str(src),
            "file_type": "xlsx",
            "sheet_count": sheet_count,
            "sheet_names": sheet_names,
            "sheets_info": sheets_info,
            "current_sheet": target_sheet,
            "sheet_data": sheet_data,
            "document_info": doc_info,
            "content": full_content,
            "total_lines": total_lines,
        }
    except Exception as e:
        logger.error(f"[ExcelReader] read_excel_document 失败: {e}", exc_info=True)
        return {"success": False, "error": f"读取Excel文档失败: {e}"}


def _extract_sheet_data_doc(sheet) -> Dict[str, Any]:
    """提取工作表结构化数据（文档级读取用）"""
    data = {
        "name": sheet.title,
        "max_row": sheet.max_row,
        "max_column": sheet.max_column,
        "headers": [],
        "rows": [],
        "data": [],
    }

    if sheet.max_row and sheet.max_row >= 1 and sheet.max_column:
        headers = []
        for col in range(1, sheet.max_column + 1):
            val = sheet.cell(1, col).value
            headers.append(str(val) if val is not None else "")
        data["headers"] = headers

        rows = []
        for row in range(1, sheet.max_row + 1):
            row_data = []
            for col in range(1, sheet.max_column + 1):
                row_data.append(sheet.cell(row, col).value)
            rows.append(row_data)
        data["rows"] = rows

        if sheet.max_row > 1:
            data["data"] = rows[1:]

    return data


def _format_sheet_to_text_doc(sheet) -> str:
    """将工作表格式化为固定列宽文本"""
    if not sheet.max_row or sheet.max_row == 0 or not sheet.max_column:
        return "(空工作表)"

    col_widths = []
    for col in range(1, sheet.max_column + 1):
        max_width = 0
        for row in range(1, sheet.max_row + 1):
            val = sheet.cell(row, col).value
            cell_str = str(val) if val is not None else ""
            max_width = max(max_width, len(cell_str))
        col_widths.append(min(max_width + 2, 50))

    text_lines = []
    for row in range(1, sheet.max_row + 1):
        row_parts = []
        for col in range(1, sheet.max_column + 1):
            val = sheet.cell(row, col).value
            cell_str = str(val) if val is not None else ""
            width = col_widths[col - 1]
            row_parts.append(cell_str.ljust(width))
        text_lines.append(" | ".join(row_parts))

    return "\n".join(text_lines)


def _extract_document_info_doc(workbook, path: Path) -> Dict[str, Any]:
    """提取文档元信息"""
    info = {
        "file_name": path.name,
        "file_size": path.stat().st_size,
        "sheet_count": len(workbook.sheetnames),
        "active_sheet": workbook.active.title if workbook.active else None,
    }

    try:
        if hasattr(workbook, "properties") and workbook.properties:
            props = workbook.properties
            info.update({
                "title": props.title or "",
                "author": props.creator or "",
                "created": str(props.created) if props.created else "",
                "modified": str(props.modified) if props.modified else "",
            })
    except Exception:
        pass

    return info
```

无需新增 import，文件顶部已有 `from pathlib import Path`、`import openpyxl`、`from loguru import logger`。

---

## Step 5: 修改 excel_process_tool.py — 增强 _handle_read

**文件**：`src/tools/excel/excel_process_tool.py`

**状态**：✅ 已完成

替换 `_handle_read` 方法（~行 234-246）：

```python
async def _handle_read(self, ctx: PipelineContext, params: Dict) -> Dict:
    from src.tools.excel.excel_reader import read_sheet, read_excel_document

    if not ctx.file_paths:
        return {"success": False, "error": "read 操作需要 file_paths 参数"}

    file_path = ctx.file_paths[0]

    # 精确范围读取 或 公式读取 → 使用 read_sheet（结构化数据）
    if params.get("range") or params.get("include_formulas"):
        return read_sheet(
            file_path,
            sheet_name=params.get("sheet_name"),
            cell_range=params.get("range"),
            include_formulas=params.get("include_formulas", False),
        )

    # 默认 → 使用 read_excel_document（完整文档信息 + 文本内容）
    return read_excel_document(
        file_path,
        sheet_name=params.get("sheet_name"),
    )
```

---

## Step 6: 更新 excel_tool_design.md 设计文档

**文件**：`docs/tools/excel/excel_tool_design.md`

**状态**：✅ 已完成

### 修改点清单

| 位置 | 变更内容 |
|------|---------|
| §2.1 核心能力表格 | 删除"数据分析"和"图表生成"两项 |
| §3.2 架构图 | 移除 `excel_analyzer.py` 和 `excel_chart.py` |
| §4.1 目录结构 | 移除 `excel_analyzer.py` 和 `excel_chart.py` |
| §4.2 TOOL_DESCRIPTION | 替换为 §3.3.1 的新版描述 |
| §4.3 TaskType | 移除 `ANALYZE` 和 `CHART` |
| §4.3 路由 Prompt | 移除 analyze 和 chart 操作定义 |
| §4.4.5 excel_chart.py 章节 | **整节删除** |
| §4.4.8 excel_analyzer.py 章节 | **整节删除** |
| §4.4.1 excel_reader.py 章节 | 追加 `read_excel_document()` 说明 |
| §4.5 Pipeline 上下文 | 删除 `analysis_result` 字段 |
| §4.6 Pipeline 组合示例 | 删除含 analyze/chart 的组合 |
| §9 开发优先级 | 移除 excel_analyzer 和 excel_chart 行 |

---

## Step 7: 运行测试

**状态**：✅ 已完成

### 7.1 检查现有测试

```bash
grep -r "excel_analyzer\|excel_chart" tests/
grep -r "analyze.*excel\|chart.*excel" tests/ -i
```

如有针对 excel_analyzer 或 excel_chart 的测试，删除或更新。

### 7.2 运行测试

```bash
pytest tests/unit/ -x -q
pytest tests/integration/ -x -q
```

### 7.3 验证清单

- [ ] Excel 工具调用 analyze 操作时返回"无效操作"错误
- [ ] Excel 工具调用 chart 操作时返回"无效操作"错误
- [ ] TOOL_DESCRIPTION 中不再包含"统计分析"和"生成图表"
- [ ] Excel 工具 read 操作（无 range/include_formulas）返回 `document_info` 和 `content` 字段
- [ ] Excel 工具 read 操作（有 range）仍返回 `headers`、`rows`、`merged_cells`
- [ ] FileReaderTool 的 ExcelReader 正常工作
- [ ] SmartDataAnalysisTool 正常工作（分析和图表）

---

## Step 8: 更新 docs/ideas.md

**状态**：✅ 已完成

将 Excel 工具重构条目的状态从 📋 待开发 更新为 ✅ 已完成。

---

## 进度跟踪

| Step | 描述 | 状态 |
|------|------|------|
| 1 | 删除 excel_analyzer.py 和 excel_chart.py | ✅ |
| 2 | 修改 excel_process_tool.py（移除 analyze/chart） | ✅ |
| 3 | 修改 excel_router.py（移除 analyze/chart 路由） | ✅ |
| 4 | 修改 excel_reader.py（新增 read_excel_document） | ✅ |
| 5 | 修改 excel_process_tool.py（增强 _handle_read） | ✅ |
| 6 | 更新 excel_tool_design.md | ✅ |
| 7 | 运行测试 | ✅ |
| 8 | 更新 docs/ideas.md | ✅ |
