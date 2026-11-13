---
name: bid-opportunity-evaluator
description: >
  招标信息获取与投标可行性评估（投标决策前置技能），最终产出 Word 格式评估报告。
  适用于用户要求「找/筛选招标机会」「判断这个项目值不值得投」「分析招标公告/招标文件」
  「评估投标可行性（资质要求/业绩要求/保证金/竞争对手分析）」「决定是否参与投标」。
  四步工作流：收集招标公告 → 解析招标文件画像 → 硬门槛与财务测算 → GO/NO-GO 决策建议。
  全流程规则化脚本执行（默认 0 次大模型调用，实测约 11 秒），报告按模版排版，
  模版优先级依次为：用户会话提供、资料库检索、技能自带模板，保留模版原有章节与样式。
  不生成投标文件本身（那是 bid-doc-generator 的职责）。
metadata:
  version: "1.0.0"
  author: aid-work-agent
requires_tools:
  - read
  - cp
  - image_read
  - knowledge_base_search
  - knowledge_file_search
---

# 招标信息获取与投标可行性评估 · Bid Opportunity Evaluator

## 一、技能定位

本技能解决投标决策的**第一步**：在投入人力编制投标文件之前，先判断这单**值不值得投**。

对应四步工作流：

| 步骤 | 内容 | 本技能做法 |
|---|---|---|
| ① 收集招标公告，获取招标文件 | 找到项目、拿到完整招标文件 | **检索与下载由上层 Agent 完成**；本技能解析交付到手的公告/招标文件 |
| ② 分析项目概况 | 项目地点、**数量指标**（设备台数 / 材料面积 / 备件套数）、技术规格、工期 | 规模指标**通用提取**（数量+单位），从采购清单表取权威值 |
| ③ 评估投标可行性 | 资质要求、业绩要求、保证金、竞争对手分析 | 硬门槛逐条客观比对 + 财务测算 + 六维评分卡 |
| ④ 决定是否参与投标 | 结论与建议 | GO / CAUTION / NO_GO 三档决策 + 风险清单 + 行动建议 |

**竞品分析分两层**：

- **实证竞品**（真实企业名）——由上层 Agent 检索或我方情报提供，落 `competitors.json`；
  技能不内置任何品牌名，代号式名称（竞品A / 某某公司）会被自动拦截剔除。
- **竞争者类型画像**——脚本从招标文件客观特征推导，全程不含任何企业名；
  在未取得实证名单时，它本身就是可执行的竞争分析。

> **技能定位**：本技能是**行业无关、企业无关**的通用工具。
> 不内置任何企业名称、产品名称、项目名称或地域特例；所有主体信息均来自运行时输入。
>
> **与 `bid-doc-generator` 的分工**：
> 本技能决定「投不投」；`bid-doc-generator` 决定「投的话怎么写标书」。
> 本技能判定为 GO 后，才进入后者生成投标文件。

### 边界（不要使用本技能）

- 编制投标文件/标书 → 用 `bid-doc-generator` 技能。
- 供应商资质证书真伪核验、信用/风险审查 -> 用 `supplier-qualification-review` 技能。
- 生成合同文本 -> 用 `contract-generator` 技能。
- 本技能只做投标决策前置评估，上述场景即使出现在投标准备流程中也各自走对应技能。

## 二、运行环境注意（必读）

- 每次 `skill_execute` 调用都在独立的临时工作目录中执行，调用间不共享 cwd。所有中间产物
  （`tender_announce.json` / `tender_profile.json` / `assessment_draft.json` / `evaluation.json`
  / `competitors.json` 及最终 Word 报告）**必须用绝对路径**读写，统一放在 `$SKILL_TMP_DIR`
  下的任务目录中（环境变量已注入，持久不清理）。本文档中出现的 `work/...` 相对路径一律
  映射为 `$SKILL_TMP_DIR/work/...`。
