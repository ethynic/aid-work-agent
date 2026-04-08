# format 子命令 JSON 操作 Schema

## 顶层结构

```json
{
  "operations": [ ... ]   // 必须：格式操作列表
}
```

操作按顺序执行。所有操作都基于文档副本，原始文件不受影响。

## scope 通用概念

多个操作使用 `scope` 字段指定应用范围：

| scope 值 | 匹配目标 |
|----------|---------|
| "all" | 所有段落（正文 + 标题 + 列表） |
| "paragraphs" | 仅 Normal 样式的段落 |
| "heading1" | Heading 1 样式的段落 |
| "heading2" | Heading 2 样式的段落 |
| "heading3" | Heading 3 样式的段落 |
| "headingN" | Heading N 样式的段落（N=1-9） |
| "table" | 表格中的所有段落 |
| 样式名称 | 匹配指定样式名的段落（如 "Title"、"Subtitle"） |

## 操作类型

### set_font — 设置字体

按 scope 范围设置字体属性。只修改指定了的属性，未指定的属性保持不变。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_font" |
| scope | string | 是 | 应用范围（见上表） |
| font_name | string | 否 | 字体名称（支持中文：宋体/黑体/仿宋/楷体/微软雅黑） |
| font_size | number | 否 | 字号（磅） |
| bold | boolean | 否 | 粗体 |
| italic | boolean | 否 | 斜体 |
| underline | boolean | 否 | 下划线 |
| color | string | 否 | 颜色（RRGGBB 或 #RRGGBB） |

常用颜色：黑色 `000000`，深蓝 `1F4E79`，蓝色 `2E75B6`，红色 `FF0000`，灰色 `808080`

示例 — 将正文设为宋体12号，标题设为黑体蓝色：
```json
[
  {"op": "set_font", "scope": "paragraphs", "font_name": "SimSun", "font_size": 12},
  {"op": "set_font", "scope": "heading1", "font_name": "SimHei", "font_size": 16, "color": "1F4E79"},
  {"op": "set_font", "scope": "heading2", "font_name": "SimHei", "font_size": 14, "color": "2E75B6"}
]
```

### set_paragraph_format — 设置段落格式

按 scope 范围设置段落格式属性。

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_paragraph_format" |
| scope | string | 是 | 应用范围 |
| alignment | string | 否 | 对齐：LEFT, CENTER, RIGHT, JUSTIFY |
| line_spacing | number | 否 | 行距倍数（1.0 单倍, 1.5 一倍半, 2.0 双倍） |
| space_before | number | 否 | 段前间距（磅） |
| space_after | number | 否 | 段后间距（磅） |
| first_line_indent | number | 否 | 首行缩进（厘米） |

示例 — 公文格式：
```json
[
  {"op": "set_paragraph_format", "scope": "paragraphs", "alignment": "JUSTIFY", "line_spacing": 1.5, "first_line_indent": 0.74},
  {"op": "set_paragraph_format", "scope": "heading1", "alignment": "CENTER", "space_before": 12, "space_after": 6}
]
```

### set_page_margins — 设置页面边距

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_page_margins" |
| top | number | 否 | 上边距（厘米） |
| bottom | number | 否 | 下边距（厘米） |
| left | number | 否 | 左边距（厘米） |
| right | number | 否 | 右边距（厘米） |

示例：
```json
{"op": "set_page_margins", "top": 3.7, "bottom": 3.5, "left": 2.8, "right": 2.6}
```

### set_page_size — 设置页面大小

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_page_size" |
| size | string | 否 | 页面大小：A4, A3, Letter, B5 |
| orientation | string | 否 | 方向：portrait, landscape |

示例：
```json
{"op": "set_page_size", "size": "A4", "orientation": "portrait"}
```

### set_header — 设置页眉

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_header" |
| text | string | 否 | 页眉文本 |
| alignment | string | 否 | 对齐：LEFT, CENTER, RIGHT |
| font_name | string | 否 | 字体 |
| font_size | number | 否 | 字号 |

示例：
```json
{"op": "set_header", "text": "内部文件 — 机密", "alignment": "CENTER", "font_size": 9}
```

### set_footer — 设置页脚

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_footer" |
| text | string | 否 | 页脚文本（会添加在页码前） |
| include_page_number | boolean | 否 | 是否添加页码（默认 false） |
| alignment | string | 否 | 对齐 |
| font_name | string | 否 | 字体 |
| font_size | number | 否 | 字号 |

示例：
```json
{"op": "set_footer", "text": "第 ", "include_page_number": true, "alignment": "CENTER", "font_size": 9}
```

### set_table_style — 设置表格样式

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| op | string | 是 | "set_table_style" |
| table_index | number | 否 | 表格索引（默认 0） |
| style | string | 否 | 表格样式名（默认 "Table Grid"） |
| header_row_bold | boolean | 否 | 表头行是否加粗 |

示例：
```json
{"op": "set_table_style", "table_index": 0, "style": "Table Grid", "header_row_bold": true}
```

## 完整示例：公文格式化

```json
{
  "operations": [
    {"op": "set_page_size", "size": "A4", "orientation": "portrait"},
    {"op": "set_page_margins", "top": 3.7, "bottom": 3.5, "left": 2.8, "right": 2.6},
    {"op": "set_font", "scope": "paragraphs", "font_name": "FangSong", "font_size": 16},
    {"op": "set_paragraph_format", "scope": "paragraphs", "alignment": "JUSTIFY", "line_spacing": 2.0, "first_line_indent": 0.74},
    {"op": "set_font", "scope": "heading1", "font_name": "SimHei", "font_size": 22, "color": "000000"},
    {"op": "set_paragraph_format", "scope": "heading1", "alignment": "CENTER", "line_spacing": 2.0},
    {"op": "set_header", "text": "", "alignment": "CENTER"},
    {"op": "set_footer", "text": "", "include_page_number": true, "alignment": "CENTER", "font_size": 10}
  ]
}
```
