# 数据库兼容性简化与测试重建计划

## 目标

1. 删除 SQLite 和 MySQL 兼容代码，简化为纯 PostgreSQL
2. 保留 GaussDB/openGauss 兼容扩展点
3. 重建数据库交互层测试代码

---

## 第一阶段：删除 SQLite/MySQL 兼容代码

### 1.1 修改 `src/db/database.py`

| 任务 | 说明 |
|------|------|
| 移除 `sqlite3` import | 全文搜索 `import sqlite3` 删除 |
| 移除 `get_sqlite_path()` | 此函数仅 SQLite 使用 |
| 简化 `get_db_placeholder()` | 直接返回 `%s` |
| 简化 `get_date_offset()` | 保留 PostgreSQL 分支删除 else 分支 |
| 移除 SQLite 连接分支 | `get_db_connection()` 中的 sqlite 分支 |
| 移除 `_init_sqlite()` | 删除此函数 |
| 移除 `PGRow` 包装器 | 直接使用 psycopg2 RealDictCursor |
| 移除 `DictCursorWrapper` | 不再需要占位符转换 |
| 移除 `DB_TYPE` 变量 | 改用常量 `DB_TYPE = "postgresql"` |

### 1.2 修改 `src/db/models.py`

| 任务 | 说明 |
|------|------|
| 移除 `get_db_placeholder()` 调用 | 全文替换为 `%s` |
| 移除 `DB_TYPE` import | 不再需要 |

### 1.3 修改 `src/db/remote_credential.py`

| 任务 | 说明 |
|------|------|
| 移除 `get_db_placeholder()` 调用 | 全文替换为 `%s` |

### 1.4 其他文件清理

| 文件 | 任务 |
|------|------|
| `src/saas/middleware.py` | 检查 SQLite 兼容代码如有则删除 |
| `.env.example` | 默认改为 PostgreSQL |
| `tests/conftest.py` | 统一使用 PostgreSQL |
| `docs/POSTGRES_*.md` | 考虑删除或标记为归档 |
| `requirements.txt` | 可移除 `sqlite` 相关注释 |

---

## 第二阶段：GaussDB/openGauss 兼容设计

### 2.1 兼容策略

```
当前: SQLite ←→ PostgreSQL (两套逻辑)
目标: PostgreSQL ←→ GaussDB/OpenGauss (抽象兼容层)
```

### 2.2 需要适配的方言差异

| 功能 | PostgreSQL | GaussDB/openGauss | 适配方案 |
|------|-----------|------------------|-----------|
| 自增 ID | `SERIAL` | `SERIAL` | 兼容 |
| 时间函数 | `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP` | 兼容 |
| 全文检索 | `tsvector` + GIN | 兼容 | 直接使用 |
| 向量搜索 | `pgvector` | `vector` 插件 | 需确认插件名 |
| JSON 类型 | `JSONB` | 兼容 | 直接使用 |
| 数组类型 | `ARRAY` | 兼容 | 直接使用 |
| 连接驱动 | `psycopg2` | `psycopg2` | 兼容 |

### 2.3 扩展点设计

```python
# src/db/database.py 新增

def get_db_type() -> str:
    """获取数据库类型"""
    return os.getenv("DB_TYPE", "postgresql")

# 方言适配器抽象
class DBDialect:
    """数据库方言适配器基类"""
    def get_placeholder(self) -> str: ...
    def get_date_offset(self, days: int) -> str: ...
    def get_concat(self, *args) -> str: ...

class PostgreSQLDialect(DBDialect): ...
class GaussDBDialect(DBDialect): ...
class OpenGaussDialect(DBDialect): ...

# 根据 DB_TYPE 动态选择
def get_dialect() -> DBDialect:
    db_type = get_db_type()
    if db_type in ("postgresql", "gaussdb", "opengauss"):
        return PostgreSQLDialect()
    raise ValueError(f"Unsupported database: {db_type}")
```

---

## 第三阶段：测试代码重建

### 3.1 需测试的数据库交互接口

按 API 模块分组：

