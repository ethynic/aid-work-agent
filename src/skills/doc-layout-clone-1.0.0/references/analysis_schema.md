# 分析缓存与答案缓存格式 Analysis Schema

第 2 步的产物是 `analysis.json`，第 3 步的产物是 `answers.json`。它们属于**具体某次文档任务**，放在 $SKILL_TMP_DIR 任务目录中，不放进技能目录。技能本身不感知任何样版的字段。

`scripts/fill_doc.py` 是这两个缓存的通用解释器：它不知道文档内容，只知道按缓存执行。

---

## 一、`analysis.json`

```json
{
  "template": "<样版文件名>.docx",
  "analysis_note": "本次分析结论的自然语言说明，便于复核",
  "fields": [ ... ],
  "derived_fields": { "total_upper": "cny_upper:total" },
  "list_tables": [ ... ],
  "cell_updates": [ ... ]
}
```

| 键 | 必填 | 说明 |
|----|------|------|
| `template` | 否 | 样版文件名，仅作记录；实际路径由命令行传入 |
| `analysis_note` | 否 | 分析结论摘要，便于人工复核 |
| `fields` | 是 | 需要改写/回填的字段清单 |
| `derived_fields` | 否 | 不向用户提问、由公式算出的字段（按声明顺序计算） |
| `list_tables` | 否 | 可增减行的清单表（可多张）；旧键 `service_tables` 兼容 |
| `cell_updates` | 否 | 不改行数、只更新数值的单元格（汇总表等） |

### 1.1 `fields[]`

```json
{
  "key": "project_name",
  "label": "项目名称",
  "question": "本次的项目名称是什么？",
  "required": true,
  "default": null,
  "formula": null,
  "phase": 1,
  "depends_on": null,
  "skip_when_blocked": true,
  "hint": null,
  "locators": [ ... ]
}
```

| 键 | 说明 |
|----|------|
| `key` | 字段唯一键，与 `answers.json` 的键对应 |
| `label` / `question` | 展示名与第 3 步的提问话术 |
| `required` | 缺失时报告标 `missing_required`，且 `ok=false` |
| `default` | 用户未答时的默认值（可选） |
| `formula` | 有值时不再提问，由表达式计算（见 1.3） |
| `phase` | 问答轮次序号，默认 1；小的先问。用于「先问方向，再问细节」 |
| `depends_on` | 见 1.2.1；未满足时本轮不问，也不计缺失，留到下一轮 |
| `skip_when_blocked` | 默认 `true`：依赖未满足就跳过。设 `false` 则无论如何都要问 |
| `hint` | 给用户的填写提示（单位、格式、可从哪份材料取数），只影响提问不写进文档。注明应从哪份知识库/参考资料取数；找不到时按缺口处理，不得退而取样例值 |
| `locators` | **填在哪里**，可多个；一个值出现在多处时写多个 |

> 关于 `default` / `formula`：用来填**凡能由来源真实值/已收集答案确定性算出的派生值**——当天日期、流水编号、合计/小计、人民币大写、比例/税价换算、单价×数量、日期与期限推算等，**一律自动算、不向用户提问**（见 SKILL.md「自动计算」）。**不得用它们生成业务事实基础数据**（甲方名称、真实成交额、人名、签署日期、结论、条款等）——那属于编造，违反最高铁律；这类基础值缺失时按缺口处理（问用户或留待补），但**由它们派生出的合计/大写/分期等仍应自动算出**。区分标准：一次性写入的基础事实值 = 业务事实（不得编造）；对已知值做确定性四则/函数运算的结果 = 派生值（应自动算）。

#### 1.2.1 `depends_on`（多轮问答的驱动开关）

复杂场景常出现「后一个问题的形态取决于前一个答案」：选了“是”才需要填比例，选了某个类型才需要填该类型的专属字段。用 `depends_on` 表达这种依赖，问答自然分成多轮：

```json
{"key": "settle_mode", "label": "结算方式", "required": true, "phase": 1}

{"key": "installment_count", "label": "分期期数", "required": true, "phase": 2,
 "depends_on": {"key": "settle_mode", "equals": "分期付款"}}

{"key": "warranty_years", "label": "质保年限", "required": true, "phase": 3,
 "depends_on": {"all": [{"key": "settle_mode", "equals": "分期付款"},
                        {"key": "installment_count", "not_empty": true}]}}
```

支持形态（可嵌套）：

