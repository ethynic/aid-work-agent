# 数据契约：quotes.json / comparison.json / marks.json

所有价格进入比价前必须先变成同一结构。字段名固定，缺字段用空串 / `null` / 默认值补齐，
不要把「列序号」当成字段，也不要把不同来源写成不同 schema。

## 1. quotes.json 顶层

```json
{
  "generated_at": "2026-11-10",
  "defaults": {"currency": "CNY", "tax_included": true, "tax_rate": 0.13},
  "files": ["甲公司报价.xlsx", "乙公司报价.xlsx"],
  "warnings": ["...解析提示..."],
  "item_count": 12,
  "items": [ { /* 报价行 */ } ]
}
```

## 2. 报价行 items[] 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `item_id` | str | 自动 | `extract_quotes.py` 生成 `q0001`…，手工 JSON 可留空 |
| `supplier` | str | ✓ | 供应商名 / 资料库文档名 / 网站名（+「(参考)」后缀）。**同名来源会合并成一列** |
| `source_type` | str | ✓ | `upload`（用户上传）/ `library`（资料库）/ `web`（互联网） |
| `source_file` | str | | 原始文件名；`library`/`web` 可留空 |
| `source_ref` | str | | 资料库文档名/条目 ID，或网页完整 URL |
| `source_date` | str | ✓ | 报价日期或采集日期 `YYYY-MM-DD`；缺失会填运行当天，需在摘要提示 |
| `code` | str | | 供方物料编号；跨供应商编号通常不同，不作为唯一匹配依据 |
| `name` | str | ✓ | 品名/产品名称 |
| `spec` | str | | 规格 / 型号 / 材质 / 尺寸；**影响价格的关键参数必须填** |
| `brand` | str | | 品牌 |
| `qty` | number | | 数量。**恒为 `qty_uom` 计量单位下的数量**（与 `uom` 解耦） |
| `qty_uom` | str | | 数量单位，默认同 `uom`。单价按元/吨、数量按千克时填「千克」 |
| `qty_to_price_uom` | number | | **跨量纲时必填**：1 个数量单位 = 多少个价格单位（1 包 = 0.05 箱 → 0.05）。同量纲不需要填 |
| `uom` | str | ✓ | 单价的计价单位（**照抄源单，不要预先换算**） |
| `standard_uom` | str | | 归一后的单位。**由脚本按组自动判定**，人工填仅作参考；不同量纲不会自动合并 |
| `uom_known` | bool | | 单位别名是否被识别；未识别时人工填 `standard_uom` |
| `unit_price` | number/null | ✓ | 原始单价，正数；`quote_status=no_quote` 时为 `null` |
| `price_raw` | str | | 价格单元格原文（如 `¥4,200.00`），用于回写定位 |
| `currency` | str | ✓ | `CNY/USD/EUR/GBP/JPY/HKD` |
| `tax_included` | bool | ✓ | 单价是否含税 |
| `tax_rate` | number | | 税率，`0.13` 表示 13%（填 `13` 也会被归一化） |
| `unit_multiplier` | number | | **默认留空**。仅当脚本无法自动判定的商定折算才填（如"480元/箱 ÷ 20包"=24元/包填 0.05）；**填了必须同时换算数量** |
| `amount_reported` | number | | 源报价单上的金额列数值，用于「单价×数量 vs 源单金额」交叉自检 |
| `amount_tax_included` | bool | | 源单金额列的税基（含税/未税）。缺省视为与 `tax_included` 同口径 |
| `quote_status` | str | | `quoted`（正常）/ `no_quote`（源单未报出价格，如"面议""暂不报价"）。`no_quote` 条目参与品项集合但不计价 |
| `quote_note` | str | | `no_quote` 的原因原文（取自备注/价格单元格） |
| `note` | str | | 备注：价格类型、是否含运费、最小起订量等 |
| `match_key` | str | | 显式分组键；填写后跳过自动匹配，同一 `match_key` 视为同一品项 |
| `match_confidence` | number | | 匹配置信度，默认 1.0 |
| `locator` | object | ✓ | 回写标色定位，见第 3 节 |

