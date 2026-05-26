---
name: order-inventory
description: >
  订单处理库存管理技能，提供商品管理、库存查询、库存预留/提交/释放等 CLI 操作。
  支持商品 CRUD、库存检查、预留防超卖、低库存预警、外部 ERP 库存同步。
init_script: inventory_tool.py
metadata:
  openclaw:
    emoji: "📦"
    requires:
      bins: ["python"]
---

# 订单处理库存管理技能

## 概述

本技能提供商品和库存管理操作能力，通过 CLI 脚本执行：
- 商品管理（创建、查询、更新、列表）
- 库存查询（SKU 库存检查、低库存预警）
- 库存预留（预留防超卖、提交扣减、释放归还）
- 库存调整（手动调整、外部 ERP 同步）

所有操作通过 `skill_execute` 执行 `python scripts/inventory_tool.py` 脚本完成。

## CLI 命令

### create-product — 创建商品

创建新商品，同时自动创建对应的库存记录（初始数量为 0）：

```bash
python scripts/inventory_tool.py create-product \
  --product-sku SKU001 \
  --product-name "商品A" \
  [--category "电子产品"] \
  [--brand "品牌X"] \
  [--model "型号Y"] \
  [--specifications '{"尺寸":"10x20cm","颜色":"白色"}'] \
  [--unit 个] \
  [--cost-price 50.00] \
  [--selling-price 100.00] \
  [--description "商品描述"] \
  [--tags "热销,新品"] \
  [--status active] \
  [--weight 1.5] \
  [--barcode "6901234567890"] \
  [--external-product-id EXT001] \
  [--supplier "供应商A"] \
  [--tenant-id TENANT_ID]
```

**参数**：
- `--product-sku`（必填）：商品 SKU 编码
- `--product-name`（必填）：商品名称
- `--category`（可选）：商品分类
- `--brand`（可选）：品牌
- `--model`（可选）：型号
- `--specifications`（可选）：规格参数，JSON 字符串
- `--unit`（可选，默认 个）：计量单位
- `--cost-price`（可选）：成本价
- `--selling-price`（可选）：销售价
- `--description`（可选）：商品描述
- `--tags`（可选）：标签，逗号分隔
- `--status`（可选，默认 active）：商品状态（active/discontinued/draft）
- `--weight`（可选）：重量(kg)
- `--barcode`（可选）：条形码
- `--external-product-id`（可选）：外部系统商品 ID
- `--supplier`（可选）：供应商
- `--tenant-id`（可选）：租户 ID

### get-product — 查询商品详情

查询商品详情及库存状态：

```bash
python scripts/inventory_tool.py get-product --product-id prod_xxxx
```

**参数**：
- `--product-id`（必填）：商品 ID（product_id 字段）

**返回**：商品基本信息、库存状态（total/reserved/available）

### list-products — 查询商品列表

```bash
python scripts/inventory_tool.py list-products [--category "电子产品"] [--status active] [--keyword "商品"] [--page 1] [--page-size 20]
```

**参数**：
- `--category`（可选）：按分类过滤
- `--status`（可选）：按状态过滤
- `--keyword`（可选）：搜索商品名称或 SKU
- `--page`（可选，默认 1）：页码
- `--page-size`（可选，默认 20）：每页数量

### update-product — 更新商品信息

```bash
python scripts/inventory_tool.py update-product \
  --product-id prod_xxxx \
  [--product-name "新商品名"] \
  [--category "新分类"] \
  [--brand "新品牌"] \
  [--model "新型号"] \
  [--specifications '{"尺寸":"15x25cm"}'] \
  [--unit 箱] \
  [--cost-price 60.00] \
  [--selling-price 120.00] \
  [--description "新描述"] \
  [--tags "促销"] \
  [--status discontinued] \
  [--weight 2.0] \
  [--barcode "6901234567891"] \
  [--external-product-id EXT002] \
  [--supplier "供应商B"]
```