| 形态 | 含义 |
|------|------|
| `{"key": "x"}` 或 `{"key":"x","not_empty":true}` | x 有值即满足 |
| `{"key": "x", "equals": "值"}` | 等于该值 |
| `{"key": "x", "not_equals": "值"}` | 不等于该值 |
| `{"key": "x", "in": ["a","b"]}` | 取值在集合内 |
| `{"all": [dep, ...]}` | 全部满足 |
| `{"any": [dep, ...]}` | 任一满足 |

**关键规则**：被依赖挡住且标了 `required` 的字段，本轮**不进 `missing_required`**，而是记进报告的 `deferred` / `verification.checks.deferred_by_dependency`。因此「第二轮还没问完」不会把 `ok` 打成 `false`，也不会阻塞已确定内容的回填。是否该继续问，由 `probe.py` 的 `ready_to_fill` 判定，不看回填报告。

> 判断依据只能来自答案本身（某个字段有没有值、等不等于某值）。不要写成业务条件表达式，也不要指望它读取文档内容——那属于第 2 步分析时的判断。

> 字段值写入采用「一次性哨兵替换」：所有定位先写唯一临时标记，最后统一解析成真值，
> 因此 A 字段填进去的值即使恰好等于 B 字段的锚点，也不会被二次替换。

### 1.2 `locators[]` 五种定位方式

坐标与 `dump_template.py` 输出一致：`P<n>` 是顶层段落下标，`T<t> R<r> C<c>` 是表格单元格。

| `kind` | 参数 | 用途 |
|--------|------|------|
| `literal` | `text`（样版原文，必填）、`scope`、`value_template` | 把样版里某个具体值/样例文字替换掉。最常用 |
| `regex` | `pattern`、`value_template` | 原文格式不固定或需整段替换，`{m0}` 代表匹配原文 |
| `after_label` | `label`、`stop`（可选，遇到即停）、`value_template` | 标签后留空/待填，如 `项目名称：` 后面 |
| `cell` | `table` / `row` / `col` | 直接写整个单元格，适合样版里的空白格 |
| `paragraph` | `index` | 整段重写，谨慎使用 |

通用可选参数：

- `where`: `body` / `cell` / `headerfooter`，限定搜索范围。
- `paragraph_index`: 配合 `where: body`，只作用于该正文段落。
- `table` / `row` / `col`: 把 `literal` / `regex` 限定在某个单元格内。
- `contains`: 只作用于含该子串的段落。
- `scope`: `all`（默认）/ `first` / 整数 N（前 N 处）。
- `value_template`: 写入格式，默认 `{value}`。
- `sample`: 仅 `cell` 用；优先做局部文本替换以保留更多原有格式。
- `check_absent`: `true` 时复核该原文已彻底消失（写进报告 warnings）。

`value_template` 可用变量：

| 变量 | 含义 |
|------|------|
| `{value}` | 原值 |
| `{value_fmt}` | 数字千分位，如 `100000` → `100,000` |
| `{value_upper}` | 人民币中文大写 |
| `{index}` | 清单行序号（从 1 开始），仅清单列可用 |
| `{清单条目字段}` | 如 `{qty}` `{unit}` `{name}`，仅清单列可用 |

### 1.3 公式表达式

用于 `formula`、`derived_fields`、`list_tables[].columns[].formula`、`total_cells[].formula`、`cell_updates[].formula`。

- 简写：`sum:amount`（清单某列求和）、`cny_upper:total`（金额大写）、`sum_where:category=software:amount`。
- 通用表达式：`total * 0.5`、`100 - first_pay_ratio`、`sum('amount')`、
  `sum_where('category','software','amount')`、`fmt(total * 0.5 / 12) + ' /月 × 12月'`。
- 可用函数：`sum`、`sum_where`、`cny_upper`、`fmt`、`count`；变量来自已算出的字段与清单条目。
- 表达式只允许数字、字段名、四则运算与上述函数，不接受任意代码。

### 1.4 `list_tables[]`（动态多行清单）

```json
{
  "table_index": 1,
  "rows_source": "items",
  "items_filter": "category == 'software'",
  "header_rows": 1,
  "sample_rows": [1, 2, 3],
  "footer_rows": [4],
  "columns": [
    {"col": 0, "sample": "1", "value_template": "{index}", "label": "序号"},
    {"key": "name",       "col": 1, "sample": "（示例名称）", "label": "项目名称"},
    {"key": "unit_price", "col": 2, "sample": "100,000", "value_template": "{value_fmt}"},
    {"key": "qty",        "col": 3, "sample": "1 套", "value_template": "{qty} {unit}"},
    {"key": "amount",     "col": 4, "sample": "100,000", "value_template": "{value_fmt}", "formula": "unit_price*qty"},
    {"key": "desc",       "col": 5, "label": "说明"}
  ],
  "total_cells": [
    {"footer_index": 0, "col": 4, "formula": "sum('amount')", "value_template": "{value_fmt}"}
  ]
}
```

