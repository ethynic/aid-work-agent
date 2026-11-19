---
name: doc-layout-clone
description: >
  样版板式复刻技能。根据任意 Word(.docx) 或 PDF(.pdf) 样版文件、模板文件或历史文件，
  生成「板式与样版一致、内容按新要求改写」的新 Word 文档。适用于照着样板/历史文件产出
  报告、函件、协议、投标文档、台账说明、表格清单等；支持按依赖关系自动分轮问答（复杂
  场景不必一次问全）、字段多时生成信息采集表让用户离线填写、清单行动态增减、PDF 转可
  编辑 Word 并校验文本覆盖率与生成后板式指纹。用户说「按这个模板/样版/历史文件生成一份
  新的 Word」「保持同样板式换个内容」「照着这个 PDF 做一份 Word」时使用。
metadata:
  version: "1.3.0"
  author: aid-work-agent
dependencies:
  - python-docx
  - pdf2docx
  - PyMuPDF
requires_tools:
  - cp
---

# 样版板式复刻技能 Doc Layout Clone

## 铁律：工作流固定，技能不随样版改变

本技能的工作流**永远是下面 4 步**，与样版是 Word 还是 PDF、属于什么行业、内容是合同还是报告完全无关：

1. **选定样版并预处理**（PDF 转可编辑 DOCX；DOCX 直用），一次调用完成转储与缓存检查。
2. **运行时分析样版**：判断新要求下哪些内容要改、每项改在哪里，写成 `analysis.json` 并缓存。**字段清单只能由本次读样版得出。**
3. **收集信息**：字段少就在对话里一次问全；字段多或需用户提供结构化数据（财务/人员/知产/市场等）时，生成一份**黄色高亮标注示例值**的 Word《信息采集表》让用户填写（用户也可回"先用示例数据"直接沿用示例）。
4. **一次回填 + 报告验收**：生成 `output.docx`，用报告确认板式一致、定位命中。

以下做法一律禁止：

- 在 `SKILL.md`、references 或脚本里，为某类文档写死字段清单、问答话术、关键词映射或固定值。
- 在样版里插入占位符，或以任何方式修改样版原件。样版就是用户提供的真实文件。
- 把「需要改什么」预先登记成配置；它只能在第 2 步运行时读样版分析得出。
- 凭文件名猜测字段含义；把样版/历史文件里的示例值当作本次应当沿用的权威数据。
- **把本次业务内容写进脚本**。用户给的数据只能落在 `answers.json`；`analysis.json` 只描述「改哪里」。
- 擅自「美化」版式（改字体、改颜色、重排段落）。用户没要求时，版式必须原样继承。

**新增样版 = 用户上传新的 `.docx` / `.pdf`，技能一个字都不用改。**

### 不得把一次任务变成「专用生成器」

复用同一份样版做第二个客户时，正确做法是**重新跑第 3 步问答 + 第 4 步回填**（分析缓存命中可省掉第 2 步），而不是改上一次的脚本里的字符串。任何以 `build_*.py` / `gen_*.py` 之类命名的、把客户名、金额、政策名、条款文字硬编码在 Python 里的脚本，都只属于那一次任务；**它们不属于本技能，也不得被当作本技能产出的一部分留下**。若某次任务确实需要一段可复用的逻辑，它只能进化为 `scripts/` 下的通用脚本，且不得包含任何业务常量。

## 工作目录（缓存都放 $SKILL_TMP_DIR，技能目录只读）

每次 `skill_execute` 调用都在独立的临时工作目录中执行，调用间不共享 cwd。所有中间产物
**必须用绝对路径**读写，统一放在 `$SKILL_TMP_DIR` 下的任务目录中（环境变量已注入，持久不清理）：

```bash
WORK=$SKILL_TMP_DIR/doc_layout/<任务名或 YYYYMMDD-HHMM>
mkdir -p "$WORK"
```

| 文件 | 产生步骤 | 内容 |
|------|----------|------|
| `template_dump.txt` | 1（`prep.py`） | 样版结构化转储（落盘，**不必读回全文**） |
| `_converted/*.docx` | 1（仅 PDF） | PDF 转换出的可编辑 Word 基底（带 `.hash` 复用标记） |
| `analysis.json` | 2 或缓存命中 | **分析缓存**：需要改哪些项 + 每项填在哪里 + 清单表结构 |
| `answers.json` | 3/4（stdin 自动落盘；`probe.py --merge` 也会写入） | **答案缓存**：本次新要求下的实际内容 |
| `fields.json` / `信息采集表.docx` | 3（仅信息采集表分支） | 采集表字段定义 / 交给用户填写的采集表 |
| `output.docx` / `fill_report.json` | 4 | 成品 / **含板式验收结论**的报告 |
| `$SKILL_TMP_DIR/doc_layout_cache/<hash>.analysis.json` | 4 自动 | 按源样版 hash 的跨任务复用缓存 |

