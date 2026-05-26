# 订单处理智能体 — 深度设计文档

## Context

企业员工智能代理系统需要新增"订单处理智能体"，实现从订单创建到完成的全流程自动化管理。该智能体需要：
- 拥有自闭环的订单管理模块（创建、审批、发货、退换货）
- 可通过 http_api 工具对接第三方 ERP/OMS 系统
- 对外暴露 REST API + Webhooks + MCP Server，供外部系统和其他智能体调用

与现有 after-sales 子智能体采用**方案C**：订单处理是订单数据真实来源，售后通过 order_id 交叉引用。

---

## 一、框架基础设施差距分析

### 已具备（无需构建）

| 基础设施 | 位置 | 说明 |
|---------|------|------|
| HTTP API 工具 | `src/tools/network/http_api.py` | 完整 HTTP 客户端，支持 ${ENV_VAR} 替换 |
| 通知服务 | `src/services/notification_service.py` | 邮件 + Webhook 渠道 |
| 定时任务 | `src/scheduler/manager.py` | APScheduler + Redis 分布式锁 |
| 表自动初始化 | skill 的 `init_script` + `init_tables()` | 延迟建表 |
| Redis 状态管理 | `src/core/redis_client.py` | 分布式锁、Pub/Sub、缓存 |
| SSE 实时推送 | `src/main.py` SSEConnectionManager | 跨 Worker 广播 |
| 租户环境变量 | `src/db/subagent_env_var.py` | 每租户 API 密钥/URL 存储 |
| 审批模式参考 | contract-approval skill | 外部 OA 审批流程 |

### 需要新建

| 缺口 | 解决方案 | 复杂度 |
|------|---------|--------|
| 库存锁定/预留 | `order-inventory` skill 中用 `SELECT FOR UPDATE` 行锁 + 预留表 | 中 |
| Webhook 入站端点 | `src/api/order_processing.py` 新增 `/webhooks/` 路由 + 签名验证 | 低 |
| MCP Server | **全新基础设施**：`src/mcp/` 模块，实现 MCP 协议的 tool discovery + 调用转发 | **高** |
| 跨子智能体协调 | 通过共享 order_id 交叉引用 + 主智能体 delegate_to_subagent | 低 |

---

## 二、子智能体设计

### 文件：`subagents/order-processing/SUBAGENT.md`

```yaml
---
name: 订单处理智能体
description: 全流程订单管理智能体，支持订单创建、审批、发货、退货退款等完整生命周期管理，并可对接外部 ERP/OMS 系统
version: 1.0.0
author: system
capabilities:
  - order_create
  - order_query
  - order_approval
  - order_shipping
  - order_returns
  - inventory_check
  - logistics_tracking
  - external_erp_sync
triggers:
  keywords:
    - 下单
    - 订单
    - 创建订单
    - 查询订单
    - 订单状态
    - 审批
    - 发货
    - 物流
    - 运费
    - 退货
    - 退款
    - 换货
    - 库存
    - 备货
    - 出库
    - 入库
    - 订单统计
    - 销售报表
tools:
  inherit: true
  additional:
    - http_api
skills:
  allowed:
    - order-core
    - order-inventory
    - order-logistics
    - order-api
context:
  max_input_tokens: 12000
  max_output_tokens: 4000
business_pages:
  - id: order-list
    title: 订单管理
    icon: document
    route: /order-processing/orders
  - id: inventory
    title: 库存查询
    icon: package
    route: /order-processing/inventory
  - id: shipping-records
    title: 发货记录
    icon: truck
    route: /order-processing/shipping
  - id: order-stats
    title: 订单统计
    icon: chart
    route: /order-processing/stats
---
```

Markdown body 参考 after-sales/SUBAGENT.md 格式，包含：身份定位、用户信息感知、服务流程（创建订单→审核→发货→退货退款）、工具使用规则（4个 skill 的 skill_execute 命令）、安全规则、降级策略。

---

## 三、技能设计

### Skill 1: `order-core-1.0.0` — 核心订单生命周期

**目录**：`src/skills/order-core-1.0.0/`
**SKILL.md**：声明 `init_script: order_tool.py`
**主要脚本**：`scripts/order_tool.py`

**状态机**：
```
draft → pending_approval → approved → processing → shipped → delivered → completed
                       ↘ rejected     ↘ cancelled
shipped/delivered → return_requested → returned
```

