## Context

系统现有 `chat_records` 表已存储完整的Token消耗数据（`prompt_tokens`, `completion_tokens`），但缺乏直观的统计报表功能。已有的用量报告（`UsageReports.vue`）展示的是聚合数据，不满足按租户汇总和按对话明细查看的需求。

现有架构：
- 后端：FastAPI，已有 `UsageLogDB` 类提供租户用量聚合查询，`ChatRecordDB` 类提供会话记录访问
- 前端：Vue 3 + Vue Router，平台管理后台路由 `/portal/*`，租户前台路由 `/t/:tenant_id/*`
- 权限：平台管理员 (`platform_admin`)、租户管理员 (`tenant_admin`)
- 现有报表：`UsageReports.vue` 展示租户聚合数据（输入/输出Token趋势、模型统计、用户明细）

约束条件：
1. 历史测试数据需排除：`prompt_tokens=0 and completion_tokens=0`
2. Token数量显示单位：百万Token，保留2位小数
3. 月份选择器使用现有UI组件库
4. 租户明细需分页（每页100条）
5. 暂不实现导出功能和缓存策略

## Goals / Non-Goals

**Goals:**
1. 为平台管理员提供跨租户Token消耗汇总视图，支持按月筛选
2. 为租户管理员提供本租户Token消耗明细视图，支持按月筛选和分页
3. 数据展示准确，排除测试数据，单位统一
4. 界面集成到现有菜单系统，权限控制正确
5. 查询性能可接受，支持按月数据量级别

**Non-Goals:**
1. 导出为CSV/Excel格式
2. 数据缓存机制
3. 实时数据更新（报表数据非实时）
4. 跨月对比分析
5. Token消耗预警功能

## Decisions

### 1. API设计决策
- **平台报表API**：`GET /api/admin/token-usage?month=YYYY-MM`
  - 认证：仅平台管理员 (`is_platform_admin`)
  - 响应结构：包含汇总信息和租户列表
- **租户明细API**：`GET /api/saas/reports/token-details?month=YYYY-MM&page=1&page_size=100`
  - 认证：租户管理员 (`require_admin`)
  - 响应结构：包含汇总信息和分页的对话明细列表

**替代方案考虑**：曾考虑复用现有 `/api/saas/reports/` 路由，但现有接口是聚合统计，不满足明细需求。新设计保持接口职责单一。

### 2. 数据查询决策
- **月份处理**：前端传递 `YYYY-MM`，后端转换为 `YYYY-MM-01 00:00:00` 到 `YYYY-MM-31 23:59:59`
- **测试数据过滤**：WHERE条件增加 `(prompt_tokens != 0 OR completion_tokens != 0)`
- **单位转换**：后端计算原始Token数，前端转换为百万单位并保留2位小数
- **分页实现**：租户明细使用SQL的 `LIMIT/OFFSET`，平台报表无需分页（租户数量有限）

**替代方案考虑**：曾考虑在后端进行单位转换，但决定在前端进行以保持API响应数据原始性，便于未来可能的数据导出。

### 3. 前端架构决策
- **组件复用**：复用现有表格组件和月份选择器组件
- **路由集成**：
  - 平台报表：`/portal/token-usage`
  - 租户明细：`/t/:tenant_id/token-usage`
- **菜单集成**：
  - 平台菜单：在 `PortalLayout.vue` 的 `portalMenuItems` 中添加
  - 租户菜单：在 `MenuSidebar.vue` 的 `adminSubMenuItems` 中添加
- **权限控制**：租户明细页面仅租户管理员可见，通过现有 `isTenantAdmin` 计算属性控制

### 4. 性能优化决策
- **索引检查**：确保 `chat_records` 表有 `(tenant_id, created_at)` 复合索引
- **查询优化**：按月查询减少数据扫描范围
- **前端虚拟滚动**：暂不实现，分页已满足需求

## Risks / Trade-offs

### 1. 查询性能风险
- **风险**：租户明细分页查询在数据量大时可能较慢
- **缓解**：按月查询天然分区，`created_at` 索引优化，考虑后期添加 `(tenant_id, created_at)` 复合索引

### 2. 数据准确性风险
- **风险**：排除测试数据的逻辑可能误排除有效数据（极少数真实对话token数为0）
- **缓解**：逻辑为 `prompt_tokens=0 AND completion_tokens=0`，真实对话极不可能两者同时为0

### 3. 前端显示一致性问题
- **风险**：单位转换和格式化在前端可能导致不一致
- **缓解**：使用统一的格式化工具函数，确保所有显示位置使用相同逻辑

### 4. 权限控制风险
- **风险**：租户管理员可能通过API直接访问其他租户数据
- **缓解**：后端API严格校验 `tenant_id`，租户管理员只能访问自己租户的数据

### 5. 月份边界处理风险
- **风险**：不同时区可能导致月份计算偏差
- **缓解**：使用UTC时间或数据库服务器时区，确保月份计算一致