---
name: followup-tracking
description: 跟进记录管理与质量评估，支持创建跟进记录、待跟进提醒、AI外呼记录、跟进质量评估
version: 1.0.0
init_script: followup_manager.py
metadata:
  trigger_keywords:
    - 跟进记录
    - 跟进提醒
    - 待跟进
    - 逾期跟进
    - 跟进质量
    - 质量评估
    - 外呼记录
    - AI外呼结果
  category: followup
---

# 跟进跟踪技能

## 何时使用此技能

当用户需要：
- 创建或查看跟进记录
- 查看待跟进提醒或逾期跟进
- 评估跟进质量
- 记录 AI 外呼结果
- 查看跟进统计

## 命令列表

```bash
# 初始化表（确保跟进相关表存在）
python scripts/followup_manager.py init_tables

# 创建跟进记录
python scripts/followup_manager.py add-record --lead-id LEAD_ID --user-id USER_ID --type phone --content "电话沟通" [--outcome positive] [--next-followup-at DATETIME]

# 查询跟进记录
python scripts/followup_manager.py list-records --user-id USER_ID [--lead-id ID] [--date-from DATE] [--date-to DATE] [--limit 50]

# 获取跟进详情
python scripts/followup_manager.py get-record --record-id RECORD_ID

# 评估跟进质量（调用 LLM）
python scripts/followup_manager.py evaluate-quality --record-id RECORD_ID

# 批量质量评估
python scripts/followup_manager.py batch-evaluate --user-id USER_ID [--date-from DATE] [--date-to DATE]

# 获取待跟进提醒
python scripts/followup_manager.py get-reminders --user-id USER_ID [--due-before DATETIME]

# 获取逾期跟进（经理视图）
python scripts/followup_manager.py get-overdue

# 提醒统计
python scripts/followup_manager.py reminder-stats --user-id USER_ID

# 记录 AI 外呼结果
python scripts/followup_manager.py record-ai-call --lead-id LEAD_ID --user-id USER_ID --call-id CALL_ID --transcript "..." --sentiment positive --summary "..." [--duration 120]

# 批量记录外呼结果
python scripts/followup_manager.py batch-ai-call-results --results '[{...}]'
```

## 输出格式

所有命令输出 JSON，格式：`{"success": bool, "data": {...}, "error": "..."}`

## 错误处理

- 参数缺失或格式错误返回 `success: false` 并附带 `error` 字段
- 数据库错误附带 `debug` 字段（生产环境不返回详细错误）