## 何时使用

用户说「按这个模板生成一份新的 Word」「照着这份历史文件改成这次的内容」「保持同样板式把 PDF 做成 Word」「用这个样版做一份 XX 报告」等。**只改内容、不改板式**是核心特征；若用户要求重新设计版式（重排版、换风格），不要使用本技能，直接按新要求撰写并生成文档即可。

**不要使用本技能**：
- 把数据填入既有 **Excel** 模板 → 用 `excel-to-template` 技能。
- 从招标文件出发生成整套投标响应文档 → 用 `bid-doc-generator` 技能（如需套用历史标书版式，本技能可在其流程内使用）。
- 生成合同文本 → 用 `contract-generator` 技能。

---

## 性能准则：最少往返（务必遵守）

脚本都是毫秒级（PDF 转换除外），耗时几乎全部来自每一次 `skill_execute` 调用对应的大模型推理。目标是**用尽量少的工具往返完成文档**，而不是优化脚本速度。

| 准则 | 做法 |
|------|------|
| **合并机械步骤** | 第 1+2 步用 `prep.py` 一次调用搞定，不要分别跑 scan / dump / 转换 / 读脚本源码 |
| **只读摘要，不读全文** | `prep.py` 的 `compact_dump` + `anchors` + `tables_schema` 已足够写 `analysis.json`；**不要**把整份 dump、脚本源码读进上下文 |
| **复用分析结果** | `analysis_cache.hit=true` 时 `analysis.json` 已就位，**直接跳过第 2 步**，不读 dump、不重写 analysis |
| **一次问全** | 第 3 步把**当前已解锁**的待改项 + 建议默认值列成一张清单问完；只有存在 `depends_on` 依赖时才分轮，不要把一个能一轮问完的文档拆成多轮 |
| **一次回填 + 报告验收** | 第 4 步用 stdin 传 answers，一次调用完成「写答案 + 生成 + 板式验收」；看 `summary` / `verification` / `layout` 即可，**不要再打开 docx 读回核对** |
| **默认填满** | 能推断的（日期、编号、合计、固定条款）一律 `default` / `formula`，不向用户提问 |
| **并行无依赖调用** | 互不依赖的调用放在同一条消息里并行发起 |

标准往返预算：**同样版复用约 3–4 次**（prep → 问答 → 回填 → 交付）；**首次约 5–6 次**（prep → 写 analysis → 问答 → 回填 → 交付）。存在依赖的复杂文档，每多一轮问答 +1 次（用 `probe.py`，不要人工数依赖）。

## 第 1+2 步：一次编排（选样版 + 转换 + 转储 + 缓存检查）

```
python3 <技能目录>/scripts/prep.py --work "$WORK" --templates <样版实际路径（单个文件或目录）> \
    [--template <选定的文件名或绝对路径>] [--pages 1-3] \
    [--cache-dir "$SKILL_TMP_DIR/doc_layout_cache"] [--no-compact]
```

- 样版来自**会话上传信息**（冒号后面是实际上传路径），**始终使用实际上传路径**，不要用原始文件名拼路径。
- 单份样版自动选定；多份时 `prep.py` 先返回精简目录，选定后再调用并传 `--template`。

`prep.py` 一次调用完成：扫描样版（`.docx` / `.pdf`）→ 选定样版 → PDF 转可编辑 DOCX / DOCX 直用 → dump 落盘 → 计算**源样版** SHA256 → 检查 `analysis.json` 缓存。stdout **只输出精简 JSON 摘要**：

- `catalog`：样版清单（文件名 / 格式 / 标题 / 段落或页数 / 摘要）
- `selected`：`file` / `path` / `format` / **`hash`**（第 4 步 `--cache-key` 要用它）
- `working_template`：实际用于回填的 docx 路径（PDF 时为转换产物）
- `conversion`：PDF 转换报告（`converter` / `pdf_pages` / **`coverage` 文本覆盖率** / `warnings`）
- `layout_fidelity`：`exact-docx`（原 Word 直用，板式为 1:1）或 `converted-from-pdf`（PDF 转换，接近但不保证像素级）
- `compact_dump`：紧凑坐标全文（`P003|原文` / `T0 R01 C02|原文`）
- `anchors`：候选待改位置的**精确 repr**，`locators` 的 `text` 直接从这里逐字抄，无需再跑脚本核对
- `tables_schema`：每张表的行列数与前几行内容，供 `list_tables` 声明
- `layout_signature`：样版板式指纹（页面/边距/样式分布/表结构），用于验收对比
- `analysis_cache.hit`：命中时 `analysis.json` 已就位，**直接跳到第 3 步**
- `next_action`：明确下一步做什么

