---
name: excel-layout-clone
description: >
  Excel 模板复刻技能。根据 Excel 模板（.xlsx/.xlsm/.xltx/.xltm）和新数据/需求生成新的
  Excel 文件，保留多个 Sheet、计算公式、合并单元格、表格、数据验证、条件格式、图表、批注、
  定义名称、打印设置、宏等元素，只改变填写内容和数据行数。用户说「按这个 Excel 模板生成
  新的」「保持模板样式换个数据」「照着这个表做一份新的」「把清单行数换成这次的」时使用。
  若用户要求重新设计样式，应转向表格创建/美化流程（excel_process 工具）。
metadata:
  version: "1.1.0"
  author: aid-work-agent
dependencies:
  - lxml
requires_tools:
  - cp
---

# Excel 模板复刻技能 Excel Layout Clone

## 何时使用

用户说「按这个 Excel 模板生成一份新的」「保持模板样式换个数据」「照着这个表做一份新的」等。**只改填写内容与数据行数、样式与结构原样继承**是核心特征；若用户要求重新设计样式（美化、换配色、重排布局），不要使用本技能，改用 `excel_process` 工具按新要求生成。

**不要使用本技能**：

- 把**多个来源的脏 Excel 报表**合并填写到一张标准模板 -> 用 `excel-to-template` 技能（多来源抽取 + 校验 + 汇总场景）。
- 复刻 **Word(.docx) / PDF** 样版生成新 Word 文档 -> 用 `doc-layout-clone` 技能。
- 需要读取/分析 Excel 数据做统计、修改样式 -> 用 `excel_process` 工具。

本技能与上述技能的关键区别：模板就是用户提供的真实文件，本技能对 xlsx 包内 XML 做**手术式修改**，未触及的部件（样式、主题、媒体、VBA 宏、数据透视、切片器、形状、批注）**按字节保留**——这是 openpyxl 全量重写做不到的。

## 铁律：工作流固定，技能不随模板改变

本技能的工作流**永远是下面 4 步**，与模板属于什么行业、有几个 Sheet、是什么内容完全无关：

1. **选定模板并预处理**：扫描模板 -> 选定 -> 结构转储 -> 提取锚点/候选表 -> 检查缓存，一次调用完成。
2. **运行时分析模板**：判断新要求下哪些单元格要改、清单表是哪几行、哪些值可推导，写成 `analysis.json` 并缓存。**字段清单只能由本次读模板得出。**
3. **单轮问答**一次性收集所有差异内容，写入答案（或通过 stdin 直接给第 4 步）。
4. **一次回填 + 报告验收**：生成 `output.xlsx`，用报告确认元素保真、定位命中、公式与行数正确。

以下做法一律禁止：

- 在 `SKILL.md`、`references` 或脚本里，为某类表格写死字段清单、问答话术、关键词映射或固定值。
- 在模板里插入占位符，或以任何方式修改模板原件。模板就是用户提供的真实文件。
- 把「需要改什么」预先登记成配置；它只能在第 2 步运行时读模板分析得出。
- 凭文件名猜测字段含义；把模板/历史文件里的示例值当作本次应当沿用的权威数据。
- 用「重新生成一个 xlsx」的方式回填（例如从零写入样式，或用 `excel_process` 重写）。本技能只对模板包内 XML 做手术式修改，**未触及的部件按字节保留**。
- 擅自「美化」样式（改字体、改颜色、改列宽、重排 Sheet）。用户没要求时，样式必须原样继承。

**新增模板 = 用户上传新的 `.xlsx` / `.xlsm` / `.xltx` / `.xltm`，直接作为 `prep.py --templates` 参数传入，技能一个字都不用改。** `.xls` 老格式先让用户另存为 `.xlsx`。

## 工作目录（缓存都放 $SKILL_TMP_DIR，技能目录只读）

