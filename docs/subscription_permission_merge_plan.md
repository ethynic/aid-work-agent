# 订阅与权限表合并改造计划

> 版本: v1.0
> 创建日期: 2026-04-29
> 状态: 待评审通过
> 预计工时: 1人天

---

## 一、项目背景

### 1.1 问题现状

当前 SaaS 模块存在两张职责重叠的表：

| 表名 | 职责 | 问题 |
|------|------|------|
| `tenant_agent_permissions` | 租户级白名单：能不能用某个智能体 | 仅控制"有没有"，不控制数量和时间 |
| `subscriptions` | 付费订阅记录：买没买、买了多少、什么时候过期 | 有时间、有配额、但权限检查不查此表 |

**核心矛盾：**

```python
# checker.py 第55行 - 只检查 permissions，不检查订阅
if not TenantAgentPermissionDB.has_permission(conn, tenant_id, agent_id):
    return False
```

导致：
1. ✅ 租户在 permissions 表有记录 → 可以使用
2. ❌ 但 subscriptions 中订阅已过期 / 不存在 → 理论上不能用
3. ⚠️ **实际代码不检查第2条！用户可以免费用但 token 不计费

### 1.2 触发场景

本次改造同时解决以下两个需求：

1. **架构简化**：合并两张表，消除数据不一致风险
2. **实例并发控制**：在 subscriptions 表增加 `instance_quota` 字段，支持按实例数收费

---

## 二、设计方案

### 2.1 总体方案

**方案选择：方案二（starts_at + expires_at 时间窗口）

**核心思想**：
- 删除 `tenant_agent_permissions` 表
- 权限检查和配额查询统一走 `subscriptions` 表
- 通过 `starts_at` 和 `expires_at` 精确控制生效时间窗口

### 2.2 表结构变更

#### subscriptions 表增强：

```sql
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS instance_quota INTEGER DEFAULT 1;

-- 创建复合索引：租户 + 子智能体类型 + 时间 + 状态
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range 
    ON subscriptions(tenant_id, subagent_type, starts_at, expires_at, status);
```

**变更后完整表结构：**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL | 主键 |
| subscription_id | TEXT | 订阅唯一ID |
| tenant_id | TEXT | 租户ID |
| user_id | TEXT | 用户ID（可选，按用户订阅）|
| subagent_type | TEXT | 子智能体类型 |
| instance_quota | INTEGER | 并发实例数配额，默认1 |
| billing_cycle | TEXT | 计费周期：monthly/yearly |
| unit_price | REAL | 单价 |
| token_quota | INTEGER | token配额，-1表示不限 |
| tokens_used | INTEGER | 已用token数 |
| status | TEXT | 状态：active/expired/cancelled |
| payment_status | TEXT | 支付状态：pending/paid/failed |
| starts_at | TIMESTAMP | 生效时间，默认当前时间 |
| expires_at | TIMESTAMP | 过期时间 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

### 2.3 保留的表

**`user_agent_permissions` 表** 仍然需要，用于租户内用户级授权：

- 租户级：subscriptions 控制"租户能使用哪些智能体、多少实例
- 用户级：user_agent_permissions 控制"租户内哪些用户能使用"

两层授权是正交关系，不重复。

---

## 三、关键业务逻辑

### 3.1 权限检查与配额获取（一体化）

```python
def get_agent_quota(tenant_id: str, agent_id: str) -> Tuple[bool, int]:
    """
    获取租户对某个智能体的访问权限和当前配额

    SQL查询逻辑：
    - status = 'active'
    - starts_at <= CURRENT_TIMESTAMP（已生效）
    - expires_at > CURRENT_TIMESTAMP（未过期）

    Returns:
        (has_access: bool, instance_quota: int)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) > 0 AS has_access,
                COALESCE(MAX(instance_quota), 0) AS instance_quota
            FROM subscriptions
            WHERE tenant_id = %s 
              AND subagent_type = %s 
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (tenant_id, agent_id))
        row = cursor.fetchone()
        return row["has_access"], row["instance_quota"]
```

### 3.2 时间窗口示例

**场景：** 某租户5月租用1实例，6月升级到2实例

| subscription_id | subagent_type | instance_quota | starts_at | expires_at |
|-----------------|---------------|----------------|------------|------------|
| sub_may_001 | trade-specialist | 1 | 2026-05-01 00:00:00 | 2026-05-31 23:59:59 |
| sub_jun_002 | trade-specialist | 2 | 2026-06-01 00:00:00 | 2026-06-30 23:59:59 |

**不同时间点的查询结果：

