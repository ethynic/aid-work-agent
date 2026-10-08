# items.json 数据格式与编制要点（数据驱动版）

> 本格式下，生成器**不设任何固定的空间/大类/文件清单**。空间、大类、文件数全部由
> 条目数据里的 `space` / `theme` / `category` 三个维度**动态发现**决定。你只需把每一条
> 目正确打上这三个标签，文件拆分与命名会自动完成。

## 顶层结构

```jsonc
{
  "project": {                       // 抬头区（第1-5行 D列）
    "title": "Material & Product Quotation List  /  室内材料及产品报价清单",
    "project": "项目名（与 PDF 一致；analyze_pdf 可自动取）",
    "project_no": "编号（无则填 -）",
    "issue_date": "16 SEP 2026"
  },
  "parameters": { ... },             // 数量联动参数（见下）
  "precise_images": { "HR-1601": ["x", 18, 421] },   // 条目编号 -> 精确图片 xref
  "page_renderings": { "32": 165 },                   // 页号 -> 该页主视觉 xref（空间效果图）
  "disable_auto_image": true,   // 可选：关闭"标题就近/页面最大图"自动取图（矢量平面图场景用）
  "items": [ /* 见下，每条带 space/theme/category 三个维度 */ ]
}
```

## 三个核心维度（决定拆分）

每个 `item` 必须带：

| 字段 | 含义 | 取值规则 |
|---|---|---|
| `space` | 空间名 | **直接取设计手册里的真实空间名**（HappyRoom / Public Areas / F&B / Façade / Bathroom Pod …），不要自行翻译或缩写。同一个空间的所有条目用同一字符串 |
| `theme` | 大类（报价拆分维度） | 用户指定的报价大类，如 `外立面与室外` / `硬装材料` / `家具软装` / `机电与设备` / `艺术品` … |
| `category` | 产品类别 | 文件内再分区用，如 `建材 Building Materials` / `家具 Furniture` / `灯具 Lighting` / `窗帘 Curtains` / `饰品 Decor` / `标识 Signage` / `洁具五金 Bathroom Fixtures & Fittings` / `五金 Ironmongery` / `景观 Landscape` / `机电设备 MEP` |

动态规则（生成器自动执行，无需你写）：

1. 扫描所有 `item.space` → 得到**空间集合**。
2. 扫描所有 `item.theme` → 得到**大类集合**。
3. 按 `(space, theme)` 分组 → 仅当该组合**含有真实产品**（非 TBC 占位）时才生成文件
   ` {space}_{theme}.xlsx`。**整类无真实产品（如 艺术品 只有 TBC 占位）则不生成任何文件**。
4. 文件内按 `category` 分区（■），每类别「小计」，文件末「合计总金额」=SUM(各类别小计)。

> 因此：空间名 = 文档真实名；大类/类别 = 你按需求设定的标签；文件数 = 非空 (空间×大类) 组合数。
> 源文档变更（新增空间、某大类无产品）后，重跑脚本即自动增减文件。

## TBC 占位条目

尚待深化、暂无可报价真实产品的条目，加 `"tbc": true` 且**不写 `qty_rule`**：

```jsonc
{"code": "FF-HR-C01", "cn": "客房窗帘（待定）", "en": "Guestroom Curtains (TBC)",
 "space": "HappyRoom", "theme": "家具软装", "category": "窗帘 Curtains",
 "tbc": true, "uom": "Sq.M", "basis": "手册未指定；待软装深化", "vendor": "TO BID",
 "ref": "2.4 (待深化)"}
```

生成器：**TBC 条目只在它所属 (space,theme) 组合本身已含真实产品时才被列出**；若某
(space,theme) 组合只有 TBC 条目，则该文件不生成（即"无产品不生成"）。

## 参数表 parameters（数量自动计算的关键）

```jsonc
"parameters": {
  "客房总数": {"value": 500, "remark": "待业主提供；填写后所有「每间客房×」条目自动联动"},
  "大堂面积": {"value": 200, "remark": "手册 3.7：180–220 ㎡，取中值"},
  "走廊面积": {"value": null, "remark": "待按平面图填写"}
}
```

生成器会在每个工作簿建「参数 Parameters」页，主表数量列用公式引用这些单元格
（如 `=IF('参数 Parameters'!$B$9="","",'参数 Parameters'!$B$9*(1+0.05))`）。
**参数留空 → 该行数量显示空白；填入后全表联动重算**。

## 条目算量规则 qty_rule

