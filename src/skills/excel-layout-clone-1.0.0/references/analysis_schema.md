# 分析缓存与答案缓存格式 Analysis Schema

第 2 步产物 `analysis.json`、第 3/4 步产物 `answers.json`、第 4 步产物 `fill_report.json` 都属于**具体某次表格任务**，统一放在 `$SKILL_TMP_DIR` 下的任务工作目录（见 SKILL.md「工作目录」），不放进技能目录。脚本不知道任何具体表格内容，只按缓存执行。

**坐标约定：`analysis.json` 中所有坐标都是模板原始坐标。** 动态增减行后，引擎自动把定位平移到新行号；不要自己推算「扩展后第几行」。

---

## 一、`analysis.json`

```json
{
  "template": "报价模板.xlsx",
  "analysis_note": "封面 2 个字段 + 明细表按条数扩展 + 汇总表固定公式",
  "fields": [ ... ],
  "derived_fields": {"total_amount": "sum:items.amount"},
  "list_tables": [ ... ],
  "cell_updates": [ ... ]
}
```

| 键 | 必填 | 说明 |
|----|------|------|
| `template` | 否 | 模板文件名，仅作记录 |
| `analysis_note` | 否 | 分析结论摘要，便于复核 |
| `fields` | 是 | 需要改写/回填的字段清单 |
| `derived_fields` | 否 | 不向用户提问、由表达式算出的字段（按声明顺序计算） |
| `list_tables` | 否 | 可增减行的明细表（**每张 Sheet 最多一张**） |
| `cell_updates` | 否 | 行数固定、只更新数值/公式的单元格 |

### 1.1 `fields[]`

```json
{
  "key": "client_name",
  "label": "客户名称",
  "question": "本次客户名称是什么？",
  "required": true,
  "default": null,
  "derived": null,
  "kind": "text",
  "value_template": null,
  "locators": [ ... ]
}
```

| 键 | 说明 |
|----|------|
| `key` | 唯一键，与 `answers.json` 的键对应 |
| `label` / `question` | 展示名与第 3 步提问话术 |
| `required` | 缺失时报告标 `missing_required` 且 `ok=false` |
| `default` | 用户未答时的默认值 |
| `derived` | 有值时不再提问，由表达式计算 |
| `kind` | `auto`（默认）/ `text` / `number` / `date` / `bool` / `formula` / `blank` / `keep_formula` |
| `value_template` | 写入格式，如 `{value}`、`{value_fmt}`、`客户：{value}`；**数字字段一般不要设**，否则会写成文本 |
| `locators` | 填在哪里，可多个；一个值出现在多处写多个定位 |

`kind=formula` 时，`value` 直接作为 Excel 公式写入（自动去掉开头 `=`），可用 `{row}` 占位符代表目标行号。写入后清除缓存值，Excel 打开时重算。

`kind=date` 接受 `YYYY-MM-DD`、`YYYY-MM-DD HH:MM` 或 Excel 序列号；模板单元格的日期格式样式会保留，需确保 `analysis.json` 里该单元格原本就是日期格式。

### 1.2 `locators[]` 四种定位方式

坐标与 `prep.py` 的 `compact_dump` 一致：`S[Sheet名] A1`。定位均在**模板原始坐标**上解析。

| `kind` | 参数 | 用途 |
|--------|------|------|
| `cell` | `sheet`、`ref`（A1）或 `row`+`col` | 直接写单元格；最常用、最稳 |
| `label` | `sheet`、`label`、`offset_row`、`offset_col`、`occurrence` | 找到含标签的单元格，按偏移写入旁边的空格 |
| `value` | `sheet`、`text`、`exact`、`offset_row`、`offset_col`、`occurrence` | 找到含样例值的单元格并覆盖（用于「换个值」） |
| `formula` | `sheet`、`contains`、`offset_row`、`offset_col`、`occurrence` | 找到含指定片段的公式格并覆盖 |

通用可选参数：

- `occurrence`：第几个命中（从 0 开始）；默认 0。
- `exact`：`label` / `value` 是否整格精确匹配；默认 false（子串匹配）。
- `check_absent`：复核原文是否彻底消失（写进报告 warnings）。
- `template_ref`：写入类型/样式推断参考的模板单元格；不写则按目标格自身推断。

> `label` / `value` / `formula` 定位在**扩展后的工作簿**上搜索，因此不必手动计算行号偏移。

### 1.3 表达式（仅用于 `derived_fields` / `fields[].derived` / `cell_updates[].formula` 的非公式分支）

- 简写：`sum:items.amount`、`count:items`、`min:items.price`、`sum_where(...)`。
- 通用表达式：`total * 0.5`、`100 - first_pay_ratio`、`fmt(total * 0.5)`、`cny_upper(total)`。
- 可用函数：`sum` / `min` / `max` / `count` / `round` / `abs` / `cny_upper` / `fmt` / `len` / `float` / `int` / `str` / `col` / `sum_where`。
- 变量来自已算出的字段与 `answers.json` 的数组。表达式只允许数字、字段名、四则运算与上述函数，不接受任意代码。
- **凡是 Excel 自己能算的（合计、乘法、比率），优先写 `kind=formula` 的 Excel 公式，不要用这里的表达式。**

### 1.4 `list_tables[]`（动态多行明细）