| 键 | 说明 |
|----|------|
| `table_index` | 表序号（与 dump 一致） |
| `rows_source` | `answers.json` 中的数组键，默认 `items` |
| `items_filter` | 可选，按条目字段过滤，用于同一份清单拆到多张表 |
| `min_items` | 可选，默认 1；收集时提示至少给几条，`0` 表示允许空表 |
| `item_hint` | 可选，告诉用户每条至少要有哪些字段，只影响提问 |
| `header_rows` | 表头行数，不参与动态扩展 |
| `sample_rows` | **样版里的全部样例数据行下标**（不含表头/合计）；生成时这些行被删除并按实际条数克隆替换。**漏列的样例行不会被删，会作为空行残留在新文档中** |
| `footer_rows` | 合计/小计行下标，保留并可用 `total_cells` 更新数值 |
| `columns[].key` | 对应清单条目的字段；列公式的结果会写回条目，供合计与派生字段使用。序号列只是展示，可省略 `key` |
| `columns[].sample` | 样例行中的原文；提供时做局部替换（保留格式），不提供则整格写入 |
| `columns[].value_template` | 写入格式，支持 `{value}` `{value_fmt}` `{value_upper}` `{index}`（行序号，从 1 开始）与清单条目字段。**序号列用 `"{index}"` 自动编号**；不声明则克隆行会继承样例行的旧序号（如全是"1"） |
| `columns[].formula` | 列值计算，如 `unit_price*qty` |
| `total_cells[]` | `footer_index`（指向 `footer_rows` 第几行）或 `row`（绝对行号）+ `col` + `formula` |

