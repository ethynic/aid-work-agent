---
name: complaint-core
description: >
  投诉处理核心技能，提供投诉建档、分类、案例匹配、升级处理等CLI操作。
  所有投诉数据操作都通过本技能完成。
init_script: complaint_tool.py
metadata:
  version: "1.0.0"
  author: aid-work-agent
  openclaw:
    emoji: "📋"
    requires:
      bins: ["python"]
---

## 投诉处理核心技能

本技能提供投诉处理的全部CLI操作命令。通过 `skill_execute` 调用。

### 命令列表

| 命令 | 功能 | 必要参数 | 可选参数 |
|------|------|----------|----------|
| `analyze-sentiment` | 分析客户情绪 | `--text` | `--context` |
| `classify-complaint` | 投诉分类 | `--description` | `--context` |
| `create-complaint` | 创建投诉记录 | `--user-id`, `--description`, `--category` | `--order-id`, `--urgency`, `--customer-emotion`, `--contact-info`, `--tenant-id`, `--session-id`, `--sub-category` |
| `update-complaint` | 更新投诉状态 | `--complaint-id`, `--status` | `--resolution`, `--assigned-to`, `--notes` |
| `match-cases` | 匹配相似历史案例 | `--description` | `--category`, `--top-k` |
| `escalate-complaint` | 升级投诉 | `--complaint-id`, `--reason` | `--escalate-to`, `--urgency` |
| `list-complaints` | 查询投诉列表 | `--user-id` | `--status`, `--category`, `--urgency`, `--limit` |
| `get-complaint` | 查询单条投诉详情 | `--complaint-id` | — |
| `create-followup` | 创建跟进任务 | `--complaint-id`, `--action`, `--due-date` | `--assigned-to` |
| `stats` | 投诉统计 | — | `--period`, `--group-by` |
| `add-interaction` | 记录交互记录 | `--complaint-id`, `--content`, `--sender-type` | `--interaction-type` |

### 调用示例

```
skill_execute(
  skill="complaint-core",
  command="python scripts/complaint_tool.py analyze-sentiment --text \"客户消息\"",
  content=""
)
```

### 数据表

本技能管理以下4张业务数据表（启动时自动创建）：

- `bs_complaint_handling_complaints` — 投诉主表
- `bs_complaint_handling_interactions` — 投诉交互记录
- `bs_complaint_handling_case_solutions` — 历史案例解决方案库
- `bs_complaint_handling_followups` — 跟进任务表
