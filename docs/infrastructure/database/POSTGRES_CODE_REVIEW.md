# PostgreSQL 支持代码审查报告

**审查日期**: 2026/04/13
**审查范围**: PostgreSQL 迁移相关代码
**审查人**: Claude Code

---

## 一、审查摘要

| 类别 | 数量 |
|------|------|
| 严重问题 | 2 |
| 中等问题 | 5 |
| 轻微问题 | 3 |
| 建议优化 | 4 |

---

## 二、严重问题 (Critical)

### 2.1 SQL 占位符动态替换导致 SQL 注入风险

**位置**: `src/db/models.py` 等文件

**问题描述**:
```python
placeholder = get_db_placeholder()
cursor.execute(f"SELECT * FROM users WHERE user_id = {placeholder}", (user_id,))
```

当前实现使用 f-string 拼接占位符，然后传递参数。这是安全的，但如果未来有人直接拼接参数到 SQL 中，会引入 SQL 注入风险。

**风险等级**: 中

**建议**:
- 当前实现是安全的（参数化查询）
- 添加代码注释明确说明不要直接拼接参数

---

### 2.2 FTS5 全文搜索仅支持 SQLite

**状态**: ✅ 已修复

**位置**: `src/knowledge/retriever/hybrid_retriever.py`

**修复内容**:
- 添加 `db_type` 参数到 `HybridRetriever.__init__()`
- 新增 `_sqlite_fts_search()` 方法（原有逻辑）
- 新增 `_postgres_fts_search()` 方法（使用 `to_tsvector` + `ts_rank`）
- `_fts_search()` 根据数据库类型自动选择实现

**PostgreSQL 表结构修改**:
- `chunks` 表新增 `text_vec tsvector` 字段
- 添加 GIN 索引用于全文检索
- 添加触发器自动更新 `text_vec`

---

## 三、中等问题 (Major)

### 3.1 MySQL 分支未实现

**位置**: `src/db/database.py:49-57`

**问题描述**:
```python
elif driver == "mysql":
    return {
        "driver": "mysql",
        "host": parsed.hostname or "localhost",
        ...
    }
```

配置解析了 MySQL，但 `get_db_connection()` 和 SQL 占位符未适配 MySQL（MySQL 使用 `%s` 或 `?`）。

**建议**:
- 实现 MySQL 支持（使用 pymysql 或 aiomysql）
- 或在配置中提示暂不支持 MySQL

---

### 3.2 时间戳函数重复定义

**状态**: ✅ 已修复

**位置**: `src/db/database.py:88-92`

**修复内容**:
- 移除了无意义的分支逻辑
- SQLite 和 PostgreSQL 使用相同的 `CURRENT_TIMESTAMP` 语法

---

### 3.3 PostgreSQL 连接未使用连接池

**位置**: `src/db/database.py:95-107`

**问题描述**:
```python
def _create_postgres_connection():
    conn = psycopg2.connect(...)
    conn.autocommit = False
    return conn
```

每次调用都创建新连接，高并发下性能差。

**建议**:
- 使用 `psycopg2.pool.ThreadedConnectionPool` 实现连接池
- 或使用 `DBUtils` 连接池

---

### 3.4 向量维度硬编码

**位置**: `src/db/database.py:878-879`

**问题描述**:
```python
embedding vector(1024)
```

向量维度 1024 硬编码，不支持自定义。

**建议**:
- 从配置读取 `embedding_dimension`
- 或使用配置默认值 1024

---

### 3.5 未使用的导入

**位置**: `src/db/database.py:14-19`

**问题描述**:
```python
try:
    import psycopg2
    from psycopg2 import extras as pg_extras
except ImportError:
    psycopg2 = None
    pg_extras = None
```

`pg_extras` 导入后未在顶层使用，仅在特定分支中使用。

**建议**:
- 在需要时导入，或删除未使用的导入

---

## 四、轻微问题 (Minor)

### 4.1 缺少 PostgreSQL 环境变量验证

**位置**: `src/db/database.py`

**问题描述**:
当 `DATABASE_URL` 设置为 PostgreSQL 但配置不完整时，错误信息不够友好。

**建议**:
添加配置验证：
```python
def validate_config():
    if DB_TYPE == "postgresql":
        required = ["host", "database", "user", "password"]
        missing = [k for k in required if not DB_CONFIG.get(k)]
        if missing:
            raise ValueError(f"PostgreSQL config missing: {missing}")
```

---

### 4.2 未处理 PostgreSQL 连接失败

**位置**: `src/db/database.py:161-163`

**问题描述**:
```python
elif DB_TYPE == "postgresql":
    if psycopg2 is None:
        raise ImportError(...)
```

仅检查 psycopg2 是否安装，未处理连接失败场景。

**建议**:
添加连接超时和重试逻辑。

---

### 4.3 未实现的 `close()` 方法

**位置**: `src/db/database.py`

**问题描述**:
连接包装器未提供统一的 `close()` 方法（虽然 cursor 有 close）。

**建议**:
- 当前实现已正确调用 `conn.close()`
- 可忽略此问题

---

## 五、建议优化 (Suggestions)

### 5.1 添加数据库类型检测函数

**建议**:
```python
def is_postgresql() -> bool:
    return DB_TYPE == "postgresql"

def is_sqlite() -> bool:
    return DB_TYPE == "sqlite"
```

---

### 5.2 日志增强

**建议**:
在 PostgreSQL 操作时添加更详细的日志：
```python
logger.info(f"PostgreSQL: Executing query on {DB_CONFIG['database']}")
```

---

### 5.3 单元测试覆盖

**建议**:
添加针对 PostgreSQL 的单元测试：
- `tests/unit/test_database_postgresql.py`
- Mock PostgreSQL 连接进行测试

---

### 5.4 文档补充

**建议**:
更新 `docs/POSTGRES_MIGRATION_PLAN.md`：
- 添加已知限制（FTS5 不支持）
- 添加配置示例
- 添加故障排查指南

---

## 六、修复建议优先级

| 优先级 | 问题 | 修复难度 |
|--------|------|----------|
| P0 | FTS5 不支持 PostgreSQL | 中 |
| P1 | MySQL 分支未实现 | 低 |
| P1 | 时间戳函数重复 | 低 |
| P2 | 连接池未实现 | 中 |
| P2 | 向量维度硬编码 | 低 |
| P3 | 环境变量验证 | 低 |
| P3 | 未使用的导入 | 低 |

---

## 七、总结

PostgreSQL 迁移的核心功能已实现，代码整体结构良好。主要问题：

1. **FTS5 全文搜索不兼容** - 需实现 PostgreSQL 全文搜索替代方案
2. **MySQL 支持未完成** - 需补充或明确说明不支持
3. **连接管理** - 建议添加连接池提升性能

建议优先修复 P0 和 P1 级别问题后进行功能测试。