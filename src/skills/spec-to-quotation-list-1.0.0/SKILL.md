---
name: spec-to-quotation-list
description: 把任意设计手册 / 设计规范 / FF&E 规范 / 材料 spec book PDF 转成中英双语「室内材料报价清单」Excel，按项目实际业态（客房、公共区、餐饮、外立面、后勤、材料标准等）动态拆分成多个文件，从 PDF 抠取产品图或空间效果图嵌入图片列，并用参数表驱动数量自动计算（数量写成引用参数单元格的公式，填入房量/面积后全表联动重算），单价留空待供应商填写。This skill should be used when the user provides a design specification PDF (plus optionally an Excel template) and asks for a material / FF&E / interior quotation list, or asks to split a quotation list by room type or area.
metadata:
  version: "1.0.0"
  author: aid-work-agent
dependencies:
  - fitz
  - openpyxl
---

# 设计手册转材料报价清单（通用版）

## 用途

把设计规范 PDF 转成可直接发供应商询价的 Excel 报价清单。
**项目名、业态分类、文件拆分方案一律从用户传入的 PDF 动态推导**，不预设酒店/住宅/办公等任何业态。

## 运行环境注意（必读）

- **每次 skill_execute 调用都在独立的临时工作目录中执行**，调用间不共享 cwd。所有中间产物（analysis.json、items.json、生成的 xlsx）**必须用绝对路径**读写，推荐统一放在 `$SKILL_TMP_DIR` 下的一次性子目录中（环境变量已注入）：
  ```bash
  OUT=$SKILL_TMP_DIR/spec2quote_$(date +%s)
  mkdir -p $OUT
  python scripts/analyze_pdf.py <pdf> --json $OUT/analysis.json
  ```
  下一步引用上一步产物时，显式写出 `$OUT/analysis.json` 等绝对路径，不要依赖"上一步的当前目录"。