**参数**：
- `--product-id`（必填）：商品 ID
- 其余可选字段同 create-product

### check-stock — 查询库存

按 SKU 查询库存状态：

```bash
python scripts/inventory_tool.py check-stock --sku SKU001
```

**参数**：
- `--sku`（必填）：商品 SKU

**返回**：quantity_total、quantity_reserved、quantity_available（= total - reserved）、low_stock_threshold、is_low_stock

### reserve-stock — 预留库存

为订单预留库存，使用 `SELECT FOR UPDATE` 防止超卖：

```bash
python scripts/inventory_tool.py reserve-stock \
  --order-id ord_xxxx \
  --sku SKU001 \
  --quantity 5 \
  [--expires-minutes 60]
```

**参数**：
- `--order-id`（必填）：订单 ID
- `--sku`（必填）：商品 SKU
- `--quantity`（必填）：预留数量
- `--expires-minutes`（可选，默认 60）：预留过期时间（分钟）

**逻辑**：
1. 锁定库存行（SELECT FOR UPDATE）
2. 检查 available = total - reserved >= quantity
3. 充足则更新 quantity_reserved += quantity，插入预留记录
4. 不足则返回错误及当前可用数量

### commit-reservation — 提交预留

付款后提交预留，扣减实际库存：

```bash
python scripts/inventory_tool.py commit-reservation --reservation-id rsv_xxxx
```

**参数**：
- `--reservation-id`（必填）：预留 ID

**逻辑**：
1. 查找预留记录（status=active）
2. 扣减 inventory.quantity_total -= quantity
3. 归还 inventory.quantity_reserved -= quantity
4. 更新 reservation.status = 'committed'

### release-reservation — 释放预留

取消或超时时释放预留：

```bash
python scripts/inventory_tool.py release-reservation --reservation-id rsv_xxxx
```

**参数**：
- `--reservation-id`（必填）：预留 ID

**逻辑**：
1. 查找预留记录（status=active）
2. 归还 inventory.quantity_reserved -= quantity
3. 更新 reservation.status = 'released'

### update-stock — 手动调整库存

手动增减库存（可正可负）：

```bash
python scripts/inventory_tool.py update-stock \
  --sku SKU001 \
  --quantity-delta 10 \
  [--reason "入库补货"]
```

**参数**：
- `--sku`（必填）：商品 SKU
- `--quantity-delta`（必填）：变更数量（正数为增加，负数为减少）
- `--reason`（可选）：调整原因

### sync-from-external — 外部库存同步

从外部 ERP 系统同步库存快照：

```bash
python scripts/inventory_tool.py sync-from-external \
  --items '[{"product_sku":"SKU001","quantity_total":100,"product_name":"商品A"},{"product_sku":"SKU002","quantity_total":50,"product_name":"商品B"}]'
```

**参数**：
- `--items`（必填）：库存数据，JSON 数组，每项包含 product_sku、quantity_total、product_name

**逻辑**：对每项数据进行 UPSERT，更新 last_synced_at

### list-low-stock — 低库存预警

查询库存低于阈值的商品列表：

```bash
python scripts/inventory_tool.py list-low-stock [--page 1] [--page-size 20]
```

**参数**：
- `--page`（可选，默认 1）：页码
- `--page-size`（可选，默认 20）：每页数量

## 使用场景

1. **商品全生命周期**：从创建到停用的完整商品管理
2. **库存预留防超卖**：创建订单时预留库存，付款后扣减，取消时释放
3. **低库存预警**：及时发现库存不足的商品，触发补货流程
4. **外部系统对接**：从 ERP 同步库存快照，保持数据一致

## 数据库表

本技能维护以下数据库表（自动创建）：
- `bs_order_processing_products` — 商品信息表
- `bs_order_processing_inventory` — 库存快照表
- `bs_order_processing_inventory_reservations` — 库存预留表
