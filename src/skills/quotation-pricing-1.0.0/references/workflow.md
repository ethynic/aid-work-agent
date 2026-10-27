# 端到端示例：给一份报价清单填价

以下用一个虚构项目演示完整流程，便于照做。

## 输入
- `HappyRoom_家具软装.xlsx`（由 spec-to-quotation-list 生成，I 列单价全空）
- 含条目：`HR-1601 单人沙发`、`HR-1602 双人沙发`、`HR-1701 边几`

## 阶段 0：分析结构
```bash
python scripts/parse_quotation.py HappyRoom_家具软装.xlsx --json items_extracted.json --project "Demo Hotel"
```
控制台：
```
已抽取条目：3 条，来自 1 个文件
  - HappyRoom_家具软装.xlsx : 3 条
写出 -> items_extracted.json
```

## 阶段 1：确认范围（确认门）
用 `ask_user` 问用户：本次报全部，还是局部？用户答「全部」。
→ 编辑 `items_extracted.json`，三条 `in_scope` 全设为 `true`。

## 阶段 2：知识库匹配
先 `knowledge_file_search` 按文件名定位「价目表」类文档，再 `knowledge_base_search` 逐条语义检索：
- `HR-1601 单人沙发` → 知识库命中「单人沙发 KF-201，绒布，W700，¥1,200（来源：价目表 2026.xlsx）」，规格一致 →
  `unit_price=1200, source="知识库-KB", kb_match="单人沙发 KF-201（价目表 2026.xlsx）", spec_consistent=true`
- `HR-1602 双人沙发` → 知识库有「双人沙发 KF-202，但为 W1600 真皮」，原清单要「布艺 W1800」→ 规格不一致，替换款 →
  `unit_price=2600, source="知识库-KB(替换)", spec_consistent=false,
   replacement_desc="原：布艺 W1800 → 替换：真皮 W1600（库存替代，需业主确认）"`
  替换项汇总经用户确认（`ask_user`）后进入回填。
- `HR-1701 边几` → 知识库未命中，`unit_price` 留空。

## 阶段 3：询问建议价（确认门）
用 `ask_user` 问：「HR-1701 边几在知识库未找到，是否由我给建议参考价？」用户答「可以」。
→ `unit_price=680, source="AI建议参考价", note="大模型建议，非成交价，需复核"`。

## 阶段 4：回填
```bash
python scripts/fill_quotation.py --json items_extracted.json
```
生成 `HappyRoom_家具软装_priced.xlsx`：I 列填价，M 列来源，N 列说明，并新增「替换与差异 Replacements」sheet（含 HR-1602 的替换说明）。用 `cp` 工具注册回传用户。

## 阶段 5：复核
```bash
python scripts/review.py --json items_extracted.json
```
```
=== 复核报告 ===
报价范围条目总数：3
已报价：3
未报价：0
规格不一致/已替换：1
知识库来源：2，AI建议来源：1
全部完成？ 是 ✅
```
全部完成，交付 `_priced.xlsx` 与复核摘要。
