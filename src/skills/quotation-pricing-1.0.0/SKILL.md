---
name: quotation-pricing
description: 根据「室内材料报价清单」Excel（spec-to-quotation-list 产出或外部同类清单）按知识库填写单价。流程：①解析清单结构与条目；②确认门——与用户确认报价范围（全部或局部空间/大类/类别/编号）；③知识库优先匹配产品与价格（规格一致取库价；不一致视为替换产品，必须留痕差异说明；不确定降级「待确认」）；④确认门——知识库缺项时询问用户是否由大模型给建议参考价并标注来源；⑤回填 Excel（I 列单价、M/N 列来源说明、新增「替换与差异」sheet）；⑥复核范围内每一项是否都已报价。This skill is used when the user has a quotation-list Excel and asks to fill in unit prices / do the pricing / 报价填写 / 填价 / 套价 / 给报价清单填价格 / 按知识库报价.
metadata:
  version: "1.0.0"
  author: aid-work-agent
requires_tools:
  - knowledge_base_search
  - knowledge_file_search
dependencies:
  - openpyxl
---

# 报价清单填写（按知识库报价 / 分阶段交互）

## 用途

承接 `spec-to-quotation-list` 产出的 Excel 报价清单（每文件 3 表：主表 / 参数 Parameters / Notes 说明，主表列序 A 编号、B 描述、C:D 区域（合并）、E 数量、F 单位、G 品牌、H 图片、I 单价、J 合价、K 数量依据、L 依据），**把 I 列（单价）填起来**，并产出替换差异与复核报告。也兼容外部来源的同类列序清单。

**核心原则：**

1. **先分析、再定范围**：先读清单结构，与用户确认「本次要报哪些」——可以是全部，也可以只报局部（指定空间 / 大类 / 类别 / 具体编号）。
2. **知识库优先**：优先用知识库（租户知识库价目资料 / 会话内上传的本地价目表）里的产品与价格报价。
3. **规格一致性**：匹配时比对规格，若不一致要用「替换产品」，必须留痕差异说明并经用户确认；关键规格不确定时降级「待确认」，不得自动成交。
4. **缺项询问建议价**：知识库找不到的，询问用户是否由大模型给建议参考价；若同意，则由大模型给价并**标注来源**（AI建议参考价，非成交价）。
5. **末道复核**：确认报价范围内的每一项是否都已完成报价。

