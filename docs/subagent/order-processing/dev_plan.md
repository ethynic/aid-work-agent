# 订单处理智能体 — 开发计划

## Context

本文档是订单处理智能体的开发计划，基于 [设计文档](./design.md) 实施。MCP Server 基础设施的开发计划单独见 [MCP Server 开发计划](../../infrastructure/mcp_server_dev_plan.md)。

## 前置依赖

- MCP Server 基础设施（可选，Phase 5 之后才需要）
- 现有系统框架（Agent、Skill、Tool、Subagent 体系）

## 开发任务

---

### Phase 1: 核心订单管理（3-4 天）

#### T1.1 子智能体配置
- [x] 创建 `subagents/order-processing/SUBAGENT.md`
  - YAML frontmatter（name、capabilities、triggers、tools、skills、business_pages）
  - Markdown body（系统提示：身份定位、服务流程、工具使用规则、安全规则、降级策略）
- **参考**：`subagents/after-sales/SUBAGENT.md`

#### T1.2 核心 Skill 框架
- [x] 创建 `src/skills/order-core-1.0.0/SKILL.md`
  - 声明 `init_script: order_tool.py`
  - 技能说明、命令列表、使用规则
- [x] 创建 `src/skills/order-core-1.0.0/scripts/order_tool.py`
  - `init_tables()`: 创建 5 张表（orders, order_items, status_history, approvals, webhook_events）
  - 状态机定义（`STATUS_TRANSITIONS` 字典）
  - 订单 CRUD：`create-order`, `get-order`, `list-orders`, `update-order`, `cancel-order`
  - 状态转换：`change-status`（含合法性校验、自动写 status_history）
  - 审批流：`create-approval`, `approve-order`, `reject-order`
  - 统计：`stats`
  - ID 生成：`ord_` + uuid4 hex[:12]
  - 租户隔离：所有查询带 tenant_id
- **参考**：`src/skills/complaint-core-1.0.0/scripts/complaint_tool.py`
- **实际行数**：1217 行

#### T1.3 外部 API 配置 Skill
- [x] 创建 `src/skills/order-api-1.0.0/SKILL.md`
- [x] 创建 `src/skills/order-api-1.0.0/scripts/load_api_config.py`
  - 从 `storage/tenants/{tenant_id}/order-api.md` 加载 API 配置
  - 返回 Markdown 格式的 API 说明
- **参考**：`src/skills/after-sales-api-1.0.0/scripts/load_api_config.py`
- **实际行数**：84 行

#### T1.4 验证
- [x] Python 语法检查通过
- [x] CLI --help 输出正确（11 个命令均注册）
- [x] 启动系统，验证 5 张表自动创建（bs_order_processing_orders/items/status_history/approvals/webhook_events）
- [x] CLI 端到端测试：create-order → get-order → list-orders → change-status → stats 全部通过

---

### Phase 2: 商品与库存管理（2-3 天）

#### T2.1 库存 Skill 框架
- [x] 创建 `src/skills/order-inventory-1.0.0/SKILL.md`
  - 声明 `init_script: inventory_tool.py`
- [x] 创建 `src/skills/order-inventory-1.0.0/scripts/inventory_tool.py`
  - `init_tables()`: 创建 3 张表（products, inventory, inventory_reservations）
  - **商品管理**：
    - `create-product`: 新增商品（SKU、名称、分类、品牌、规格、价格等）
    - `get-product`: 商品详情
    - `list-products`: 商品列表（分页、分类筛选、关键词搜索）
    - `update-product`: 更新商品信息
  - **库存管理**：
    - `check-stock`: 查询 SKU 库存（可用量 = total - reserved）
    - `reserve-stock`: 预留库存（`SELECT FOR UPDATE` 防超卖，写入 reservations 表）
    - `commit-reservation`: 付款后提交预留（扣减 total，清空 reservation）
    - `release-reservation`: 取消/超时释放预留
    - `update-stock`: 手动调整库存
    - `sync-from-external`: 从外部 ERP 同步库存快照
    - `list-low-stock`: 低库存预警列表
- **实际行数**：1054 行

#### T2.2 与订单流程集成
- [x] 在 `order_tool.py` 的 `create-order` 中调用库存预留
- [x] 在 `cancel-order` 中调用库存释放
- [x] 验证创建订单时自动检查并预留库存