### 缓存复用规则

- 缓存放在 `$SKILL_TMP_DIR/doc_layout_cache/`（按源样版内容 hash 命名），**不写进技能包**，也不写用户上传目录。
- 第 4 步生成成功（`ok=true`）时 `fill_doc.py` 自动入缓存。调用时传 `--cache-key <prep 输出的 selected.hash>`，使 PDF 场景也能正确命中；不传时按工作 docx 自身 hash 缓存。
- 源样版内容一变 hash 就变，缓存自动失效；自检失败（`ok=false`）**不会**入缓存。
- 命中缓存仍要按本次新要求核对 `answers.json`，但**字段清单与定位逻辑无需重做**。

### PDF 样版的特别说明

- `prep.py` 用 `pdf2docx` 尽量还原板式；转换为可编辑 Word 后，后续流程与 Word 样版完全相同。
- **先看 `conversion.coverage`（文本覆盖率）**：>=0.7 可正常按坐标回填；<0.7 说明部分内容在文本框/图片/扫描页中，**必须向用户说明并建议提供 Word 原件或先 OCR**（OCR 可配合 `paddleocr_doc_parsing` 工具），不要假装能改。
- 扫描件（可提取文字 <80 字符）转换后基本不可编辑，同上处理。
- 只需其中几页时传 `--pages 1-3,5`（大 PDF 必传，转换耗时与页数成正比）。
- 转换产物确定性复用（带 `.hash` 标记），不会每次重复转换。

### 分析样版（仅 `analysis_cache.hit=false` 时执行）

依据 `prep.py` 输出的摘要判断：

- **新要求下要变的内容**：相对本次需求的主体名称、编号、日期、金额/数值、期限、责任人、结论、清单项……以及需要整段改写的说明文字。
- **固定内容保留原值，不要拿去问用户**：样版本身自带的固定条款、公司抬头、页眉页脚、通用说明、表格标题、格式性空白等。
- **历史文件玩法**：把历史文件当成「已填好的样例」——只把「新旧要求之间会变的值」列为字段；历史文件里的旧值只是**定位锚点**，不是本次答案。
- **样版/历史文件里的示例数值不是权威数据**：如用户提供了资料库/标准值/新要求文件，一律以用户本次提供的为准；样版里的示例仅用于定位与版式参考。
- 每项信息**填在文档哪个位置、用什么定位方式**；一个值出现多处要写多个 locator。
- 有无可增减行的清单表：指出表序号、表头行、样例行、合计行、列与字段的映射。
- 派生关系（合计＝各行之和、大写、比例换算等）写成 `formula`，**不向用户提问**。

把结论写成 `$WORK/analysis.json`，格式见 `references/analysis_schema.md`。硬性要求：

- `locators[].text` 必须**逐字来自 dump / anchors**，不得臆造；优先用 `cell` / `paragraph` 坐标。
- 用 `literal` 时确认原文可唯一定位，必要时用 `where` / `contains` / `table,row,col` / `scope` 限定。
- 标好 `required`；能算出的写 `formula` / `default`。
- **发现「后一问的形态取决于前一答」时，用 `phase` + `depends_on` 声明依赖**，不要预先把两个分支都问一遍（详见 `references/analysis_schema.md` 1.2.1）。常见形态：选"分期"才有期数、选某类主体才有该类专属材料、清单为空时才需要先定条目模板。
- 清单表用 `list_tables` 声明，`sample_rows` 指出样例行（生成时按实际条数克隆替换，可多可少）。
- 写好后，用一两句话向用户复述「本次需要收集哪几类信息」，再进入第 3 步。

## 第 3 步：收集信息（对话清单 或 信息采集表）

把所有当前已解锁的待改项连同建议默认值列出来；用户回复后直接进入第 4 步。仅当答案本身有歧义/缺失时才补问。

### 选对话清单还是信息采集表