- 技能目录运行时为只读；`scripts/` 下的脚本通过 `skill_execute` 调用，脚本路径会被自动映射为绝对路径。
- 用户上传文件的路径来自会话上传信息（冒号后面是实际上传路径），**始终使用实际上传路径**，
  不要用原始文件名拼路径。
- 中间产物的读取使用 `read` 工具；最终 Word 评估报告交付使用 `cp` 工具带出。
- **扫描版（图片型）招标 PDF**：脚本只能抽取文本层，扫描件会得到空结果。
  此类输入先用 `image_read` 视觉读取关键章节并整理为文本后，走 `--from-text` 方式传入，
  或提示用户提供文字版，不要凭空补全要求条目。

## 三、性能原则：默认 0 次大模型调用

**本技能的全流程可完全由脚本跑完**（实测 11.6 秒，0 次模型调用）：

```bash
python scripts/run_pipeline.py \
    --tender <招标文件.docx|pdf> \
    [--announce <招标公告.pdf>] \
    --company <company_profile.json> \
    --template <模版.docx> \
    --out <输出目录> [--today YYYY-MM-DD]
```

一条命令产出完整 Word 评估报告。**大模型只在下列情形才需要介入**：

| 情形 | 大模型做什么 |
|---|---|
| 需要更精准的竞争格局判断（如已知本地竞争者名单） | 修订 `assessment_draft.json` 的 `competition` |
| 需要战略判断（如该客户是战略重点） | 修订 `strategy` |
| 招标文件表述特殊，规则化打分偏差明显 | 修订 `commercial_scores` |
| 需要补充规则未覆盖的风险 | 追加 `risks` |

**修订方式**：跑完流水线后改 `02-研判/assessment_draft.json`，再单独重跑评估与填充：

```bash
python scripts/evaluate_bid.py --profile 01-画像/tender_profile.json \
    --company company_profile.json --assessment 02-研判/assessment_draft.json --out 03-评估/
python scripts/fill_template.py --evaluation 03-评估/evaluation.json \
    --template <模版.docx> --out 04-报告/
```

> **模版路径说明**：会话/资料库均无模版时，直接使用技能自带
> `assets/投标可行性评估报告模板.docx`（技能目录内**没有** `templates/` 目录）。
> `run_pipeline.py` **省略 `--template` 即自动用自带模版**；单独跑 `fill_template.py`
> 时 `--template` 必填，把该自带模版的绝对路径传给它即可，不要猜其他路径。

**为什么这样设计**：竞争格局、战略价值、商务可接受度这三项虽名为「主观」，
但其**依据全部来自招标文件的客观特征**（价格分权重、业绩分权重、标段划分、
联合体限制、品牌门槛、质保期、付款比例、卸货责任…），可规则化量化。
规则化结果偏保守（打分略低于人工研判），但**硬门槛、决策结论、风险项完全一致**——
这正是应当由机器做、而不该每次调用模型做的部分。

### 脚本接口速查（禁止读源码）

**禁止用 `grep`/`sed` 翻看 `scripts/*.py` 源码来理解接口或评分逻辑**——所有命令行
签名与输出结构以本节为准；输入档案字段细节读 `references/assessment-guide.md` §七，
模版机制读 `references/template-mechanism.md`。读源码既慢又费轮次。

**脚本 CLI 一览**：

| 脚本 | 用途 | 关键参数 |
|---|---|---|
| `run_pipeline.py` | 一键全流程 | `--tender`（必需）`--company`（必需）`--out`（必需）；可选 `--announce` `--template`（**缺省即用自带模版**）`--assessment` `--competitors` `--today` |
| `tender_intel.py` | 解析公告/招标文件 | 子命令 `announce` / `document`；`<file>` + `--out`（必需）；`--from-text` 传正文 |
| `competitor_intel.py` | 竞品情报 | 子命令 `probe`（`--profile` `--out`）/ `verify`（`--input` `--out`） |
| `auto_assess.py` | 规则化研判 | `--profile` `--company` `--out`（必需）；可选 `--competitors` |
| `evaluate_bid.py` | 评估决策 | `--profile` `--company` `--out`（必需）；可选 `--assessment`（缺省自动规则化）`--today` |
| `fill_template.py` | 模版填充 | `--evaluation` `--template` `--out`（均必需）；可选 `--mapping` `--layout` `--title` |
| `inspect_template.py` | 模版结构解析 | `<模版.docx>` + `--out` |