## 3. locator 定位对象（标色的关键）

| kind | 必需字段 | 说明 |
|---|---|---|
| `xlsx` | `file`, `sheet`, `row`, `price_col`, `price_text` | 行列均 1-based；也可用 `cell`（如 `F5`）覆盖 |
| `csv` | `file`, `row`, `price_col`, `price_text` | 会转成带色 xlsx 副本 |
| `docx` | `file`, `price_text`，可选 `table`/`row`/`price_col` | 优先按表格单元格定位，否则全文按价格文本高亮 |
| `pdf` | `file`, `price_text`，可选 `page`/`bbox` | `page` 1-based；`bbox=[x0,y0,x1,y1]` 为 PDF 坐标 |
| `image` | `file`, `bbox=[x0,y0,x1,y1]` | 像素坐标，画半透明红/绿框 |
| `none` | — | 资料库/网络价、或无法定位的条目；不参与源单标色 |

`file` 一律写**绝对路径**。`price_text` 写报价单里出现的价格原文（如 `4,200.00`），
标色脚本用它反查单元格 / `page.search_for()` / docx run 文本。

## 4. 归一化规则（compare_prices.py）

按顺序执行，结果写入 `price_norm`；任何一步无法完成 → `price_norm=null` + `norm_reasons`，判定 `uncomparable`。

1. **币种**：`currency != target_currency` 时查 `--fx {"USD":7.15}`，`price_norm = unit_price × rate`。
   无汇率不猜、不联网取实时汇率，直接标不可比。
2. **税务**：默认 `--tax-basis incl`；`tax_included=false` → `×(1+tax_rate)`；
   `--tax-basis excl` 时含税价 → `÷(1+tax_rate)`；`asis` 不折算（仅在所有来源口径一致时使用）。
3. **单位（关键，金额守恒）**：同量纲单位自动换算到**组标准单位**。
   - **组标准单位** = 组内「数量单位」出现次数最多者；并列时取该量纲的基准单位。
     量纲表见 `scripts/qc_common.py` 的 `UOM_SPEC`：
     件/个/只/pcs 与 千件/千只/万件 同属 `count`（千件=1000件）；kg/克/斤/磅 与 吨 同属 `weight`；
     米/cm/mm/km/英寸/英尺 属 `length`；㎡/亩 属 `area`；m³/L/mL/加仑 属 `volume`；小时/分钟/秒/天/月 属时间。
     语义不同、不可互换的计数单位（支/根/条/张/片/块/把/副/双/瓶/袋/罐/盒/托/盘/卷/桶/箱/包/捆…）
     **各自独立成量纲**，同组遇到不同量纲时判「不可比」而不是硬凑。
   - **价格单位 `uom` 与数量单位 `qty_uom` 可以不同**，`qty` 恒为 `qty_uom` 下的数量。
     设 `mult_q`=数量单位倍率、`mult_p`=价格单位倍率、`mult_t`=标准单位倍率，
     `ratio_eff` = 1 个数量单位等于多少个价格单位：
     ```
     同量纲:  ratio_eff = mult_q / mult_p            （1 个 = 0.001 千件）
     跨量纲:  ratio_eff = 条目显式给的 qty_to_price_uom  （1 包 = 0.05 箱 → 0.05）

     price_norm = price_src × ratio_eff × (mult_t / mult_q)   ← 每标准单位的价格
     qty_std    = qty_src   × (mult_q / mult_t)               ← 标准单位下的数量
     amount_std = price_norm × qty_std
     ```
     当数量与价格同单位时（最常见），`ratio_eff=1` 且 `mult_q=mult_p` ⇒ 金额守恒。
     **只换单价不换数量，会让合计金额随"选哪个标准单位"漂移（典型 1000 倍）**，
     是本技能历史上同输入不同结论的头号根因。
   - 跨量纲且未给 `qty_to_price_uom` → 该条标 `uncomparable` 并提示，**绝不臆测**。
