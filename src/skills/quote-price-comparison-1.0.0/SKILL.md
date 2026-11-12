---
name: quote-price-comparison
description: >
  供应商报价单横向比价技能。上传一份或多份任意格式报价单（xlsx/csv/docx/pdf/图片），
  先把条目抽取为统一结构（quotes.json），再与租户知识库中的历史报价单、互联网公开产品
  价格三方对比；归一化币种/含税/单位口径后按基准价判定偏贵与偏便宜，产出对比结果摘要、
  价格横向对比 Excel，并在源报价单「副本」上把偏贵价格标红、偏便宜价格标绿（含批注）。
  脚本只做确定性的解析、归一化、计算、写文件；知识库检索、联网比价、规格判断由上级
  智能体在对话中完成。适用于采购比价、多家报价对比、货比三家、报价单价格审核等场景。
metadata:
  version: "1.2.0"
  author: aid-work-agent
dependencies:
  - openpyxl
  - python-docx
  - PyMuPDF
  - Pillow
requires_tools:
  - knowledge_base_search
  - knowledge_file_search
  - web_search
  - web_fetch
  - cp
---

# 供应商报价单横向比价（上传单 + 知识库 + 互联网）

## 一、技能定位与边界

- 把「多家供应商报价单」与「知识库历史报价」「互联网公开价格」放在同一张可比口径的表里，
  给出结论摘要、横向对比 Excel，并把判定结果回标到源报价单上：**偏贵标红、偏便宜标绿**。
- 三类来源一律先转成同一份 `quotes.json`，用 `source_type` 区分 `upload` / `library` / `web`；
  脚本只做确定性的解析、归一化、计算、写文件；检索、规格判断、价格真伪判断由上级智能体在对话中完成。
- **不要使用本技能**：
  - 单一供应商、单份报价的录入/存档（无横向对比诉求）——直接录入业务数据即可；
  - 旅游/酒店/景点类报价——走 travel-quote 技能与对应业务页面；
  - 供应商资质真伪与信用风险审查——走 supplier-qualification-review 技能（可与本技能在同一对话串联使用）。

## 二、运行环境注意（必读）

- 每次 `skill_execute` 调用都在独立的临时工作目录中执行，调用间不共享 cwd。所有中间产物
  （quotes.json、comparison.xlsx、run_report.json 等）**必须用绝对路径**读写，统一放在
  `$SKILL_TMP_DIR` 下的任务目录中（环境变量已注入，持久不清理）：

  ```bash
  WORK=$SKILL_TMP_DIR/quote_compare/<项目名或 YYYYMMDD-HHMM>
  mkdir -p "$WORK"
  ```

- 技能目录运行时为只读；`scripts/` 下的脚本通过 `skill_execute` 调用，脚本路径会被自动映射为绝对路径。
- 用户上传的文件路径来自会话上传信息（冒号后面是实际上传路径），**始终使用实际上传路径**，不要用原始文件名拼路径。
- 依赖（openpyxl、python-docx、PyMuPDF、Pillow）已在 requirements.txt 中，容器内开箱可用；
  `.xls` 先另存为 `.xlsx`；扫描版 PDF 与图片报价单走多模态读取（`image_read` 工具）+ 手工 JSON。
- 交付前只需读 `$WORK/run_report.json` 一个文件即可掌握全部结论与风险，不必逐个打开
  `comparison.json` / `marks.json` / `unmarked.md` / Excel。

## 三、确定性要求（同一份输入必须产出同一份结论）

这是本技能最容易出错的地方：**同一批报价单由不同执行者（或不同次执行）跑出完全不同的合计金额**，
根因是「单位换算只作用于单价、不作用于数量」。本技能的硬性约束：

1. **金额守恒**：组内所有来源换算到同一标准单位后，`单价 × 数量` 必须与源报价单金额一致。
   换算时**单价 ×f、数量 ×g**（同量纲时 g=1/f），两者同步。
2. **标准单位由脚本判定**，不靠人工填 `unit_multiplier`：组标准单位 = 组内数量单位出现次数最多者；
   并列时取该量纲的基准单位（千件→件、kg→吨…）。