**输出 JSON 顶层结构**（修订时直接改对应字段后重跑，不读源码）：

- `assessment_draft.json`（auto_assess 产出，可人工修订后作 `--assessment` 传入）：
  `product_match_rate` / `product_match_basis` / `commercial_scores`（键值分数字典）/
  `commercial_basis` / `competition{score,basis}` / `competitors`（清单）/
  `competitors_by_type` / `competitor_source` / `strategy{score,basis}` / `risks` /
  `recommendations`。
- `evaluation.json`（evaluate_bid 产出，fill_template 的输入，一般不手改）：
  `company_name` / `project`（项目要素字典）/ `decision{code,label,reason,failed_gates}` /
  `score_total` / `scorecard`（六维分数）/ `gates`（硬门槛逐项 `{gate,requirement,our_status,passed}`）/
  `economics`（控制价/保证金/资金占用/建议报价区间等）/ `time` / `risks` / `recommendations`。

**修订流向**：招标文件特征（如评分办法、规模指标、商务条款）的解析偏差改
`tender_profile.json` 对应键后重跑 `auto_assess.py` / `evaluate_bid.py`（`evaluation.json`
的 `project` 字典每次重跑由 profile 重建，**直接补键会在重跑时被覆盖**）；研判结论偏差改
`assessment_draft.json` 后重跑 `evaluate_bid.py`。

## 四、Inputs

| 输入 | 必需 | 说明 | 可接受形态 |
|---|---|---|---|
| ① 招标公告 | 建议 | 项目背景、时间节点、限价、报名方式 | PDF / DOCX / 网页文本 |
| ② 招标文件 | 必需 | 资格门槛、采购清单、评分办法、商务条款 | PDF / DOCX / XLSX |
| ③ 我方资质与经营档案 | 必需 | 主体资格、资质证照、财务、业绩、团队、资金承受力 | `company_profile.json` |
| ④ 主观研判 | **可选** | 竞争格局、战略价值、商务可接受度的深化判断 | `assessment.json` |
| ⑤ 竞品情报 | **可选** | 真实可指认的主要竞品及定位、价格带、区域、优劣势、来源 | `competitors.json`（由 `competitor_intel.py verify` 产出） |

**缺 ③ 时不能给结论** —— 没有我方档案就无法做门槛比对与财务测算。
此时先向用户索取，或由大模型先从已有资料整理成 `company_profile.json`
（字段速查见 `references/assessment-guide.md` §七；事实项不得编造，缺失写 `[待补充]`）。

**缺 ④ 不影响出报告** —— 脚本会规则化推导全部主观项（见「性能原则」）。

## 五、Workflow（四步法）

### Step 1 · 收集招标公告，获取招标文件

> **搜索能力不在本技能内。** 本技能不联网、不内置检索脚本。
> 招标公告的**发现与下载由上层 Agent 完成**（使用上层可用的检索工具：
> `web-search-pro` 技能、行业信息库、企业知识库等），上层 Agent 将公告
> **文件或正文文本**交付给本技能即可。本技能只负责解析已落地的内容。

```bash
# 方式 A：上层 Agent 交付了公告文件
python scripts/tender_intel.py announce <招标公告.pdf|docx> --out work/announce/

# 方式 B：上层 Agent 只拿到公告正文文本
python scripts/tender_intel.py announce x --from-text "<公告正文>" --out work/announce/
```