#### Auth 模块 (`src/api/auth.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| POST /auth/captcha | 验证码生成、存储 | P1 |
| POST /auth/captcha/validate | 验证码校验 | P1 |
| POST /auth/phone/send-code | 短信验证码发送+存储 | P1 |
| POST /auth/login | 用户登录、Token 生成 | P1 |
| POST /auth/register | 用户注册 | P1 |
| GET /auth/me | 获取当前用户信息 | P1 |
| PATCH /auth/profile | 更新用户资料 | P2 |
| POST /auth/phone/login | 手机验证码登录 | P1 |

#### Session 模块 (`src/api/session.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| GET /api/session | 获取会话列表 | P1 |
| POST /api/session | 创建会话 | P1 |
| GET /api/session/{id} | 获取会话详情 | P1 |
| PATCH /api/session/{id} | 更新会话 | P2 |
| DELETE /api/session/{id} | 删除会话 | P1 |
| GET /api/session/{id}/messages | 获取消息列表 | P1 |
| POST /api/session/{id}/messages | 发送消息 | P1 |
| GET /api/session/{id}/records | 获取会话记录 | P2 |
| GET /api/session/{id}/token-usage | Token 统计 | P2 |

#### Credentials 模块 (`src/api/credentials.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| POST /api/credentials | 创建凭据（加密存储） | P1 |
| GET /api/credentials | 获取凭据列表 | P1 |
| GET /api/credentials/{id} | 获取凭据详情 | P1 |
| PUT /api/credentials/{id} | 更新凭据 | P1 |
| DELETE /api/credentials/{id} | 删除凭据 | P1 |

#### Scheduled Task 模块 (`src/api/scheduled_task.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| GET /api/scheduled-tasks | 获取任务列表 | P1 |
| POST /api/scheduled-tasks | 创建定时任务 | P1 |
| GET /api/scheduled-tasks/{id} | 获取任务详情 | P1 |
| POST /api/scheduled-tasks/{id}/pause | 暂停任务 | P1 |
| POST /api/scheduled-tasks/{id}/resume | 恢复任务 | P1 |
| DELETE /api/scheduled-tasks/{id} | 删除任务 | P1 |
| POST /api/scheduled-tasks/{id}/run | 立即执行 | P2 |
| PUT /api/scheduled-tasks/{id}/schedule | 更新调度配置 | P1 |
| GET /api/scheduled-tasks/{id}/logs | 获取执行日志 | P1 |

#### Email Settings 模块 (`src/api/email_settings.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| GET /api/email-settings | 获取邮箱配置 | P1 |
| POST /api/email-settings | 配置邮箱（SMTP/IMAP） | P1 |
| DELETE /api/email-settings | 删除邮箱配置 | P1 |

#### Knowledge 模块 (`src/knowledge/api.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| POST /api/knowledge/upload | 上传文档 | P1 |
| GET /api/knowledge/documents | 文档列表 | P1 |
| POST /api/knowledge/search_documents | 全文检索 | P1 |
| DELETE /api/knowledge/documents/{id} | 删除文档 | P1 |
| GET /api/knowledge/documents/{id}/chunks | 获取文本块 | P2 |

#### Customer 模块 (`src/api/customer.py`)
| 接口 | 测试内容 | 优先级 |
|------|----------|--------|
| GET /api/customer/customers | 客户列表 | P1 |
| GET /api/customer/customers/{id} | 客户详情 | P1 |
| GET /api/customer/emails | 客户邮件列表 | P1 |
| GET /api/customer/stats | 客户统计 | P2 |

### 3.2 测试组织结构

```
tests/
├── unit/
│   ├── conftest.py              # Fixtures: db connection, test user
│   ├── test_auth_endpoints.py   # Auth API 测试（新增）
│   ├── test_session_endpoints.py # Session API 测试（新增）
│   ├── test_credentials_endpoints.py # Credentials API 测试（新增）
│   ├── test_scheduled_task_endpoints.py # 定时任务 API 测试（新增）
│   ├── test_email_settings_endpoints.py # 邮箱配置测试（新增）
│   ├── test_knowledge_endpoints.py # 知识库 API 测试（新增）
│   └── test_customer_endpoints.py # 客户管理 API 测试（新增）
├── integration/
│   └── conftest.py              # 测试数据库初始化
└── conftest.py                  # 全局 fixtures
```