**skill_execute 命令**：

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `create-order` | 创建订单 | `--user-id`, `--items` (JSON), `--customer-name`, `--customer-phone`, `--customer-address` |
| `get-order` | 订单详情 | `--order-id` |
| `list-orders` | 订单列表 | `--user-id`, `--status`, `--page`, `--page-size` |
| `update-order` | 更新字段 | `--order-id` + 字段 |
| `cancel-order` | 取消订单 | `--order-id`, `--reason` |
| `change-status` | 状态转换 | `--order-id`, `--new-status`, `--note` |
| `create-approval` | 创建审批 | `--order-id`, `--assigned-to`, `--note` |
| `approve-order` | 批准 | `--order-id`, `--approver`, `--note` |
| `reject-order` | 驳回 | `--order-id`, `--approver`, `--reason` |
| `get-status-history` | 状态历史 | `--order-id` |
| `stats` | 统计 | `--period`, `--group-by` |

**创建 5 张表**：orders, order_items, status_history, approvals, webhook_events（order-inventory 另创建 3 张表：products, inventory, inventory_reservations；order-logistics 创建 1 张表：shipments；共 **9 张表**）

### Skill 2: `order-inventory-1.0.0` — 库存管理

**目录**：`src/skills/order-inventory-1.0.0/`
**SKILL.md**：声明 `init_script: inventory_tool.py`
**主要脚本**：`scripts/inventory_tool.py`

**核心逻辑**：
- 库存预留：创建订单时预留库存（`SELECT FOR UPDATE` 防超卖）
- 预留提交：付款后提交预留，扣减实际库存
- 预留释放：取消/超时时释放预留
- 低库存预警：数量低于阈值时返回告警
- 外部同步：通过 http_api 从外部 ERP 同步库存快照

**skill_execute 命令**：`check-stock`, `reserve-stock`, `commit-reservation`, `release-reservation`, `update-stock`, `sync-from-external`, `list-low-stock`, `list-products`, `get-product`, `create-product`, `update-product`

**创建 3 张表**：products, inventory, inventory_reservations

### Skill 3: `order-logistics-1.0.0` — 物流发货

**目录**：`src/skills/order-logistics-1.0.0/`
**SKILL.md**：声明 `init_script: logistics_tool.py`
**主要脚本**：`scripts/logistics_tool.py`

**skill_execute 命令**：`create-shipment`, `update-tracking`, `get-shipment`, `list-shipments`, `query-tracking`, `confirm-delivery`

**创建 1 张表**：shipments

### Skill 4: `order-api-1.0.0` — 外部系统 API 配置

**目录**：`src/skills/order-api-1.0.0/`
**SKILL.md**：无 init_script
**主要脚本**：`scripts/load_api_config.py`

与 after-sales-api 完全相同的模式：从 `storage/tenants/{tenant_id}/order-api.md` 加载每租户 API 配置。返回 Markdown 格式的 API 说明供 LLM 理解如何调用外部 ERP/OMS。

---

## 四、数据库 Schema

所有表命名 `bs_order_processing_{entity}`，含 `tenant_id`，`CREATE TABLE IF NOT EXISTS` 幂等创建，无外键/触发器。

### 表 1: `bs_order_processing_orders` — 订单主表

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_orders (
    id SERIAL PRIMARY KEY,
    order_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    customer_name TEXT,
    customer_phone TEXT,
    customer_address TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    total_amount NUMERIC(12,2),
    currency TEXT DEFAULT 'CNY',
    discount_amount NUMERIC(12,2) DEFAULT 0,
    shipping_fee NUMERIC(10,2) DEFAULT 0,
    final_amount NUMERIC(12,2),
    approval_status TEXT,
    approved_by TEXT,
    approved_at TIMESTAMP,
    approval_note TEXT,
    rejection_reason TEXT,
    payment_status TEXT DEFAULT 'unpaid',
    payment_method TEXT,
    payment_transaction_id TEXT,
    paid_at TIMESTAMP,
    notes TEXT,
    tags TEXT,
    external_order_id TEXT,
    source TEXT DEFAULT 'internal',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, status)`, `(user_id, status)`, `(order_id)`

### 表 2: `bs_order_processing_order_items` — 订单明细

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_order_items (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    order_id TEXT NOT NULL,
    item_id TEXT UNIQUE NOT NULL,
    product_sku TEXT,
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price NUMERIC(10,2) NOT NULL,
    discount_rate NUMERIC(5,4) DEFAULT 0,
    subtotal NUMERIC(10,2) NOT NULL,
    product_snapshot TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, order_id)`