每次 `skill_execute` 调用都在独立的临时工作目录中执行，调用间不共享 cwd。所有中间产物
**必须用绝对路径**读写，统一放在 `$SKILL_TMP_DIR` 下的任务目录中（环境变量已注入，持久不清理）：

```bash
WORK=$SKILL_TMP_DIR/excel_layout/<任务名或 YYYYMMDD-HHMM>
mkdir -p "$WORK"
```

| 文件 | 产生步骤 | 内容 |
|------|----------|------|
| `template_dump.txt` | 1（`prep.py`） | 模板结构化转储（落盘，**不必读回全文**） |
| `analysis.json` | 2 或缓存命中 | **分析缓存**：改哪些项 + 填在哪里 + 清单表结构 |
| `answers.json` | 3/4（stdin 自动落盘） | **答案缓存**：本次新要求下的实际内容 |
| `output.xlsx` / `fill_report.json` | 4 | 成品 / **含保真验收结论**的报告 |
| `$SKILL_TMP_DIR/excel_layout_cache/<hash>.analysis.json` | 4 自动 | 按源模板 hash 的跨任务复用缓存 |

## 性能准则：最少往返（务必遵守）

脚本都是毫秒级，耗时几乎全部来自每一次 `skill_execute` 调用对应的大模型推理。目标是**用尽量少的往返完成表格**，而不是优化脚本速度。

| 准则 | 做法 |
|------|------|
| **合并机械步骤** | 第 1+2 步用 `prep.py` 一次调用搞定，不要分别跑扫描、转储、读取脚本源码 |
| **只读摘要** | `compact_dump` + `anchors` + `sheets[].candidate_tables` 已足够写 `analysis.json`；**不要**把整份 dump、脚本源码读进上下文 |
| **复用分析结果** | `analysis_cache.hit=true` 时 `analysis.json` 已就位，**直接跳到第 3 步**，不读 dump、不重写 analysis |
| **一次问全** | 第 3 步把所有待填项 + 建议默认值列成一张清单**一轮问完**，不要 1–3 项分多轮 |
| **一次回填 + 看报告** | 第 4 步用 stdin 传 answers，一次调用完成「写答案 + 生成 + 验收」；只看 `summary` / `verification` / `layout`，**不要再打开 xlsx 读回核对** |
| **默认填满** | 日期、编号、合计、固定条款能推断的一律 `default` / `derived` / Excel 公式，不向用户提问 |
| **并行无依赖调用** | 互不依赖的调用放在同一条消息里并行发起 |

标准往返预算：**同模板复用约 3–4 次**（prep -> 问答 -> 回填 -> 交付）；**首次约 5–6 次**（prep -> 写 analysis -> 问答 -> 回填 -> 交付）。

## 第 1+2 步：一次编排（选模板 + 转储 + 缓存检查）

```
python3 <技能目录>/scripts/prep.py --work "$WORK" --templates <模板实际路径（单个文件或目录）> \
    [--template <选定的文件名或绝对路径>] [--cache-dir "$SKILL_TMP_DIR/excel_layout_cache"] \
    [--max-lines 500] [--no-compact]
```

- 模板来自**会话上传信息**（冒号后面是实际上传路径），**始终使用实际上传路径**，不要用原始文件名拼路径。
- 单份模板自动选定；多份时 `prep.py` 先返回精简目录，选定后再带 `--template` 调一次。

`prep.py` 一次调用完成：扫描模板（`.xlsx/.xlsm/.xltx/.xltm`）-> 选定 -> 结构转储落盘 -> 计算**源模板** SHA256 -> 检查缓存。stdout **只输出精简 JSON 摘要**：

