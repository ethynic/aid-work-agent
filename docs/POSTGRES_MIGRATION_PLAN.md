# SQLite 迁移 PostgreSQL 计划文档

## 背景

当前项目使用 SQLite 数据库，在多用户并发写入场景下存在性能瓶颈（写入延时、锁等待）。切换到 PostgreSQL 可解决并发写入问题，提升系统稳定性。

## 当前数据库使用分析

### 1. 数据库配置层
**文件**: `src/db/database.py`
- 当前已支持 SQLite/PostgreSQL/MySQL 切换，通过 `DATABASE_URL` 环境变量
- `get_db_connection()` 返回 SQLite 连接
- 需要适配 PostgreSQL 连接（使用 psycopg2）

### 2. 数据库表定义（共 12 张核心表）
| 表名 | 用途 | 位置 |
|------|------|------|
| users | 用户 | database.py |
| chat_sessions | 会话 | database.py |
| chat_messages | 消息 | database.py |
| chat_records | 对话记录 | database.py |
| sms_codes | 短信验证码 | database.py |
| remote_credentials | 远程凭据 | database.py |
| tokens | Token | database.py |
| scheduled_tasks | 定时任务 | database.py |
| scheduled_task_logs | 任务日志 | database.py |
| documents | 知识库文档 | database.py |
| chunks | 知识库文本块 | database.py |
| user_email_settings | 邮箱配置 | database.py |

### 3. SaaS 多租户表（8 张）
**文件**: `src/saas/db/tables.py`
- tenants, tenant_admins, tenant_admin_tokens
- subscriptions, agent_instances
- tenant_channel_configs, tenant_users, payment_orders

### 4. 需要修改的文件清单
```
核心修改：
- src/db/database.py              # 数据库连接适配
- src/db/models.py                # UserDB/SessionDB/MessageDB/ChatRecordDB
- src/db/remote_credential.py     # RemoteCredentialDB
- src/db/email_credential.py      # EmailCredentialDB
- src/saas/db/tables.py           # SaaS 表定义
- src/scheduler/db.py             # ScheduledTaskDB/ScheduledTaskLogDB
- src/knowledge/vector_db/vector_db.py  # 向量数据库适配

依赖修改：
- src/knowledge/service.py        # 知识库服务
- src/knowledge/api.py            # 知识库 API
- src/knowledge/retriever/hybrid_retriever.py
- tests/conftest.py               # 测试 fixtures
```

## PostgreSQL 迁移方案

### 方案 A：保持原生 SQL（推荐）
- 使用 `psycopg2` 或 `asyncpg` 替代 `sqlite3`
- 保持现有代码风格，只修改 SQL 语法差异

### 方案 B：引入 SQLAlchemy ORM
- 较大改动，需要重写数据访问层
- 长期可维护性更好，但迁移成本高

**推荐方案 A**，最小改动原则。

## 实施步骤

### 阶段 1：基础设施准备 ✅（已完成）
1. 在 `deploy/` 目录创建 PostgreSQL Docker 部署脚本
2. 创建数据库初始化脚本（迁移现有表结构）

### 阶段 2：数据库连接层适配（待实施）
1. 修改 `src/db/database.py`：
   - 添加 PostgreSQL 连接支持（使用 psycopg2）
   - 保持 `get_db_connection()` 接口兼容
   - 修改占位符：`?` → `%s`

### 阶段 3：表结构迁移（待实施）
1. 修改 `src/db/database.py` 中 `init_database()` 函数
   - SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL`
   - 或使用 SQLAlchemy Core 建表

### 阶段 4：数据访问层适配（待实施）
修改以下文件的 SQL 语法：
- `src/db/models.py`
- `src/db/remote_credential.py`
- `src/db/email_credential.py`
- `src/scheduler/db.py`
- `src/saas/db/*.py`

### 阶段 5：向量数据库适配（待实施）
- sqlite-vec 不支持 PostgreSQL
- 方案：使用 `pgvector` 扩展
- 需要修改 `src/knowledge/vector_db/vector_db.py`

### 阶段 6：测试验证（待实施）
1. 单元测试通过
2. 集成测试通过
3. 手动验证核心功能

## SQL 语法差异对照

| 场景 | SQLite | PostgreSQL |
|------|--------|------------|
| 占位符 | `?` | `%s` |
| 自增主键 | `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` |
| 布尔值 | `0/1` | `true/false` |
| 文本 JSON | TEXT | JSONB |
| 时间戳 | `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP` |
| 字符串截断 | `SUBSTR()` | `SUBSTRING()` |
| 批量插入 | `INSERT INTO ... VALUES (?,?),(?,?)` | 相同语法 |

## 向量数据库方案

### 推荐方案：pgvector
1. 在 PostgreSQL 启用 pgvector 扩展
2. 创建向量列：`embedding vector(1024)`
3. 修改向量搜索 SQL

## 部署脚本

已创建以下文件：
- `deploy/docker-compose.postgres.yml` - PostgreSQL Docker Compose 配置
- `deploy/init-postgres.sql` - 数据库初始化脚本
- `deploy/POSTGRES_DEPLOY.md` - 部署指南

### 快速启动
```bash
cd deploy
docker compose -f docker-compose.postgres.yml up -d
```

### 配置应用连接
```bash
DATABASE_URL=postgresql://aid_user:aid_secure_pass_2024@localhost:5432/aid_work_agent
```

## 验证方法

1. **启动测试**：修改 DATABASE_URL 为 postgresql://...
2. **功能验证**：
   - 用户注册/登录
   - 创建会话、发送消息
   - 知识库上传和检索
   - 定时任务创建和执行
3. **并发测试**：多用户同时操作，验证无锁等待

## 回滚计划

- 保留 SQLite 数据库文件
- 通过环境变量快速切换回 SQLite
- 重要数据迁移前备份