### 表 3: `bs_order_processing_status_history` — 状态变更历史

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_status_history (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    order_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_by TEXT,
    change_reason TEXT,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, order_id)`

### 表 4: `bs_order_processing_approvals` — 审批记录

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_approvals (
    id SERIAL PRIMARY KEY,
    approval_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    order_id TEXT NOT NULL,
    approval_type TEXT NOT NULL DEFAULT 'order_approval',
    status TEXT NOT NULL DEFAULT 'pending',
    requested_by TEXT NOT NULL,
    assigned_to TEXT,
    amount_threshold NUMERIC(12,2),
    resolved_by TEXT,
    resolved_at TIMESTAMP,
    note TEXT,
    rejection_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, status)`, `(order_id)`

### 表 5: `bs_order_processing_products` — 商品信息

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_products (
    id SERIAL PRIMARY KEY,
    product_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    product_sku TEXT NOT NULL,
    product_name TEXT NOT NULL,
    category TEXT,
    brand TEXT,
    model TEXT,
    specifications TEXT,                 -- JSON: 规格参数（尺寸、重量、颜色等）
    unit TEXT DEFAULT '个',              -- 计量单位（个、件、箱、kg 等）
    cost_price NUMERIC(10,2),            -- 成本价
    selling_price NUMERIC(10,2),         -- 销售价
    description TEXT,
    images TEXT,                         -- JSON 数组: 图片 URL 列表
    tags TEXT,                           -- JSON 数组: 标签
    status TEXT DEFAULT 'active',        -- active/discontinued/draft
    weight NUMERIC(8,2),                 -- 重量(kg)，用于运费计算
    barcode TEXT,
    external_product_id TEXT,            -- 外部系统商品 ID
    supplier TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, product_sku)
);
```

索引：`(tenant_id, category)`, `(tenant_id, product_sku)`, `(tenant_id, status)`

### 表 6: `bs_order_processing_inventory` — 库存快照

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_inventory (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    product_sku TEXT NOT NULL,
    product_name TEXT,
    quantity_total INTEGER NOT NULL DEFAULT 0,
    quantity_reserved INTEGER NOT NULL DEFAULT 0,
    quantity_available INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER DEFAULT 10,
    external_product_id TEXT,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, product_sku)
);
```

索引：`(tenant_id, product_sku)`

### 表 7: `bs_order_processing_inventory_reservations` — 库存预留

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_inventory_reservations (
    id SERIAL PRIMARY KEY,
    reservation_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    order_id TEXT NOT NULL,
    product_sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, order_id)`, `(status, expires_at)`

### 表 8: `bs_order_processing_shipments` — 发货记录

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_shipments (
    id SERIAL PRIMARY KEY,
    shipment_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    order_id TEXT NOT NULL,
    carrier TEXT,
    tracking_number TEXT,
    shipping_method TEXT,
    weight NUMERIC(8,2),
    shipping_address TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    shipped_at TIMESTAMP,
    delivered_at TIMESTAMP,
    estimated_delivery TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, order_id)`, `(tracking_number)`

### 表 9: `bs_order_processing_webhook_events` — Webhook 事件

```sql
CREATE TABLE IF NOT EXISTS bs_order_processing_webhook_events (
    id SERIAL PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload JSON NOT NULL,
    processed BOOLEAN DEFAULT false,
    processing_result TEXT,
    error_message TEXT,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);
```

索引：`(tenant_id, event_type)`, `(processed, received_at)`

---

## 五、REST API 设计

**文件**：`src/api/order_processing.py`，前缀 `/api/order-processing`

### 订单 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/orders/list` | 订单列表（分页、按状态/关键词/日期筛选） |
| GET | `/orders/detail/{order_id}` | 订单详情（含明细、状态历史、发货信息） |
| POST | `/orders/create` | 创建新订单 |
| PUT | `/orders/update/{order_id}` | 更新订单字段 |
| POST | `/orders/cancel/{order_id}` | 取消订单 |