4. **同组量纲冲突**：组内数量单位出现不同量纲 → 该组标不可比并写进「异常与不可比」。
5. **金额自检**：若条目带 `amount_reported`，核对 `单价_src × 数量_src × ratio_eff` 与源单金额，
   偏差 >1% 写进「异常与不可比」（用于抓出数量/单位抄错）。
   源单金额列与价格列若税基不同（一个未税一个含税），先按税率折算到同一口径再比。

## 5. 品项匹配规则

1. 显式 `match_key` 优先。
2. 否则按 `norm(品名)|norm(规格)|数量单位量纲` 精确分组；`norm()` = 全角转半角 + 小写 + 去标点空白。
   **量纲而非具体单位**是关键：千件报价与个报价必须归入同一组，再由组标准单位统一换算；
   若把具体单位写进键，两者会天然分裂成两组，只能靠人工强行对齐 `unit_multiplier`，
   不同执行者会得到不同口径的结论。
3. 精确组之间算 `相似度 = 0.7×品名相似 + 0.3×规格相似`（规格为空时只用品名），
   相似度为「字符集 Jaccard ×0.45 + 序列比对 ×0.55」。
4. `≥ --match-threshold`（默认 0.82）且量纲一致 → 并查集合并，并把合并动作写进「异常与不可比」。
5. `0.60 ~ 阈值` → 只记「疑似同款未合并」，**不自动合并**。
6. 同一来源在同组出现多条（数量阶梯价）→ 取最接近 `--target-qty` 的一条，其余记异常。

## 6. 基准与红绿判定

```
base_pool = 组内 source_type ∈ baseline_sources（默认 upload,library）且可比的 price_norm
baseline  = median(base_pool)   # 或 min / mean；--baseline web|library 时只用该类来源
若 base_pool 为空且有网络价 → baseline = 网络价中位数，标注「仅网络参考基准」
若仍为空 → no_baseline，整组不做红绿判定

deviation = (price_norm - baseline) / baseline
deviation >  +tolerance(默认 0.05) → expensive   偏贵   红
deviation <  -tolerance           → cheap       偏便宜 绿
区间内                            → neutral     不标色
source_type=web 且未加入基准      → reference   仅参考，不染色
```

- 只有 1 条可比报价：`no_baseline`，**不判绿**。
- 每组输出 `min_price / max_price / gap / gap_pct / best_source`。
- **价差统计口径**：最低价/最高价/最大价差/价差率/可比来源数**只统计上传 + 资料库来源**，
  网络价不进这些统计（否则"这次搜到几条网价"就会改变结论数字）。
- 综合总价只在「所有上传来源都有报价且可比」的品项上累加，避免覆盖品项不同造成误比。
- 采购数量取组内上传来源换算后的数量；各来源数量不一致时按最大数量口径算并记入「异常与不可比」。

## 7. marks.json（标色指令）

```json
{
  "generated_at": "2026-11-10",
  "red_fill": "FFFFC7CE", "red_font": "FF9C0006",
  "green_fill": "FFC6EFCE", "green_font": "FF006100",
  "count": 5,
  "items": [{
    "group_id": "G001", "flag": "expensive", "name": "冷轧钢板", "spec": "1.0mm*1250*2500",
    "supplier": "甲公司", "price": 4520.0, "baseline": 4200.0, "deviation": 0.0762,
    "source_file": "甲公司报价.xlsx",
    "locator": {"kind": "xlsx", "file": "D:/.../甲公司报价.xlsx", "sheet": "Sheet1",
                "row": 5, "price_col": 6, "price_text": "4520"},
    "comment": "偏贵(红)｜基准 4200.00 CNY｜本项 4520.00｜偏差 +7.6%｜容差 ±5%"
  }]
}
```

只包含 `source_type=upload` 且 `flag ∈ {expensive, cheap}` 的条目。
`mark_sources.py` 逐条处理，失败写入 `unmarked.md`，绝不静默丢弃。

## 8. 手工构造 JSON 示例（扫描件 / 图片 / 资料库 / 网络）

