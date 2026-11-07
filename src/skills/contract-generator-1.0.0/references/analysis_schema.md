# 分析缓存与答案缓存格式 Analysis Cache Schema

第 2 步的产物是 `analysis.json`，第 3 步的产物是 `answers.json`。它们属于**具体某次合同任务**，
放在用户工作区，不放进技能目录。技能本身不感知任何模板的字段。

`scripts/fill_contract.py` 是这两个缓存的通用解释器：它不知道合同内容，只知道按缓存执行。

---

## 一、`analysis.json`

```json
{
  "template": "<你放入 assets/templates 的合同模板名>.docx",
  "deploy_mode": "公有云",
  "price_table": "<你的价格库.csv（来自资料库/标准售价表，已核对）>",
  "analysis_note": "本次分析结论的自然语言说明，便于复核",
  "fields": [ ... ],
  "derived_fields": { "total_upper": "cny_upper:total_amount" },
  "service_tables": [ ... ],
  "cell_updates": [ ... ]
}
```

| 键 | 必填 | 说明 |
|----|------|------|
| `template` | 是 | 模板文件名，仅作记录；实际路径由命令行传入 |
| `deploy_mode` | 否 | 查价过滤用，如 `公有云` / `私有云` |
| `price_table` | 否 | 价格表路径（你提供的价格库 CSV）；命令行 `--prices` 优先。技能不内置价格数据 |
| `analysis_note` | 否 | 分析结论摘要，便于人工复核 |
| `fields` | 是 | 需要收集/回填的字段清单 |
| `derived_fields` | 否 | 不向用户提问、由公式算出的字段（按声明顺序计算） |
| `service_tables` | 否 | 产品/服务清单表（可多张），支持动态增删行 |
| `cell_updates` | 否 | 不改行数、只更新数值的单元格（汇总表、付款计划等） |

### 1.1 `fields[]`

```json
{
  "key": "party_a",
  "label": "甲方公司全称",
  "question": "客户（甲方）公司全称？",
  "required": true,
  "default": null,
  "formula": "cny_upper:total",
  "price_lookup": {"query_key": "product", "mode": "公有云"},
  "locators": [ ... ]
}
```

| 键 | 说明 |
|----|------|
| `key` | 字段唯一键，与 `answers.json` 的键对应 |
| `label` / `question` | 展示名与第 3 步的提问话术 |
| `required` | 缺失时回填报告标 `missing_required` |
| `default` | 用户未答时的默认值（可选） |
| `formula` | 有值时不再提问，由表达式计算（见 1.3） |
| `price_lookup` | 答案缺失时按 `query_key` 指向的答案字段值从**你提供的**价格库查价（严禁使用模板中的示例价格）；`mode` 覆盖 `deploy_mode` |
| `locators` | **填在哪里**，可多个；一个值出现在文档多处时写多个 |

> 字段值写入采用「一次性哨兵替换」：所有定位先写唯一临时标记，最后统一解析成真值，
> 因此 A 字段填进去的值即使恰好等于 B 字段的锚点，也不会被二次替换。

### 1.2 `locators[]` 五种定位方式

坐标与 `dump_template.py` 输出一致：`P<n>` 是顶层段落下标，`T<t> R<r> C<c>` 是表格单元格。

| `kind` | 参数 | 用途 |
|--------|------|------|
| `literal` | `text`（模板中的原文，必填）、`scope`、`value_template` | 把模板里某个具体值/样例文字替换掉。最常用 |
| `regex` | `pattern`、`value_template` | 原文格式不固定或需整段替换（如「大写： 叁仟 元整」） |
| `after_label` | `label`、`stop`（可选，遇到即停）、`value_template` | 标签后留空/待填，如 `地    址：` 后面 |
| `cell` | `table` / `row` / `col` | 直接写整个单元格，适合模板里的空白格 |
| `paragraph` | `index` | 整段重写，谨慎使用 |

通用可选参数：

- `where`: `body` / `cell` / `headerfooter`，限定搜索范围。
- `paragraph_index`: 配合 `where: body`，只作用于该正文段落。
- `table` / `row` / `col`: 把 `literal` / `regex` 限定在某个单元格内。
- `contains`: 只作用于含该子串的段落。
- `scope`: `all`（默认）/ `first` / 整数 N（前 N 处）。
- `value_template`: 写入格式，默认 `{value}`。
- `sample`: 仅 `cell` 用；优先做局部文本替换以保留更多原有格式。
- `check_absent`: `true` 时，生成后复核该原文已彻底消失，否则告警。

`value_template` 可用变量：

| 变量 | 含义 |
|------|------|
| `{value}` | 原值 |
| `{value_fmt}` | 数字千分位，如 `110000` → `110,000` |
| `{value_upper}` | 人民币中文大写 |
| `{index}` | 清单行序号（从 1 开始），仅清单列可用 |
| `{清单条目字段}` | 如 `{qty}` `{unit}` `{name}`，仅清单列可用 |

### 1.3 公式表达式

用于 `formula`、`derived_fields`、`service_tables[].columns[].formula`、`total_cells[].formula`、`cell_updates[].formula`。

- 简写：`sum:amount`（清单某列求和）、`cny_upper:total`（金额大写）、`sum_where:category=software:amount`。
- 通用表达式：`total * 0.5`、`100 - first_pay_ratio`、`sum('amount')`、
  `sum_where('category','software','amount')`、`fmt(total * 0.5 / 12) + ' /月 × 12月'`。
- 可用函数：`sum`、`sum_where`、`cny_upper`、`fmt`、`count`；变量来自已算出的字段与清单条目。
- 表达式只允许数字、字段名、四则运算与上述函数，不接受任意代码。

### 1.4 `service_tables[]`（动态多行清单）

