---
name: order-logistics
description: >
  订单处理物流发货技能，提供发货记录创建、物流状态追踪、签收确认等 CLI 操作。
  支持发货管理、物流追踪查询、自动推进订单状态。
init_script: logistics_tool.py
metadata:
  openclaw:
    emoji: "🚚"
    requires:
      bins: ["python"]
---

# 订单物流发货技能

## 概述

本技能提供订单物流发货操作能力，通过 CLI 脚本执行：
- 发货管理（创建发货记录、更新物流信息）
- 物流追踪（按运单号查询、按订单查询发货列表）
- 签收确认（确认签收并自动推进订单状态）

所有操作通过 `skill_execute` 执行 `python scripts/logistics_tool.py` 脚本完成。

## CLI 命令

### create-shipment — 创建发货记录

为订单创建发货记录，自动将订单状态从 `processing` 推进为 `shipped`：

```bash
python scripts/logistics_tool.py create-shipment \
  --order-id ord_xxxx \
  [--carrier "顺丰速运"] \
  [--tracking-number SF1234567890] \
  [--shipping-method express] \
  [--weight 2.5] \
  [--shipping-address "北京市朝阳区xxx"] \
  [--estimated-delivery 2026-05-30] \
  [--notes "易碎品，轻拿轻放"] \
  [--tenant-id TENANT_ID]
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--carrier`（可选）：承运商
- `--tracking-number`（可选）：物流运单号
- `--shipping-method`（可选）：发货方式（如 express、standard、freight）
- `--weight`（可选）：包裹重量（kg）
- `--shipping-address`（可选）：收货地址
- `--estimated-delivery`（可选）：预计送达日期
- `--notes`（可选）：备注
- `--tenant-id`（可选）：租户 ID

**发货状态流转规则**：
- `pending` → `picked_up`、`cancelled`
- `picked_up` → `in_transit`、`cancelled`
- `in_transit` → `out_for_delivery`、`cancelled`
- `out_for_delivery` → `delivered`、`cancelled`
- `pending` → `cancelled`

### update-tracking — 更新物流追踪信息

更新发货记录的物流状态，支持状态流转校验：

```bash
python scripts/logistics_tool.py update-tracking \
  --shipment-id sht_xxxx \
  [--tracking-number SF1234567890] \
  [--carrier "顺丰速运"] \
  [--status in_transit] \
  [--estimated-delivery 2026-05-31] \
  [--notes "已到达北京转运中心"]
```

**参数**：
- `--shipment-id`（必填）：发货记录 ID
- `--tracking-number`（可选）：更新运单号
- `--carrier`（可选）：更新承运商
- `--status`（可选）：更新物流状态（需符合状态流转规则）
- `--estimated-delivery`（可选）：更新预计送达日期
- `--notes`（可选）：更新备注

### get-shipment — 查询发货详情

查询指定发货记录的完整信息：

```bash
python scripts/logistics_tool.py get-shipment --shipment-id sht_xxxx
```

**参数**：
- `--shipment-id`（必填）：发货记录 ID

**返回**：发货记录完整信息，包含物流状态、承运商、运单号、时间等

### list-shipments — 查询发货列表

按订单或状态查询发货记录列表：

```bash
python scripts/logistics_tool.py list-shipments [--order-id ord_xxxx] [--status shipped] [--page 1] [--page-size 20]
```

**参数**：
- `--order-id`（可选）：按订单 ID 过滤
- `--status`（可选）：按发货状态过滤
- `--page`（可选，默认 1）：页码
- `--page-size`（可选，默认 20）：每页数量

### query-tracking — 按运单号查询物流

通过物流运单号查询发货记录：

```bash
python scripts/logistics_tool.py query-tracking --tracking-number SF1234567890
```

**参数**：
- `--tracking-number`（必填）：物流运单号

**返回**：发货记录信息。若本地数据库未找到该运单号，返回提示建议查询外部物流 API

### confirm-delivery — 确认签收

确认发货签收，自动将订单状态从 `shipped` 推进为 `delivered`：

```bash
python scripts/logistics_tool.py confirm-delivery --shipment-id sht_xxxx [--notes "客户已签收"]
```

**参数**：
- `--shipment-id`（必填）：发货记录 ID
- `--notes`（可选）：签收备注

**前置条件**：订单当前状态必须为 `shipped`

## 使用场景

1. **发货管理**：订单审批通过后创建发货记录，自动推进订单状态
2. **物流追踪**：更新物流节点状态，查询运单实时信息
3. **签收确认**：确认客户签收，完成订单发货环节
4. **物流查询**：按运单号或订单号查询物流详情

## 数据库表

本技能维护以下数据库表（自动创建）：
- `bs_order_processing_shipments` — 发货记录表

同时操作以下由 order-core 维护的表：
- `bs_order_processing_orders` — 订单主表（状态推进）
- `bs_order_processing_status_history` — 状态变更历史表
