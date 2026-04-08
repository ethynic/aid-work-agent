# modify 子命令 JSON 操作 Schema

## 顶层结构

```json
{
  "operations": [ ... ]   // 必须：操作列表，按顺序执行
}
```

操作按数组顺序依次执行。每个操作执行后文档状态立即更新，后续操作基于更新后的文档。因此，删除段落会导致后续段落索引前移。

## 操作类型

### replace_text — 全局查找替换

在文档所有位置（正文段落、表格、页眉页脚）查找并替换文本。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "replace_text" |
| target | string | 是 | 要查找的文本 |
| replacement | string | 是 | 替换为的文本 |

示例：
```json
{"op": "replace_text", "target": "XX科技有限公司", "replacement": "YY智能技术有限公司"}
```

### insert_paragraph — 插入段落

在指定段落索引后插入新段落。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "insert_paragraph" |
| after_index | number | 是 | 在哪个段落索引后插入（-1 表示追加到末尾） |
| text | string | 是 | 段落文本 |
| style | string | 否 | 段落样式（默认 "Normal"） |
| font_name | string | 否 | 字体 |
| font_size | number | 否 | 字号 |

示例：
```json
{"op": "insert_paragraph", "after_index": 5, "text": "这是新插入的段落", "font_name": "SimSun", "font_size": 12}
```

### delete_paragraph — 删除段落

删除指定索引的段落。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "delete_paragraph" |
| index | number | 是 | 要删除的段落索引 |

示例：
```json
{"op": "delete_paragraph", "index": 3}
```

### replace_paragraph — 替换段落内容

替换指定段落索引的文本，保留其位置。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "replace_paragraph" |
| index | number | 是 | 段落索引 |
| text | string | 是 | 新文本内容 |
| style | string | 否 | 新样式 |
| font_name | string | 否 | 字体 |
| font_size | number | 否 | 字号 |
| bold | boolean | 否 | 粗体 |
| color | string | 否 | 颜色 |

示例：
```json
{"op": "replace_paragraph", "index": 2, "text": "更新后的内容", "style": "Normal", "font_name": "SimSun"}
```

### insert_table — 插入表格

在文档末尾添加表格。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "insert_table" |
| after_index | number | 否 | 在哪个段落后插入（默认末尾） |
| headers | string[] | 是* | 表头行 |
| rows | string[][] | 是* | 数据行 |
| style | string | 否 | 表格样式（默认 "Table Grid"） |

headers 和 rows 至少提供一个。

示例：
```json
{"op": "insert_table", "headers": ["项目", "状态"], "rows": [["项目A", "进行中"], ["项目B", "已完成"]]}
```

### delete_table — 删除表格

删除指定索引的表格（按文档中出现的顺序编号）。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "delete_table" |
| index | number | 是 | 表格索引（从 0 开始） |

### modify_table_cell — 修改表格单元格

修改指定表格中指定单元格的文本。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "modify_table_cell" |
| table_index | number | 是 | 表格索引（从 0 开始） |
| row | number | 是 | 行索引（从 0 开始） |
| col | number | 是 | 列索引（从 0 开始） |
| text | string | 是 | 新文本 |

示例：
```json
{"op": "modify_table_cell", "table_index": 0, "row": 1, "col": 2, "text": "新数据"}
```

### add_page_break — 添加分页符

在文档当前位置添加分页符。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "add_page_break" |
| after_index | number | 否 | 在哪个段落后插入（默认末尾） |

## 完整示例

将一个简单文档修改为正式报告格式：

```json
{
  "operations": [
    {"op": "replace_paragraph", "index": 0, "text": "2026年度工作报告", "style": "Heading 1"},
    {"op": "insert_paragraph", "after_index": 0, "text": "报告日期：2026年4月8日", "font_name": "SimSun", "font_size": 11},
    {"op": "replace_text", "target": "待定", "replacement": "已完成"},
    {"op": "insert_table", "headers": ["季度", "营收", "增长率"], "rows": [["Q1", "100万", "15%"], ["Q2", "120万", "20%"]]}
  ]
}
```
