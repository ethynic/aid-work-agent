---
name: excel-data-assistant
description: >
  Spreadsheet and tabular data processing skill. Converts Markdown tables and lists into formatted
  downloadable Excel files, analyzes uploaded CSV/Excel files (headers, data types, statistics, preview
  rows), cleans data based on user requirements (fill nulls, drop duplicates, rename columns, filter
  rows, type conversion), and generates aggregated summaries with native Excel charts (bar, line, pie,
  scatter) exported as downloadable multi-sheet Excel files. Use this skill whenever the user mentions
  Excel, spreadsheets, CSV, data export, data cleaning, data analysis, aggregation, charts from data,
  or wants to convert tables/lists to Excel — even if they don't explicitly say "Excel" or "spreadsheet".
  Also trigger on mentions of downloading data, exporting reports, summarizing tabular data, analyzing
  uploaded data files, or any request involving tabular data processing.
---

# Excel Data Assistant

Process tabular data end-to-end: analyze, clean, aggregate, chart, and export as Excel.

## When to Use

| User says... | Use script |
|---|---|
| "Export this table to Excel" / "Convert to spreadsheet" / "导出Excel" | `md_to_excel.py` |
| "Analyze this file" / "What's in this CSV" / "分析这个数据" | `analyze_data.py` |
| "Clean the data" / "Remove duplicates" / "Fill nulls" / "清洗数据" | `clean_data.py` |
| "Summarize by department" / "Make a chart" / "汇总做图表" | `aggregate_chart.py` |

The typical flow is: analyze → clean → aggregate+chart. Each step produces output that informs the next. After analysis, you understand the data well enough to suggest cleaning operations. After cleaning, the data is ready for aggregation and charts.

## Script 1: Markdown to Excel

Convert markdown tables (`| col | col |`) and lists (`1. item` / `- item`) into a formatted Excel file with styled headers, borders, and auto-sized columns. Each table or list becomes a separate sheet.

```bash
python scripts/md_to_excel.py \
  --input "MARKDOWN_CONTENT_OR_FILE_PATH" \
  --output-dir "uploads/{user_id}" \
  --filename "显示文件名.xlsx"
```

`--input` accepts either a file path (if the file exists) or raw markdown text. The script auto-detects which one it is.

Output JSON includes `file_path` — pass this to `register_download_file` so the user can download it.

## Script 2: Analyze Data

Read a CSV or Excel file and return a structured analysis: file metadata, per-column type inference and statistics, preview of the first N rows, and auto-generated data quality suggestions. This gives you a complete picture of the data so you can recommend cleaning steps.

```bash
python scripts/analyze_data.py \
  --file "FILE_PATH" \
  --sheet "Sheet1" \
  --preview-rows 100
```

`--sheet` is optional (defaults to first sheet for Excel). `--preview-rows` defaults to 100.

The output JSON has:
- `file_info`: file type, encoding, sheets, row/column counts
- `columns`: per-column type, null count, unique count, numeric stats or top values
- `preview`: first N rows as JSON array
- `suggestions`: auto-detected issues (high null rate, duplicates, empty columns)

After running this, summarize the key findings for the user and suggest next steps based on the `suggestions` field. For example, if there are many nulls in a column, ask the user if they want to fill them or drop them.

## Script 3: Clean Data

Apply a sequence of data cleaning operations specified as a JSON object. You construct the operations JSON based on the analysis results and what the user asks for. Don't ask the user about each individual operation — combine related operations into one call.

```bash
python scripts/clean_data.py \
  --file "FILE_PATH" \
  --operations '{
    "drop_empty_rows": true,
    "fill_na": {"金额": 0, "备注": "无"},
    "drop_duplicates": {"subset": ["订单号"], "keep": "first"},
    "rename_columns": {"旧列名": "新列名"},
    "change_types": {"金额": "float", "日期": "datetime"},
    "strip_whitespace": ["姓名"],
    "replace_values": {"状态": {"进行中": "处理中"}},
    "filter_rows": {"金额": ">= 100"},
    "drop_columns": ["临时列"]
  }' \
  --output-dir "uploads/{user_id}" \
  --filename "清洗后数据.xlsx"
```

Not all operations are needed every time — include only the ones relevant to the user's request. Operations that reference non-existent columns are skipped gracefully.

For the full list of operations and their parameter formats, read `references/operations.md`.

## Script 4: Aggregate and Chart

Group data by specified columns, compute aggregation metrics, and generate Excel-native charts. The output is a multi-sheet Excel file: raw data sheet + aggregated data sheet + one sheet per chart.

```bash
python scripts/aggregate_chart.py \
  --file "FILE_PATH" \
  --aggregations '{
    "group_by": ["部门"],
    "metrics": {"金额": ["sum", "mean", "count"]},
    "sort_by": {"金额_sum": "desc"},
    "charts": [
      {"type": "bar", "title": "部门金额汇总", "x": "部门", "y": "金额_sum"},
      {"type": "pie", "title": "部门占比", "x": "部门", "y": "金额_sum"}
    ]
  }' \
  --output-dir "uploads/{user_id}" \
  --filename "汇总报表.xlsx"
```

The `y` field in chart config refers to aggregated column names, which follow the pattern `{column}_{function}`. For example, if `metrics` has `{"金额": ["sum"]}`, the aggregated column is `金额_sum`.

Chart types: `bar` (comparisons), `line` (trends), `pie` (proportions, best with ≤ 8 groups), `scatter` (correlations).

For full parameter details, read `references/operations.md`.

## File Download

All scripts generate files to `uploads/{user_id}/`. After a script completes and returns `file_path` in its JSON output, call the `register_download_file` tool to register the file:

```
register_download_file(
  file_path="<file_path from script output>",
  display_name="<user-friendly filename>.xlsx"
)
```

This is necessary because the frontend download system uses `file_id` to locate files — `register_download_file` handles the registration and returns a `download_url` that the user can click.

Show the download link to the user after registration. A good format: "文件已生成，[点击下载](download_url)".

## Typical Workflows

### Full data analysis pipeline
1. User uploads CSV/Excel → "帮我分析这个数据"
2. Run `analyze_data.py` → present summary + suggestions
3. User: "清洗一下，去掉空行和重复的"
4. Run `clean_data.py` with appropriate operations → register file → offer download
5. User: "按部门汇总，做个柱状图"
6. Run `aggregate_chart.py` with group_by + chart config → register file → offer download

### Quick markdown export
1. User sends a markdown table in chat → "导出为Excel"
2. Run `md_to_excel.py` with the markdown content → register file → offer download

### Data cleaning only
If the user just wants to clean data without aggregation, `clean_data.py` alone is sufficient. Combine multiple cleaning operations in a single call rather than running the script multiple times.

## Error Handling

- **"pandas 未安装"**: The script auto-detects missing dependencies and returns a clear error. Install with `pip install pandas`.
- **"文件不存在"**: The file path may be wrong. Check if the path from the upload system is correct.
- **"未在 Markdown 中找到表格或列表"**: The markdown format may not be recognized. Supported: pipe tables `| a | b |`, ordered lists `1. item`, unordered lists `- item`.
- **"group_by 列不存在"**: Column names are case-sensitive and must match exactly. Run `analyze_data.py` first to see exact column names.

If a script fails, show the error message to the user and suggest a fix. Don't retry with the same parameters.