| 查询时间 | has_access | instance_quota | 说明 |
|----------|-----------|---------------|------|
| 2026-05-30 | true | 1 | 5月套餐生效中 |
| 2026-06-01 | true | 2 | 自动切换到6月套餐 |
| 2026-07-01 | false | 0 | 都过期了 |

### 3.3 权限检查完整流程

```
用户请求 /api/chat/stream
    ↓
TenantContextMiddleware 解析 tenant_id
    ↓
check_agent_access(agent_id, user) 调用 get_agent_quota
    ↓
┌─ has_access = false? → 返回 403 无权限 ─┐
│                                              │
└─ has_access = true? → 进入并发控制逻辑    │
                   ↓                           │
            ConcurrencyControlService          │
                acquire_lock(tenant_id, agent_id)  │
                   ↓                           │
            申请 instance_quota 内允许?          │
                   ↓                           │
            超过配额 → 排队 / 返回稍后重试        │
            配额内 → 执行任务               │
```

---

## 四、实施步骤

### Phase 1：数据库变更（0.2天）

**涉及文件：** `deploy/init-postgres.sql`, `deploy/db_update.sql`

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1.1 | 给 subscriptions 加 `starts_at` 字段 | 默认值 CURRENT_TIMESTAMP | |
| 1.2 | 给 subscriptions 加 `instance_quota` 字段 | 默认值 1 | |
| 1.3 | 创建复合索引 | `idx_subscriptions_time_range` | |
| 1.4 | 数据迁移：从 tenant_agent_permissions 迁移数据 | 迁移到 subscriptions 表，设置 starts_at=创建时间，expires_at=null（永久有效） | |

**迁移SQL脚本：**

```sql
-- deploy/db_update.sql

-- 1. 新增字段
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS instance_quota INTEGER DEFAULT 1;

-- 2. 创建索引
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range 
    ON subscriptions(tenant_id, subagent_type, starts_at, expires_at, status);

-- 3. 数据迁移：把 tenant_agent_permissions 迁移到 subscriptions
INSERT INTO subscriptions (
    subscription_id, tenant_id, subagent_type, instance_quota, 
    status, starts_at, created_at, updated_at
)
SELECT 
    'sub_mig_' || md5(random()::text)::uuid::text AS subscription_id,
    tenant_id,
    agent_id AS subagent_type,
    1 AS instance_quota,
    'active' AS status,
    CURRENT_TIMESTAMP AS starts_at,
    CURRENT_TIMESTAMP AS created_at,
    CURRENT_TIMESTAMP AS updated_at
FROM tenant_agent_permissions tap
WHERE NOT EXISTS (
    SELECT 1 FROM subscriptions s 
    WHERE s.tenant_id = tap.tenant_id 
      AND s.subagent_type = tap.agent_id
      AND s.status = 'active'
);

-- 4. 备份旧表（不直接删除）
ALTER TABLE tenant_agent_permissions RENAME TO tenant_agent_permissions_backup;
```

### Phase 2：权限检查逻辑改造（0.2天）

**涉及文件：** `src/saas/permissions/checker.py`

| 步骤 | 操作 | 说明 |
|------|------|------|
| 2.1 | 新增 `get_agent_quota()` 函数 | 一体化查询权限和配额 | |
| 2.2 | 修改 `check_agent_access()` | 改为查询 subscriptions 表 | |
| 2.3 | 修改 `get_allowed_agent_ids_for_user()` | 改为查询 subscriptions | |
| 2.4 | 修改 `count_tenant_allowed_agents()` | 改为查询 subscriptions | |

### Phase 3：权限管理API改造（0.3天）

**涉及文件：** `src/saas/api/permissions.py`, `src/saas/db/permission_db.py`

| 步骤 | 操作 | 说明 |
|------|------|------|
| 3.1 | 修改 GET /api/saas/permissions/tenant/{tenant_id} | 查询 subscriptions 表 | |
| 3.2 | 修改 POST /api/saas/permissions/tenant/{tenant_id} | 创建/更新 subscriptions | |
| 3.3 | 新增 instance_quota 参数支持 | API支持设置实例数配额 | |
| 3.4 | 保留用户级权限API不变 | user_agent_permissions 相关接口不变 | |
| 3.5 | 删除 TenantAgentPermissionDB 类 | 或标记为 deprecated | |

### Phase 4：SubscriptionDB 增强（0.1天）

**涉及文件：** `src/saas/db/subscription_db.py`

| 步骤 | 操作 | 说明 |
|------|------|------|
| 4.1 | create() 增加 starts_at 参数 | 支持设置生效时间 | |
| 4.2 | 新增 get_current_quota() 方法 | 查询当前生效的配额 | |
| 4.3 | 其他现有方法兼容 | 确保不破坏现有逻辑 | |