| 情形 | 做法 |
|------|------|
| 待改字段 <=5，都是简单值 / 方向选择 | 在对话里一次问全（见下方"分轮问答"） |
| 待改字段 >=6，或需用户提供财务 / 人员 / 知识产权 / 市场等**结构化数据** | **生成 Word《信息采集表》让用户填写**（见下方"信息采集表流程"） |
| 用户明说"信息太多，给我个表填" | 生成信息采集表 |
| 用户说"先用示例数据" | 不要求填写；把 sample 当答案直接回填，交付时提示这些是示例值 |

- 已有答案、`default`、`formula` 的字段不重复问。
- 样版中的固定内容不问；只有用户主动声明「抬头/主体要换成我方」时才整体替换（这属于明确指令，但仍保留版式）。
- 清单项让用户逐条给出（条数不限，可多于或少于样版样例行），每条至少给出用于定位的名称/关键字段；需要归类的补 `category` 等，供多表拆分与小计使用。
- 未提供的非必填字段保持样版原值不动。

### 信息采集表流程（字段多时用）

思路：把"在聊天里追问一大堆"改成"给一份能离线填写的表"。字段来自第 2 步的 `analysis.json`，**示例值必须在表里高亮**，让用户一眼分清"示例"与"真实"。

1. 写 `$WORK/fields.json`：按 `references/intake_form_schema.md` 组织分组与字段；`key` 与 `answers.json` 字段对应，`sample` 填样版里的示例值，`required` 标必填。字段清单只由本次分析样版得出，不写进技能包。
2. 生成采集表（示例值自动**黄色高亮**）：

```
python3 <技能目录>/scripts/make_intake.py "$WORK/fields.json" "$WORK/信息采集表.docx" \
    [--title "..."] [--note "黄色高亮=示例数据，请替换为真实值；沿用示例可保留高亮"]
```

3. 用 `cp` 工具把采集表复制为用户可见的交付文件，告诉用户三选一：① 在 Word 里填真实值后发回；② 回"先用示例数据"直接沿用；③ 部分填、其余沿用示例。
4. 回收：用户填好后（上传路径以会话上传信息为准）读回，映射到 `answers.json`，再走第 4 步。

```
python3 <技能目录>/scripts/make_intake.py --extract <填好的信息采集表实际路径> \
    "$WORK/fields.json" --out "$WORK/intake_values.json"
```

- 回收按 `fields.json` 分组与行序读第 3 列，**不依赖用户是否保留高亮**。
- 用户明确"沿用示例"的字段，用原 `sample` 作为答案；交付时须在回复里提示这些是示例数据、待换真实值。
- **高亮只用于采集表，不得进入成品** `output.docx`：成品的板式必须 1:1 继承样版，不得因为示例值高亮而在回填时给成品加高亮。

### 分轮问答（仅对话清单分支；存在字段依赖时）

只有一种情况：**某个待收集项的内容取决于本轮还没拿到的答案**。此时在 `analysis.json` 里给它写 `depends_on`，然后：

```
python3 <技能目录>/scripts/probe.py "$WORK/analysis.json" --round 1          # 看第一轮问什么
python3 <技能目录>/scripts/probe.py "$WORK/analysis.json" "$WORK/answers.json" \
    --round 2 --merge <本轮新答案>.json --merge-out "$WORK/answers.json"
```

- 每轮**只问 `open_fields` + `open_lists`**，`blocked_fields` 留到下一轮，这正是分轮的意义。
- `ready_to_fill=true` 时停止提问，进第 4 步。不要凭感觉判断「差不多问完了」——以它为准。
- 用户在一次回复里主动多给了后续答案也没问题：`probe.py` 会按实际答案重新算，该跳过的就跳过。
- 依赖只能建立在**答案字段自身**（有无值、等不等于某值），不要写成业务判断或去读文档内容。

**不要**把本可一轮问完的文档硬拆成多轮：往返成本实打实，拆轮的唯一理由是依赖。

## 第 4 步：一次回填 + 报告验收

把 `answers.json` 通过 stdin 一次传入（`-`）：

```
python3 <技能目录>/scripts/fill_doc.py <prep输出的working_template> "$WORK/analysis.json" - \
    "$WORK/output.docx" \
    --report "$WORK/fill_report.json" \
    --cache-dir "$SKILL_TMP_DIR/doc_layout_cache" --cache-key <prep输出的selected.hash>
```

**只看回填报告即可复核，不要再打开 docx 读回核。** 报告关键字段：

