# 实战示例一：FF&E 规范书（spec_book，约 80–90 页的酒店/商业规范书）

本文件**仅供参考**，用于理解「规范书型」文档的处理范式。新项目请重新用
`scripts/probe_pdf.py` 核对页码与 xref，不要照抄下面的具体数值。

## 1. 文档类型识别

`analyze_pdf.py` 对规范书类文档的输出：`doc_type = spec_book`。
特征：文字多、含品牌产品图与规格表，数量可从正文读出。

```
文档类型 : spec_book {avg_chars_per_page: 540.2, vector_pages: 0}
  → 处理范式: FF&E 规范书（按业态拆分，数量从正文读）
建议拆分：01_客房报价清单.xlsx 客房 … / 02_公共区域报价清单.xlsx 公共区域 …
```

## 2. 项目识别

- `project_name` 通常能自动取到（封面文字或高频页眉），必要时用
  `--project-name "..."` 覆盖。
- 目录：很多规范书 PDF 的书签是 "Slide N" 占位符，脚本会自动回退到
  「目录页标题+页码」行或正文页眉词频来识别章节。

## 3. 关键页图文结论（示意，实际请重新 probe）

| 页 | 内容 | xref 结论（示意） |
|---|---|---|
| 18 | 灯具与开关插座（与第 68 页复用同批图） | 灯泡 / 书桌台灯 / 床头壁灯 / 单联开关 / 双联开关 / 插座 / 万能插座 |
| 21 | 预制卫浴模块 | 模块照片 |
| 23 | 可补充式洗护瓶 | 洗护瓶 |
| 24 / 25 | 客房用品 | 纸包装 / 牙刷 / 剃须 / 拖鞋 / 洗衣袋 / 衣架 / 浴袍 / 浴巾 / 马克杯 / 托盘 |
| 30 | 主标识 | 入口图腾 / 大堂标识 / 屋顶标识 |
| 59–61 | 客房科技 | 电话 / 壁挂 AP / 门锁 / 电视 / 节能器 |
| 63 | 墙面/地面饰面 | 墙布 / PVC 踢脚线 / 地毯 / 编织墙布 |
| 71–76 | 固定家具照片 | 厨房柜 / 衣柜 / 全身镜 / 书桌 / 行李架 / 床头板 |
| 78 / 79 | 洁具与卫浴五金 | 龙头 / 恒温淋浴柱 / 坐便器 / 台上盆 / 淋浴门 / 纸巾架 / 镜 / 浴巾架 |

## 4. 拆分结果（数据驱动，示意）

本技能**不写死拆分清单**。把每条目打上 `space`(真实空间名) / `theme`(大类) / `category`(产品类别)，
生成器自动：

- 动态发现空间集合与大类集合，按 `(space, theme)` 组合生成 `{space}_{theme}.xlsx`。
- 例如同一本规范书会动态产出：`HappyRoom_硬装材料.xlsx` / `HappyRoom_家具软装.xlsx` /
  `HappyRoom MEP_机电与设备.xlsx` / `Bathroom Pod_硬装材料.xlsx` / `Public Areas_硬装材料.xlsx` /
  `Façade_外立面与室外.xlsx` / `Standards & Suppliers_硬装材料.xlsx` …（具体文件数由数据决定）。
- **卫浴模块 Bathroom Pod 单独成文件**（工厂预制、供应商不同于现场硬装），靠给这些条目打
  `space="Bathroom Pod"` 实现——这是**人工判断 + 数据驱动**的典型场景（不必在代码里写死）。
- **艺术品整类不生成**：若只有 TBC 占位、无真实可报价产品，该 (space,theme) 组合被跳过。
- 配图率 >95%：纯表格/纯文字页（如发电机组、空调、隔声性能、洁具流量限值）确无图，留空或效果图兜底。
- 图片用 JPEG(q80, dpi130)，多个文件合计约 4MB。

## 5. 数量公式（正文驱动）

规范书的数量多能从正文读出，用 `per_room` / `area` / `fixed` 三种规则：

