# 资质/技术/商务三张对照表（Step 4 使用）

本文件定义如何把「我方资质信息、产品及价格信息」与「招标要求」对齐，形成三张可审计的对照表。
这三张表是投标书合规性的核心证据，也是 `scripts/build_matrix.py` 的输入。

## 1. 资格符合性审查对照表（qualification matrix）

字段：`req_id`(来自 requirements.json 的 Q*)、`requirement`、`threshold`、`our_evidence`(我方佐证)、`status`(满足/不满足/部分)、`note`

填法：
- `status=满足`：我方佐证完全覆盖门槛，附材料名称（如「营业执照副本，注册资本 1.2 亿」）。
- `status=部分`：基本满足但有缺口，note 写明缺口与补救（如「认证覆盖，但范围不含XX」）。
- `status=不满足`：如实标注，note 写明风险与是否构成否决项（对应 `disqualification` 的 D*）。

## 2. 技术规格响应/偏离表（technical deviation）

字段：`spec_id`(T*)、`requirement`、`our_parameter`(我方产品参数)、`deviation`(无偏离/正偏离/负偏离)、`evidence`(产品说明书/检测报告页)、`note`

填法：
- `无偏离`：参数完全等于或优于要求（等于即无偏离；明显优于且主动突出时标「正偏离」）。
- `正偏离`：优于招标要求，可作为技术评分加分项，在技术方案中重点呈现。
- `负偏离`：低于要求，**必须如实写出**，不得改写为满足；若为实质性（★）条款，标红并提示否决风险。

## 3. 商务条款响应/偏离表（commercial deviation）

字段：`term_id`(B*)、`term`、`requirement`、`our_commitment`(我方承诺)、`deviation`、`note`

填法：对齐付款、工期、质保、履约担保、投标有效期等；我方无法完全接受处标「负偏离」并给出替代方案。

## 对齐输出（供 build_matrix.py 消费）

将三张表汇总为一个 JSON，结构：

```json
{
  "qualification": [ { "req_id":"Q1", "requirement":"...", "our_evidence":"...", "status":"满足", "note":"" } ],
  "technical":     [ { "spec_id":"T1", "requirement":"...", "our_parameter":"...", "deviation":"无偏离", "evidence":"...", "note":"" } ],
  "commercial":    [ { "term_id":"B1", "term":"付款方式", "requirement":"...", "our_commitment":"...", "deviation":"无偏离", "note":"" } ]
}
```

保存为 `matrix.json`，交给 `scripts/build_matrix.py` 生成 Markdown + CSV 偏离表。

## 风险标注规则

- 任何 `status=不满足` 或 `deviation=负偏离` 且对应 `mandatory:true` 的条目，视为**高风险（否决级）**，
  在偏离表顶部与交付说明中集中列出，绝不可遗漏或淡化。
- 生成投标书前，必须确认所有高风险项已显式处理（补充材料 / 书面说明 / 用户已知悉）。