#### T2.3 验证
- [x] Python 语法检查通过
- [x] CLI --help 输出正确（11 个命令均注册）
- [x] 测试库存预留/提交/释放完整流程（43 个单元测试全部通过）
- [x] 测试超卖防护（reserve-stock 库存不足场景测试通过）

---

### Phase 3: 物流发货（1-2 天）

#### T3.1 物流 Skill 框架
- [x] 创建 `src/skills/order-logistics-1.0.0/SKILL.md`
  - 声明 `init_script: logistics_tool.py`
- [x] 创建 `src/skills/order-logistics-1.0.0/scripts/logistics_tool.py`
  - `init_tables()`: 创建 1 张表（shipments）
  - `create-shipment`: 创建发货记录（关联 order_id，承运商，运单号）
  - `update-tracking`: 更新物流状态
  - `get-shipment`: 查询发货详情
  - `list-shipments`: 按订单列出发货记录
  - `query-tracking`: 查询物流追踪（本地记录或外部 API）
  - `confirm-delivery`: 确认签收（更新订单状态为 delivered）
- **实际行数**：~420 行

#### T3.2 与订单流程集成
- [x] 发货时自动将订单状态从 `processing` 推进到 `shipped`
- [x] 签收时自动推进到 `delivered`

#### T3.3 验证
- [x] Python 语法检查通过
- [x] CLI --help 输出正确（6 个命令均注册）
- [x] 测试创建发货 → 更新物流 → 确认签收完整流程（34 个单元测试全部通过）

---

### Phase 4: REST API（2-3 天）

#### T4.1 API 路由文件
- [ ] 创建 `src/api/order_processing.py`
  - **订单 CRUD**: `GET/POST/PUT /orders/*`
  - **状态与审批**: `POST /orders/{id}/change-status`, `GET/POST /approvals/*`
  - **商品**: `GET/POST/PUT /products/*`
  - **库存**: `GET /inventory/*`
  - **发货**: `GET /shipments/*`
  - **统计**: `GET /stats/overview`, `GET /stats/trend`
  - **Webhook**: `POST /webhooks/{event_type}`
  - 全部端点带租户隔离（使用 `getAuthHeader()` / `X-Tenant-Id`）
- **参考**：`src/api/complaint_handling.py`, `src/api/customer_followup.py`
- **预计行数**：~600-800 行

#### T4.2 注册路由
- [ ] 在 `src/main.py` 中 `app.include_router(order_processing.router)`

#### T4.3 数据库迁移文件
- [ ] 更新 `deploy/init-postgres.sql` — 添加 9 张表的 CREATE TABLE + 索引
- [ ] 更新 `deploy/db_update.sql` — 添加增量变更注释和 DDL

#### T4.4 Webhook 实现
- [ ] Webhook 签名验证（HMAC-SHA256，shared secret 从 subagent_env_vars 读取）
- [ ] 事件记录到 `bs_order_processing_webhook_events`
- [ ] 后台异步处理：将 webhook 事件映射到订单状态变更

#### T4.5 验证
- [ ] 通过 curl/Postman 测试全部 API 端点
- [ ] 测试 Webhook 回调处理
- [ ] 测试租户隔离（不同租户数据不可交叉访问）

---

### Phase 5: 前端页面（2-3 天）

#### T5.1 订单管理页面
- [ ] 创建 `frontend/src/pages/order-processing/OrderList.vue`
  - 订单列表（表格 + 分页 + 状态筛选 + 关键词搜索）
  - 使用 PortalLayout + AppHeader 布局

#### T5.2 订单详情页
- [ ] 创建 `frontend/src/pages/order-processing/OrderDetail.vue`
  - 订单基本信息、商品明细、状态时间线、发货信息

#### T5.3 商品管理页面
- [ ] 创建 `frontend/src/pages/order-processing/ProductList.vue`
  - 商品列表 + 新增/编辑弹窗

#### T5.4 库存与发货页面
- [ ] 创建 `frontend/src/pages/order-processing/InventoryList.vue`
- [ ] 创建 `frontend/src/pages/order-processing/ShipmentList.vue`

#### T5.5 订单统计页面
- [ ] 创建 `frontend/src/pages/order-processing/OrderStats.vue`
  - 状态分布饼图 + 趋势折线图

#### T5.6 前端路由与 API
- [ ] 在前端路由中注册以上页面
- [ ] 创建 `frontend/src/api/orderProcessing.ts` — API 调用封装

#### T5.7 验证
- [ ] 前端 `npm run build` 构建通过
- [ ] 各页面功能验证