```jsonc
// 每个客房都有的物品 → per_room
{"code": "HR-1601", "cn": "床头壁灯", "en": "Bedside wall sconce",
 "qty_rule": {"mode": "per_room", "param": "客房总数", "per": 2}}

// 按面积铺贴 → area
{"code": "PA-3301", "cn": "大堂地毯", "en": "Lobby carpet", "uom": "Sq.M",
 "qty_rule": {"mode": "area", "param": "大堂面积", "multiply": 1.0, "waste": 0.08}}

// 手册写死的量 → fixed
{"code": "FB-1001", "cn": "主入口图腾", "en": "Main entrance totem",
 "qty_rule": {"mode": "fixed", "value": 1}}
```

未写规则的条目，脚本会按「每间客房 2 个」「3 台」「×4」等文本自动识别；
识别不出且非面积项则留空（浅黄底），推导依据写进 `basis` 列，不得凭空填数。

## 5.1 数据驱动条目写法（本技能标准格式）

每条目带 `space` / `theme` / `category` 三个维度，文件拆分由它们决定：

```jsonc
{
  "project": {"title": "Material & Product Quotation List", "project": "Hotel101", "issue_date": "16 SEP 2026"},
  "parameters": {
    "客房总数": {"value": 500, "remark": "待业主提供；填写后所有 per_room 条目联动"},
    "卧室地面面积": {"value": 11.39, "remark": "手册 2.1：HappyRoom 卧室 11.39 ㎡"}
  },
  "precise_images": {"HR-1103": ["x", 63, 39]},
  "page_renderings": {"32": 165},
  "items": [
    {"code": "HR-1103", "space": "HappyRoom", "theme": "硬装材料", "category": "建材 Building Materials",
     "cn": "卧室地毯", "en": "Bedroom carpet", "uom": "Sq.M",
     "qty_rule": {"mode": "area", "param": "卧室地面面积", "multiply": 1.0, "waste": 0.05},
     "basis": "按卧室地面面积+5%损耗", "vendor": "BANIG / TO BID", "ref": "2.1 (P.11)",
     "spec": ["100% nylon", "6mm membrane backing"]},
    {"code": "HR-2404", "space": "HappyRoom", "theme": "家具软装", "category": "家具 Furniture",
     "cn": "床架（1.5m）", "en": "Bed base (1.5m)", "uom": "Each",
     "qty_rule": {"mode": "per_room", "per": 1}, "basis": "每间客房 1 张",
     "vendor": "U-Choice", "ref": "2.4 (P.21)", "spec": ["solid timber"]},
    {"code": "FF-HR-C01", "space": "HappyRoom", "theme": "家具软装", "category": "窗帘 Curtains",
     "tbc": true, "cn": "客房窗帘（待定）", "en": "Guestroom Curtains (TBC)", "uom": "Sq.M",
     "basis": "手册未指定；待软装深化", "vendor": "TO BID", "ref": "2.4 (待深化)"}
  ]
}
```

→ 生成器动态产出：`HappyRoom_硬装材料.xlsx`（含 HR-1103 等）、`HappyRoom_家具软装.xlsx`
（含 HR-2404 真实家具；`FF-HR-C01` 因同属 家具软装 且含真实产品，作为 TBC 占位列出、
数量留空）；`HappyRoom MEP_机电与设备.xlsx` 等。空间名即文档里的 `HappyRoom`。


## 6. 踩过的坑（通用，适用于所有规范书）

- **同批图片对象跨页复用**：`get_image_info` 的 xref 在两页相同，说明是同一张图。
  若 A 页图文关系读得准、B 页含同样 xref，可用 A 页结论反推 B 页。
- **PDF 自带图片替代文字**：部分 PDF 会把 alt text（"A light bulb with a green base"）
  渲染在图旁，其坐标与图片重合，可直接反推图中内容（在 `get_text("words")` 里可见）。
- **Ref. 里的页码写法不统一**（`P.63` / `P.16, 35` / `P.63-66`），解析时要全部兼容，
  否则图片兜底会漏。
- **PNG(150dpi) 让文件膨胀到 40MB**，改成 JPEG(130dpi/q80) 后降到约 4MB。
