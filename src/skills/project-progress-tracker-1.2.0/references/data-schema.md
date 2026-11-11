# projects.json 字段规范

唯一数据真源。UTF-8，根对象含 `meta` 与 `projects` 两个键。看板由 `build_dashboard.py` 校验后注入。

## 顶层

```json
{
  "meta": {
    "generated_at": "2026-11-10T19:46:00+08:00",
    "as_of": "2026-11-10",
    "title": "<公司名> · 项目进度看板",
    "subtitle": "<业务线> · 多项目进度健康度巡检",
    "source_mode": "upload | knowledge | mixed | demo",
    "demo": true,
    "totals": {
      "total": 8,
      "on_track": 3,
      "delayed": 3,
      "overdue": 2,
      "avg_progress": 66.5,
      "high_risk": 3
    },
    "overall_status": "需重点关注",
    "executive_summary": "3~5 句总体结论……",
    "top_risks": ["……", "……"],
    "recommendations": ["……", "……"]
  },
  "projects": [ /* 见下 */ ]
}
```

`totals` 由构建脚本**自动重算**（以 `projects[]` 为准），AI 写入的值仅作交叉校验，不一致以重算值为准并在 `logs/` 留痕。

## projects[] 单项

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✓ | 稳定唯一键，如 `p001`；增量更新时用它对齐历史 |
| `name` | string | ✓ | 项目名 |
| `client` | string | | 客户 / 所属部门 |
| `owner` | string | | 项目负责人 |
| `status` | enum | ✓ | `on_track` / `delayed` / `overdue` |
| `status_reason` | string | ✓ | 一句话判定理由，必须含具体日期或数字 |
| `confidence` | number | ✓ | 0~1 |
| `progress` | number | ✓ | 实际完成率 0~100 |
| `planned_progress` | number | ✓ | 计划完成率 0~100 |
| `start_date` | string | | `YYYY-MM-DD` |
| `due_date` | string | | `YYYY-MM-DD` |
| `days_remaining` | int | | 负值表示已超期天数（由脚本重算） |
| `risk_level` | enum | ✓ | `high` / `medium` / `low` |
| `closed` | bool | | 是否已结项，默认 false |
| `demo` | bool | | 是否为演示数据 |
| `summary` | string | ✓ | AI 一句话进度结论 |
| `highlights` | string[] | | 近期关键进展 2~4 条 |
| `risks` | string[] | | 风险 2~4 条，具体不空泛 |
| `next_actions` | string[] | | 下一步 2~4 条，可执行 |
| `evidence_gaps` | string[] | | 材料缺口 / 数据冲突说明 |
| `milestones` | object[] | | `{name, planned, actual, state}`；`state` ∈ `done` / `done_late` / `pending` / `overdue` |
| `sources` | object[] | | `{file, type, date, excerpt}`；`type` ∈ `report` / `communication` / `milestone` / `acceptance` / `other` |
| `communications` | object[] | | `{date, channel, from, excerpt, signal}`；`signal` ∈ `blocked` / `progress` / `resolved` / `neutral` |
| `status_history` | object[] | | `{as_of, status, progress}`，用于看板趋势 |
| `updated_at` | string | | ISO 8601 |

## 最小示例

以建材（瓷砖 / 岩板）制造企业为例，其余行业把名词替换即可，字段结构不变：

```json
{
  "id": "p001",
  "name": "佛山基地3号宽体窑炉技改",
  "client": "制造中心 · 佛山基地",
  "owner": "梁国锋",
  "status": "overdue",
  "status_reason": "计划 2026-10-31 完成点火投产验收，截至 2026-11-10 仍未获批复点火，已逾期 10 天。",
  "confidence": 0.94,
  "progress": 82,
  "planned_progress": 100,
  "start_date": "2026-07-01",
  "due_date": "2026-10-31",
  "risk_level": "high",
  "summary": "窑体改造与冷调全部完成，卡在特种设备登记与环保联网批复，已逾期 10 天。",
  "highlights": ["10 月 22 日完成耐火材料更换与低氮燃烧器调试", "空窑冷调 72 小时通过，温差控制在 ±3℃"],
  "risks": ["燃气调压站特种设备使用登记未办结，属外部审批不可控"],
  "next_actions": ["升级至园区管委会与市监局锁定验收时间", "同步出具停窑损失测算"],
  "evidence_gaps": [],
  "milestones": [
    {"name": "技改方案评审", "planned": "2026-07-20", "actual": "2026-07-18", "state": "done"},
    {"name": "点火投产验收", "planned": "2026-10-31", "actual": null, "state": "overdue"}
  ],
  "sources": [
    {"file": "3号窑炉技改_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "环保在线监测联网批复仍未下发，点火排期存在风险。"}
  ],
  "communications": [
    {"date": "2026-11-06", "channel": "企业微信", "from": "园区安监", "excerpt": "特种设备登记还在走流程，下周再看看。", "signal": "blocked"}
  ],
  "status_history": [
    {"as_of": "2026-10-20", "status": "delayed", "progress": 70},
    {"as_of": "2026-11-10", "status": "overdue", "progress": 82}
  ],
  "updated_at": "2026-11-10T19:46:00+08:00"
}
```

## 校验规则（build_dashboard.py 执行）

1. `id` 唯一；`status` 必须是三态之一；`progress` / `planned_progress` / `confidence` 为 0~100 / 0~1 的数值。
2. 缺任一必填字段 → 报错并列出项目 id，不生成残缺看板。
3. `days_remaining` 与 `totals` 由脚本按 `as_of` 重算覆盖。
4. 数组字段缺失按空数组补全；字符串字段缺失按空串补全并记 warning。
5. `demo: true` 时看板顶部显示「演示数据」标识，避免被误当真实汇报。
