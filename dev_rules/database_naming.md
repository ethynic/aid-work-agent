# 数据库表命名规范

## 概述

本文档规定了 AID Work Agent 系统中数据库表的命名规则，确保表名清晰、可维护，并支持多租户隔离。

## 表分类

### 1. 系统表（System Tables）

系统表是智能体运行必需的表，维持原命名不变。

**示例表**：
- `users` - 用户表
- `chat_sessions` - 会话表
- `chat_messages` - 消息表
- `chat_records` - 会话记录表
- `matched_customers` - 匹配客户表（系统级）
- `documents` - 文档表
- `chunks` - 文本块表

### 2. 业务数据表（Business Data Tables）

业务数据表是子智能体产生的业务数据，表名以 `bs_` 开头。

**命名规则**：
```
bs_[subagent]_[tablename]
```

- `[subagent]`：子智能体名称，`-` 替换为 `_`
- `[tablename]`：原业务表名

**示例**：
| 子智能体 | 原表名 | 业务表名 |
|---------|--------|---------|
| trade-specialist | matched_customers | bs_trade_specialist_matched_customers |
| trade-specialist | customer_emails | bs_trade_specialist_customer_emails |

## 租户隔离要求

所有业务数据表（`bs_` 开头）**必须**包含 `tenant_id` 字段，实现租户数据隔离。

```sql
CREATE TABLE IF NOT EXISTS bs_trade_specialist_matched_customers (
    id SERIAL PRIMARY KEY,
    customer_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL,  -- 租户ID，必需字段
    user_id TEXT NOT NULL,
    ...
);
```

## 子智能体表初始化机制

子智能体在加载时，会自动初始化所有需要的业务数据表。

### 初始化流程

1. **Skill 加载时**：在 `SkillLoader._init_skill_tables()` 中调用各 skill 的表初始化函数
2. **数据库初始化时**：在 `init_database()` 中调用各 skill 的表初始化函数

### 示例：trade-customer skill

```python
# src/skills/trade-customer-1.0.0/scripts/customer_manager.py
def init_tables():
    """初始化客户相关表"""
    # 创建业务数据表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_trade_specialist_matched_customers (
            id SERIAL PRIMARY KEY,
            customer_id TEXT UNIQUE NOT NULL,
            tenant_id TEXT NOT NULL,
            ...
        )
    """)
```

## 命名检查清单

新增业务数据表时，请确认以下事项：

- [ ] 表名以 `bs_` 开头
- [ ] 表名包含子智能体名称（`-` 替换为 `_`）
- [ ] 表中包含 `tenant_id` 字段（用于租户隔离）
- [ ] 在 skill 加载时调用 `init_tables()` 初始化表
- [ ] 更新本文档，添加新表到示例表格