产出 `tender_announce.json`，含：项目名称、项目编号、招标人、招标代理、联系人、
建设地点、招标范围、工期/交货期、质量标准、资金来源、
**投标截止时间**、开标地点、**招标控制价**、**投标保证金**、代理服务费、投标有效期、
联合体限制、密封/签章要求、规模指标、报名与获取文件的平台。

**判断节点（脚本已算）**：

- 截止时间是否已过 → 直接 NO_GO
- 剩余 ≤3 天 → 编制时间不足，CAUTION
- 是否需要交保证金才能获取招标文件 → 记入 `pending_items`

> 若上层 Agent 尚未取得公告，本技能应**提示需要公告或招标文件**，而不是自行尝试联网检索。

### Step 2 · 解析招标文件，建立项目画像

```bash
# 方式 A：上层 Agent 交付了招标文件
python scripts/tender_intel.py document <招标文件.docx|pdf> --out work/profile/

# 方式 B：上层 Agent 只拿到招标文件正文文本
python scripts/tender_intel.py document x --from-text "<招标文件正文>" --out work/profile/
```

产出 `tender_profile.json`，含：**项目概况**（地点、范围、工期、质量、资金来源）、
**规模指标**（从采购清单表提取，带来源标注）、技术规格原文、资格要求原文与逐条拆分、
**评分办法**（方法、总分、商务/技术/报价分值、评分因素、基准价算法、价格扣分规则）、
**否决投标情形**、关键商务条款（支付/结算/质保/售后/交货/品牌要求）。

> **规模指标必须通用**：识别「数量+单位」组合，覆盖
> 台/套/个/辆/件/座/栋/组/面/处/项/标段、㎡/m²/平方米、m³、延米/米/km、吨/kg、批。
> 优先从**采购清单表**取（权威）；表内单位列缺失时沿用同表上一行单位。
> 正文散句仅作补充（易把章节号「2.1」误判成数量）。

### Step 2.5 · 竞品情报（真实企业名 + 类型画像）

竞品分析必须落到**可指认的真实企业**，不能用代号充数；但品牌名又不能固化在技能里。
因此分两步走：

```bash
# ① 生成检索任务书（依据招标特征推导「该查什么」，不含任何企业名）
python scripts/competitor_intel.py probe \
    --profile work/profile/tender_profile.json \
    --company work/company_profile.json \
    --out work/competitor_brief.json
```

任务书给出：

- `candidate_types` —— 候选竞争者类型与触发条件；
- `brand_hunt` —— **每类的品牌检索指令**：该类型的品牌特征 + 2~4 条可直接用的
  检索式（已按品类与区域填充）+ 须核实项，**每类至少要找 2 个具体品牌**；
- `required_attributes` —— 每家竞品必须给出的属性；
- `data_source_expectation` —— 来源要求（企业官网 / 中标公示 / 我方投标记录）。

**上层 Agent 用其检索能力**按任务书找到真实竞品（公共资源交易平台中标公告、
企业官网、公开报道、我方历史投标记录），填成 `competitors_raw.json`：

```json
{"source": "来源说明", "competitors": [
  {"name": "真实企业名", "type": "品牌型", "positioning": "…",
   "region": "…", "price_band": "高档", "overlap": "高",
   "strength": "…", "weakness": "…", "evidence": "中标公告 / 官网"}]}
```

```bash
# ② 校验：剔除占位代号与重复项，输出规范化清单
python scripts/competitor_intel.py verify \
    --input competitors_raw.json --out work/competitors.json
```

`verify` 会剔除「竞品A」「某某公司」「XX企业」「待补充」等代号式名称——
**这类名称不得出现在正式报告里**。剔除项会列在输出里，不会被静默吞掉。

**检索的优先顺序**（决定报告里出现哪类品牌）：

1. **同区域同品类的中标公告**——公共资源交易平台上本项目所在区域、同品类的
   近年中标/候选人名单，最能反映真实对手；
2. **当地监管抽检公示**——市场监督管理局的建材抽检结果会列出
   「被检主体（本地经销商）+ 生产厂家」，同时暴露本地渠道与在售品牌；