---

### Phase 6: 测试与协调（1-2 天）

#### T6.1 单元测试
- [ ] `tests/unit/tools/test_order_tool.py` — order-core skill 脚本测试
- [ ] `tests/unit/tools/test_inventory_tool.py` — order-inventory skill 脚本测试
- [ ] `tests/unit/tools/test_logistics_tool.py` — order-logistics skill 脚本测试

#### T6.2 集成测试
- [ ] `tests/integration/test_order_flow.py` — 完整订单流程测试
  - 创建订单 → 检查库存 → 审批 → 发货 → 签收 → 完成
- [ ] Webhook 事件处理测试

#### T6.3 跨智能体协调测试
- [ ] 与 after-sales 子智能体协调测试
  - 订单处理创建订单 → 售后发起退货 → 订单状态更新为 returned
- [ ] 验证 order_id 交叉引用正确

#### T6.4 租户与多 Worker 测试
- [ ] 不同租户创建订单，验证数据完全隔离
- [ ] 多 Worker 环境下库存预留一致性测试

---

## 文件清单

### 新建文件

| 文件 | Phase | 用途 |
|------|-------|------|
| `subagents/order-processing/SUBAGENT.md` | T1.1 | 子智能体配置 |
| `src/skills/order-core-1.0.0/SKILL.md` | T1.2 | 核心技能文档 |
| `src/skills/order-core-1.0.0/scripts/order_tool.py` | T1.2 | 核心业务逻辑 |
| `src/skills/order-api-1.0.0/SKILL.md` | T1.3 | 外部 API 配置技能 |
| `src/skills/order-api-1.0.0/scripts/load_api_config.py` | T1.3 | 配置加载器 |
| `src/skills/order-inventory-1.0.0/SKILL.md` | T2.1 | 库存技能文档 |
| `src/skills/order-inventory-1.0.0/scripts/inventory_tool.py` | T2.1 | 商品+库存逻辑（1054 行） |
| `src/skills/order-logistics-1.0.0/SKILL.md` | T3.1 | 物流技能文档 |
| `src/skills/order-logistics-1.0.0/scripts/logistics_tool.py` | T3.1 | 物流逻辑（~420 行） |
| `src/api/order_processing.py` | T4.1 | REST API 路由 |
| `frontend/src/pages/order-processing/OrderList.vue` | T5.1 | 订单管理页 |
| `frontend/src/pages/order-processing/OrderDetail.vue` | T5.2 | 订单详情页 |
| `frontend/src/pages/order-processing/ProductList.vue` | T5.3 | 商品管理页 |
| `frontend/src/pages/order-processing/InventoryList.vue` | T5.4 | 库存页面 |
| `frontend/src/pages/order-processing/ShipmentList.vue` | T5.4 | 发货记录页 |
| `frontend/src/pages/order-processing/OrderStats.vue` | T5.5 | 统计页面 |
| `frontend/src/api/orderProcessing.ts` | T5.6 | 前端 API 封装 |
| `tests/unit/tools/test_order_tool.py` | T6.1 | 核心逻辑测试 |
| `tests/unit/tools/test_inventory_tool.py` | T6.1 | 库存逻辑测试 |
| `tests/unit/tools/test_logistics_tool.py` | T6.1 | 物流逻辑测试 |
| `tests/integration/test_order_flow.py` | T6.2 | 集成测试 |

### 修改文件

| 文件 | Phase | 变更 |
|------|-------|------|
| `src/main.py` | T4.2 | 注册 `order_processing.router` |
| `deploy/init-postgres.sql` | T4.3 | 添加 9 张表 DDL |
| `deploy/db_update.sql` | T4.3 | 添加增量迁移 |

## 总工期：约 12-17 天

| Phase | 工期 | 依赖 |
|-------|------|------|
| Phase 1: 核心 | 3-4 天 | 无 |
| Phase 2: 商品库存 | 2-3 天 | Phase 1 |
| Phase 3: 物流 | 1-2 天 | Phase 1 |
| Phase 4: REST API | 2-3 天 | Phase 1-3 |
| Phase 5: 前端 | 2-3 天 | Phase 4 |
| Phase 6: 测试 | 1-2 天 | Phase 1-5 |

Phase 2 和 Phase 3 可以并行开发（均只依赖 Phase 1）。

MCP Server 基础设施独立开发，不在本计划范围内。订单处理的 MCP 工具注册在 MCP Server 完成后对接即可。