```json
{
  "table_index": 1,
  "rows_source": "service_items",
  "items_filter": "category == 'software'",
  "header_rows": 1,
  "sample_rows": [1, 2, 3],
  "footer_rows": [4],
  "columns": [
    {"key": "name",       "col": 0, "sample": "样例产品名", "label": "项目名称"},
    {"key": "unit_price", "col": 1, "sample": "110,000", "value_template": "{value_fmt}", "price_lookup": true, "query_key": "name"},
    {"key": "qty",        "col": 2, "sample": "1 套", "value_template": "{qty} {unit}"},
    {"key": "amount",     "col": 3, "sample": "110,000", "value_template": "{value_fmt}", "formula": "unit_price*qty"},
    {"key": "desc",       "col": 4, "label": "功能简述"}
  ],
  "total_cells": [
    {"footer_index": 0, "col": 4, "formula": "sum('amount')", "value_template": "{value_fmt}"}
  ]
}
```

| 键 | 说明 |
|----|------|
| `table_index` | 表序号（与 dump 一致） |
| `rows_source` | `answers.json` 中的数组键，默认 `service_items` |
| `items_filter` | 可选，按条目字段过滤，用于同一份清单拆到多张附件表 |
| `header_rows` | 表头行数，不参与动态扩展 |
| `sample_rows` | **模板里的样例行下标**，生成时被删除并按实际条数克隆替换 |
| `footer_rows` | 合计/小计行下标，保留并可用 `total_cells` 更新数值 |
| `columns[].key` | 对应清单条目的字段；列公式的结果会写回条目，供合计与派生字段使用 |
| `columns[].sample` | 样例行中的原文；提供时做局部替换（保留格式），不提供则整格写入。长文本建议不提供 |
| `columns[].price_lookup` | 该列为空时按 `query_key`（默认 `name`）从**你提供的**价格库取价（严禁使用模板中的示例价格） |
| `columns[].formula` | 列值计算，如 `unit_price*qty` |
| `total_cells[]` | `footer_index`（指向 `footer_rows` 第几行）或 `row`（绝对行号）+ `col` + `formula` |

行数完全由 `answers.json` 的清单条数决定：**可多于、少于模板样例行，也可以为 0**。
新行由样例行 XML 深拷贝而来，边框、底纹、行高、列宽、字体全部继承，版式不走样。

### 1.5 `cell_updates[]`

行数固定的表（费用汇总、付款计划）只需更新数值：

```json
{"table": 15, "row": 1, "col": 2, "formula": "sum_where('category','software','amount')", "value_template": "{value_fmt}"}
```

---

## 二、`answers.json`

```json
{
  "_meta": {"client": "客户简称", "note": "问答过程备注"},
  "party_a": "某某科技有限公司",
  "sign_date": "2026 年 11 月 5 日",
  "service_items": [
    {"name": "定制私有化 AI 客服智能体", "category": "software",
     "unit_price": "", "qty": "1", "unit": "套", "qty_text": "1套",
     "amount": "", "desc": "功能描述"}
  ],
  "packages": [ {"name": "标准版", "amount": "12000", "points": "30000"} ]
}
```

- 顶层每个键对应 `analysis.json` 的 `fields[].key`；`_` 开头的键被忽略。
- 数组键（默认 `service_items`）对应 `service_tables[].rows_source`，条数任意。
- 单价、总价等留空即可：查价由 `price_lookup` 补、计算由 `formula` 补，结果会写回清单。
- 未提供的非必填字段保持模板原值不动。

---

## 三、`fill_report.json`（第 4 步校验报告）

| 键 | 含义 |
|----|------|
| `ok` | 无必填缺失、无未命中定位、**且内置自检通过**时为 `true` |
| `fields_filled` / `fields_total` | 回填字段数 |
| `missing_required` | 缺失的必填字段 |
| `unmatched_locators` | 没在模板中找到的定位（多半是分析缓存写错原文） |
| `price_lookups` | 从你提供的价格库取到的价格及命中条目 |
| `derived` / `item_computed` | 自动算出的字段值与清单列值 |
| `service_tables` | 每张清单表扩展前后的行数与实际条数 |
| `total_cells` / `cell_updates` | 汇总单元格写入值 |
| `warnings` | 残留样例值、大写金额重复、表达式失败等 |
| `summary` | **一行验收结论**：ok / 字段 / 清单项 / 未命中定位 / 警告 / 大写重复 |
| `verification` | **验收详情**：各字段最终值、清单表行数、金额原文行、checks（占位符残留、大写「元整…元整」重复、自检失败等） |
| `analysis_cached` | 本次 `analysis.json` 是否按模板 hash 入缓存；`ok=false` 时跳过 |

`ok=false` 时回到第 2/3 步修正缓存后重跑，**不要修改模板**。看 `summary` + `verification` 即可完成验收，**无需再打开 `output.docx` 读回核对**。

---

## 四、执行效率约定（最少往返）

- `scripts/prep.py` 一次调用完成「扫描 + dump + 锚点 + 缓存检查」，输出的 `compact_dump` / `anchors` / `tables_schema` 已足以写 `analysis.json`；`anchors[].repr` 是精确原文，`locators[].text` 直接逐字抄用，**不必再单独跑脚本核对锚点**。
- `analysis.json` 按模板内容 hash 缓存在用户工作区 `_analysis_cache/`；`prep.py` 命中时自动复制到工作目录，**直接跳过第 2 步**。第 4 步 `ok=true` 时自动入缓存；自检失败不入缓存。
- 第 4 步 `answers` 参数可传 `-`，从 stdin 读 JSON（脚本自动落盘为 `answers.json`），**一次工具调用完成「写答案 + 生成 + 验收」**。
- 字段能由 `default` / `formula` 推出的不要提问；第 3 步把所有必填项一轮问全。
