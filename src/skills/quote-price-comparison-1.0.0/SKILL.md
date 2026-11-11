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
  version: "1.0.0"
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
  （quotes.json、comparison.xlsx、marks.json 等）**必须用绝对路径**读写，统一放在
  `$SKILL_TMP_DIR` 下的任务目录中（环境变量已注入，持久不清理）：

  ```bash
  WORK=$SKILL_TMP_DIR/quote_compare/<项目名或 YYYYMMDD-HHMM>
  mkdir -p "$WORK"
  ```

- 技能目录运行时为只读；`scripts/` 下的脚本通过 `skill_execute` 调用，脚本路径会被自动映射为绝对路径。
- 用户上传的文件路径来自会话上传信息（冒号后面是实际上传路径），**始终使用实际上传路径**，不要用原始文件名拼路径。
- 依赖（openpyxl、python-docx、PyMuPDF、Pillow）已在 requirements.txt 中，容器内开箱可用；
  `.xls` 先另存为 `.xlsx`；扫描版 PDF 与图片报价单走多模态读取（`image_read` 工具）+ 手工 JSON。

## 三、默认口径（已生效，可单点调整，不阻塞开工）

| 项 | 默认取值 | 调整方式 |
|---|---|---|
| 基准价 | `upload` + `library` 归一化单价的**中位数** | `--baseline min\|mean\|median\|web\|library`、`--baseline-sources` |
| 红绿容差 | **±5%**；> +5% 偏贵(红)，< -5% 偏便宜(绿)，区间内不标色 | `--tolerance` |
| 税务口径 | 统一折算为**含税**单价（未税价 ×(1+税率)） | `--tax-basis incl\|excl\|asis`、`--default-tax-rate` |
| 币种 | 统一 CNY；跨币种必须显式给汇率，否则该条标「不可比」 | `--fx '{"USD":7.15}'`、`--target-currency` |
| 单位 | 只做别名归一（个/件/pcs->件 等）；不同量纲不隐式换算 | 在条目里显式填 `unit_multiplier` |
| 品项匹配 | 编号/品名+规格+单位精确分组；相似度 ≥0.82 自动合并，0.60~0.82 列「疑似同款」不合并 | `--match-threshold`、手工填 `match_key` |
| 网络价 | **仅参考列，默认不参与基准与红绿判定**（零售/挂牌价 ≠ 采购成交价） | `--include-web-in-baseline` |
| 源单标色 | 只改 `*_marked.*` 副本，原件不动；只标上传报价单的偏贵/偏便宜项 | `--suffix` |

颜色：`FFFFC7CE`(红底)/`FF9C0006`(红字)、`FFC6EFCE`(绿底)/`FF006100`(绿字)。
红=贵、绿=便宜按用户口径执行，不套用证券涨跌配色。

## 四、红线（必须遵守）

1. **不改原件**：所有标色只作用于复制出来的副本；源文件保持只读。
2. **不硬凑可比**：单位不同量纲、规格明显不同、币种无汇率、税务口径无法折算、价格缺失 -> 标 `uncomparable` 并写进「异常与不可比」，绝不擅自换算或估算。
3. **单一来源不判绿**：只有 1 条可比报价时标 `no_baseline`，不得因「没有更贵的」而标便宜。
4. **每个价格可回溯**：`source_file` / `source_ref`（知识库文档名或 URL）/ `source_date` 必填其一；网络价不得编造数字或链接，查不到就写「未见公开报价」。
5. **网络价只作参考**：不得把电商零售价、挂牌价直接当成交依据，也不得据此判定供应商「贵」。
6. **疑似同款由 agent 判定**：低相似度条目只列清单，确属同款时在 `quotes.json` 显式写同一个 `match_key` 后重跑，不靠调低阈值硬并。
7. **事实不可编造**：知识库未命中、网络无公开报价时如实标注，不用大模型「常识估价」冒充市场价。

## 五、工作流

### 0. 建工作目录

```bash
WORK=$SKILL_TMP_DIR/quote_compare/<项目名或 YYYYMMDD-HHMM>
mkdir -p "$WORK"
```

