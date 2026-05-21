---
name: after-sales-core
description: >
  售后服务核心技能，提供内部工单和退换货记录的 CLI 操作。
  适用于无外部系统时的降级操作，或需要记录到本地数据库的场景。
init_script: after_sales_tool.py
metadata:
  openclaw:
    emoji: "🔧"
    requires:
      bins: ["python"]
---

# 售后服务核心技能

## 概述

本技能提供售后服务内部操作能力，通过 CLI 脚本执行：
- 内部工单管理（创建、查询、列表）
- 退换货记录管理（创建、查询）

所有操作通过 `skill_execute` 执行 `python scripts/after_sales_tool.py` 脚本完成。

## CLI 命令

### create-ticket — 创建内部工单

当用户需要售后帮助且无外部系统时，创建内部工单记录：

```bash
python scripts/after_sales_tool.py create-ticket --user-id USER_ID --description "问题描述" --category return
```

**参数**：
- `--user-id`（必填）：用户 ID
- `--description`（必填）：问题描述
- `--category`（必填）：问题分类
- `--order-id`（可选）：关联订单号
- `--priority`（可选，默认 normal）：优先级

**category 取值**：`order_issue`、`return`、`exchange`、`repair`、`usage`、`other`

**priority 取值**：`low`、`normal`、`high`、`urgent`

### query-ticket — 查询工单

```bash
python scripts/after_sales_tool.py query-ticket --ticket-id ast_xxxx
```

### list-tickets — 列出用户工单

```bash
python scripts/after_sales_tool.py list-tickets --user-id USER_ID [--status open]
```

### create-return — 创建退换货记录

```bash
python scripts/after_sales_tool.py create-return --user-id USER_ID --order-id ORD123 --type return --reason "质量问题"
```

**参数**：
- `--type`（必填）：`return`（退货）或 `exchange`（换货）
- `--items`（可选）：涉及商品，JSON 字符串

### query-returns — 查询退换货记录

```bash
python scripts/after_sales_tool.py query-returns --user-id USER_ID [--order-id ORD123]
```

## 使用场景

1. **无外部系统**：租户未配置外部售后系统时，使用内部工单管理
2. **内部记录**：即使有外部系统，也可用内部表记录 Agent 处理过程
3. **降级处理**：外部系统不可用时的备选方案

## 数据库表

本技能维护以下数据库表（自动创建）：
- `bs_after_sales_tickets` — 售后工单表
- `bs_after_sales_ticket_messages` — 工单消息表
- `bs_after_sales_returns` — 退换货记录表
