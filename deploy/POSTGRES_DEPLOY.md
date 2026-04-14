# PostgreSQL 部署指南

本目录包含用于搭建 PostgreSQL 数据库的 Docker 配置。

## 架构方案

**单实例 + 双数据库**：一个 PostgreSQL 容器同时服务生产和测试环境，通过数据库名和用户名隔离。

| 环境 | 数据库名 | 用户名 | 用途 |
|------|---------|--------|------|
| 生产 | `aid_work_agent` | `aid_user` | 正式业务数据 |
| 测试 | `aid_work_agent2` | `aid_user2` | 开发测试数据 |

**优势**：
- 节省服务器资源（共享内存和连接池）
- PostgreSQL 原生支持多数据库逻辑隔离，安全可靠
- 测试用户只能访问测试库，防止误操作生产数据

## 快速启动

### 1. 启动 PostgreSQL

```bash
cd deploy

# 使用默认配置启动
docker compose -f docker-compose.postgres.yml up -d

# 使用自定义配置启动
POSTGRES_PASSWORD=YourStrongPassword docker compose -f docker-compose.postgres.yml up -d
```

### 2. 验证连接

```bash
# 检查容器运行状态
docker ps | grep aid-postgres

# 测试生产库连接
docker exec -it aid-postgres psql -U aid_user -d aid_work_agent -c "SELECT version();"

# 测试测试库连接
docker exec -it aid-postgres psql -U aid_user2 -d aid_work_agent2 -c "SELECT version();"
```

### 3. 配置应用连接

在 `.env` 文件中设置数据库连接：

```bash
# 生产环境
DATABASE_URL=postgresql://aid_user:Aid_2026@localhost:5433/aid_work_agent

# 测试环境
DATABASE_URL=postgresql://aid_user2:Aid_2026@localhost:5433/aid_work_agent2
```

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| PostgreSQL | 5433 | 数据库（宿主机映射端口） |
| pgAdmin | 5050 | 数据库管理界面（宿主机映射端口） |

## pgAdmin 使用

启动后访问 `http://服务器IP:5050`，使用以下凭据登录：

| 参数 | 值 |
|------|------|
| 邮箱 | `admin@aid-admin.com`（可通过 `PGADMIN_EMAIL` 环境变量修改） |
| 密码 | `Aid_2026`（可通过 `PGADMIN_PASSWORD` 环境变量修改） |

登录后添加服务器连接：

| 参数 | 值 |
|------|------|
| 主机名/地址 | `postgres`（容器内网络名） |
| 端口 | `5432`（容器内部端口） |
| 维护数据库 | `aid_work_agent` |
| 用户名 | `aid_user` |
| 密码 | `Aid_2026` |

> **注意**：pgAdmin 连接 PostgreSQL 时使用容器内部网络，主机名填 `postgres`，端口填 `5432`（不是宿主机映射的 5433）。

## Navicat 连接参数

| 参数 | 生产环境 | 测试环境 |
|------|---------|---------|
| 主机 | 服务器IP | 服务器IP |
| 端口 | 5433 | 5433 |
| 数据库 | `aid_work_agent` | `aid_work_agent2` |
| 用户名 | `aid_user` | `aid_user2` |
| 密码 | `Aid_2026` | `Aid_2026` |

## 常用操作

### 查看日志
```bash
docker compose -f docker-compose.postgres.yml logs -f postgres
```

### 停止服务
```bash
docker compose -f docker-compose.postgres.yml down
```

### 停止并删除数据（重置数据库）
```bash
docker compose -f docker-compose.postgres.yml down -v
```

### 备份数据库

使用自动备份脚本（推荐）：

```bash
# 手动执行备份
chmod +x deploy/backup_postgres.sh
./deploy/backup_postgres.sh

# 设置 crontab 每天凌晨 1:00 自动备份
crontab -e
# 添加以下行：
0 1 * * * /var/www/agent2/deploy/backup_postgres.sh >> /var/www/agent2/deploy/logs/backup.log 2>&1
```

备份文件存储在 `deploy/backups/<日期>/` 目录下，自动压缩为 `.sql.gz`，保留最近 30 天。

手动备份（不使用脚本）：

```bash
# 备份生产库
docker exec -it aid-postgres pg_dump -U aid_user aid_work_agent > backup_prod.sql

# 备份测试库
docker exec -it aid-postgres pg_dump -U aid_user2 aid_work_agent2 > backup2.sql
```

### 恢复数据库

```bash
# 恢复生产库
docker exec -i aid-postgres psql -U aid_user aid_work_agent < backup_prod.sql

# 恢复测试库
docker exec -i aid-postgres psql -U aid_user2 aid_work_agent2 < backup2.sql
```

## 数据持久化

- PostgreSQL 数据存储在 `postgres_data` 卷中
- 删除容器不会丢失数据
- 删除卷需要使用 `down -v` 命令

## 生产优化配置

容器已内置以下生产优化参数：

| 参数 | 值 | 说明 |
|------|------|------|
| `max_connections` | 200 | 生产+测试共用建议200 |
| `shared_buffers` | 512MB | 共享缓冲区（物理内存25%） |
| `effective_cache_size` | 1536MB | 有效缓存大小（物理内存75%） |
| `work_mem` | 16MB | 每个排序/哈希操作内存 |
| `maintenance_work_mem` | 256MB | 维护操作内存 |
| `wal_level` | replica | 支持复制备份 |
| `log_min_duration_statement` | 1000ms | 记录>1秒的慢查询 |
| `random_page_cost` | 1.1 | SSD 优化 |

测试用户额外设置了 `statement_timeout = 30s`，防止测试慢查询拖垮生产。

## 安全注意事项

1. **修改默认密码**：在 `.env` 文件或环境变量中设置强密码
2. **限制网络访问**：生产环境应配置防火墙规则，只允许可信 IP 访问 5433 端口
3. **定期备份**：建立自动化备份策略
4. **用户隔离**：测试用户 `aid_user2` 只能访问测试库，无法操作生产数据

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| POSTGRES_DB | aid_work_agent | 生产数据库名 |
| POSTGRES_USER | aid_user | 生产用户名 |
| POSTGRES_PASSWORD | Aid_2026 | 生产密码（**请修改**） |
| POSTGRES_PORT | 5433 | 宿主机端口 |
| POSTGRES2_PASSWORD | Aid_2026 | 测试用户密码（**请修改**） |
| PGADMIN_EMAIL | admin@aid-admin.com | pgAdmin 登录邮箱 |
| PGADMIN_PASSWORD | Aid_2026 | pgAdmin 登录密码（**请修改**） |
| PGADMIN_PORT | 5050 | pgAdmin 宿主机端口 |