| mode | 字段 | 生成的数量 |
|---|---|---|
| `fixed` | `value` | 直接写死数值（如跑步机 3 台） |
| `per_room` | `per`、`param`(默认"客房总数")、`waste` | `客房总数 × per × (1+waste)` |
| `area` | `param`、`waste`、`multiply` | `面积 × multiply × (1+waste)` |
| `expr` | `expr`，参数用 `{参数名}` 占位 | 自定义表达式，自动替换并加空值保护 |

未写 `qty_rule` 时，脚本会按文本自动识别：

- 「每间客房 2 个」「每个卫生间 1 套」→ `per_room`
- 「3 台」「×4」「数量 1」→ `fixed`
- 单位为面积但识别不出空间 → 保持空白（不臆造面积）

**识别不出来的条目宁可留空**，也不要编造面积或房量。

## item 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| code | ✓ | 编号。前缀按空间自取（HR/BP/PA/FB/EX/MT…）+ 分组 + 流水 |
| space | ✓ | 文档真实空间名（维度①） |
| theme | ✓ | 大类名（维度②） |
| category | ✓ | 产品类别（维度③，文件内分区用） |
| cn / en | ✓ | 中文名 / 英文名。**en 要能用 `page.search_for` 在 PDF 里搜到**，图片自动匹配依赖它 |
| uom | ✓ | Sq.M / Meter / Each / Set / Lot / Pair |
| basis | ✓ | 数量计算依据：写清推导规则，如"按墙面展开面积+5%损耗" |
| vendor | ✓ | 手册认可供应商 + 备选；无指定写 "TO BID" |
| spec | ✓ | 英文规格要点数组，逐条成 bullet |
| cn_note |  | 中文说明，一行讲清关键规格 |
| scope |  | 甲供 Owner Supplied / 乙供 Contractor / 待确认 TBC |
| ref | ✓ | 依据章节页码，如 "4.7 (P.42)"；页码用于图片兜底与复核 |
| img |  | 明确知道图片时填；不填则走三级策略 |
| qty_rule |  | 见上；留空则文本自动识别 |
| tbc |  | true = 待深化占位（不计入"是否生成文件"，数量留空） |

## 图片引用 ref 的三种写法

| 写法 | 含义 | 适用 |
|---|---|---|
| `["x", 页号, xref]` | 精确某个图片对象（栅格） | 手册里能定位到具体图 |
| `[页号, 序号]` | 该页第 N 张（0 起，排除右上角 logo） | 同页多图、按顺序取 |
| `["r", 页号, (x0,y0,x1,y1)?]` | **栅格化 PDF 矩形区域**（矢量平面图无图片对象时用）；省略坐标=整页 | 建筑平面图、矢量图集 |

> 矢量图集（如住宅公寓楼）：平面图是矢量绘制，没有可抽取的栅格图片对象。
> 此类项目应：① 在代表页条目上用 `["r", 页号] 直接栅格化平面图作为图片；
> ② 顶层加 `"disable_auto_image": true` 关闭自动取图，否则同一张图会被贴满每一行。

## 套数表驱动型项目（图纸集 / 公寓，schedule_driven）

`analyze_pdf.py` 判为 `schedule_driven` / `drawing_set` 时，按此范式构建数据（仍走数据驱动）：

1. `space` = 户型/单元类型（Studio / 1-bed / 2-bed / …）；`theme` = 你指定的大类（如 `硬装材料`）。
2. `parameters` = 各户型套数（从公寓套数一览表读，如 `Studio_units: 87`），来源写进 `remark`。
3. `qty_rule` 用 `expr`：`{"mode":"expr","expr":"{2bed_units}*2"}`，生成器自动替换为参数页单元格并加空值保护。
4. 平面图取图：代表条目写 `img: ["r", 页号]`，顶层加 `"disable_auto_image": true`。
5. 品牌/单价留空，由供应商填写；不要编造。

## 编制条目时的取舍

- **只收录可报价的实物/工序**：性能指标（防火等级、STC、水流量限值）可单独成行但标注"不单独计价"。
- **覆盖三类内容**：空间章节给"做法"（墙/地/顶/门/固定家具/设备），附录给"品牌型号"，技术要求章节给"合规约束"。
- **手册写死的量直接进 basis**：如"3 台跑步机""1 套主入口图腾"，便于后续直接填 Qty。
- **反复出现的通用做法重复列出**：同一材料在多个空间出现时，在各空间文件都列一行，方便分包报价。
- **甲供项不要删**：保留但标注 Owner Supplied，供业主最后确认报价边界。
- **空间名务必来自文档**：HappyRoom 不要写成"标间"，Public Areas 不要写成"公区"——AI 不得自行翻译或缩写。