### 3.3 测试 Fixture 设计

```python
# tests/unit/conftest.py 示例

import pytest
from src.db.database import get_db_connection

@pytest.fixture
def test_db():
    """测试数据库连接（使用测试数据库）"""
    with get_db_connection() as conn:
        yield conn

@pytest.fixture
def test_user(test_db):
    """创建测试用户"""
    # 插入测试用户数据
    # 返回 user_id
    yield "test_user_001"
    # 清理测试用户

@pytest.fixture  
def auth_headers(test_user):
    """获取认证头（Token）"""
    # 生成测试 Token
    return {"Authorization": "Bearer test_token_xxx"}
```

### 3.4 测试优先级排序

| 批次 | 模块 | 原因 |
|------|------|------|
| 1 | Auth | 核心认证，其他模块依赖 |
| 2 | Session | 会话管理核心业务 |
| 3 | Credentials | 敏感数据存储 |
| 4 | Scheduled Task | 定时任务、后台任务 |
| 5 | Email Settings | 用户配置存储 |
| 6 | Knowledge | 文档上传+检索 |
| 7 | Customer | 业务数据 |

---

## 第四阶段：执行检查清单

- [x] 完成 `src/db/database.py` 简化
- [x] 完成 `src/db/models.py` 修改
- [x] 完成 `src/db/remote_credential.py` 修改
- [x] 更新 `.env.example`
- [x] 更新 `tests/conftest.py`
- [x] 更新 `src/db/__init__.py` 导出
- [x] 清理 `src/knowledge/vector_db/vector_db.py` SQLite 代码
- [x] 清理 `src/knowledge/retriever/hybrid_retriever.py` SQLite 代码
- [x] 清理 `src/knowledge/service.py` SQLite 代码
- [x] 清理 `src/api/auth.py` SQLite 代码
- [x] 清理 `src/api/customer.py` SQLite 代码
- [x] 清理 `src/channels/session.py` SQLite 代码
- [x] 清理 `src/scheduler/db.py` SQLite 代码
- [x] 清理 `src/saas/*` 模块 SQLite 代码
- [x] 编写 Auth 模块测试（P1）
- [x] 编写 Session 模块测试（P1）
- [x] 编写 Credentials 模块测试（P1）
- [x] 编写 Scheduled Task 模块测试（P1）
- [x] 编写 Email Settings 模块测试（P1）
- [x] 编写 Knowledge 模块测试（P1）
- [x] 编写 Customer 模块测试（P1）
- [ ] 运行所有测试验证通过（需要 PostgreSQL 数据库）
- [ ] 更新 README 文档（移除 SQLite 说明）

## 变更摘要

### 已删除的 SQLite/MySQL 兼容代码
- `src/db/database.py`：移除 SQLite 连接逻辑、`PGRow` 包装器、`_init_sqlite()` 函数
- `src/db/models.py`：移除 `get_db_placeholder()` 调用，改为硬编码 `%s`
- `src/db/remote_credential.py`：同上
- `src/knowledge/vector_db/vector_db.py`：移除 `VectorDBSQLite` 类
- `src/knowledge/retriever/hybrid_retriever.py`：移除 `DB_TYPE` 依赖
- `src/knowledge/service.py`：移除 `DB_TYPE` 分支判断
- 所有 API 和 SaaS 模块：SQLite 占位符 `?` 替换为 PostgreSQL 的 `%s`

### 简化后的架构
- 统一使用 PostgreSQL（含 GaussDB/openGauss 兼容）
- `get_db_connection()` 返回包装的 cursor，支持 `commit()/rollback()`
- 默认数据库连接字符串改为 `postgresql://postgres:postgres@localhost:5432/aid_work_agent`

---

## 预期收益

1. **代码简化**：移除 ~200 行数据库兼容代码
2. **维护性提升**：单一代码路径减少心智负担
3. **测试覆盖**：数据库交互层 100% 覆盖
4. **扩展性**：保留 GaussDB/openGauss 兼容能力