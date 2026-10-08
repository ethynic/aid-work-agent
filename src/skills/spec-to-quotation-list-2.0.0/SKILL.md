---
name: spec-to-quotation-list
description: 把任意设计手册 / 设计规范 / FF&E 规范 / 材料 spec book PDF 转成中英双语「室内材料报价清单」Excel。采用分阶段交互工作流：先扫描文档动态发现「空间」与「大类」，交用户确认/调整（两个确认门）；都通过后，再按确认的结构（空间×大类）生成多份 Excel 清单。每份文件内按产品类别（建材/家具/灯具/窗帘/饰品/标识/洁具五金等）分区并带合计总金额公式；空间名一律用文档真实名称；无真实产品的大类/组合不生成文件；用户声明的大类可跨空间合并为一份全项目文件；供应商名录等非产品条目不进主表。This skill should be used when the user provides a design specification PDF (plus optionally an Excel template) and asks for a material / FF&E / interior quotation list, or asks to split a quotation list by space or category.
metadata:
  version: "2.0.0"
  author: aid-work-agent
dependencies:
  - fitz
  - openpyxl
---

# 设计手册转材料报价清单（分阶段交互 / 按空间×大类拆分）

## 用途

把设计规范 PDF 转成可直接发供应商询价的 Excel 报价清单。

**工作流核心：分阶段交互，先确认结构再生成。**

1. **扫描发现空间**：先用 `discover_structure.py` 扫描原始文档，动态发现有哪些「空间」（房间/区域），给出证据（目录层级 + 页码）。
2. **确认空间（第 1 个确认门）**：把候选空间清单交给用户，**用户可重命名 / 合并 / 拆分 / 增删**。未确认前**不得生成任何 Excel**。
3. **确认大类（第 2 个确认门）**：基于文档关键词与已有条目，提出「大类」（外立面/硬装材料/家具软装/机电设备/艺术品…）候选，交用户确认/调整，并主动问哪些大类要跨空间合并。
4. **按确认结构生成**：两个确认门都通过后，再编/修 `items.json`（用确认后的空间名与大类名），运行生成器产出多份 Excel。
5. **校验**：自动校验 + 人工复核。

**清单的固有原则：**

- **按空间拆分**：每个真实空间单独成一份 Excel 文件（如 `HappyRoom_家具软装.xlsx`、`Façade_外立面与室外.xlsx`）。
- **按大类独立报价**：外立面、硬装材料、家具软装、机电设备等各自独立成文件；同一空间的不同大类也是不同文件。
- **文件内按产品类别分区**：每份文件内按产品类别（建材/家具/灯具/窗帘/饰品/标识/洁具五金/五金/景观/机电设备…）用 `■` 分区，每类别有「小计」，文件末有「合计总金额 Grand Total」公式。
- **数据驱动，不写死清单**：空间数、大类数、文件数全部由确认后的结构 + 条目数据动态决定。源文档/结构变更后重跑即自动增减文件。
- **无真实产品不生成**：某 (空间×大类) 组合或某大类只有 TBC 占位、没有真实可报价产品 → 不生成文件。
- **空间名用文档真实名**：HappyRoom 不要写成"标间"，Public Areas 不要写成"公区"；AI 不得自行翻译或缩写。

> 项目名、空间名、大类标签一律从用户传入的 PDF 与用户需求动态推导，不预设酒店/住宅/办公等任何业态或固定拆分。

## 运行环境注意（必读）

- **每次 skill_execute 调用都在独立的临时工作目录中执行**，调用间不共享 cwd。所有中间产物（analysis.json、structure_proposal.json、confirmed_structure.json、分片 items、items.json、生成的 xlsx）**必须用绝对路径**读写，推荐统一放在 `$SKILL_TMP_DIR` 下的一次性子目录中（环境变量已注入）：
  ```bash
  OUT=$SKILL_TMP_DIR/spec2quote_$(date +%s)
  mkdir -p $OUT
  python scripts/analyze_pdf.py <pdf> --json $OUT/analysis.json
  ```
  下一步引用上一步产物时，显式写出 `$OUT/analysis.json` 等绝对路径，不要依赖"上一步的当前目录"。