### 状态与审批

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/orders/{order_id}/change-status` | 状态转换 |
| GET | `/orders/{order_id}/history` | 状态变更历史 |
| GET | `/approvals/list` | 审批列表 |
| POST | `/approvals/{order_id}/approve` | 批准订单 |
| POST | `/approvals/{order_id}/reject` | 驳回订单 |

### 库存

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/products/list` | 商品列表（分页、按分类/关键词筛选） |
| GET | `/products/detail/{product_id}` | 商品详情（含库存状态） |
| POST | `/products/create` | 新增商品 |
| PUT | `/products/update/{product_id}` | 更新商品信息 |
| GET | `/inventory/list` | 库存列表 |
| GET | `/inventory/check` | 查询 SKU 库存 |
| GET | `/inventory/low-stock` | 低库存预警 |

### 发货

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/shipments/list` | 发货记录列表 |
| GET | `/shipments/detail/{shipment_id}` | 发货详情 |
| GET | `/shipments/tracking/{tracking_number}` | 物流追踪 |

### 统计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/stats/overview` | 订单状态汇总、收入统计 |
| GET | `/stats/trend` | 日/周/月订单趋势 |

### Webhook 入站

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/webhooks/{event_type}` | 接收外部系统推送事件 |

Webhook 端点通过 shared secret（存储在 subagent_env_vars 中）验证签名，事件记录到 `bs_order_processing_webhook_events`，后台异步处理。

---

## 六、MCP Server 设计

### 新增模块：`src/mcp/`

MCP (Model Context Protocol) Server 是**全新基础设施**，让外部 AI 智能体和工具能够通过标准 MCP 协议调用订单系统能力。

### 架构

```
外部 AI 智能体 / MCP Client
    ↓ MCP 协议 (stdio / SSE)
src/mcp/server.py — MCP Server
    ↓ 调用