- 用户上传的 PDF 路径从会话附件上下文获取（绝对路径），直接传给脚本即可。
- 大段 items.json 内容**不要内联在 bash 命令里**（转义和长度都易出错）：先用文件写入方式落盘（如 `cat > $OUT/items.json`，内容走 skill_execute 的代码块通道），或分批小文件追加，再传给生成脚本。
- 生成的 xlsx 交付给用户时，**逐个文件**调用 cp 工具注册下载：
  `cp(source_file_path="$OUT/NN_业态_报价清单.xlsx", display_name="NN_业态_报价清单.xlsx", register_download=True)`

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/analyze_pdf.py` | 识别项目名、**文档类型**（spec_book / schedule_driven / drawing_set）、章节结构、套数一览表候选页，给出拆分建议 |
| `scripts/probe_pdf.py` | 逐页列出图片 xref/坐标与按行聚类的文字，用于图文配对 |
| `scripts/merge_items.py` | 合并分批编写的条目分片（`$OUT/parts/*.json`）为完整 items.json，含编号冲突硬校验 |
| `scripts/generate_quotation_lists.py` | 依据 items.json 生成 xlsx：抬头/分组/数据行/图片/参数页/数量公式/Notes；支持 `--size-budget-mb` 体积预算自动降质 |
| `scripts/validate.py` | 生成后自动校验：3 表结构、表头、数量联动参数页、合价公式、图片数、体积，输出「已算量/总条目」 |

## 流程

### 0. 先判文档类型（决定后续范式）

用 `analyze_pdf.py` 跑一遍，**先看输出的「文档类型」这一行**，它决定整个处理方式：

| 文档类型 | 特征 | 处理范式 | 实战示例 |
|---|---|---|---|
| `spec_book` | 文字多、含品牌产品图与规格表，数量可从正文读 | 按业态拆分（客房/公共/餐饮/外立面/后勤/材料），数量用 `per_room`/`area`/`fixed` 从正文算 | `references/worked_example_spec_book.md` |
| `schedule_driven` | 存在「套数一览表 / 户型清单」（单页 ≥5 个单元编码），产品只是少量图例 | 按户型/单元拆分，套数进参数页，条目数量用 `expr` 公式 = 套数 × 每套件数；矢量图用 `img=["r",页号]` + `disable_auto_image` | `references/worked_example_schedule_driven.md` |
| `drawing_set` | 文字稀疏、大量矢量绘制（平面图/户型图），无栅格产品图、也无套数表 | 按区域/楼层/单元拆分，矢量图用 `img=["r",页号]` + `disable_auto_image`；数量多来自面积/比例/固定参数 | `references/worked_example_drawing_set.md` |

> 不要跳过这步直接套规范书流程：`schedule_driven` / `drawing_set` 若按业态章节拆分，
> 会被误分成一个无意义的「其他空间」文件（已修复：命中套数即标 schedule_driven，
> 否则矢量密集即标 drawing_set，并改提示「按户型/区域拆分」）。

### 1. 解析 PDF 结构（决定"这是什么项目、拆几个文件"）

```bash
python scripts/analyze_pdf.py <pdf> [--json $OUT/analysis.json]
```

脚本会输出：

| 输出 | 用途 |
|---|---|
| `project_name` | 项目名（PDF 元信息 → 封面最长行 → 全书最常见页眉 → 文件名，逐级回退） |
| `sections` | 顶层章节及其起止页、图片数（目录识别：PDF 书签 → 目录页「标题+页码」→ 正文页眉词频） |
| `suggested_files` | 按业态归并后的建议文件（客房/公共区域/餐饮/外立面及室外/后勤与机电/材料标准与供应商/其他空间） |

**脚本建议只是起点**，需按下面规则人工裁决：

- 章节数 > 8 时合并同类（脚本已按业态归并，可再合并）；章节数 < 3 时改用"楼层/户型/材料类别"拆。
- **供应商完全不同的包要拆开**：如酒店里"卫浴模块"单独成文件（工厂预制，供应商是 pod 厂商，
  与现场硬装不是一个报价主体）。参考 `references/worked_example_spec_book.md`。
- 纯文字章节（品牌概述、安全、环保、技术）一般**不产生报价条目**，只在 Notes 里作约束说明。
- 附录/材料标准章节合并成一个"材料标准与供应商"文件，作为全项目通用标准。
- 文件命名用 `NN_<业态名>_报价清单.xlsx`；条目前缀按业态取 2 个大写字母
  （客房 HR / 卫浴 BP / 公共 PA / 餐饮 FB / 外立面 EX / 材料 MT，其他业态自取）。
- 住宅/公寓类（`schedule_driven`）按**户型**拆分（Studio / 1-bed / 2-bed / …），
  前缀自取（如 ST / 1B / 2B），套数进参数页、数量用 `expr` 公式——见
  `references/worked_example_schedule_driven.md`。
- 纯矢量图纸集（`drawing_set`，无套数表）按**区域/楼层/单元**拆分，数量多来自面积/比例参数，
  见 `references/worked_example_drawing_set.md`。
- 三类文档的完整实战见 `references/worked_example_spec_book.md`（规范书）、
  `references/worked_example_schedule_driven.md`（套数表驱动）、
  `references/worked_example_drawing_set.md`（纯矢量图纸集）。

### 2. 读样板（给了样板才做）

用 openpyxl 逐格读取用户给的 Excel 样板（合并范围、表头、列宽、字号、填充、边框），
覆盖脚本默认值。细节见 `references/output_format.md`；没给样板就按该文档的 HBA FF&E 默认风格生成。

### 3. 通读内容、编条目

```bash
python scripts/probe_pdf.py <pdf> --all        # 图片普查
python scripts/probe_pdf.py <pdf> 20 21        # 某页的图片 + 文字行
```

按第 1 步确定的文件/分组，把条目写成 `items.json`（结构见 `references/items_json_schema.md`）。
条目的 `en` 要能在 PDF 里被 `search_for` 命中——图片自动匹配依赖它。
没给 `code` 时脚本会按 `code_prefix-分组序号+流水` 自动生成。

**大项目必须分批编条目**（条目总数 > 50 或拆分文件 > 3 时）：

1. 每个拆分文件一个分片，落在固定分片目录：`$OUT/parts/NN_<业态名>.json`（NN 用拆分序号）。
   分片 schema 与完整 items.json 相同，但顶层 `files` 只含**一个**文件对象，只带该文件用到的
   `parameters` / `precise_images` / `page_renderings`。
2. 每轮只编一批（10~25 条），编完立即落盘并简报进度（本批条目数 / 累计条目数），不要在
   上下文里长期持有已完成分片的内容。
3. 全局参数（客房总数等跨文件共用的）只在第一个分片定义一次，后续分片不得重定义。
4. 全部分片完成后合并（编号冲突会报错，按提示修改分片）：

```bash
python scripts/merge_items.py --parts-dir $OUT/parts --out $OUT/items.json
```

**数量要尽量算出来，不要整列留空**：

1. 在 `parameters` 里集中列出驱动量（客房总数、各空间面积、损耗率等）；能从手册里读到的
   （如"标准户型 21 ㎡""大堂 180–220 ㎡"）直接填中值并注明来源，读不到的留 `null`。
2. 给条目写 `qty_rule`（`fixed` / `per_room` / `area` / `expr`）。脚本对未写规则的条目会按
   "每间客房 N 个""3 台"等文本自动识别，识别不出才留空。
3. 生成的数量是**引用参数页的公式**，不是死数——业主后续给出房量/面积后，改参数页一处即可全表重算。

### 4. 配图

按 `references/pdf_image_matching.md` 确定关键页图文对应关系，结论写进
`precise_images`（编号 → `["x", 页号, xref]`）与 `page_renderings`（页号 → 主视觉 xref）。
脚本内置三级取图：条目图 → precise_images → 标题自动匹配 → 章节页最大图兜底 → 文件兜底。

### 5. 生成

```bash
python scripts/generate_quotation_lists.py --pdf <spec.pdf> --items $OUT/items.json --out $OUT/ \
       [--analysis $OUT/analysis.json] [--project-name "项目名"] [--issue-date "16 SEP 2026"] \
       [--size-budget-mb 15]
```

`--analysis` 会在 items.json 未填项目名时自动填入 PDF 识别出的项目名。
条目多、图片多的项目**建议直接带 `--size-budget-mb 15`**：生成后单文件超预算时自动降质
重嵌（dpi 与 JPEG 质量各乘 0.8，最多 4 轮，下限 60），无需手工干预；也可用 `--dpi` /
`--jpg-quality` 一开始就压低质量。

### 6. 校验与交付

**先跑自动校验**（必做，替代手工逐文件 reopen）：

```bash
python scripts/validate.py $OUT/ [--max-mb 20]
```

脚本逐项检查：3 表结构（主表→`参数 Parameters`→`Notes 说明`）、表头齐全、
数量列是否引用参数页、合价 `=IF(OR(E="",I=""),…)` 公式、图片数、文件体积，
并汇总「已算量 / 总条目」。返回非 0 表示有待处理项。

**校验通过后交付**：逐个文件调用 cp 工具注册下载回传给用户（见「运行环境注意」）。

手工复核重点（自动校验兜不住的）：

- **参数页**：参数齐全、带来源说明；留空的参数有明确 remark 说明待谁提供
- **数量列**：抽查公式指向的参数单元格是否正确（如"大堂地毯"是否引用了大堂面积）；
  统计"已算量 / 总条目"并如实告知用户哪些条目仍为空、缺少什么数据才能算出
- 体积（>20MB 时按提示用 `--size-budget-mb` 重新生成，脚本会自动降质重嵌）
- 自动校验若报「数量公式未引用参数页」——通常是 `items.json` 里 `expr`/`area`
  的 `{参数名}` 与 parameters 的 key 不一致，或用了死数而非参数；据此回改数据重生成

## 输出结构

每个工作簿 3 张表：

1. 主表——抬头区、按业态分组的数据行、图片列、Amount 公式、Total 合计行
2. `参数 Parameters`——驱动数量计算的参数（改一处，全表联动）
3. `Notes 说明`——编制依据、甲供乙供、品牌替代、待业主补充信息、本文件涵盖范围

格式约定见 `references/output_format.md`。

## 硬约束

- **不编造数量**：参数缺失时该行数量留空（浅黄底），推导规则写进 K 列「数量计算依据」；
  能算的一律用参数公式算出来，但**不得凭空假设面积或房量**。
- **甲供项要标注**：手册写明 Owner Supplied 的项标注供货责任，避免报价边界含糊。
- **图片宁缺勿错**：图文关系无法确证时用空间效果图兜底或留空。
- **每条可追溯**：必须有 `ref`（章节 + 页码）。
- **动态优先于模板**：识别出与示例不同的业态时，按实际结构重写拆分方案和前缀，不要套用示例。
- 复杂中文补丁写成 `.py` 文件执行，避免 bash heredoc 转义问题。
