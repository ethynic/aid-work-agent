# 旅游报价 - 数据补全与模板工具消费设计

> 状态：✅ 已交付（按代码实际实现整合重写，2026-10-09）
> 登记：[docs/ideas_finished.md](../ideas_finished.md)「数字员工 / 子智能体」分区「旅游报价数据补全」（20260720-2103）
> 关联通用工具：[Excel 智能模板填充工具](../tools/excel/excel-template-ai-design.md)（独立交付，见 §5 边界说明）
> 关联既有设计：[酒店局部替换](design-travel-quote-hotel-swap.md) / [价格解析性能](design-travel-quote-price-parser-performance.md) / [Prompt 稳定性](design-travel-quote-prompt-stability.md)
> 原开发计划已删除（git 历史可查）；本文按当前 `src/skills/travel-quote/` 实际实现撰写。

## 1. 背景与交付形态

`travel-consultant` 子智能体调 `travel-quote` 技能（`src/skills/travel-quote/`）生成 Excel 报价单。本任务要解决的两个问题及实际交付形态：

1. **每个客户/租户的报价单版式不同** → 已交付：通用 `excel_process` 工具的 `fill_template` 能力（`src/tools/excel/excel_template_ai.py`，无状态、按用户提供的样例模板智能填充、样式保留、行数不匹配处理）已上线，其他智能体已在用；travel-quote 技能内另有自研模板引擎承接客户模板（`template_path` 全链路接线，见 §2）。
2. **报价内容项不全，无法支撑任意版式** → 领域侧扩展未实现：`row_total`/`audience_split` 人群拆分数据模型与大交通计费（原设计 Phase 1/4，非本任务核心交付，登记于 §4）。

> 交付判定（2026-10-09 用户澄清）：本任务的交付物是**通用 Excel 工具的"按用户提供的模板填充"能力**，不是改造旅游报价 skill——travel-quote 保持技能内自研引擎（同样具备客户模板消费能力），两者并存；旅游智能体将来要改走通用工具时只是配置/适配层接入（`generate.py` 调 `fill_template` + data 适配），不构成 skill 重构。

## 2. 现行实现：技能内模板引擎（excel_export.py）

入口 `export_with_template(quote_data, template_path)`（`excel_export.py:31`），模板缺失时回退 `_export_simple`（openpyxl 手写报表，再回退 JSON）。

模板机制（`excel_export.py:52-203`）：

- **区块标记**：定位 `{{#items}}` / `{{/items}}` 区块标记行，删除标记行后按 `len(items)` 插入数据行；
- **样式克隆**：逐格复制模板行的 font/alignment/border/fill/number_format/行高，`_replace_placeholders` 正则替换 `{{变量名}}`（float 千分位格式化）；
- **版式保持**：同类别行纵向合并；区块外合并单元格按行偏移重锚定；随队老师列探测；
- **默认模板**：`templates/default.xlsx`（`create_default_template.py` 生成），null 模板路径时使用。

编排流程（`generate.py:51-260`）：`parse_itinerary`（LLM 解析行程文本）→ `resolve_resources`（检索车辆/景点/酒店/餐/导游）→ 季节/距离/各项成本计算（vehicle/attraction/hotel/meal/guide/other_fees.py）→ 汇总 items → `export_with_template` 导出 xlsx（`:227`）→ 返回 JSON（含 file_path、中文表头 rows、合计）。

调用约束见 `src/skills/travel-quote/SKILL.md`（仅允许 `python scripts/generate.py` 与 `update_hotel.py`，`template_path` 参数为否选项）。

## 3. 接入链路（调用方视角）

```text
travel-consultant 子智能体（SUBAGENT.md 阶段二）
  └─ skill_execute → travel-quote 技能（可选 template_path=客户模板路径）
       └─ generate.py：解析→检索→计费→汇总
            └─ excel_export.export_with_template(items, template_path)
                 ├─ 有模板：区块展开 + 样式克隆 + 合并单元格重锚定
                 └─ 无模板：默认模板 → 简表 → JSON 逐级回退
```

## 4. 领域侧扩展（非本任务核心交付，未实现，如实登记）

| 项 | 原设计 | 现状 |
|----|--------|------|
| `row_total` / `audience_split` / `group` 数据模型 | item 加行总价与人群分摊，QuoteData 加聚合 | 未实现（`subtotal` 仍为人均口径） |
| 大交通计费模块（`transport.py` + DB 表） | 机票/高铁等按人群票种计费 | 未实现 |
| 签证/小费/装备等零项扩展 | other_fees 配置扩展 | 未实现 |

后续如需上述能力，按新需求立项；无论走自研引擎还是通用工具的 `fill_template`，均可承接。

## 5. 与通用 Excel 工具的边界

[excel-template-ai-design.md](../tools/excel/excel-template-ai-design.md)（`excel_process` 工具的 `fill_template` 操作，实现于 `src/tools/excel/excel_template_ai.py`）是本任务交付的通用能力：按用户提供的样例模板智能填充，其他智能体已在用。travel-quote 的模板引擎是技能内领域实现，两者并存；旅游智能体如需改走通用工具，仅为配置/适配层接入，不是架构迁移。
