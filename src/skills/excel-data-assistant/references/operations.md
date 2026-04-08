# Operations Reference

Data cleaning and aggregation operation parameters for the `clean_data` and `aggregate_chart` scripts.

Load this file when constructing the `--operations` or `--aggregations` JSON parameter.

---

## Clean Data Operations (`--operations`)

Pass a JSON object where each key is an operation name and the value configures it. Operations apply in the order listed below. If a column referenced in an operation doesn't exist, that operation is skipped silently.

### drop_empty_rows
Remove rows where ALL columns are null/empty.
```json
"drop_empty_rows": true
```

### fill_na
Fill null values in specified columns. Supports specific values or forward/backward fill.
```json
"fill_na": {
  "金额": 0,
  "备注": "无",
  "日期": "ffill",
  "数量": "bfill"
}
```
- Numeric value → fill with that number
- String value → fill with that string
- `"ffill"` → forward fill (use previous row's value)
- `"bfill"` → backward fill (use next row's value)

### drop_duplicates
Remove duplicate rows based on specified columns.
```json
"drop_duplicates": {
  "subset": ["订单号"],
  "keep": "first"
}
```
- `subset`: list of columns to check for duplicates (required)
- `keep`: which duplicate to keep - `"first"` (default), `"last"`, or `"none"` (drop all duplicates)

### rename_columns
Rename columns. Only existing columns are renamed.
```json
"rename_columns": {
  "old_column_name": "New Display Name",
  "Unnamed: 0": "序号"
}
```

### change_types
Convert column data types. Invalid values become null.
```json
"change_types": {
  "金额": "float",
  "数量": "int",
  "日期": "datetime",
  "备注": "str"
}
```
Supported types:
- `"int"` / `"integer"` → integer (null-safe Int64)
- `"float"` → floating point number
- `"str"` → text string
- `"datetime"` → date/time (auto-parses common date formats)

### strip_whitespace
Remove leading/trailing whitespace from text columns.
```json
"strip_whitespace": ["姓名", "地址", "部门"]
```

### replace_values
Replace specific values within columns.
```json
"replace_values": {
  "状态": {
    "进行中": "处理中",
    "已完成": "完成"
  },
  "性别": {
    "M": "男",
    "F": "女"
  }
}
```

### filter_rows
Filter rows by condition. Only simple comparison operators are supported.
```json
"filter_rows": {
  "金额": ">= 100",
  "年龄": "< 60",
  "状态": "== 已完成"
}
```
Supported operators: `>=`, `<=`, `>`, `<`, `==`, `!=`
- For numeric columns, the value is compared as a number
- For string comparisons (==, !=), wrap the value in quotes is optional

### drop_columns
Remove entire columns.
```json
"drop_columns": ["临时列", "备注", "内部ID"]
```

### Combining operations
Multiple operations are applied in the order they appear in the JSON. This matters when operations depend on each other - for example, `rename_columns` before `fill_na` if you renamed a column:
```json
{
  "rename_columns": {"old_name": "new_name"},
  "fill_na": {"new_name": 0},
  "drop_empty_rows": true
}
```

---

## Aggregate & Chart (`--aggregations`)

### Basic structure
```json
{
  "group_by": ["column1", "column2"],
  "metrics": {
    "amount": ["sum", "mean", "count"],
    "quantity": ["sum"]
  },
  "sort_by": {"amount_sum": "desc"},
  "charts": [
    {"type": "bar", "title": "Sales by Dept", "x": "department", "y": "amount_sum"}
  ]
}
```

### group_by (required)
List of column names to group by. All columns must exist in the data.
```json
"group_by": ["部门"]
"group_by": ["部门", "年份"]
```

### metrics (required)
Mapping of column names to aggregation functions. The output column name follows the pattern `{column}_{function}`.
```json
"metrics": {
  "金额": ["sum", "mean", "count", "min", "max"],
  "人数": ["sum"]
}
```
Supported functions:
- `"sum"` → total
- `"mean"` → average
- `"count"` → number of non-null entries
- `"min"` → minimum value
- `"max"` → maximum value
- `"median"` → median value
- `"std"` → standard deviation

Result column naming: `{"金额": ["sum", "mean"]}` produces columns `金额_sum` and `金额_mean`.

### sort_by (optional)
Sort the aggregated result by one or more columns.
```json
"sort_by": {"金额_sum": "desc"}
"sort_by": {"部门": "asc", "金额_sum": "desc"}
```
- `"asc"` / `"ascending"` → smallest first
- `"desc"` / `"descending"` → largest first

### charts (optional)
List of chart configurations. Each chart becomes a separate Excel sheet.
```json
"charts": [
  {"type": "bar", "title": "Department Summary", "x": "部门", "y": "金额_sum"},
  {"type": "pie", "title": "Department Share", "x": "部门", "y": "金额_sum"},
  {"type": "line", "title": "Trend", "x": "月份", "y": "金额_sum"}
]
```

Chart types:
- `"bar"` → bar/column chart (good for comparisons)
- `"line"` → line chart (good for trends)
- `"pie"` → pie chart (good for proportions, best with ≤ 8 groups)
- `"scatter"` → scatter plot (good for correlation)

Fields per chart:
- `type` (required): chart type
- `title` (required): chart title (also used as sheet name prefix)
- `x` (required): column name for X-axis (typically the group_by column)
- `y` (required): column name for Y-axis (must be a metric output column like `金额_sum`)

Tip: The `y` value must match the aggregated column name format. If you aggregate `{"金额": ["sum"]}`, use `金额_sum` as the y value.
