# PostgreSQL 迁移 SQLite 语法修复记录

## 概述

本项目数据库从 SQLite 迁移到 PostgreSQL，系统检查后端代码中存在的 SQLite 特定语法，并修改为 PostgreSQL 兼容语法。

## 已修复的问题

### 1. LIMIT/OFFSET 参数化问题
**问题**: PostgreSQL 不支持 `LIMIT ?` 参数化查询

**影响文件**:
- `src/api/customer.py` - 2 处
- `src/channels/session.py` - 3 处
- `src/knowledge/service.py` - 2 处
- `src/saas/services/payment.py` - 1 处
- `src/saas/db/tenant_user_db.py` - 1 处
- `src/saas/db/tenant_db.py` - 2 处
- `src/skills/trade-customer-1.0.0/scripts/customer_manager.py` - 2 处

**解决方案**: 直接拼接 LIMIT/OFFSET 值

### 2. datetime() 函数差异
**问题**: SQLite 使用 `datetime('now', '-7 days')`，PostgreSQL 使用 `INTERVAL`

**解决方案**: 在 `src/db/database.py` 中添加 `get_date_offset()` 函数

```python
def get_date_offset(days: int) -> str:
    if DB_TYPE == "postgresql":
        return f"CURRENT_TIMESTAMP + INTERVAL '{days} days'"
    else:
        sign = "+" if days > 0 else ""
        return f"datetime('now', '{sign}{abs(days)} days')"
```

**影响文件**:
- `src/api/customer.py`
- `src/skills/trade-customer-1.0.0/scripts/customer_manager.py`

## 未修改的部分（保留 SQLite 兼容性）

以下代码保持在 SQLite 特定模式，用于向后兼容：

1. **VectorDBSQLite 类** (`src/knowledge/vector_db/vector_db.py`)
   - sqlite-vec 向量检索

2. **FTS5 全文检索** (`src/knowledge/retriever/hybrid_retriever.py`)
   - SQLite 特定的 FTS5 语法

3. **PRAGMA 语句**
   - SQLite 特定的性能优化配置

4. **AUTOINCREMENT**
   - SQLite 表创建脚本

5. **lastrowid 处理**
   - `src/knowledge/service.py` 中已正确处理 PostgreSQL 的 `RETURNING id`

## 关键数据库函数

| 函数 | 说明 |
|------|------|
| `get_db_placeholder()` | 返回参数占位符 (`%s` 或 `?`) |
| `get_current_timestamp()` | 返回当前时间戳 SQL |
| `get_date_offset(days)` | 返回日期偏移 SQL |
| `DB_TYPE` | 当前数据库类型 (`postgresql`/`sqlite`) |

## 修改时间

2026-04-15