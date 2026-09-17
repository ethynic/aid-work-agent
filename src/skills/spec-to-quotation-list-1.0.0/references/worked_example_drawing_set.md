# 实战示例三：纯矢量图纸集（drawing_set，无套数表的图纸集）

本文件**仅供参考**，用于理解「第三类文档」的处理范式。新项目请重新用
`scripts/analyze_pdf.py` / `scripts/probe_pdf.py` 核对，不要照抄页码。

`drawing_set` 与 `schedule_driven`（见 `worked_example_schedule_driven.md`）**取图方式完全一致**
（矢量平面图 → 栅格化 + `disable_auto_image`），区别在**拆分维度与数量来源**：

- `schedule_driven`：住宅/公寓，有「套数一览表」，按**户型**拆、套数进参数页。
- `drawing_set`：商业/办公/外立面/公共建筑等**没有可识别的单元套数表**，文字稀疏、大量矢量绘制；
  按**区域/楼层/单元**拆，数量多来自面积或比例参数（常需人工补）。

## 1. 文档类型识别

`analyze_pdf.py` 对本类文档的输出：`doc_type = drawing_set`
（单页平均文字少、矢量绘制页占比高、未命中套数一览表）。

```
文档类型 : drawing_set {'avg_chars_per_page': 120.5, 'vector_pages': 41}
  → 处理范式: 图纸集（矢量平面，按区域/楼层拆分，栅格化取图）
建议拆分：按区域_楼层拆分_报价清单.xlsx  区域/单元（图纸集驱动）
      ↳ 此文档为矢量图纸集：产品多为图纸标注，无套数表。请按区域/楼层/单元拆分，
        矢量平面图用 img=["r",页号] 栅格化，并设 disable_auto_image=true。
```

若文档其实藏着套数一览表（如 Loft/公寓），`detect_schedule` 会优先标 `schedule_driven`，
**以套数为准**走示例二范式。

## 2. 拆分维度

没有套数表时，按图纸自身结构拆：

- 图纸集常有清晰的「区域/楼层/单元」标注（如图签 title block、分区字母、楼层号）。
- 让 `analyze_pdf.py` 先列出章节/页区间，再人工按**可独立报价的交付包**归并：
  例如「塔楼标准层」「裙楼商业」「地下室机电」「外立面幕墙」各一文件。
- 供应商完全不同的包要拆开（如幕墙是专业分包，与室内硬装不是一个报价主体）。

## 3. 矢量图取图

与示例二完全相同的两招：

1. 代表条目写 `img: ["r", 页号]`（可带坐标 `["r", 页号, x0,y0,x1,y1]` 只裁某张图）。
2. 顶层 `"disable_auto_image": true` → 每份文件恰好 1 张代表图，不贴满每行。

若某页本身是栅格效果图（非矢量），可在该条目用 `["x", 页号, xref]` 精确取图，
其余仍走 `["r",页号]`。

## 4. 数量来源（面积 / 比例 / 固定）

`drawing_set` 没有「每间客房 N 个」这类可直读的量，数量公式以**面积与比例参数**为主：

```jsonc
"parameters": {
  "塔楼标准层面积": {"value": 1200, "remark": "标准层平面图实测，待按最终版填"},
  "幕墙面积":       {"value": null, "remark": "待按立面图算，留空则数量留空"}
}
```

```jsonc
// 按面积铺贴
{"code": "FA-2101", "cn": "塔楼公区地坪", "en": "Tower common-area flooring", "uom": "Sq.M",
 "qty_rule": {"mode": "area", "param": "塔楼标准层面积", "multiply": 1.0, "waste": 0.05}}

// 按图纸标注的固定数量
{"code": "FA-3301", "cn": "主入口旋转门", "en": "Main entrance revolving door",
 "qty_rule": {"mode": "fixed", "value": 2}}

// 比例换算（套数驱动同款 expr，参数来自图纸统计而非套数表）
{"code": "FA-4101", "cn": "标准层电梯厅标识", "en": "Lift-lobby signage per floor",
 "qty_rule": {"mode": "expr", "expr": "{楼层数}*1"}}
```

**参数缺失就留空**（浅黄底），推导依据写进 `basis` 列；不得凭空假设面积或层数。

## 5. 与另两类的对照

| 场景 | doc_type | 拆分维度 | 数量主来源 | 取图方式 |
|---|---|---|---|---|
| 规范书 | spec_book | 业态/空间 | 正文（per_room/area/fixed） | 抽栅格产品图（xref 三级匹配） |
| 套数表驱动 | schedule_driven | 户型/单元 | 套数一览表 → 参数页（expr） | 矢量栅格化 + 关自动取图 |
| 纯矢量图集 | drawing_set | 区域/楼层/单元 | 面积/比例/固定参数 | 矢量栅格化 + 关自动取图 |

三者共用同一套生成器与 `items.json` 数据模型，区别只在「识别 → 拆分 → 算量 → 配图」四步的策略。