**不要使用本技能**：从零把 spec PDF 转成报价清单用 `spec-to-quotation-list`；纯表格编辑（改数量/格式）直接用表格工具；非材料/产品类报价（工程量清单计价、服务报价）不适用。

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/parse_quotation.py` | 读一个或多个报价清单 xlsx（支持目录批量），抽取条目（编号/描述/规格要点/数量/单位/品牌/行号/文件）为 `items_extracted.json`，并打印结构摘要 |
| `scripts/fill_quotation.py` | 把 agent 在 `items_extracted.json` 中填写的单价/来源/替换说明**写回** Excel：填 I 列单价、M 列来源、N 列说明，并生成「替换与差异 Replacements」sheet；默认输出 `<原名>_priced.xlsx` |
| `scripts/review.py` | 读 `items_extracted.json`，校验报价范围内每一项是否都已报价，输出 `review_report.json` 与控制台复核摘要（未报价清单 / 替换项 / 来源统计） |

> 知识库检索、规格一致性判定、建议价生成等「智能」环节由 agent 在对话中完成（见流程），脚本只做确定性的解析 / 回填 / 校验。

## 流程（分阶段交互，含确认门）

所有中间产物（items_extracted.json、review_report.json、priced.xlsx）用绝对路径存放在 `$SKILL_TMP_DIR` 下的一次性子目录，跨命令引用一律写绝对路径。

### 0. 分析清单结构与内容

```bash
python scripts/parse_quotation.py <清单.xlsx 或 目录> --json items_extracted.json [--project "项目名"]
```

脚本抽取所有条目并打印：
- 涉及哪些文件 / 空间 / 大类（由文件名推断，如 `HappyRoom_家具软装.xlsx` → 空间 HappyRoom、大类 家具软装）
- 每个文件多少条、是否含 TBC 占位、当前 I 列是否全空（未报价）

**把这份结构摘要读给用户**，作为下一阶段定范围的基础。

### 1. 确认报价范围（★ 确认门：交用户确认）

调用 `ask_user` 发起结构化提问并等待实际作答：**本次要报哪些？**

- **全部**：所有文件、所有条目。
- **局部**：按空间 / 大类 / 类别 / 具体编号裁剪，例如「只报 HappyRoom 与 Bathroom Pod」「只报洁具五金类别」「只报 HR-1601、HR-1602 两条」。

`questions` 包含一题 `type=single`、`required=true`，候选项放 `options[].label`（如「全部条目」「只报指定范围（正文写明）」），保留 `allow_custom_text=true`，确认门设置 `allow_skip=false`。正文同时给出条目统计与范围选项明细；渠道端按提问文字作答。

确认后，**在 `items_extracted.json` 里把命中条目的 `in_scope` 设为 `true`，其余设为 `false`**（直接编辑该 JSON 即可）。未确认前不要回填任何价格。

> TBC 占位、甲供（Owner Supplied）条目默认**不纳入报价范围**（甲供由业主报价），除非用户明确要代填。

### 2. 知识库匹配报价（知识库优先 + 规格一致性）

对每个 `in_scope=true` 的条目：

1. **检索知识库**：价目表/产品目录类资料优先用 `knowledge_file_search` 按文件名定位（如「价目表」「产品目录」「price list」），命中后按文档定位价格；再用 `knowledge_base_search` 按「中文名/英文名 + 关键规格」语义检索（如 `单人沙发 velvet W700`）。检索返回文本片段，从中**提取产品名与单价**，并记录来源文档名。若用户在会话内另给了本地产品价目表（Excel/CSV），直接读它建内存索引，精确优先。详见 `references/kb_search.md`。
2. **判定匹配与一致性**：
   - **命中且规格一致**（尺寸/材质/颜色/品牌/型号等关键规格全部对得上）→ 取知识库单价，填 `unit_price`，`source` 写 `"知识库-KB"`，`kb_match` 写匹配产品名 + **来源文档名**，`spec_consistent=true`。
   - **命中但规格不一致**（任一关键项不同 → 视为替换产品）→ 填替换产品的单价，`source="知识库-KB(替换)"`，`spec_consistent=false`，并在 `replacement_desc` 写清「原规格 vs 替换规格」差异与替换理由。**替换项汇总后必须经用户过目确认**（`ask_user`，正文列全部替换对照），确认后才允许进入回填。
   - **关键规格存疑 / KB 命中多条候选无法确证** → 降级 `source="待确认"`，单价留空，`note` 写明存疑点。**不得凭低置信匹配成交。**
   - **未命中** → `unit_price` 留空，标记待建议（`source=null` 留待阶段 3）。

把结果写回 `items_extracted.json` 对应字段。

### 3. 询问是否给建议报价（★ 确认门：仅对缺项）

收齐所有「知识库未命中」的条目，用 `ask_user` 一次性问用户（`allow_skip=false`）：**以下 N 项在知识库未找到，是否由我给出建议参考价？**

- **同意** → 由大模型基于市场常识给出参考单价，填 `unit_price`，`source="AI建议参考价"`，并在 `note` 注明「大模型建议，非成交价，需复核」。
- **拒绝** → `source="未报价"`，`note="待用户提供/待询价"`，保留空单价。

> 建议价必须**标注来源**（AI建议参考价），不得冒充知识库或成交价。

### 4. 回填 Excel

```bash
python scripts/fill_quotation.py --json items_extracted.json [--indir <清单所在目录>] [--outdir <输出目录>]
```

脚本对每个文件：
- 在 I 列写 `unit_price`（J 列合价公式 `=IF(OR(E="",I=""),"",E*I)` 已存在，打开 Excel 自动重算，无需改）。
- 在 M 列写 `source`（报价来源），N 列写 `note` 或「替换：…」说明。
- 若有 `spec_consistent=false` / `replacement_desc` 的条目，生成 **「替换与差异 Replacements」** sheet，逐条列出：编号、原产品、KB匹配、规格差异/替换说明、单价、来源。
- 输出 `<原名>_priced.xlsx`（不破坏原文件）。

生成的每个 `_priced.xlsx` 用 `cp` 工具逐个注册回传给用户（`register_download=True`，`display_name` 用 `NN_空间_大类_priced.xlsx`）。

### 5. 复核（末道闸门）

```bash
python scripts/review.py --json items_extracted.json
```

脚本核对 `in_scope=true` 的条目是否都有 `unit_price`，输出：
- 范围内条目总数 / 已报价 / 未报价 / 替换项 / 知识库来源数 / AI建议来源数
- **未报价清单**（若有，必须回到阶段 2–3 补齐或明确标记）
- **替换项清单**
- 结论「全部完成？是/否」

**只有 review 显示「全部完成」才交付**；若有未报价项，向用户说明哪些是「故意留空（待询价/甲供/待确认）」还是「遗漏」。

## 硬约束

- **先确认范围、后回填**：阶段 1 确认门前不得写任何价格到 Excel。
- **知识库优先、来源可溯**：凡来自知识库的价必须标 `知识库-KB`（替换标 `知识库-KB(替换)`）且 `kb_match` 记录来源文档名；AI 建议必须标 `AI建议参考价`，且写明「非成交价」。任何价格都要能追到来源之一：`知识库-KB` / `知识库-KB(替换)` / `AI建议参考价` / `未报价` / `待确认`。
- **规格不一致必须显式替换并留痕**：不得静默用不对版的产品顶替；差异说明进「替换与差异」sheet，且交付前必须经用户过目确认。
- **低置信降级**：关键规格存疑或 KB 多候选无法确证时标 `待确认`，不自动成交。
- **币种一致**：清单与知识库价目币种不一致时不得直接填价，标 `待确认` 并在 `note` 写明两种币种。
- **不编造数量/规格**：仅填价格，数量（E）与规格（B）以原清单为准，不改。
- **不破坏原文件**：回填默认产出 `<原名>_priced.xlsx`；除非用户要求 `--indir` 同目录且显式同意覆盖。
- **TBC/甲供默认不报价**：除非用户明确纳入范围。
- **范围外不填**：`in_scope=false` 的条目一律留空。

## 输出结构

- `<原名>_priced.xlsx`（经 cp 注册回传）：主表 I/M/N 列已填，附「替换与差异 Replacements」sheet。
- `review_report.json` + 控制台摘要：复核结论。
- （过程中）`items_extracted.json`：agent 填写的中转数据，可保留备查。

## 变更记录 Changelog

### v1.0.0（2026-10-27）

初版移植自 workbuddy quotation-pricing v1.0.0：分析结构 → 确认范围 → 知识库优先匹配（规格一致性 + 替换留痕 + 低置信降级待确认）→ 缺项询问 AI 建议价（标来源）→ 复核完整性。配套 `parse_quotation.py` / `fill_quotation.py` / `review.py` 三脚本（原样移植，列序与 spec-to-quotation-list 2.0.1 对齐版一致）。适配本环境：knowledge_file_search/knowledge_base_search 检索、ask_user 确认门、cp 回传、币种约束、v1 替换差异不嵌图片。