3. **价格单位与数量单位可以不同**：
   - `uom` = 单价的计价单位；`qty_uom` = 数量的计量单位（默认同 `uom`）；`qty` 恒为 `qty_uom` 下的数量。
   - 同量纲（如 单价元/吨、数量千克）按倍率自动换算。
   - 跨量纲（如 单价元/箱、数量包）**必须显式给 `qty_to_price_uom`**（1 数量单位 = 多少价格单位，
     如 1 包 = 0.05 箱）；未给则该条标不可比并提示，**绝不臆测**。
   - **改数量单位必须同步改数量值**（如 80,000 个 → 80 千件，两个都要改）；
     脚本会比对同组各来源的采购数量，不一致时明确报出是哪一家、原值是什么。
4. **网络价不参与任何统计口径**：只进参考列，不进基准、不进红绿判定、不进最低/最高价与价差率，
   也不计入「可比来源数」。否则"搜到几条网价"就能改变结论数字。
5. **未报价行必须保留**：源单里"图纸未确认""面议""暂不报价"的行要作为 `quote_status=no_quote` 条目留下，
   不能静默丢弃——丢了就会导致两次执行的品项集合不同。
6. **PDF 优先走表格识别**：文本层 PDF 报价单几乎都是真表格，必须用 PyMuPDF `find_tables()`；
   文本行兜底会把单元格逐行当成条目（价格变成 "50"、"1250" 这类碎片），只能用于真正的纯文本报价单。

## 四、默认口径（已生效，可单点调整，不阻塞开工）

| 项 | 默认取值 | 调整方式 |
|---|---|---|
| 基准价 | `upload` + `library` 归一化单价的**中位数** | `--baseline min\|mean\|median\|web\|library`、`--baseline-sources` |
| 红绿容差 | **±5%**；> +5% 偏贵(红)，< -5% 偏便宜(绿)，区间内不标色 | `--tolerance` |
| 税务口径 | 统一折算为**含税**单价（未税价 ×(1+税率)） | `--tax-basis incl\|excl\|asis`、`--default-tax-rate` |
| 币种 | 统一 CNY；跨币种必须显式给汇率，否则该条标「不可比」 | `--fx '{"USD":7.15}'`、`--target-currency` |
| 单位 | 同量纲单位**自动换算**到组标准单位（千件↔件、kg↔吨），单价与数量同步换算保证金额守恒；不同量纲（件 vs 套 vs 吨）不合并，标不可比 | 组标准单位自动判定；必要时在条目里填 `match_key` |
| 品项匹配 | 编号/品名+规格+**量纲**精确分组；相似度 ≥0.82 自动合并，0.60~0.82 列「疑似同款」不合并 | `--match-threshold`、手工填 `match_key` |
| 网络价 | **仅参考列**，不参与基准、红绿判定、最低/最高价、价差率与来源数 | `--include-web-in-baseline` |
| 源单标色 | 只改 `*_marked.*` 副本，原件不动；只标上传报价单的偏贵/偏便宜项 | `--suffix` |
| 金额自检 | 与源报价单金额列交叉核对（单价×数量 vs 源单金额），偏差>1% 记入「异常与不可比」 | 自动 |

颜色：`FFFFC7CE`(红底)/`FF9C0006`(红字)、`FFC6EFCE`(绿底)/`FF006100`(绿字)。
红=贵、绿=便宜按用户口径执行，不套用证券涨跌配色。

## 五、红线（必须遵守）

1. **不改原件**：所有标色只作用于复制出来的副本；源文件保持只读。
2. **不硬凑可比**：不同量纲、规格明显不同、币种无汇率、税务口径无法折算、价格缺失 -> 标 `uncomparable` 并写进「异常与不可比」，绝不擅自换算或估算。
3. **单一来源不判绿**：只有 1 条可比报价时标 `no_baseline`，不得因「没有更贵的」而标便宜。
4. **每个价格可回溯**：`source_file` / `source_ref`（知识库文档名或 URL）/ `source_date` 必填其一；网络价不得编造数字或链接，查不到就写「未见公开报价」。
5. **网络价只作参考**：不得把电商零售价、挂牌价直接当成交依据，也不得据此判定供应商「贵」。
6. **疑似同款由 agent 判定**：低相似度条目只列清单，确属同款时在 `quotes.json` 显式写同一个 `match_key` 后重跑，不靠调低阈值硬并。
7. **事实不可编造**：知识库未命中、网络无公开报价时如实标注，不用大模型「常识估价」冒充市场价。
8. **单位换算不得手工造口径**：`unit_multiplier` 只在脚本无法自动判定时使用（如"480元/箱 ÷ 20包"这类商定折算），
   且必须同时换算数量；不得用它把不同量纲强行拉到一起。跨量纲用 `qty_to_price_uom` 显式声明。