3. **品牌官网与行业榜单**——用于头部品牌型竞争者；
4. **我方历史投标记录**——我方参与过的同项目开标记录。

**竞争者类型画像**由脚本自动生成（无需任何输入）：依据品牌门槛、价格分权重、
招标人类别、业绩门槛等特征，推导 6~7 类可能出现的对手及其威胁点与应对策略，
全程不含企业名。未取得实证竞品时，报告输出该画像并明确说明。

### Step 3 · 评估投标可行性

**默认走规则化研判（0 次大模型调用）**：

```bash
python scripts/auto_assess.py --profile work/profile/tender_profile.json \
    --company work/company_profile.json --out work/assessment_draft.json

python scripts/evaluate_bid.py --profile work/profile/tender_profile.json \
    --company work/company_profile.json --out work/eval/ --today 2026-06-10
```

`evaluate_bid.py` 的 `--assessment` **可省略**——省略时自动用规则化研判结果。
需要人工/模型深化时再传 `--assessment work/assessment.json`。

**规则化推导依据**（全部来自招标文件客观特征）

| 项 | 推导依据 |
|---|---|
| 产品参数满足度 | 清单条目数 vs 我方可供型号数 |
| 竞争格局 | 价格分权重、业绩分权重与业绩满足度、标段划分、联合体限制、品牌门槛、项目金额量级、质保期 |
| 战略价值 | 项目金额量级、招标人体系业绩重合度、地域服务网络覆盖、清单品类集中度、产能占用率 |
| 商务可接受度 | 预付款比例、付款账期、结算周期、质保期与 730 日惯例对比、违约金/赔偿/没收条款、卸货责任与安全自担条款 |
| 风险清单 | 否决项数量、签章要求、保证金占比、页数上限、清单同名异规格项、业绩缺口、资金占用率 |
| 投标建议 | 决策结论 + 报价区间 + 保证金 + 业绩材料 + 递交动作 |

**硬性资格门槛核对（客观，脚本判定，不接受主观分）**
主体资格 / 经营范围覆盖 / 生产许可或厂家授权 / 财务要求 / 纳税社保 /
类似项目业绩（按年限+类型+金额过滤）/ 信誉 / 联合体限制 / 保证金可承受额度。

**财务测算（脚本计算，不接受手工填数）**
控制价、投标保证金、履约保证金（绝对额或百分比）、代理服务费、
**资金占用合计及占限价比**、按目标毛利率估算的**成本线**与目标毛利、
基准价法下的**建议报价区间**（依据「高 1% 扣 X 分、低 1% 扣 Y 分」反推）。

**六维评分卡**

| 维度 | 权重 | 性质 | 依据 |
|---|---|---|---|
| 资质与能力匹配 | 25% | 客观 | 硬门槛通过率 + 产品参数满足度 |
| 业绩匹配度 | 15% | 客观 | 可用业绩项数 / 要求项数 |
| 商务条件可接受度 | 15% | 半规则 | 规则化打分，可由模型修订 |
| 经济性 | 20% | 客观 | 毛利率、资金占用占比 |
| 竞争格局 | 15% | 半规则 | 招标特征量化，可由模型修订 |
| 战略价值 | 10% | 半规则 | 招标特征量化，可由模型修订 |

**竞品分析输出两张表**：

- **「竞争者类型」表（主表）**——**按类型分组，组下列出具体品牌**：
  竞争者类型 / 该类型下的具体品牌 / 家数 / 最高威胁度 / 对本项目的威胁点 /
  我方应对策略。每种类型必占一行：有实证品牌的，列出品牌名与威胁度；
  尚无品牌的，写明「该类型尚未识别到具体品牌 —— 应为具备〈该类型特征〉的企业」，
  **不留空**。
