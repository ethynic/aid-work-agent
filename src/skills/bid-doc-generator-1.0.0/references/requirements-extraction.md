# 招标要求结构化提取模型（Step 2 使用）

本文件定义如何从「甲方招标书要求」中抽取结构化要求清单 `requirements.json`。
目标是把一份非结构化的招标文件，转成可逐条核对、可逐条响应的「考题清单」。

## 提取指令（prompt 模板）

> 以下指令可直接作为对招标书的解析提示词（可配合读取 PDF/DOCX 内容后执行）：
>
> "请通读这份招标文件，按下方 JSON Schema 抽取全部要求，不遗漏任何一处对投标人的约束。
> 凡招标文件中出现『须/应/必须/不得/不低于/具备/提供』等强制性措辞，一律纳入对应类别。
> 无法确定的字段填 null 并在 note 标注。"

## requirements.json Schema

```json
{
  "meta": {
    "tender_name": "项目名称",
    "tender_no": "招标编号",
    "purchaser": "甲方/采购人",
    "bidder_subject": "对投标人的主体要求摘要",
    "bid_validity_days": 90,
    "deadline": "截标时间",
    "currency": "CNY",
    "source_file": "招标书文件名"
  },
  "qualification": [
    {
      "id": "Q1",
      "category": "基本资格",
      "requirement": "具备独立法人资格，注册资本不低于 X 万元",
      "threshold": "≥X 万元",
      "evidence_needed": "营业执照副本",
      "mandatory": true,
      "note": ""
    }
  ],
  "document_components": [
    { "id": "C1", "name": "投标函", "required": true, "note": "须法定代表人或授权人签字并盖章" }
  ],
  "technical_specs": [
    {
      "id": "T1",
      "clause": "技术条款编号/章节",
      "requirement": "产品参数/服务功能要求",
      "metric": "量化指标（如有）",
      "mandatory": true,
      "note": ""
    }
  ],
  "scoring": {
    "method": "综合评分法/经评审最低价/合理低价",
    "weights": { "商务": 0.2, "技术": 0.5, "价格": 0.3 },
    "price_formula": "基准价计算与扣分规则描述",
    "key_items": [
      { "id": "S1", "dimension": "类似业绩", "score": 10, "evidence": "业绩合同/中标通知书" }
    ]
  },
  "disqualification": [
    { "id": "D1", "clause": "否决条款", "condition": "投标保证金未按时到账", "note": "一票否决" }
  ],
  "commercial_terms": [
    { "id": "B1", "term": "付款方式", "requirement": "预付款30%，验收后付至95%", "note": "" }
  ],
  "format_requirements": {
    "binding": "胶装/线装",
    "copies": "正本1份副本4份",
    "seal_signature": "逐页小签/骑缝章/法人章",
    "page_limit": "技术标不超过200页",
    "other": "电子光盘1份"
  }
}
```

## 提取要点（易漏项提醒）

1. **资格门槛的隐性条件**：除明文资质外，注意「近三年无重大违法」「信用中国查询」「联合体限制」「特定行业许可」。
2. **带星号（*）条款**：政府/国企采购常用「★」标注实质性要求，违反即废标，必须单列为 `mandatory: true`。
3. **报价口径**：注意是「含税总价」「全费用单价」还是「量单计价」；税率、币种、报价有效期。
4. **证明材料的原件/复印件/公证件要求**：影响 `evidence_needed` 与后续材料准备。
5. **评分细则的佐证映射**：每个评分项都要落到具体材料，写入 `scoring.key_items[*].evidence`，便于在标书中建立「评分索引」。
6. **否决条款全集**：逐一列入 `disqualification`，生成标书时做自检清单。

## 输出约束

- 每条要求必须有稳定 `id`（Q/T/B/D/S/C 前缀 + 序号），供偏离表与响应索引回溯。
- `mandatory` 字段区分「实质性/一票否决」与「一般要求」，直接决定风险等级。
- 解析完成后，将 `requirements.json` 存入工作目录，作为 Step 4/6 的对照基准。