9. **金额必须对得上源单**：归一化后各家金额合计应等于源报价单的总金额栏；对不上必须先查清再出结论。
10. **先跑自检再交付**：流水线报告 `run_report.json` 的 `result.blocking` 非空时，
    说明数据自相矛盾，**必须先查清再出结论**，不得直接交付。

## 六、工作流

### 主路径（推荐）：一键流水线，1 次调用 + 1 份报告

`run_pipeline.py` 把「抽取 → 比价 → 标色 → 自检」串成一次 `skill_execute` 调用，
输出机器可读的 `run_report.json`——**只需读这一个文件**，不必再看中间产物。

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/run_pipeline.py <报价单1.xlsx> <报价单2.pdf> --work-dir $WORK --supplier-map '{\"甲公司报价.xlsx\":\"甲公司\",\"乙公司报价.pdf\":\"乙公司\"}'")
```

常用参数：`--quotes <已有 quotes.json>`（跳过抽取）、`--stop-after extract|compare`（分步调试）、
`--no-mark`（不标色）、`--fx '{"USD":7.15}'`、`--tolerance 0.05`、`--include-web-in-baseline`。

跑完看控制台末尾的结论块：

- `✓ 无阻断性问题，可直接交付` -> 可直接交付；
- `⚠ 必须人工处理` -> 数据自相矛盾（金额不符 / 单位量纲冲突），**必须先查清**；
- `ℹ 需知悉` -> 提示性信息（如源单未报价项），不阻塞交付。

也可以直接读 `$WORK/run_report.json`，关键字段：

| 字段 | 含义 |
|---|---|
| `result.auto_ok` | `true` = 无阻断问题 |
| `result.blocking` / `result.notice` | 必须处理项 / 提示项 |
| `result.amount_conservation` | 各家归一化金额 vs 源单总额的逐家守恒校验 |
| `result.per_supplier_total` | 各家可比金额合计 |
| `result.best_source` | 综合最优来源 |
| `quotes.by_supplier` | 每家的报价/未报价数、缺规格项、未报价项清单 |
| `mark` | 标色总数、成功数、副本清单、未标色项 |

### 分步路径（需要中间介入时）

需要人工补充知识库价/网络价、或需要分步排查时走以下步骤。

#### 0. 建工作目录

```bash
WORK=$SKILL_TMP_DIR/quote_compare/<项目名或 YYYYMMDD-HHMM>
mkdir -p "$WORK"
```

#### 1. 抽取上传报价单 -> `quotes.json`

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/extract_quotes.py <报价单1.xlsx> <报价单2.pdf> --out $WORK/quotes.json --supplier-map '{\"甲公司报价.xlsx\":\"甲公司\",\"乙公司报价.pdf\":\"乙公司\"}'")
```

- 支持 `.xlsx/.xlsm`（多 sheet、任意列序表头）、`.csv`、`.docx`、文本型 `.pdf`、`.json`、`.txt`。
- 供应商名优先取 `--supplier-map`，否则取 `--supplier`，否则从文件名推断。
- 每条记录带 `locator`（文件/sheet/行/价格列/价格文本），这是标色步骤回写的依据，**不要丢弃**。
- **PDF 处理顺序**：先看脚本是否已用 `find_tables()` 抽出表格行（数量应等于报价单行数）。
  若控制台提示"文本行兜底抽取"，说明该 PDF 表格未被识别，需改用 `image_read` 工具读表，
  按 `references/data-contract.md` 的 schema 手工写 `manual_quotes.json`（`locator.kind` 填 `pdf`/`image`，
  附 `price_text` 或 `bbox`），再把该 JSON 路径传给 `extract_quotes.py --merge`。
- **未报价行要保留**：源单里"图纸未确认""面议""暂不报价"的行会被抽成 `quote_status=no_quote` 的占位条目，
  参与品项集合但不计价。不要手工删掉，否则两次执行的品项集合会不一致。
- 读完控制台的 `warnings`：缺规格、单位未识别、由总价推算单价等都要在最终摘要里说明。
- **单位交给脚本**：不要手工填 `unit_multiplier` 去"统一"单位。脚本会按量纲自动换算
  （千件↔件、kg↔吨），且单价与数量同步换算以保证金额守恒。
  只有**跨量纲**（如 单价元/箱、数量包）才需在条目里补 `qty_to_price_uom`。

