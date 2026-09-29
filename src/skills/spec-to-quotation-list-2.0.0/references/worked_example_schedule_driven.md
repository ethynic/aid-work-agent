# 实战示例二：套数表驱动型（schedule_driven，住宅/公寓图纸集）

本文件**仅供参考**，用于理解「第二类文档」的处理范式。新项目请重新用
`scripts/probe_pdf.py` / `scripts/analyze_pdf.py` 核对，不要照抄页码与套数。

规范书型（spec_book，见 `worked_example_spec_book.md`）文字多、含品牌产品图、数量从正文读。
本类（schedule_driven）是**图纸集 / 套数表驱动**：矢量平面图 + 公寓套数一览表，
无品牌无单价。`analyze_pdf.py` 会自动区分二者并给出不同建议。

## 1. 文档类型识别

`analyze_pdf.py` 对本类文档的输出：

```
文档类型 : schedule_driven {'schedule_pages': [3, 2, 4, 5, 8]}
建议拆分：按户型_单元拆分_报价清单.xlsx  住宅户型/单元（套数表驱动）
      ↳ 此文档为套数表/图纸集驱动型：产品多为少量图例，数量由「套数一览表」
        （疑似 P.3）驱动。请读取该页套数，按户型/单元拆分文件……
```

判定依据：单页出现 ≥5 个单元编码（如 `02A1_1`…）。
规范书类文档单元编码为 0，不会被误判。

## 2. 项目识别

- 此类文档常是外文建筑图集（公寓单元平面图 + 家具洁具图例 + 公寓一览表）。
- **没有品牌 / 单价 / 产品详述** → 这些列留空待供应商填，不要编造。

## 3. 关键决策

| 决策 | 做法 | 原因 |
|---|---|---|
| 拆分维度 | **按户型**（Studio / 1-bed / 2-bed / 3-bed / 4-bed）各一文件 | 户型是此文档天然「空间类型」；套数一览表正是按户型统计 |
| 条目来源 | 图例页「标准户型家具洁具图例」 | 全项目标准配置，按房间分组（厨房/卫浴/起居/卧室） |
| 数量来源 | 「公寓套数一览表」的套数 | 数量 = 套数 × 每套件数 |
| 套数 | 写入「参数 Parameters」页 | 改一处，全表联动 |
| 平面图 | 矢量绘制，无栅格图片对象 | 见第 4 节 |

## 4. 矢量平面图的取图技巧（核心坑）

此类文档的平面图是 **矢量绘制**（`get_drawings()` 有内容，`get_image_info` 几乎无栅格对象），
无法像规范书那样抽取图片 xref。两招：

1. **栅格化区域**：在代表条目上写 `img: ["r", 页号]`（省略坐标=整页），生成器用
   `page.get_pixmap(clip=rect, dpi=100)` 把该页矢量内容渲染成 JPEG 嵌进去。
2. **关闭自动取图**：顶层加 `"disable_auto_image": true`。否则自动取图会把同一张
   平面图（或某页的栅格图）贴满每一行。配合「仅代表条目设 `img`、文件不设 `default_img`」，
   每份文件恰好嵌入 1 张代表平面图。

> 纯矢量图纸集（drawing_set，见 `worked_example_drawing_set.md`）同样用
> `["r", 页号] + disable_auto_image` 处理。

## 5. 数量公式（套数驱动）

参数页（来自一览表）：

```jsonc
"parameters": {
  "Studio_units": {"value": 87, "remark": "Studio / 1-room+K 套数（公寓一览表 P.2）"},
  "1bed_units":   {"value": 35, "remark": "1-bedroom 套数（P.2）"},
  "2bed_units":   {"value": 83, "remark": "2-bedroom 套数（P.2）"},
  "3bed_units":   {"value": 7,  "remark": "3-bedroom 套数（P.2）"},
  "4bed_units":   {"value": 1,  "remark": "4-bedroom 套数（P.2）"}
}
```

条目数量用 `expr` 模式，参数以 `{参数名}` 占位（生成器自动替换为参数页单元格引用并加空值保护）：

```jsonc
{"code": "ST-K01", "cn": "灶具（小厨房单元）", "en": "Cooker – small kitchen unit",
 "uom": "EA", "basis": "每户 1 套", "qty_rule": {"mode": "expr", "expr": "{Studio_units}*1"}}
{"code": "2B-R03", "cn": "衣柜 深600", "en": "Wardrobe depth 600",
 "uom": "EA", "basis": "每户 2 组（按卧室数）", "qty_rule": {"mode": "expr", "expr": "{2bed_units}*2"}}
```

生成后主表 E 列为 `=IF('参数 Parameters'!$B$4="","",'参数 Parameters'!$B$4*1)`，
填了套数即全表联动。

## 6. 卧室家具的配置假设（需用户确认）

文档只给「标准户型图例」，未明确每卧室床/衣柜/书桌数。采用的假设：

- 双人床 `double`、单人床 `single` 按户型给定（如 2-bed：1 双人 + 1 单人）；
- 衣柜、书桌 **每卧室 1 组/张**（数量 = 卧室数）；
- Studio 用翻折壁床带沙发（无独立卧室）。

这些假设写进 `basis` 列并可在 Notes 说明，用户若调整只需改数据里的 `TYPES` 再重生成。

## 7. 踩过的坑

- **`analyze_pdf` 初版把图纸集误判成单一「其他空间」**：因为按章节归类的角色识别对
  图纸集无效。修复后改为先 `detect_schedule`，命中即标 `schedule_driven` 并提示按户型拆分。
- **自动取图贴满每行**：矢量图集没有可信的产品图，必须 `disable_auto_image` + 仅在代表条目设 `img`。
- **数量写死会失真**：套数后续可能调整，所以一律走参数页公式，绝不用死数。