src/api/order_processing.py — 现有 REST API 逻辑复用
```

### 文件结构

```
src/mcp/
├── __init__.py
├── server.py              # MCP Server 主类，协议处理
├── tools.py               # MCP Tool 定义（映射到 skill_execute 命令）
├── transport.py           # 传输层（stdio + SSE）
└── config.py              # MCP 配置（端口、认证、允许的 tools）
```

### MCP Tools 暴露

| MCP Tool 名 | 对应操作 | 参数 |
|-------------|---------|------|
| `order_create` | 创建订单 | customer_name, items[], phone, address |
| `order_get` | 查询订单 | order_id |
| `order_list` | 订单列表 | status, page, page_size |
| `order_update` | 更新订单 | order_id, fields |
| `order_cancel` | 取消订单 | order_id, reason |
| `order_approve` | 审批订单 | order_id, approver, note |
| `order_reject` | 驳回订单 | order_id, approver, reason |
| `inventory_check` | 检查库存 | skus[] |
| `product_list` | 商品列表 | category, keyword, page |
| `product_get` | 商品详情 | product_id |
| `product_create` | 新增商品 | name, sku, price, category, specifications |
| `shipment_create` | 创建发货 | order_id, carrier, tracking_number |
| `shipment_track` | 物流追踪 | tracking_number |
| `order_stats` | 订单统计 | period, group_by |

### 启动方式

MCP Server 作为独立进程运行，通过 `python -m src.mcp.server` 启动。支持两种传输模式：
- **stdio**：本地 CLI / 编辑器插件集成
- **SSE**：远程 HTTP 连接（端口可配置，默认 8765）

### 认证

- API Key 认证：MCP 客户端需提供 key，验证通过 `subagent_env_vars` 存储
- 租户隔离：每个 MCP 连接绑定 tenant_id

### 实现策略

MCP Server 不重复实现业务逻辑，而是**转发到现有的 skill 脚本**：
- MCP tool call → 解析参数 → 调用对应的 skill 脚本命令（如 `python scripts/order_tool.py create-order ...`）→ 返回结果

复用已有的 `skill_execute` 执行路径，只是入口从 LLM 工具调用变为 MCP 协议调用。

---

## 七、与售后子智能体协调

### 方案C：订单为真实来源，售后引用

- **订单处理**拥有 `bs_order_processing_orders` 全部订单数据
- **售后**的 `bs_after_sales_returns` 已有 `order_id` 字段，存储对订单处理表的引用
- 售后创建退货时，通过 order_id 关联到订单处理
- 退货完成后，售后智能体（或主智能体）通过 `skill_execute("order-core", "change-status --order-id X --new-status returned")` 更新订单状态
- **无需数据迁移**，现有售后表保持不变

---

## 八、实施计划

### Phase 1 — 核心（3-4 天）

1. 创建 `subagents/order-processing/SUBAGENT.md`
2. 创建 `src/skills/order-core-1.0.0/`（SKILL.md + `scripts/order_tool.py`，含 init_tables + 订单 CRUD + 状态机）
3. 创建 `src/skills/order-api-1.0.0/`（SKILL.md + `scripts/load_api_config.py`）
4. 验证表自动创建

### Phase 2 — 库存与物流（2-3 天）

5. 创建 `src/skills/order-inventory-1.0.0/`（含库存预留逻辑）
6. 创建 `src/skills/order-logistics-1.0.0/`（含发货追踪逻辑）
7. 更新 SUBAGENT.md 的 skills.allowed

### Phase 3 — REST API（2-3 天）

8. 创建 `src/api/order_processing.py`（全部端点）
9. 在 `src/main.py` 注册路由
10. 更新 `deploy/init-postgres.sql` 和 `deploy/db_update.sql`

### Phase 4 — Webhook 集成（1-2 天）

11. 实现 webhook 入站端点 + 签名验证
12. 实现事件处理器（webhook → 订单状态变更）

### Phase 5 — MCP Server（3-4 天）

13. 创建 `src/mcp/` 模块（server.py, tools.py, transport.py, config.py）
14. 实现 stdio + SSE 传输
15. 实现 MCP tool → skill_execute 转发
16. 认证与租户隔离

### Phase 6 — 测试（1-2 天）

17. 单元测试：`tests/unit/tools/test_order_tool.py`
18. 集成测试：`tests/integration/test_order_flow.py`
19. 与售后子智能体协调测试
20. 租户隔离 + 多 Worker 测试

---

## 九、创建的文件清单

| 文件 | 用途 |
|------|------|
| `subagents/order-processing/SUBAGENT.md` | 子智能体配置 + 系统提示 |
| `src/skills/order-core-1.0.0/SKILL.md` | 核心技能文档 |
| `src/skills/order-core-1.0.0/scripts/order_tool.py` | 核心业务逻辑（~800-1000行） |
| `src/skills/order-inventory-1.0.0/SKILL.md` | 库存技能文档 |
| `src/skills/order-inventory-1.0.0/scripts/inventory_tool.py` | 库存逻辑（~400-500行） |
| `src/skills/order-logistics-1.0.0/SKILL.md` | 物流技能文档 |
| `src/skills/order-logistics-1.0.0/scripts/logistics_tool.py` | 物流逻辑（~400-500行） |
| `src/skills/order-api-1.0.0/SKILL.md` | 外部 API 配置技能 |
| `src/skills/order-api-1.0.0/scripts/load_api_config.py` | 配置加载器（~80行） |
| `src/api/order_processing.py` | REST API 路由（~600-800行） |
| `src/mcp/__init__.py` | MCP 模块 |
| `src/mcp/server.py` | MCP Server 主类 |
| `src/mcp/tools.py` | MCP Tool 定义与转发 |
| `src/mcp/transport.py` | stdio + SSE 传输层 |
| `src/mcp/config.py` | MCP 配置 |

## 修改的文件

| 文件 | 变更 |
|------|------|
| `src/main.py` | 添加 `app.include_router(order_processing.router)` |
| `deploy/init-postgres.sql` | 添加 9 张表的 CREATE TABLE |
| `deploy/db_update.sql` | 添加迁移注释和 DDL |

## 关键参考文件

- `subagents/after-sales/SUBAGENT.md` — SUBAGENT.md 格式参考
- `src/skills/after-sales-core-1.0.0/scripts/after_sales_tool.py` — skill 脚本结构参考
- `src/skills/after-sales-api-1.0.0/` — 外部 API 配置 skill 参考
- `src/api/complaint_handling.py` — REST API 路由参考
- `src/api/customer_followup.py` — 完整 CRUD API 参考

## 验证方式

1. **启动验证**：启动系统后检查 `bs_order_processing_*` 9 张表自动创建
2. **对话验证**：通过 Gradio UI 或前端与订单处理智能体对话，测试创建订单→审批→发货→完成流程
3. **API 验证**：通过 curl/Postman 调用 REST API 端点，验证 CRUD 和状态转换
4. **Webhook 验证**：模拟外部系统 POST 到 webhook 端点，验证事件处理
5. **MCP 验证**：通过 MCP Client 连接，验证 tool discovery 和调用
6. **租户隔离验证**：不同租户创建订单，验证数据完全隔离
7. **售后协调验证**：创建订单后通过售后智能体发起退货，验证 order_id 关联和状态更新