#### 2. 补充知识库价与互联网价

两步互相独立，任一为空都不阻断后续流程，但要在摘要里写明「本次未命中」。

**2a. 知识库历史报价单**（详见 `references/sourcing.md`）

1. 用 `knowledge_base_search` 以「品名 + 关键规格 + 报价/单价」检索知识库，命中后查看
   `documents` 的 `title` / `file_path`；需要按文件名定位时用 `knowledge_file_search` 拿 `file_path`。
2. 命中文档是结构化报价单（xlsx/csv/docx）且 `file_path` 可直接访问时，交给脚本合并：

   ```
   skill_execute(skill="quote-price-comparison",
     command="python3 scripts/extract_quotes.py <file_path> --out $WORK/quotes.json --source-type library --source-ref '<知识库文档名>' --merge")
   ```

3. 只能读到文本片段（如知识库返回段落、价格表条目）时，把读到的「产品-规格-单价-文档名-日期」
   按 `data-contract.md` 的 schema 写成 `library_prices.json`（`source_type=library`，
   `locator={"kind":"none"}`），再 `--merge`：

   ```
   skill_execute(skill="quote-price-comparison",
     command="python3 scripts/extract_quotes.py $WORK/library_prices.json --out $WORK/quotes.json --source-type library --merge")
   ```

   片段里只有「总价」没有「单价」-> 不推算，标「价格不完整」并跳过。知识库条目不参与源单标色。

**2b. 互联网公开价格**

用 `web_search` / `web_fetch` 工具检索（不可用时可用 web-search-pro 技能兜底）。
按 `references/sourcing.md` 的 query 模板与可信来源顺序检索，每个品项尽量取 2~3 个独立来源，
记录 URL、采集日期、价格类型、规格吻合度，写 `web_prices.json`：

```json
{"items":[
  {"supplier":"京东自营(参考)","source_type":"web","source_ref":"https://...","source_date":"2026-11-10",
   "name":"冷轧钢板","spec":"1.0mm*1250*2500","uom":"吨","qty":1,"unit_price":4380,
   "currency":"CNY","tax_included":true,"tax_rate":0.13,"standard_uom":"吨",
   "note":"零售挂牌价，含税，不含运费","locator":{"kind":"none"}}
]}
```

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/extract_quotes.py $WORK/web_prices.json --out $WORK/quotes.json --source-type web --merge")
```

没有可信公开报价时写 `note="未见公开报价"`，**不要填估算价**。

#### 3. 归一化、匹配、判定 -> 横向对比 Excel + 摘要

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/compare_prices.py --quotes $WORK/quotes.json --out-dir $WORK --fx '{\"USD\":7.15}'")
```

产出：`comparison.xlsx`（横向对比 / 明细数据 / 异常与不可比 / 口径与图例）、
`comparison.json`、`marks.json`、`summary.md`。

检查「异常与不可比」sheet 与 `comparison.json.anomalies`：

- 规格明显不同却被合并 -> 在 `quotes.json` 里给条目写不同 `match_key` 后重跑；
- 确属同款但被列「疑似同款」-> 给这些条目写同一个 `match_key` 后重跑；
- 币种无汇率 -> 有可靠汇率时用 `--fx` 重跑，否则保留「不可比」；
- 出现「源单自检：单价×数量 与源单金额列不符」-> 说明该条的数量或单位口径有问题，**先查清再出结论**；
- 出现「组内上传来源采购数量不一致」-> 各家报价的采购量不同，合计按最大数量口径算，需人工确认。

**出结论前必须做的三项自检**（防止同一批报价单跑出不同答案）：
1. 归一化后各来源金额合计 = 源报价单的总金额栏（`comparison.json` 的 `amount_std` 求和）；
2. `横向对比` sheet 的「标准单位」列在各组内唯一且明确；
3. `价差率` 与 `最大价差` 不含网络价（口径与图例 sheet 已写明）。

> 走一键流水线时，这三项已由 `run_pipeline.py` 自动完成并写入 `run_report.json`，无需重复手工核对。