### 1. 抽取上传报价单 -> `quotes.json`

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/extract_quotes.py <报价单1.xlsx> <报价单2.pdf> --out $WORK/quotes.json --supplier-map '{\"甲公司报价.xlsx\":\"甲公司\",\"乙公司报价.pdf\":\"乙公司\"}'")
```

- 支持 `.xlsx/.xlsm`（多 sheet、任意列序表头）、`.csv`、`.docx`、文本型 `.pdf`、`.json`、`.txt`。
- 供应商名优先取 `--supplier-map`，否则取 `--supplier`，否则从文件名推断。
- 每条记录带 `locator`（文件/sheet/行/价格列/价格文本），这是第 4 步回写标色的依据，**不要丢弃**。
- **扫描版 PDF / 图片报价单**：脚本无法解析，用 `image_read` 工具读出表格内容，按
  `references/data-contract.md` 的 schema 手工写 `manual_quotes.json`（`locator.kind` 填 `pdf`/`image`，
  附 `price_text` 或 `bbox`），再把该 JSON 路径传给 `extract_quotes.py --merge`。
- 读完控制台的 `warnings`：缺规格、单位未识别、由总价推算单价等都要在最终摘要里说明。

### 2. 补充知识库价与互联网价

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

### 3. 归一化、匹配、判定 -> 横向对比 Excel + 摘要

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/compare_prices.py --quotes $WORK/quotes.json --out-dir $WORK --fx '{\"USD\":7.15}'")
```

产出：`comparison.xlsx`（横向对比 / 明细数据 / 异常与不可比 / 口径与图例）、
`comparison.json`、`marks.json`、`summary.md`。

检查「异常与不可比」sheet 与 `comparison.json.anomalies`：

- 规格明显不同却被合并 -> 在 `quotes.json` 里给条目写不同 `match_key` 后重跑；
- 确属同款但被列「疑似同款」-> 给这些条目写同一个 `match_key` 后重跑；
- 币种无汇率 -> 有可靠汇率时用 `--fx` 重跑，否则保留「不可比」。

### 4. 源报价单副本标红 / 标绿

```
skill_execute(skill="quote-price-comparison",
  command="python3 scripts/mark_sources.py --marks $WORK/marks.json --out-dir $WORK")
```

产出每份上传报价单的 `*_marked.xlsx` / `*_marked.docx` / `*_marked.pdf` / `*_marked.png`，
价格单元格填充红/绿并附批注（写明基准价、偏差、口径）。
`csv` 会转成带色 `*_marked.xlsx`。看 `unmarked.md` 与 `mark_report.json` 确认没有漏标；
`mark_report.json.stale_outputs` 会列出本次未更新的历史副本，重跑同一工作目录时先清理，避免旧副本被误当成新结果。

### 5. 交付

用 `cp` 工具把 `comparison.xlsx`、`summary.md`、全部 `*_marked.*` 副本注册下载链接交付用户
（`summary.md` 可作为附件交付或摘取要点写入回复）。
聊天回复只给简短结论（8~12 行）：综合最优来源与节约额、偏贵/偏便宜处数、价差最大品项、
口径风险（不可比条数、网络价仅供参考）、交付文件下载链接。详细表格一律放 Excel，不粘贴大段正文。

## 六、脚本一览

| 脚本 | 职责 |
|---|---|
| `scripts/qc_common.py` | 共享：文本/数字/单位归一化、相似度、配色常量、IO |
| `scripts/extract_quotes.py` | 任意格式报价单 -> 统一 `quotes.json` + 标色定位 |
| `scripts/compare_prices.py` | 归一化 -> 匹配 -> 基准 -> 红绿判定 -> Excel / 摘要 / 标色指令 |
| `scripts/mark_sources.py` | 按 `marks.json` 给源单副本上色，输出标色报告 |
| `references/data-contract.md` | JSON schema、归一化/匹配/判定规则、手工构造示例 |
| `references/sourcing.md` | 知识库检索与联网比价操作细则、来源留痕与可信度 |

## 七、常见故障

| 现象 | 处理 |
|---|---|
| 未识别到表头，0 条 | 检查表头是否合并单元格/跨行；按 schema 手工写 JSON 后 `--merge` |
| 品项被拆成多组 | 同名不同写法 -> 在 `quotes.json` 统一 `match_key`；不要调低阈值硬并 |
| 全部「不可比」 | 多半是单位/币种/税务口径不一致，补齐 `standard_uom`、`unit_multiplier`、`--fx` |
| 全部「无可比基准」 | 只有 1 家上传且知识库无命中；补知识库或网络参考价（网络价仅参考） |
| 标色 0 处 | 检查 `locator.file` 是否绝对路径、`row/price_col` 是否正确；看 `unmarked.md` |
| 脚本报缺依赖 | 确认容器镜像包含 requirements.txt（openpyxl/python-docx/PyMuPDF/Pillow） |