- **「竞争者（具体品牌）」表（细表）**——实证竞品逐家分析：
  具体品牌 / 类型 / 品牌定位 / 主要区域 / 价格带 / 重合度 / 威胁度 /
  相对优势 / 相对劣势 / 我方应对。

**竞争格局正文也按类型点名品牌**，句式如：
「已识别可指认的竞争者 12 家，按类型分布为 —— 工程集采型 3 家：A、B、C；
区域深耕型 4 家：D、E、F、G；头部品牌型 4 家：H、I、J、K」。
若画像中有类型尚无实证品牌，正文追加「尚缺该类竞争者的具体品牌：X、Y，建议补充检索后重评」。

**没有实证竞品时只输出类型表**，并在正文写明「尚未取得可指认的实证竞品名单
（不以代号代替真实企业名）」。**绝不虚构竞品，绝不输出代号。**

**决策规则（脚本判定）**

| 条件 | 结论 |
|---|---|
| 任一硬门槛不满足 | **NO_GO** 不建议参与 |
| 投标已截止 | **NO_GO** |
| 硬门槛全过 + 评分 ≥60 | **GO** 建议参与 |
| 硬门槛全过 + 评分 <60 | **CAUTION** 谨慎参与，须落实补偿措施 |
| 硬门槛全过 + 剩余 ≤3 天 | 降级为 CAUTION |

### Step 4 · 决定是否参与投标

**产出目标：一份 Word 格式的评估报告，且优先按指定模版排版。**

#### 4.1 定位模版（三选一，按优先级）

| 优先级 | 来源 | 谁负责 |
|---|---|---|
| ① | 会话中用户提供的模版文件 | 上层 Agent 直接取上传路径 |
| ② | 资料库中的评估报告模版 | 上层 Agent 用 `knowledge_base_search` 检索、
`knowledge_file_search` 按文件名定位并下载 |
| ③ | 技能自带 `assets/投标可行性评估报告模板.docx` | 兜底 |

> 检索结果须确认是**模版/模板**（含占位符、空白表格、章节骨架），
> 而非已填好的历史报告。资料库无命中时须**明确告知**，不可含糊。
> 资料库部分来源不可用时，须说明实际检索范围。

模版定位后先解析结构，确认章节与占位符：

```bash
python scripts/inspect_template.py <模版.docx> --out work/template/
```

#### 4.2 按模版填充，产出 Word

```bash
python scripts/fill_template.py \
    --evaluation work/eval/evaluation.json \
    --template <模版.docx> \
    --out 输出目录/ \
    [--mapping work/template/mapping.json]
```

> 走 ③ 兜底时 `--template` 传技能自带模版的**绝对路径**
> `assets/投标可行性评估报告模板.docx`；技能内不存在 `templates/` 目录，不要猜路径。

填充规则详见 `references/template-mechanism.md`：

- 模版有占位符 → 精确替换（`{{文本}}` / `{{表格:键}}` / `{{段落:键}}`）
- 模版无占位符 → 按章节标题语义匹配插入，内容就近合并，不丢内容
- **模版的章节结构、页眉页脚、页面设置、样式全部保留**，不擅自增删章节

**交付物**：`输出目录/投标可行性评估报告-<项目名>.docx`（唯一正式产出，Word 格式）
**过程件**：`evaluation.json`、`EVALUATION.md`（供复核）

## 六、Critical Rules（铁律）

1. **结论必须由门槛比对支撑**：不许凭印象说「能投」。每个 GO/NO_GO 都要能指到具体门槛条目。
2. **金额一律由脚本算**：控制价、保证金、代理费、资金占用、成本线、报价区间，
   不得手工填数或心算。
3. **时间优先**：投标截止已过 → 直接 NO_GO，不要再往下评。
4. **缺失即标注**：我方证照、财务、业绩缺失写 `[待补充]`，不得推定为「有」。
5. **规模指标以采购清单表为准**，正文散句只作参考，避免把章节号当数量。
6. **金额必须带单位才算数**：无单位的裸数字（如目录页码「87」）不是金额。
7. **主观判断须给依据**：竞争格局、战略价值、商务可接受度每一项都要写清理由，
   供决策者复核。
