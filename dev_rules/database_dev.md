# 数据库表开发规范

## 概述

本文档规定了系统数据库开发规范。

## 表分类与命名规范

### 1. 系统表（System Tables）

系统表是智能体运行必需的核心表，维持原命名不变，不需要 `bs_` 前缀。

**示例表**：
- `users` - 用户表
- `chat_sessions` - 会话表
- `chat_messages` - 消息表
- `chat_records` - 会话记录表
- `documents` - 文档表
- `chunks` - 文本块表
- `tenants` - 租户表（SaaS）
- `subscriptions` - 订阅表（SaaS）

### 2. 业务数据表（Business Data Tables）

业务数据表是子智能体产生的业务数据（如外贸智能体的客户数据、邮件数据），必须遵守以下规则。

#### 命名规则

表名必须以 `bs_` 开头，格式：

```
bs_[subagent]_[tablename]
```

- `[subagent]`：子智能体目录名称，连字符 `-` 替换为下划线 `_`
- `[tablename]`：业务表的功能名称，全小写，单词间用下划线分隔

**示例**：

| 子智能体 | 原表名 | 最终业务表名 |
|---------|--------|-------------|
| trade-specialist | matched_customers | `bs_trade_specialist_matched_customers` |
| trade-specialist | customer_emails | `bs_trade_specialist_customer_emails` |

#### 租户隔离要求

所有业务数据表（`bs_` 开头）**必须**包含 `tenant_id` 字段，实现租户数据隔离。当 SaaS 模式禁用时，该字段允许为 `NULL`。

```sql
CREATE TABLE IF NOT EXISTS bs_trade_specialist_matched_customers (
    id SERIAL PRIMARY KEY,
    customer_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT,  -- 租户ID，SAAS模式下必填，非SAAS模式可为NULL
    user_id TEXT NOT NULL,
    ...
);
```

#### 新建业务表检查清单

新增业务数据表时，请确认以下事项：

- [ ] 表名以 `bs_` 开头
- [ ] 表名包含子智能体名称（连字符 `-` 已替换为下划线 `_`）
- [ ] 表中包含 `tenant_id` 字段（用于租户隔离）
- [ ] 在 skill 加载时调用 `init_tables()` 初始化表
- [ ] 更新本文档，保持规范一致性

## 表初始化机制

子智能体业务表采用**延迟初始化**机制，在 skill 加载时自动创建表（如果表不存在）。

### 初始化流程

1. **Skill 加载阶段**：`SkillLoader._init_skill_tables()` 遍历所有已加载 skill，调用各 skill 的表初始化函数
2. **系统启动阶段**：`init_database()` 调用所有 skill 的表初始化函数

所以，业务表不需要手动在 `init-postgres.sql` 中创建，代码会自动处理。

### 示例：trade-customer skill

```python
# src/skills/trade-customer-1.0.0/scripts/customer_manager.py
def init_tables():
    """初始化客户相关表"""
    # 创建业务数据表（CREATE TABLE IF NOT EXISTS 保证幂等）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_trade_specialist_matched_customers (
            id SERIAL PRIMARY KEY,
            customer_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            ...
        )
    """)
```

## 数据库变更与迁移

开发过程中，涉及到加表、加字段、改字段等操作，**必须同时修改两个文件**：

| 文件 | 用途 | 要求 |
|------|------|------|
| `deploy/init-postgres.sql` | 全新环境的数据库初始化 | 在 `CREATE TABLE` 语句中添加新字段/新表，保证新部署能直接创建完整表结构 |
| `deploy/db_update.sql` | 现有环境的增量升级 | 所有增量变更都必须记录在此文件，**不要单独创建其它迁移文件**。每条变更前添加注释，包含变更日期和简单说明 |

**示例 `db_update.sql` 条目**：

```sql
-- 2026-4-25，chat_sessions 增加 subagent_id 字段，记录会话关联的数字员工ID
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS subagent_id TEXT;
```

## 数据库设计禁忌

开发过程中，请避免使用如下数据库特性，所有业务逻辑尽量在应用层（Python）实现：

### 1. 触发器

数据库触发器会使业务逻辑分散在代码和数据库中，难以调试、测试和版本管理。**所有业务逻辑都应在 Python 代码中实现**。

### 2. 外键约束

外键约束存在以下问题：
- 增加数据库写入开销
- 使删除/更新操作复杂化（级联删除可能误删数据）
- 在分布式环境下不一定可靠

**正确做法**：外键引用完整性在 Python 代码中检查，不要在数据库层面强制约束。

### 3. 非必要的 `NOT NULL` 约束

- 业务层面的非空检查应在 Python 代码/Pydantic 模型中实现
- 数据库层面只对必须非空的字段（如主键）设置 `NOT NULL`
- 可选字段允许为 `NULL`，给后续需求迭代留有余地

**例外**：主键、自增 ID 等必须非空的字段除外。

### 4. 存储过程/函数

所有业务逻辑都应在应用层（Python）实现，不要把业务逻辑放到数据库存储过程中。存储过程难以版本管理和调试。

### 5. 复杂视图

复杂视图难以调试和性能优化，应在应用层通过多次查询后在内存中 join 数据。

### 6. 数据库端计算

排序、过滤、聚合等计算尽量在数据库层面（利用索引），但复杂业务计算应放到应用层。
