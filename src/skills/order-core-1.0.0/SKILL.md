---
name: order-core
description: >
  订单处理核心技能，提供订单创建、查询、状态流转、审批等 CLI 操作。
  适用于订单全生命周期管理，包含订单主表、明细、状态历史、审批记录、Webhook 事件的数据库管理。
init_script: order_tool.py
metadata:
  openclaw:
    emoji: "📦"
    requires:
      bins: ["python"]
---

# 订单处理核心技能

## 概述

本技能提供订单处理核心操作能力，通过 CLI 脚本执行：
- 订单管理（创建、查询、更新、取消）
- 状态流转（状态变更、状态历史查询）
- 审批流程（创建审批、审批通过、审批拒绝）
- 订单统计

所有操作通过 `skill_execute` 执行 `python scripts/order_tool.py` 脚本完成。

## CLI 命令

### create-order — 创建订单

创建新订单，初始状态为 draft：

```bash
python scripts/order_tool.py create-order \
  --user-id USER_ID \
  --items '[{"product_sku":"SKU001","product_name":"商品A","quantity":2,"unit_price":100.00,"discount_rate":0.1,"subtotal":180.00}]' \
  [--customer-name "张三"] \
  [--customer-phone "13800138000"] \
  [--customer-address "北京市朝阳区xxx"] \
  [--currency CNY] \
  [--discount-amount 0] \
  [--shipping-fee 0] \
  [--notes "备注信息"] \
  [--tags "加急,大客户"] \
  [--external-order-id EXT123] \
  [--source internal] \
  [--tenant-id TENANT_ID] \
  [--session-id SESSION_ID]
```

**参数**：
- `--user-id`（必填）：用户 ID
- `--items`（必填）：订单明细，JSON 字符串，包含 product_sku、product_name、quantity、unit_price、discount_rate、subtotal
- `--customer-name`（可选）：客户姓名
- `--customer-phone`（可选）：客户电话
- `--customer-address`（可选）：客户地址
- `--currency`（可选，默认 CNY）：币种
- `--discount-amount`（可选，默认 0）：整单优惠金额
- `--shipping-fee`（可选，默认 0）：运费
- `--notes`（可选）：备注
- `--tags`（可选）：标签，逗号分隔
- `--external-order-id`（可选）：外部系统订单号
- `--source`（可选，默认 internal）：订单来源
- `--tenant-id`（可选）：租户 ID
- `--session-id`（可选）：会话 ID

### get-order — 查询订单详情

```bash
python scripts/order_tool.py get-order --order-id ord_xxxx
```

**参数**：
- `--order-id`（必填）：订单 ID

**返回**：订单主信息、订单明细列表、最近 10 条状态变更记录

### list-orders — 查询订单列表

```bash
python scripts/order_tool.py list-orders --user-id USER_ID [--status draft] [--keyword "张三"] [--page 1] [--page-size 20] [--date-from 2026-01-01] [--date-to 2026-05-26]
```

**参数**：
- `--user-id`（必填）：用户 ID
- `--status`（可选）：按状态过滤
- `--keyword`（可选）：搜索客户姓名或订单号
- `--page`（可选，默认 1）：页码
- `--page-size`（可选，默认 20）：每页数量
- `--date-from`（可选）：起始日期
- `--date-to`（可选）：截止日期

### update-order — 更新订单

仅允许更新 draft 状态的订单：

```bash
python scripts/order_tool.py update-order \
  --order-id ord_xxxx \
  [--customer-name "李四"] \
  [--customer-phone "13900139000"] \
  [--customer-address "上海市浦东新区xxx"] \
  [--notes "更新备注"] \
  [--tags "普通"] \
  [--discount-amount 50] \
  [--shipping-fee 10]
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--customer-name`（可选）：客户姓名
- `--customer-phone`（可选）：客户电话
- `--customer-address`（可选）：客户地址
- `--notes`（可选）：备注
- `--tags`（可选）：标签
- `--discount-amount`（可选）：整单优惠金额
- `--shipping-fee`（可选）：运费

### cancel-order — 取消订单

```bash
python scripts/order_tool.py cancel-order --order-id ord_xxxx --reason "客户要求取消"
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--reason`（可选）：取消原因

### change-status — 变更订单状态

```bash
python scripts/order_tool.py change-status --order-id ord_xxxx --status pending_approval --reason "提交审批"
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--status`（必填）：目标状态
- `--reason`（可选）：变更原因

**状态流转规则**：
- `draft` → `pending_approval`、`cancelled`
- `pending_approval` → `approved`、`rejected`、`cancelled`
- `rejected` → `draft`、`cancelled`
- `approved` → `processing`、`cancelled`
- `processing` → `shipped`、`cancelled`
- `shipped` → `delivered`、`cancelled`
- `delivered` → `completed`、`return_requested`
- `completed` → `return_requested`
- `return_requested` → `returned`

### create-approval — 创建审批记录

```bash
python scripts/order_tool.py create-approval \
  --order-id ord_xxxx \
  --requested-by USER_ID \
  [--approval-type order_approval] \
  [--assigned-to APPROVER_ID] \
  [--amount-threshold 10000] \
  [--note "大额订单审批"]
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--requested-by`（必填）：申请人 ID
- `--approval-type`（可选，默认 order_approval）：审批类型
- `--assigned-to`（可选）：审批人 ID
- `--amount-threshold`（可选）：审批金额阈值
- `--note`（可选）：审批备注

### approve-order — 审批通过

```bash
python scripts/order_tool.py approve-order --order-id ord_xxxx --resolved-by APPROVER_ID [--note "审批通过"]
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--resolved-by`（必填）：审批人 ID
- `--note`（可选）：审批备注

### reject-order — 审批拒绝

```bash
python scripts/order_tool.py reject-order --order-id ord_xxxx --resolved-by APPROVER_ID --rejection-reason "价格异常"
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--resolved-by`（必填）：审批人 ID
- `--rejection-reason`（可选）：拒绝原因

### get-status-history — 查询状态变更历史

```bash
python scripts/order_tool.py get-status-history --order-id ord_xxxx
```

**参数**：
- `--order-id`（必填）：订单 ID

### stats — 订单统计

```bash
python scripts/order_tool.py stats [--period 30d] [--group-by status]
```

**参数**：
- `--period`（可选，默认 30d）：统计周期（如 7d、30d、1m）
- `--group-by`（可选，默认 status）：分组维度（status/date）

## 使用场景

1. **订单全生命周期**：从创建到完成/退货的完整订单管理
2. **审批流程**：大额订单或特殊订单的审批控制
3. **状态追踪**：订单状态变更的完整历史记录
4. **数据分析**：订单统计与趋势分析

## 数据库表

本技能维护以下数据库表（自动创建）：
- `bs_order_processing_orders` — 订单主表
- `bs_order_processing_order_items` — 订单明细表
- `bs_order_processing_status_history` — 状态变更历史表
- `bs_order_processing_approvals` — 审批记录表
- `bs_order_processing_webhook_events` — Webhook 事件表