```json
{
  "sheet": "明细",
  "header_row": 4,
  "sample_rows": [5, 6, 7],
  "template_row": 5,
  "footer_rows": [8],
  "rows_source": "items",
  "items_filter": "category == 'software'",
  "columns": [
    {"key": "name",       "col": "B", "kind": "text"},
    {"key": "qty",        "col": "C", "kind": "number"},
    {"key": "price",      "col": "D", "kind": "number"},
    {"key": "amount",     "col": "E", "kind": "formula", "formula": "=C{row}*D{row}"},
    {"key": "remark",     "col": "F", "value_template": "{qty} {unit}"}
  ],
  "total_cells": [
    {"footer_index": 0, "col": "E", "kind": "formula",
     "formula": "=SUM(E{start_row}:E{end_row})"}
  ]
}
```

| 键 | 说明 |
|----|------|
| `sheet` | 明细所在 Sheet 名 |
| `header_row` | 表头行（不参与扩展，仅记录） |
| `sample_rows` | **模板里的样例行号，必须连续**；生成时按实际条数克隆替换 |
| `template_row` | 克隆样式来源行；默认 `sample_rows[0]` |
| `footer_rows` | 合计/小计行原始行号，保留并随扩展平移 |
| `rows_source` | `answers.json` 中的数组键，默认 `items` |
| `items_filter` | 可选，如 `category == 'software'`，用于同一份清单拆到多张表 |
| `columns[].key` | 对应清单条目的字段 |
| `columns[].col` | 列字母（`B`、`C`…） |
| `columns[].kind` | 同 `fields[].kind`；`formula` 时把 `formula` 作为 Excel 公式写入 |
| `columns[].formula` | 以 `=` 开头 → 写 Excel 公式（`{row}` 会替换为实际行号）；否则当作表达式算出数值写入 |
| `columns[].value_template` | 文本拼接模板，可用 `{value}` 与同一条目的其他字段 |
| `total_cells[]` | `footer_index`（指向 `footer_rows`）或 `row`（原始绝对行）+ `col` + `formula` |

行数完全由 `answers.json` 的数组条数决定：**可多于、少于模板样例行，也可以为 0**。新行由样例行 XML 深拷贝而来，边框、底纹、行高、列宽、字体全部继承。行号变化属预期，不计入板式漂移。

公式中的可用占位符：`{row}`（目标行）、`{start_row}` / `{end_row}`（本表数据首末行）。

### 1.5 `cell_updates[]`

行数固定的表（费用汇总、付款计划）只更新数值/公式：

```json
{"sheet": "汇总", "ref": "B10", "kind": "formula",
 "formula": "=SUM(明细!E{start_row}:E{end_row})"}
```

`ref` 用模板原始坐标，引擎按行映射自动平移。

---

## 二、`answers.json`

```json
{
  "_meta": {"client": "客户简称", "note": "问答过程备注"},
  "client_name": "某某科技有限公司",
  "sign_date": "2026-11-16",
  "items": [
    {"name": "定制开发服务", "category": "software",
     "qty": "1", "price": "110000", "unit": "套", "remark": ""}
  ]
}
```

- 顶层每个键对应 `analysis.json` 的 `fields[].key`；`_` 开头的键被忽略。
- 数组键由 `list_tables[].rows_source` 指定，默认 `items`；条数任意。
- 行内公式列留空即可（由 `columns[].formula` 生成）。
- 未提供的非必填字段保持模板原值不动。

---

## 三、`fill_report.json`

| 键 | 含义 |
|----|------|
| `ok` | 无必填缺失、无未命中定位、无板式漂移、无意外改动部件时为 `true` |
| `fields_filled` / `fields_total` | 回填字段数 |
| `missing_required` / `unmatched_locators` | 缺失必填 / 没找到的定位 |
| `derived` / `item_computed` | 自动算出的字段值与清单列值 |
| `tables` | 各清单表扩展前后行数与实际条数 |
| `total_cells` / `cell_updates` | 汇总单元格写入值 |
| `changed_parts` | 实际改动的 xlsx 包内部件；出现 `xl/styles.xml` / `xl/media/` / `vbaProject.bin` 即为异常 |
| `layout` | `identical` 与 `differences` / `expected_changes` |
| `warnings` | 公式失败、样例残留、透视/切片器等提示 |
| `summary` | **一行验收结论** |
| `verification` | `fields`（最终值）、`preview`（成品各 Sheet 前几行）、`checks`（由上述项汇总） |
| `analysis_cached` | 本次是否按模板 hash 入缓存；`ok=false` 时跳过 |

`ok=false` 时回到第 2/3 步修正缓存后重跑，**不要修改模板**。看 `summary` + `verification` + `layout` 即可完成验收，无需再打开 `output.xlsx` 读回核对。

---

## 四、执行效率约定

- `prep.py` 命中缓存时 `analysis.json` 已就位，直接跳到第 3 步；字段清单与定位逻辑无需重做。
- 第 4 步 `answers` 传 `-`，从 stdin 读 JSON 并自动落盘，一次工具调用完成「写答案 + 生成 + 验收」。
- 第 4 步务必传 `--cache-key <prep.py 输出的 selected.hash>`。
- `analysis.json` 中坐标始终是模板坐标；引擎负责所有行号平移。
