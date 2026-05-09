# 代码审查报告：逻辑混乱与硬编码枚举值

**审查日期**: 2026-05-09
**审查范围**: 数据库状态字段硬编码、逻辑混乱、前后矛盾
**审查人**: Claude Code

---

## 一、数据库状态字段硬编码问题

根据项目规范（`.claude/rules/backend_dev.md`），所有 SaaS 相关表字段的枚举值必须使用 `src/saas/models/enums.py` 中定义的枚举类。但审查发现多处代码存在硬编码字符串，违反了规范。

### 1. 用户状态硬编码

**文件**: `src/db/models.py:245, 251, 274, 280`

```python
# 第245行
"SELECT COUNT(*) as cnt FROM users WHERE tenant_id = %s AND status = 'active'",

# 第251行
WHERE tenant_id = %s AND status = 'active'

# 第274行
AND tenant_id = %s AND status = 'active'

# 第280行
WHERE role = 'platform_admin' AND status = 'active'
```

**问题**: 硬编码 `'active'`，应使用 `UserStatus.ACTIVE.value` 或直接导入 `UserStatus` 枚举。

**影响**: 如果 `UserStatus` 枚举值变更，这些查询将失效。

**建议**: 
```python
from src.saas.models.enums import UserStatus
f"WHERE tenant_id = %s AND status = '{UserStatus.ACTIVE.value}'"
```

### 2. 排队状态硬编码

**文件**: `src/saas/services/instance_service.py` 多处

```python
# 第80行
WHERE status = 'waiting'

# 第197行
status = 'busy',

# 第226行
status = 'busy',

# 第258行
WHERE instance_id = %s AND session_id = %s AND status = 'waiting'

# 第339行
status = 'idle',

# 第467行
WHERE instance_id = %s AND status = 'waiting'

# 第519行
SET status = 'cancelled'

# 第625行
status = 'idle',

# 第662行
status = 'idle',

# 第684行
status = 'idle',

# 第704行
SET status = 'expired'

# 第705行
AND status = 'waiting'

# 第714行
SET status = 'abandoned'

# 第716行
AND status = 'waiting'

# 第735行
WHERE instance_id = %s AND status = 'waiting'

# 第760行
SET status = 'ready',
```

**问题**: 这些状态字符串对应 `QueueStatus` 和 `AgentInstanceStatus` 枚举，但代码中直接硬编码。

**影响**: 状态值变更时需修改多处，容易遗漏。

**建议**: 使用 `QueueStatus.WAITING.value` 等枚举值。

### 3. 订阅状态硬编码

**文件**: `src/saas/db/subscription_db.py` 多处

```python
# 第129行
AND status = 'active'

# 第147行
AND status = 'active'

# 第165行
AND status = 'active'

# 第198行
SET status = 'cancelled'

# 第201行
AND status = 'active'

# 第208行
AND status = 'active'

# 第228行
AND status = 'active'

# 第291行
SET status = 'cancelled'

# 第292行
WHERE subagent_type = %s AND status = 'active'

# 第307行
AND s.status = 'active'
```

**问题**: 应使用 `SubscriptionStatus` 枚举。

### 4. 支付状态硬编码

**文件**: `src/saas/services/payment.py:125, 127, 143`

```python
# 第125行
SET payment_status = 'paid'

# 第127行
AND payment_status = 'pending'

# 第143行
SET status = 'active', payment_status = 'paid'
```

**问题**: 应使用 `PaymentStatus` 枚举。

### 5. 租户状态硬编码

**文件**: `src/saas/db/tenant_db.py:145`

```python
"UPDATE tenants SET status = 'deactivated'"
```

**问题**: 应使用 `TenantStatus` 枚举。

---

## 二、逻辑混乱与前后矛盾

暂时跳过

## 三、其他代码质量问题

### 1. SQL 占位符模式冗余

**文件**: 多处使用 `placeholder = "%s"` 模式

```python
placeholder = "%s"
cursor.execute(f"SELECT * FROM users WHERE username = {placeholder}", (identifier,))
```

**问题**: 使用 `f"..."` + 变量占位符 `%s` 的方式比直接写 `%s` 更难读，且给人 "SQL 拼接" 的直觉不安感。

**建议**: 直接使用 `%s`，不需要赋值 `placeholder` 变量。

### 2. 缺少枚举导入

**文件**: 多个使用硬编码状态的文件顶部缺少枚举导入

**建议**: 在文件顶部统一导入相关枚举类。

---