- `catalog`：模板清单（文件名 / 格式 / Sheet 名 / 大小 / hash）
- `selected`：`file` / `path` / `format` / **`hash`**（第 4 步 `--cache-key` 要用它）
- `working_template`：实际用于回填的模板路径（就是原模板，不复制、不修改）
- `compact_dump`：紧凑坐标内容，格式 `S[Sheet名] 单元格|值或公式`，另有 `MERGE` / `TABLE` / `DV` / `CF` / `CANDIDATE` 元素行
- `anchors`：疑似待填单元格（日期/金额/编号/标签/占位符等），`locators` 的 `text` 从这里**逐字抄**，无需再跑脚本核对
- `sheets[]`：每个 Sheet 的尺寸、公式数、合并、表格、图表、数据验证、条件格式、冻结、打印区域，以及 `candidate_tables`（**候选清单表：表头行 / 样例行 / 合计行 / 各列样例与公式模式**）
- `layout_signature`：模板的元素指纹，用于验收对比
- `analysis_cache.hit`：命中时 `analysis.json` 已就位，**直接跳到第 3 步**

### 缓存复用规则

- 缓存放在 `$SKILL_TMP_DIR/excel_layout_cache/`（按源模板内容 hash 命名），**不写进技能包**，也不写用户上传目录。
- 第 4 步生成成功（`ok=true`）时 `fill_xlsx.py` 自动入缓存；自检失败**不会**入缓存。
- 源模板内容一变 hash 就变，缓存自动失效。
- 命中缓存仍要按本次新要求核对 `answers.json`，但**字段清单与定位逻辑无需重做**。

### 分析模板（仅 `analysis_cache.hit=false` 时执行）

依据 `prep.py` 输出的摘要判断：

- **新要求下要变的内容**：相对本次需求的主体名称、编号、日期、金额/数值、客户、负责人、结论、明细行……
- **固定内容保留原值，不要拿去问用户**：模板自带的固定条款、表头、页眉页脚、通用说明、格式性空白。
- **历史文件玩法**：把历史文件当成「已填好的样例」——只把「新旧要求之间会变的值」列为字段；旧值只是**定位锚点**，不是本次答案。
- **模板/历史文件里的示例数值不是权威数据**：用户本次提供的资料优先。
- **坐标约定**：`analysis.json` 里所有 Sheet / 行号 / 单元格坐标都是**模板原始坐标**。动态增减行后由引擎统一映射，不要自己推算新行号。
- **一张 Sheet 只声明一个 `list_table`**；确需多张时说明第 2 张起按扩展后的坐标解释。
- 派生关系（合计、大写、比例）写成 `derived` / Excel 公式，**不向用户提问**。

结论写成 `$WORK/analysis.json`，格式见 `references/analysis_schema.md`。硬性要求：

- `locators[].ref`/`label`/`text` 必须**逐字来自 dump / anchors**，不得臆造。
- `sample_rows` 必须是**连续行号**；`columns[].col` 用 `B`、`C` 这样的列字母。
- 标好 `required`；能算出的写 `default` / `derived` / `formula`。
- 清单表用 `list_tables` 声明，指出表头行、`sample_rows`、`footer_rows`、列映射、行内公式与合计公式。
- 写好后，用一两句话向用户复述「本次需要收集哪几类信息」，再进入第 3 步。

## 第 3 步：一次问全（单轮）

**把所有待改项连同建议默认值列成一张清单，一轮问完**；用户一次回复后直接进入第 4 步。仅当答案本身有歧义/缺失时才补问。

- 已有答案、`default`、`derived`、Excel 公式的字段不重复问。
- 模板中的固定内容不问；只有用户主动声明「抬头/主体要换成我方」时才整体替换（仍保留样式）。
- 清单项让用户逐条给出，条数不限（可多于、少于模板样例行，也可为 0），每条至少给出用于定位的名称/关键字段。
- 未提供的非必填字段保持模板原值不动。

## 第 4 步：一次回填 + 报告验收

把答案通过 stdin 一次传入（`-`，脚本自动落盘 `answers.json`）：