- `summary`：一行结论（ok / 字段 / 清单项 / 未命中定位 / 板式一致 / 依赖暂缓 / 警告）
- `verification.fields`：每个字段的最终回填值
- `verification.tables`：各清单表实际行数与条数
- `verification.preview_lines`：成品前 12 行非空正文（可直接目视）
- `verification.checks`：未命中定位、缺失必填、**依赖暂缓**、内部标记残留、占位符残留、**板式漂移**、警告数
- `deferred`：因依赖未满足而未问的字段（条数应与 `probe.py` 的 `blocked_fields` 对得上）
- `layout`：`identical` 与 `differences`（页面尺寸/边距/段落样式/非清单表结构）；清单表行数变化属预期，不计入漂移
- `analysis_cached`：本次是否入缓存

若 `ok=false`，按 `missing_required` / `unmatched_locators` / `layout.differences` / `warnings` 回到第 2 或第 3 步修正**缓存**（不要改样版），重跑第 4 步。`deferred` 非 0 **不算失败**——它只说明还有下一轮要问，按 `probe.py` 继续即可。

## 交付

验收通过（`ok=true`）后，用 `cp` 工具把 `$WORK/output.docx` 复制为用户可见的交付文件
（目标文件名按本次任务语义命名，如 `某某项目报告.docx`），再向用户报告：
已改关键内容、清单行数、板式是否一致，并简要提示 PDF 转换的保真度限制（若适用）。
**不要**交付 `analysis.json` / `answers.json` / `fill_report.json` / `fields.json` 等中间产物
（信息采集表分支中采集表本身按第 3 步流程交付）。

---

## 关键文件

| 路径 | 作用 |
|------|------|
| `scripts/prep.py` | **第 1+2 步编排（首选）**：扫描 + 选样版 + PDF 转换 + dump + 锚点 + 缓存检查 |
| `scripts/probe.py` | 第 3 步分轮编排（对话清单分支）：按 `depends_on` 算出本轮该问什么、是否可回填、答案合并与轮次留痕 |
| `scripts/make_intake.py` | 第 3 步字段多时：**生成信息采集表（示例值黄色高亮）**，或从填好的采集表 `--extract` 读回值 |
| `scripts/pdf_to_docx.py` | PDF → 可编辑 DOCX，含文本覆盖率与扫描件检测（一般经 prep.py 调用） |
| `scripts/scan_templates.py` | 扫描 `.docx` / `.pdf` 样版目录，输出精简目录 |
| `scripts/dump_template.py` | 坐标化转储样版内容 + 板式指纹（一般经 prep.py 调用） |
| `scripts/fill_doc.py` | 第 4 步：stdin 回填 + 动态清单行 + 依赖暂缓判定 + 板式指纹验收 + 自动入缓存 |
| `references/analysis_schema.md` | `analysis.json` / `answers.json` / `fill_report.json` 完整格式（含 `depends_on` 语义） |
| `references/intake_form_schema.md` | 信息采集表 `fields.json` 格式、生成/回收命令、何时启用采集表 |
| `$SKILL_TMP_DIR/doc_layout_cache/` | 运行时缓存（**不在技能包内**），按源样版 hash 存 `analysis.json` |

## 新增样版

用户上传新的 `.docx` 或 `.pdf`（或给出文件实际路径），直接把它作为 `prep.py --templates`
参数传入即可。**不要修改本技能的任何文件，也不要给样版加占位符。** 同内容的样版会命中缓存。

---

## 变更记录

| 版本 | 变更 |
|------|------|
| 1.3.0（我方合并） | 合并上游 1.2.0 → 1.3.0：第 3 步升级为「收集信息」双轨（对话清单 / 信息采集表），新增 `scripts/probe.py`（`depends_on` + `phase` 分轮问答）、`scripts/make_intake.py`（示例值黄色高亮生成 + `--extract` 回收）与 `references/intake_form_schema.md`；`fill_doc.py` 支持依赖暂缓（`deferred`）、字段默认值兜底（优先级：用户答案 > 公式 > 默认值）、越界定位不中断、大写金额节间补零修复、缓存 key 与 prep 统一 `<ext>-<hash>`；`pdf_to_docx.py` 用 PyMuPDF 取真实页数解析 `--pages`。红线新增：不得把一次任务变成「专用生成器」、业务内容不得写进脚本、采集表高亮不得进成品。工作目录 / `cp` 交付 / 技能边界等我方口径保持不变。 |
| 1.0.0（我方移植） | 首次移植上游 1.1.0：四步工作流 + prep.py 一次编排 + 按源样版 hash 的分析缓存；工作目录改 `$SKILL_TMP_DIR`、交付改 `cp` 工具、技能边界对齐本系统。 |