8. **风险要给应对措施**，不能只列风险。
9. **本技能不产出投标文件**：判定 GO 后，投标文件编制转 `bid-doc-generator` 技能。
10. **零固化内容**：技能内不得出现任何企业名称、产品名称、项目名称、城市名或地域特例；
    所有主体信息必须来自运行时输入的档案与文件。不得内置任何示例数据充当默认值。
11. **不自行联网**：招标信息检索由上层 Agent 完成。本技能只解析交付到手的文件或正文文本；
    缺少输入时应说明「需要公告/招标文件」，而非自行搜索。
12. **竞品必须真实可指认**：报告中的竞品名称必须是真实企业/品牌名，
    由上层 Agent 检索或我方情报提供后经 `competitor_intel.py verify` 校验。
    **禁止「竞品A」「某某公司」「XX企业」「待补充」等代号进入报告**；
    也禁止在技能内固化任何真实品牌名。未取得实证竞品时，改用竞争者类型画像。
13. **模版优先且输出 Word**：评估报告必须按「会话模版 → 资料库模版 → 技能自带模版」
    的优先级取模版排版；最终交付物**必须是 Word 文件**。
    不得自造章节结构覆盖用户模版；模版无占位符时用章节语义匹配，内容不得丢失。
14. **能不调模型就不调**：规则可推导的判断（门槛核对、财务测算、竞争特征量化、
    风险抽取、建议生成）一律由脚本完成。全流程默认 **0 次大模型调用**。
    只有需要引入招标文件之外的信息（本地竞争者名单、战略意图）才请模型研判。

## 七、Resources

### references/

- `assessment-guide.md` — 评估方法论：门槛清单、评分标尺、竞争分析框架、
  报价策略推导、风险分类与应对、结论表述规范，以及
  **`company_profile.json` 与 `assessment.json` 字段速查（§七）**。
- `template-mechanism.md` — **模版机制**：三档优先级、占位符协议、
  无占位符时的章节语义匹配规则与默认关键词表、自定义映射写法。

### assets/

- `投标可行性评估报告模板.docx` — **技能自带模版**（9 章骨架 + 20 个占位符）。
  仅在「会话未提供模版」且「资料库未检索到模版」时使用。

### scripts/

- `run_pipeline.py` — **一键流水线**（推荐入口）：解析 → 研判 → 决策 → 模版填充 → Word，
  **0 次大模型调用**，实测约 11 秒。
- `tender_intel.py` — **招标信息解析器**：
  `announce` 模式（公告要素）、`document` 模式（项目画像：概况/规模/资格/评分/否决项）。
- `auto_assess.py` — **规则化自动研判**：从招标文件特征与公司档案推导
  竞争格局、战略价值、商务可接受度、产品匹配度、风险清单 → `assessment_draft.json`。
- `evaluate_bid.py` — **可行性评估与决策引擎**：硬门槛核对 + 财务测算 +
  六维评分卡 + GO/NO_GO 判定 + Markdown 报告。
  `--assessment` 可省略（自动用规则化研判）。
- `competitor_intel.py` — **竞品情报**：`probe` 生成检索任务书（不含企业名）、
  `verify` 校验回传清单并剔除占位代号与重复项。
- `inspect_template.py` — **模版解析器**：提取模版的章节骨架、表格表头、占位符。
- `fill_template.py` — **模版填充引擎**：`evaluation.json` + 模版 → 最终 Word 报告。
- `build_builtin_template.py` — 生成技能自带模版（可重新生成/定制）。
- `render_eval.py` — 直接渲染评估报告（不套模版时的简化路径，正式交付优先用 `fill_template.py`）。
- `docx_kit.py` — Word/Excel 排版基础库（封面/目录域/章节/表格/页眉页脚页码），被其他脚本 import，不单独调用。