```json
{"items":[
  {
    "supplier":"丙公司", "source_type":"upload", "source_file":"丙公司报价单.png",
    "source_date":"2026-11-08", "code":"A-01", "name":"冷轧钢板", "spec":"1.0mm*1250*2500",
    "qty":10, "uom":"吨", "standard_uom":"吨", "unit_price":4100, "currency":"CNY",
    "tax_included":true, "tax_rate":0.13, "unit_multiplier":1.0,
    "note":"含税出厂价，不含运费", "match_key":"钢板-1.0mm",
    "locator":{"kind":"image","file":"D:/.../丙公司报价单.png","bbox":[420,318,560,346],"price_text":"4100"}
  },
  {
    "supplier":"资料库-2025年度框架价", "source_type":"library",
    "source_ref":"2025年钢材框架协议价目表.xlsx", "source_date":"2025-06-01",
    "name":"冷轧钢板", "spec":"1.0mm*1250*2500", "qty":10, "uom":"吨", "standard_uom":"吨",
    "unit_price":4050, "currency":"CNY", "tax_included":true, "tax_rate":0.13,
    "match_key":"钢板-1.0mm", "locator":{"kind":"none"}
  },
  {
    "supplier":"1688批发(参考)", "source_type":"web", "source_ref":"https://...",
    "source_date":"2026-11-10", "name":"冷轧钢板", "spec":"1.0mm*1250*2500",
    "qty":1, "uom":"吨", "standard_uom":"吨", "unit_price":4380,
    "currency":"CNY", "tax_included":true, "tax_rate":0.13,
    "note":"零售挂牌价，含税不含运费，最小起订 1 吨", "match_key":"钢板-1.0mm",
    "locator":{"kind":"none"}
  }
]}
```

手工条目用 `match_key` 把三方来源钉到同一组，是最稳妥的做法（跨来源品名写法常不一致）。

## 9. 常见坑

- **同一批报价单跑出不同合计金额**：99% 是「单位换算只作用于单价、不作用于数量」。
  典型现场：鑫达按"千件"报 1,850 元、博越按"个"报 1.75 元，执行者把标准单位定成"件"时，
  单价换算 ÷1000 但数量没换 → 合计差 1000 倍。修法见第 4 节第 3 条（单价与数量同步换算）。
- **单价单位与数量单位不同**：如「单价 5,280 元/吨、数量 12,000 千克」。
  把 `qty_uom` 填「千克」即可自动换算；跨量纲（如元/箱 + 包）必须补 `qty_to_price_uom`。
- **`qty` 填错单位**：qty 恒为 `qty_uom` 下的数量。若源单写「15 箱」而 `qty_uom` 是「包」，
  应填 300 包（换算后的数量），否则金额自检会报不符。
- 合并单元格表头 / 多级表头：脚本可能识别不到，直接手工写 JSON。
- 报价单只有「金额」没有「单价」：脚本会用 `金额 ÷ 数量` 推算并写 warning，需复核。
- 同一份报价单含多个 sheet 的不同币种/税率：按 sheet 拆分后分别给 `--default-currency` 等参数。
- 价格列里写 `4200元/吨`：数值能解析，但 `price_text` 较长，标色时用数值反查更稳。
- 含「合计/小计/总计」行：脚本过滤；若供应商名出现在品名列也可能被过滤，检查 `item_count`。
- 网络价与采购价规格不同（如零售按「张」、采购按「吨」）：不要强行 `unit_multiplier` 凑，标不可比并注明。
- **PDF 报价单被"文本行兜底"解析**：控制台出现"文本行兜底抽取"即说明表格识别失败，
  此时条目价格会是 "50"、"1250" 这类碎片。改用 `find_tables()` 或多模态读表重新抽。
- **源单里的"未报价"行不见了**："图纸未确认""面议""暂不报价"的行会保留为 `quote_status=no_quote`，
  不要手工删除；删除会让两次执行的品项集合不同。
- **「金额」列与「单价×数量」对不上**：多为数量单位抄错（如把 50,000 个写成 50），
  脚本会记「源单自检」异常，须回源单核对后再出结论。