### Phase 5：测试与验证（0.2天）

| 步骤 | 操作 | 说明 |
|------|------|------|
| 5.1 | 单元测试 | 权限检查边界条件 | |
| 5.2 | 集成测试 | 完整调用链路 | |
| 5.3 | 数据回滚方案 | 出现问题时从 backup 表恢复 | |

---

## 五、涉及文件清单

### 新增文件
- 无

### 修改文件

| 文件路径 | 改动范围 |
|----------|-----------|
| `deploy/init-postgres.sql` | 更新 subscriptions 表定义和索引 |
| `deploy/db_update.sql` | 字段新增 + 数据迁移脚本 |
| `src/saas/permissions/checker.py` | 权限检查逻辑重写 |
| `src/saas/api/permissions.py` | 租户权限管理API改造 |
| `src/saas/db/permission_db.py` | 删除 TenantAgentPermissionDB 或标记 deprecated |
| `src/saas/db/subscription_db.py` | 增强 starts_at 和 instance_quota 支持 |

### 删除文件（备份后）
- `tenant_agent_permissions` 表（重命名为 backup）

---

## 六、零停机迁移方案

### Step 1：数据库变更（无破坏性）

```sql
-- 1. 加字段和索引（不影响现有业务）
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS instance_quota INTEGER DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_subscriptions_time_range ...;

-- 2. 双写：新权限写入两张表都写
-- 3. 读仍然读旧表
```

### Step 2：部署新代码，切换查询源

部署代码改为读取 subscriptions 表，验证所有场景正常。

### Step 3：数据迁移与清理

```sql
-- 迁移历史数据
INSERT INTO subscriptions (...) SELECT ... FROM tenant_agent_permissions ...;

-- 备份旧表
ALTER TABLE tenant_agent_permissions RENAME TO tenant_agent_permissions_backup;
```

### Step 4：观察期（1-2天）

运行观察期内保留 backup 表，出现问题可随时回滚。

### Step 5：最终清理

确认无问题后，删除 backup 表和 TenantAgentPermissionDB 类。

---

## 七、测试计划

### 7.1 单元测试

| 测试用例 | 验证内容 |
|----------|---------|
| test_get_agent_quota_active | 有效订阅返回正确配额 |
| test_get_agent_quota_expired | 过期订阅返回无权限 |
| test_get_agent_quota_future_starts | 未来生效的订阅当前不可用 |
| test_get_agent_quota_overlap | 订阅重叠时取最大配额正确 |
| test_check_agent_access_integration | 完整权限检查流程 |

### 7.2 集成测试

| 测试场景 | 验证内容 |
|----------|---------|
| 新建租户授权智能体 | 授权后可以访问 |
| 订阅过期 | 过期后无法访问 |
| 提前续费升级 | 新套餐在 starts_at 后自动生效 |
| 实例配额查询 | 正确返回 instance_quota |

### 7.3 回滚测试

验证从 backup 表恢复数据的流程可行。

---

## 八、风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 数据迁移遗漏 | 中 | 迁移前备份，迁移后 COUNT 对比 |
| 现有代码依赖 TenantAgentPermissionDB | 中 | 全局搜索引用，确保所有调用点都改造 |
| 权限检查逻辑变更影响其他功能 | 高 | 充分测试，保留回滚能力 |
| 第三方集成（钉钉/飞书渠道权限检查 | 中 | 检查所有渠道接入点的权限验证 |

**回滚方案：**

```sql
-- 回滚步骤1：恢复旧表名
ALTER TABLE tenant_agent_permissions_backup RENAME TO tenant_agent_permissions;

-- 回滚步骤2：回滚代码版本
-- 部署旧版本 checker.py，恢复查旧表逻辑
```

---

## 九、后续关联项目

本改造完成后，为**实例并发控制**功能铺平了道路：

1. ✅ 并发控制可直接使用 `instance_quota` 字段（无需新增表）
2. ✅ 权限检查和配额获取一体化，无需额外查询
3. ✅ 时间窗口机制天然支持套餐升级/续费场景

---

## 十、总结

| 指标 | 改造前 | 改造后 |
|------|--------|--------|
| 表数量 | 2张（permissions + subscriptions） | 1张（subscriptions） |
| 权限检查来源 | permissions | subscriptions |
| 数据一致性 | 可能不一致（有权限但无订阅） | 单一数据源，天然一致 |
| 实例配额 | 无 | 有（instance_quota） |
| 时间控制 | 无 | 精确时间窗口（starts_at + expires_at） |
| 代码复杂度 | 2套逻辑 | 1套逻辑 |
| 改造工作量 | - | 1人天 |

**结论：架构更简洁、数据更一致、天然支持实例并发控制！