#### 4. 源报价单副本标红 / 标绿

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/mark_sources.py --marks $WORK/marks.json --out-dir $WORK")
```

产出每份上传报价单的 `*_marked.xlsx` / `*_marked.docx` / `*_marked.pdf` / `*_marked.png`，
价格单元格填充红/绿并附批注（写明基准价、偏差、口径）。
`csv` 会转成带色 `*_marked.xlsx`。看 `unmarked.md` 与 `mark_report.json` 确认没有漏标；
`mark_report.json.stale_outputs` 会列出本次未更新的历史副本，重跑同一工作目录时先清理，避免旧副本被误当成新结果。

#### 5. 交付

用 `cp` 工具把 `comparison.xlsx`、`summary.md`、全部 `*_marked.*` 副本注册下载链接交付用户
（`summary.md` 可作为附件交付或摘取要点写入回复）。
聊天回复只给简短结论（8~12 行）：综合最优来源与节约额、偏贵/偏便宜处数、价差最大品项、
口径风险（不可比条数、网络价仅供参考）、交付文件下载链接。详细表格一律放 Excel，不粘贴大段正文。

### 效率约定（减少模型往返）

1. **优先一键流水线**，不要手动分步调脚本：分步调要 3 次工具调用 + 读多个中间文件，
   流水线只要 1 次调用 + 读 1 个 `run_report.json`。需要补知识库价/网络价时，
   先 `--stop-after extract` 抽取，补价合并后再重跑完整流水线。
2. **先读 `run_report.json` 再决定要不要深挖**：报告已含结论、风险、自检结果；
   只有当 `result.blocking` 非空或需要引用具体行时，才去读 Excel / `comparison.json`。
3. **不要重复跑**：先用 `--stop-after extract` 看清抽取质量（有无未识别表头、缺规格），
   确认无误再跑完整流程，避免因抽取问题反复重跑全流程。
4. **不要手工改 `quotes.json` 里的单位字段**：改 `qty_uom` 就必须同步改 `qty`，
   否则脚本会报「数量单位与数量值不同步」。脚本会自动换算，无需人工干预。

## 七、脚本一览

| 脚本 | 职责 |
|---|---|
| `scripts/run_pipeline.py` | **★ 一键流水线**：抽取 -> 比价 -> 标色 -> 自检，产出 `run_report.json`（推荐入口） |
| `scripts/qc_common.py` | 共享：文本/数字/单位归一化、单位量纲表、相似度、配色常量、IO |
| `scripts/extract_quotes.py` | 任意格式报价单 -> 统一 `quotes.json` + 标色定位 |
| `scripts/compare_prices.py` | 归一化 -> 匹配 -> 基准 -> 红绿判定 -> Excel / 摘要 / 标色指令 |
| `scripts/mark_sources.py` | 按 `marks.json` 给源单副本上色，输出标色报告 |
| `tests/test_units.py` | 单位换算回归测试（金额守恒、跨量纲、幂等性），改动换算逻辑后必跑 |
| `references/data-contract.md` | JSON schema、归一化/匹配/判定规则、手工构造示例 |
| `references/sourcing.md` | 知识库检索与联网比价操作细则、来源留痕与可信度 |

依赖：`openpyxl`（必需）；`python-docx`（docx）、`pymupdf`（pdf）、`pillow`（图片）按需。

## 八、常见故障

| 现象 | 处理 |
|---|---|
| 未识别到表头，0 条 | 检查表头是否合并单元格/跨行；按 schema 手工写 JSON 后 `--merge` |
| 品项被拆成多组 | 同名不同写法 -> 在 `quotes.json` 统一 `match_key`；不要调低阈值硬并 |
| 全部「不可比」 | 多半是单位/币种/税务口径不一致；同量纲单位已自动换算，跨量纲补 `qty_to_price_uom`、币种补 `--fx` |
| 全部「无可比基准」 | 只有 1 家上传且知识库无命中；补知识库或网络参考价（网络价仅参考） |
| 标色 0 处 | 检查 `locator.file` 是否绝对路径、`row/price_col` 是否正确；看 `unmarked.md` |
| PDF 报价单被"文本行兜底"解析 | 控制台出现该提示即说明表格识别失败，条目价格会是碎片；改用 `image_read` 读表后手工 JSON `--merge` |
| 源单里的"未报价"行不见了 | "面议""暂不报价"的行保留为 `quote_status=no_quote`，不要手工删除 |
| 「金额」列与「单价×数量」对不上 | 多为数量单位抄错，脚本记「源单自检」异常，须回源单核对后再出结论 |
| 脚本报缺依赖 | 确认容器镜像包含 requirements.txt（openpyxl/python-docx/PyMuPDF/Pillow） |