- 用户上传的 PDF 路径从会话附件上下文获取（绝对路径），直接传给脚本即可。
- 大段 items.json / confirmed_structure.json 内容**不要内联在 bash 命令里**（转义和长度都易出错）：先用文件写入方式落盘（如 `cat > $OUT/items.json`，内容走 skill_execute 的代码块通道），复杂中文补丁写成 `.py` 文件执行。
- 生成的 xlsx 交付给用户时，**逐个文件**调用 cp 工具注册下载：
  `cp(source_file_path="$OUT/NN_空间_大类.xlsx", display_name="NN_空间_大类.xlsx", register_download=True)`

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/analyze_pdf.py` | 识别项目名、**文档类型**（spec_book / schedule_driven / drawing_set）、章节结构、套数一览表候选页 |
| `scripts/discover_structure.py` | **扫描发现引擎（阶段 1/2）**：扫 PDF 提出候选「空间」(含证据页码) 与候选「大类」，输出 `structure_proposal.json` + 可读提案，供用户确认/调整 |
| `scripts/probe_pdf.py` | 逐页列出图片 xref/坐标与按行聚类的文字，用于图文配对 |
| `scripts/merge_items.py` | 合并分批编写的条目分片（`$OUT/parts/*.json`）为完整 items.json，含 code 冲突 / 空维度 / 参数死引用硬校验 |
| `scripts/generate_quotation_lists.py` | **数据驱动生成引擎**：读 items.json（受 `--structure` 约束），动态发现空间/大类，生成 `{space}_{theme}.xlsx`（`global_themes` 里的大类则跨空间合并为 1 份 `《大类》.xlsx`），含产品类别分区、小计、合计总金额、参数页联动、说明页；支持 `--size-budget-mb` 体积预算自动降质 |
| `scripts/validate.py` | 生成后自动校验：3 表结构、表头、数量联动参数页、合价公式、图片数、体积，输出「已算量/总条目」 |

## 流程（分阶段交互，含两个确认门）

### 0. 先判文档类型

```bash
python scripts/analyze_pdf.py <pdf> [--json $OUT/analysis.json]
```
看「文档类型」一行决定后续范式（详见 `references/output_format.md` 与三个 worked_example）：

| 文档类型 | 特征 | 处理范式 |
|---|---|---|
| `spec_book` | 文字多、含品牌产品图与规格表 | 按空间×大类拆分；数量从正文读 |
| `schedule_driven` | 存在「套数一览表/户型清单」 | 空间=户型；套数进参数页；数量用 `expr` 公式 |
| `drawing_set` | 文字稀疏、大量矢量绘制 | 空间=区域/楼层；矢量图用 `img=["r",页号]` + `disable_auto_image` |

### 1. 扫描发现「空间」（★ 第 1 个确认门：交用户确认/调整）

```bash
python scripts/discover_structure.py --pdf <spec.pdf> [--items items.json] [--json $OUT/structure_proposal.json]
```

脚本会打印并写出提案：

- **候选空间**：主要来自 PDF 目录（所有层级），已标注哪些「像空间」、哪些「像非空间章节（通常只进 Notes）」，并附证据页码与已有条目数（若给了 items.json）。
- **候选大类**：来自调色板 + 文档关键词/items 命中，`✓` 表示建议保留、`✗` 表示文档未明显提及建议暂不含。

**▶ 必须暂停，把这份清单交给用户确认。** 调用 `present_options` 工具出选项卡片（如「1. 按提案继续 / 2. 大类全要 / 3. 我要调整（请打字说明）」），同时在回复正文给出完整编号清单，用户点按钮或打字回复均可。用户常见的调整：

- **重命名**：如把扫描到的 "Public Areas" 按文档真实名保持，或把 "客房" 改回文档里的 "The HappyRoom"。
- **合并**：如把 Lobby / Reception / Corridor 合并为一个 "Public Areas"。
- **拆分**：如把 "Public Areas" 拆成 Lobby & Reception / Room Corridor / Gym / Pool 各自独立空间。
- **增**：手动补一个文档没有但项目需要的空间（如 Presidential Suite）。
- **删**：去掉确认不报价的空间（如纯 Notes 章节）。

**记录用户确认后的空间清单**（这是后续生成的唯一空间来源）。

### 2. 确认「大类」（★ 第 2 个确认门）

基于同一次提案的大类部分，交用户确认/调整：可增删大类（如确认要「艺术品」、或不要「机电与设备」）、可重命名（如「硬装材料」保持）。**并主动问用户：哪些大类不按空间拆分、只要一份全项目文件？**（常见：标识 Signage、客用品与易耗品 Amenities → 写进 `global_themes`）

**▶ 再次暂停，确认大类清单**（同样用 `present_options` 出选项卡片 + 正文编号清单）。

确认后，把两份确认结果写成 `$OUT/confirmed_structure.json`：

```json
{
  "spaces": ["HappyRoom", "Bathroom Pod", "Public Areas", "F&B",
             "Façade", "Entrance & Signage", "Landscape & Parking",
             "BOH Areas", "Security", "Voice & Data", "Plant",
             "Standards & Suppliers"],
  "themes": ["外立面与室外", "硬装材料", "家具软装", "机电与设备"],
  "global_themes": ["标识", "客用品与易耗品"],
  "renames": { "旧空间名": "新空间名", "旧大类名": "新大类名" }
}
```

**`global_themes`（跨空间合并的大类）**：该大类全项目只生成 **1 份** `《大类名》.xlsx`（如 `标识.xlsx`），不再产出 `{space}_{theme}.xlsx`；文件内不写 ◆ 空间抬头，直接按 ■ 产品类别分区；每条 D 列仍标注其真实来源空间，便于询价后按空间反查下单。

> `renames` 仅在用户重命名了扫描到的名称、而 items.json 里还用的是旧名时才需要；若你已经直接把 items.json 的 space/theme 改成确认名，可不写 renames。

### 3. 编/修 items.json（用确认后的空间与大类名）

把条目写成 `items.json`，**每条目打上三个维度**（结构见 `references/items_json_schema.md`）：

- `space` —— **必须是用户在阶段 1 确认过的空间名**（文档真实名）。
- `theme` —— **必须是用户在阶段 2 确认过的大类**。
- `category` —— 产品类别（文件内分区用）。

**大项目必须分批编条目**：每个拆分组合一个分片落 `$OUT/parts/NN_<组合名>.json`，每轮只编 10~25 条，编完立即落盘并简报进度（本批条目数 / 累计条目数）；全局参数只在第一个分片定义一次；全部完成后用 merge_items.py 合并再生成：

```bash
python scripts/merge_items.py --parts-dir $OUT/parts --out $OUT/items.json
```

若已有草稿 items.json，按确认结果做重命名/合并/拆分/增删即可。通读内容、配图、数量规则等细节见下「条目编写要点」。

> 此阶段可用 `discover_structure.py --items items.json` 再跑一次，核对 (空间×大类) 矩阵是否符合确认结果，作为生成前最后 sanity check。

### 4. 生成（受确认结构约束）

```bash
python scripts/generate_quotation_lists.py --pdf <spec.pdf> --items $OUT/items.json --out $OUT/excel \
       --structure $OUT/confirmed_structure.json \
       [--analysis $OUT/analysis.json] [--project-name "项目名"] [--issue-date "22 SEP 2026"] \
       [--size-budget-mb 15]
```

- 生成器**只生成** `confirmed_structure.json` 里列出的空间×大类组合；若条目里还有旧名，按 `renames` 重命名后再过滤。
- 运行时会打印「动态发现」与「结构约束」两行，便于核对确实只产出了确认过的组合。
- 不带 `--structure` 也能跑（退化成纯自动发现），但**按本技能要求，必须经过阶段 1、2 确认后再生成**，故应带上 `--structure`。
- `--size-budget-mb`：单文件体积超限时自动降 dpi/quality 重嵌（最多 4 轮），漏判兜底用；首选拆分裁决时按空间部位细分控制体积。

### 5. 校验

```bash
python scripts/validate.py $OUT/excel [--max-mb 20]
```
逐项检查：3 表结构、表头、数量引用参数页、合价公式、图片数、体积，汇总「已算量/总条目」。目录页等非报价清单工作簿自动跳过。
人工复核重点见 `references/workflow_staged.md`。

## 条目编写要点（阶段 3 内部）

- **空间拆分裁决**：供应商完全不同的包拆开（如 "Bathroom Pod" 预制卫浴整舱单独成文件）；纯文字章节不产生条目，只在 Notes 说明；附录/材料标准合并成 "Standards & Suppliers"；文档逐个房间命名时，把 `space` 直接写成真实房间名即可各自独立成文件。
- **数量尽量算出来**：在 `parameters` 集中驱动量；给条目写 `qty_rule`（fixed/per_room/area/expr）；生成的数量是引用参数页的公式，不是死数。
- **尚不可报价**的条目加 `"tbc": true` 且不写 `qty_rule`；只在所属 (space,theme) 组合本身含真实产品时才列出。
- **配图**：按 `references/pdf_image_matching.md` 定图文关系，写入 `precise_images` 与 `page_renderings`。

## 输出结构

每个工作簿 3 张表：

1. 主表——抬头区、◆ 空间标题（global_themes 合并文件无此行）、■ 产品类别分区的数据行、图片列、Amount 公式、类别「小计」、文件末「合计总金额 Grand Total」
2. `参数 Parameters`——驱动数量计算的参数（改一处，全表联动）
3. `Notes 说明`——编制依据、甲供乙供、品牌替代、待业主补充信息、本文件涵盖范围

**合计金额公式**：每个产品类别末 `小计 = SUM(该类别 J 列)`；文件末 `合计总金额 = SUM(所有类别小计 J 列)`。数量/单价填后自动累计，不会重复计。

格式约定见 `references/output_format.md`；三类文档实战见 `references/worked_example_spec_book.md` / `worked_example_schedule_driven.md` / `worked_example_drawing_set.md`；**分阶段交互完整示例见 `references/workflow_staged.md`**。

## 硬约束

- **先确认、后生成**：阶段 1（空间）与阶段 2（大类）两个确认门**都通过前，禁止生成任何 Excel 文件**。扫描/提案脚本不写文件、只出提案。
- **空数量必须回文档找/推断**：凡条目数量为空，必须先回原始文档定位证据——文档明确给出的（如"3 areas × 4 seats""1 套系统""每间 1 套"）直接采用；文档未给但可从已知数据推断的（房间尺寸/层数/面积/分区），用几何或系数推断并在 `basis` 写清推断依据与假设，再补 `qty_rule`（优先 `expr`/`per_room`/`area` 联动参数，纯推断计数用 `fixed`）。**不允许未经回文档就整片留空**。唯一可留空的是「主册类」条目：供应商主册(MT-01xx)、标准饰面库(MT-02xx)、品牌标准(MT-03xx) 属目录/规范参照，本就无采购数量。
- **不编造数量**：实在算不出且无法合理推断的条目，数量留空并在「数量计算依据」写明缺什么，绝不凭空假设面积或房量。
- **甲供项要标注**：手册写明 Owner Supplied 的标注供货责任。
- **图片宁缺勿错**：图文关系无法确证时用空间效果图兜底或留空。
- **每条可追溯**：必须有 `ref`（章节 + 页码）。
- **空间名必须来自文档**：AI 不得自行翻译或缩写（HappyRoom≠标间、Public Areas≠公区）；用户若要求用其他名，须显式说明且记入 renames。
- **无产品不生成**：某 (空间×大类) 组合或某大类只有 TBC 占位、没有真实可报价产品 → 不生成文件。文件数由确认结构 + 数据决定，不要凑数强行创建空文件。
- **非产品条目不进报价清单**：**供应商名录 / 联系方式 / 联系人 / 规范参照（如 XX Supplier List、 Directory）本身没有采购数量与单价，不是可报价产品，不得作为条目进主表**。这类信息另存一份 `suppliers_directory.json`（或写进 Notes）供询价时查阅即可。抽条目时先自查一遍：categories 里出现「供应商名录 Suppliers」等字样即为误纳入，应全部剔除（含由此变成空组合、需要撤销的对应 Excel 文件）。
- **动态优先于模板**：识别出与示例不同的业态/空间，按实际结构重写 `space`/`theme`/`category`，不要套用示例固定清单。
- **validate 未通过不得交付**，如实告知用户哪些条目数量仍为空、缺少什么数据才能算出。
- 复杂中文补丁写成 `.py` 文件执行，避免 bash heredoc 转义问题。

## 实战经验（图文核对与后处理）

1. **图文对应必须目检**：PDF 页面文字阅读顺序≠图片位置。把所有条目 `img` 引用的 xref 按 bbox 批量渲染成 contact sheet 目检一次。
2. **文本抽取会把两个产品合并成一行**：对照渲染页图数一遍产品个数再定条目数。
3. **数量口径**：洁具/灯具按 `per_room` 可算；地板/墙纸/瓷砖等面积类，文档只给总面积时净面积参数必须留 null，不得用总面积顶替。
4. **币种与源单价**：海外项目币种特殊时，写后处理脚本把 items.json 源单价注入 I 列并在 Notes 追加说明。
5. **probe_pdf 只探查关键页**，不要 `--all` 全书探查（节省上下文）。

## 变更记录 Changelog

### v2.0.0（2026-09-29，合并 workbuddy v3.1.0 分阶段交互版）

1. **分阶段交互工作流（两个确认门）**：新增 `discover_structure.py` 扫描发现引擎；生成前必须先确认「空间」与「大类」两份清单，确认结果落盘 `confirmed_structure.json`，生成器以 `--structure` 约束只产出确认过的组合。
2. **数据模型改为 items[] + space/theme/category 三维度**：文件拆分由条目数据动态发现，不再预定义 `files[]`。
3. **新增 `global_themes`（跨空间合并大类）**：标识/客用品等大类可只出 1 份全项目文件。
4. **非产品条目不进主表**：供应商名录等无数量无单价的条目剔除，另存 suppliers_directory.json。
5. **保留我方增强**：`merge_items.py` 分批编条目（已适配 items[] 新格式，新增 space/theme/category 必填校验）；`--size-budget-mb` 体积预算自动降质重嵌（生成器 ImageFinder 参数化 + degrade）；validate.py 跳过目录页等非报价工作簿；`$SKILL_TMP_DIR` 运行环境纪律。
6. 两个确认门用 `present_options` 工具出选项卡片（web 端可点击、渠道端打字回数字）。

### v1.0.0（2026-09 初版）

六步直通流程：analyze → 拆分裁决 → 样板覆盖 → probe 编条目 → generate → validate。