行数完全由 `answers.json` 的清单条数决定：**可多于、少于样版样例行，也可以为 0**。
新行由样例行 XML 深拷贝而来，边框、底纹、行高、列宽、字体全部继承，版式不走样。
板式验收时，动态表的行数变化属预期，不计入 `layout.differences`。

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
  "project_name": "（来自内容来源的项目名称）",
  "sign_date": "（来自内容来源的日期）",
  "items": [
    {"name": "（条目名称）", "category": "software",
     "unit_price": "100000", "qty": "1", "unit": "套",
     "amount": "", "desc": "（功能描述）"}
  ]
}
```

- 顶层每个键对应 `analysis.json` 的 `fields[].key`；`_` 开头的键被忽略。
- 数组键（由 `list_tables[].rows_source` 指定，默认 `items`）条数任意。
- 单价、总价等留空即可：计算由 `formula` 补，结果会写回清单。
- 未提供的非必填字段保持样版原值不动。

**内容来源约束（最高优先级）**：`answers.json` 里每个实际内容值都必须来自知识库检索、用户上传的参考资料，或用户在对话中的明确指令。**不得填入样版/历史文件里的样例值，也不得自行编造**。来源缺失的必填项，写成待补标注（如 `【待补：请提供 XX】`）或先不填入、向用户索取；只有用户明确要求"用示例 / 模拟数据"时，才允许填示例值，并需在交付时声明。`default` / `formula` 只用于**结构派生值**（日期、编号、合计、大写等），不得用来凭空生成业务事实数据。

在 `_meta` 里记录每个字段的来源以便追溯（`_` 开头键不参与回填）：

```json
"_meta": {
  "client": "客户简称",
  "sources": {"project_name": "知识库检索", "revenue_2025": "用户上传的财报.pdf"},
  "gaps": ["sign_date"],
  "note": "sign_date 来源缺失，成品留待补标注"
}
```

| `_meta` 键 | 说明 |
|-----------|------|
| `sources` | 字段名 -> 来源说明。**第 4 步来源审计的依据**：提供后，`fill_report.source_audit` 会列出 `ungrounded_fields`（有值但无来源声明的字段）；不提供则审计记为 `source_declaration: absent` 并给一条提示，**不会**把所有字段误标为无来源 |
| `gaps` | 来源缺失的字段名列表。识别缺口并留白是正确行为，报告会提醒交付前确认这些字段已留 `【待补：…】` 或已向用户索取，`unfilled_gaps` 列出其中仍为空的 |
| `rounds` / `last_round` | 由 `probe.py --merge` 自动写入，勿手工维护 |

---

## 三、`fill_report.json`（第 4 步校验报告）

| 键 | 含义 |
|----|------|
| `ok` | 无必填缺失、无未命中定位、无标记残留、**板式指纹一致**时为 `true` |
| `fields_filled` / `fields_total` | 回填字段数 |
| `missing_required` | 缺失的必填字段（**不含被 `depends_on` 挡住的**） |
| `deferred` | 因依赖未满足而本轮未问的字段及阻塞键，`checks.deferred_by_dependency` 为其条数 |
| `unmatched_locators` | 没在样版中找到的定位（多半是分析缓存写错原文） |
| `derived` / `item_computed` | 自动算出的字段值与清单列值 |
| `tables` | 各清单表扩展前后的行数与实际条数 |
| `total_cells` / `cell_updates` | 汇总单元格写入值 |
| `layout` | `identical` 与 `differences`：页面尺寸/边距/段落样式/非清单表结构对比 |
| `warnings` | 来源缺口、公式失败、板式漂移、内部标记残留、**样例残留提示**等 |
| `sample_residue` | **样版样例残留扫描**（最高铁律兜底）：`[{fragment, count}]`，样版中带业务单位的数值片段回填后仍原样保留的项。分析阶段漏纳入 locators 的样例数值不再静默通过验收。只提示不阻断、不改 `ok`：固定政策数值可合法保留，但须逐条确认是否应换成真实值；已在 locator 文本/新值/`_meta` 中声明的片段不报，正常场景为 0。`checks.sample_residue` 为其类别数 |
| `source_audit` | **来源审计**（最高铁律落地）：`declared_sources` / `declared_gaps`（取自 `answers.json._meta.sources` / `gaps`）、`ungrounded_fields`（无来源声明且非公式/默认的字段）、`unfilled_gaps`（仍为空的缺口字段）。仅提示不阻断，用于复核"内容是否都有来源、缺口是否留白" |
| `summary` | **一行验收结论**：ok / 字段 / 清单项 / 未命中定位 / 板式一致 / 警告 |
| `verification` | `fields`（最终值）、`tables`、`preview_lines`（成品前 12 行）、`checks`（含 `ungrounded_fields` / `source_gaps` 计数） |
| `analysis_cached` | 本次 `analysis.json` 是否按样版 hash 入缓存；`ok=false` 时跳过 |

`ok=false` 时回到第 2/3 步修正缓存后重跑，**不要修改样版**。看 `summary` + `verification` + `layout` 即可完成验收，**无需再打开 `output.docx` 读回核对**。

---

## 四、执行效率约定（最少往返）

- `scripts/prep.py` 一次调用完成「扫描 + 转换 + dump + 锚点 + 缓存检查」；`compact_dump` / `anchors` / `tables_schema` 已足以写 `analysis.json`；`anchors[].repr` 是精确原文，`locators[].text` 直接逐字抄用。
- `analysis.json` 按**源样版**内容 hash 缓存在 `$SKILL_TMP_DIR/doc_layout_cache/`；`prep.py` 命中时自动复制到工作目录，**直接跳过第 2 步**。第 4 步 `ok=true` 时自动入缓存；自检失败不入缓存。
- 第 4 步 `answers` 传 `-`，从 stdin 读 JSON（自动落盘为 `answers.json`），**一次工具调用完成「写答案 + 生成 + 验收」**。
- 字段能由 `default` / `formula` 推出的不要提问；第 3 步把所有必填项一轮问全。
- 第 4 步传 `--cache-key <prep.py 输出的 selected.hash>`，PDF 场景才会在下次按源 PDF 命中缓存。

### 4.1 多轮问答（`probe.py`）

单轮问全仍是默认。当字段之间存在 `depends_on` 依赖时，用 `probe.py` 决定每轮问什么：

```
# 第 1 轮：只看已有答案能解锁哪些问题
python3 scripts/probe.py <work>/analysis.json --round 1

# 用户回答后合并进答案，看第 2 轮
python3 scripts/probe.py <work>/analysis.json <work>/answers.json \
    --round 2 --merge <本轮新答案.json> --merge-out <work>/answers.json
```

`probe.py` 输出：`ready_to_fill`（是否可以直接回填）、`open_fields`（本轮该问的，按 `phase` 分组在 `by_phase`）、`blocked_fields`（依赖未满足暂不问及阻塞键）、`open_lists`（还没给条目的清单表）、`counts`。

- 每轮只问 `open_fields` + `open_lists`，**不要把 `blocked_fields` 提前问**——那正是第二轮存在的理由。
- `--merge` 会把本轮答案并入 `answers.json` 并在 `_meta.rounds` 留痕；不传 `--merge-out` 时回写 `answers` 参数所指文件。
- `ready_to_fill=true` 即可停止提问，进第 4 步。回填报告里的 `deferred` 条数应与 `blocked_fields` 一致，两者对不上说明依赖声明写错了。