```
python3 <技能目录>/scripts/fill_xlsx.py <prep输出的working_template> "$WORK/analysis.json" - \
    "$WORK/output.xlsx" \
    --report "$WORK/fill_report.json" \
    --cache-dir "$SKILL_TMP_DIR/excel_layout_cache" --cache-key <prep输出的selected.hash>
```

**只看回填报告即可复核，不要再打开 xlsx 读回核对。** 报告关键字段：

- `summary`：一行结论（ok / 字段 / 清单项 / 未命中定位 / 板式一致 / 警告）
- `verification.fields`：每个字段的最终回填值；`verification.preview`：成品各 Sheet 前几行内容
- `tables`：各清单表扩展前后的行数与实际条数；`total_cells` / `cell_updates`：汇总单元格写入值
- `layout`：`identical` 与 `differences` / `expected_changes`（清单行数变化与公式数变化属预期，不计入漂移）
- `changed_parts`：实际被改动的包内部件（不应出现 `xl/styles.xml` / `xl/media/` / `vbaProject.bin` 等）
- `warnings`：未命中定位、公式失败、透视/切片器提示等
- `analysis_cached`：本次是否按模板 hash 入缓存

若 `ok=false`，按 `missing_required` / `unmatched_locators` / `layout.differences` / `warnings` 回到第 2 或第 3 步修正**缓存**（不要改模板），重跑第 4 步。

## 交付

验收通过（`ok=true`）后，用 `cp` 工具把 `$WORK/output.xlsx` 复制为用户可见的交付文件
（目标文件名按本次任务语义命名，如 `某某公司报价单.xlsx`），再向用户报告：
已改关键内容、清单行数、元素是否保留，并提醒：**公式缓存值已清空，用 Excel/WPS 打开时会自动重算**；若后续要用 pandas 读取公式结果，先用 Excel 打开保存一次。
**不要**交付 `analysis.json` / `answers.json` / `fill_report.json` 等中间产物。

---

## 保真能力与边界

- **按字节保留**：样式、主题、共享字符串、媒体、VBA 宏、数据透视、切片器、形状、批注等未被触及的部件。
- **随行号自动平移**：单元格引用、合并区域、条件格式、数据验证、超链接、表格范围、自动筛选、定义名称（含打印区域）、图表数据源、批注位置。
- **公式重建**：写入公式时清除旧缓存值，并设置 `fullCalcOnLoad`，由 Excel/WPS 打开时重算。
- **透视表**：数据透视缓存的 `worksheetSource` 区域会随明细行同步扩展/收缩，并设置 `refreshOnLoad=1`，由 Excel 打开时重建缓存；切片器连接保留，其选项随透视刷新更新。
- **边界**：透视缓存的「记录与共享项」不在生成阶段重建（由 Excel 打开时完成）；若用不刷新缓存的程序读取，可能仍看到旧项。VBA 宏按字节保留、不重编译。富文本单元格写回后保留单元格样式，但单元格内多段富文本会合并为纯文本（报告会提示）。

---

## 关键文件

| 路径 | 作用 |
|------|------|
| `scripts/prep.py` | **第 1+2 步编排（首选）**：扫描 + 选模板 + 转储 + 锚点 + 候选表 + 缓存检查 |
| `scripts/scan_templates.py` | 扫描 `.xlsx/.xlsm/.xltx/.xltm` 模板目录 |
| `scripts/dump_template.py` | 只读转储模板结构、元素清单与版式指纹（一般经 prep.py 调用） |
| `scripts/xlsx_engine.py` | XLSX 包内 XML 手术引擎：单元格写入、动态增减行、引用平移、指纹、保存 |
| `scripts/fill_xlsx.py` | 第 4 步：stdin 回填 + 动态行 + 验收 + 自动入缓存 |
| `references/analysis_schema.md` | `analysis.json` / `answers.json` / `fill_report.json` 完整格式与示例 |
| `$SKILL_TMP_DIR/excel_layout_cache/` | 运行时缓存（**不在技能包内**），按源模板 hash 存 `analysis.json` |
