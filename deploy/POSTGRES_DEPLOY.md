# PostgreSQL 快速部署指南

本目录包含用于快速搭建 PostgreSQL 数据库的 Docker 配置。

## 快速启动

### 1. 启动 PostgreSQL

```bash
cd deploy

# 方式一：使用默认配置（密码：aid_secure_pass_2024）
docker compose -f docker-compose.postgres.yml up -d

# 方式二：使用自定义配置
POSTGRES_DB=myapp POSTGRES_USER=myuser POSTGRES_PASSWORD=mypassword \
docker compose -f docker-compose.postgres.yml up -d

# 方式三：同时启动 PostgreSQL 和 Admin UI
docker compose -f docker-compose.postgres.yml --profile admin up -d
```

### 2. 验证连接

```bash
# 检查容器运行状态
docker ps | grep aid-postgres

# 测试连接
docker exec -it aid-postgres psql -U aid_user -d aid_work_agent -c "SELECT version();"
```

### 3. 配置应用连接

在 `.env` 文件中设置数据库连接：

```bash
# PostgreSQL 连接
DATABASE_URL=postgresql://aid_user:Aid_2026@localhost:5432/aid_work_agent
```

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| PostgreSQL | 5432 | 主数据库 |
| pgAdmin | 5050 | 数据库管理 UI（可选） |

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
```bash
docker exec -it aid-postgres pg_dump -U aid_user aid_work_agent > backup.sql
```

### 恢复数据库
```bash
docker exec -i aid-postgres psql -U aid_user aid_work_agent < backup.sql
```

## pgAdmin 使用（可选）

1. 启动：添加 `--profile admin` 参数
2. 访问：http://localhost:5050
3. 登录：
   - 邮箱：admin@aid.local
   - 密码：admin123
4. 添加服务器连接：
   - Host: postgres
   - Port: 5432
   - Database: aid_work_agent
   - Username: aid_user
   - Password: Aid_2026

## 数据持久化

- PostgreSQL 数据存储在 `postgres_data` 卷中
- pgAdmin 数据存储在 `pgadmin_data` 卷中
- 删除容器不会丢失数据

## 生产环境注意事项

1. **修改默认密码**：在 `.env` 文件或环境变量中设置强密码
2. **限制网络访问**：生产环境应配置防火墙规则
3. **定期备份**：建立自动化备份策略
4. **监控**：配置数据库健康检查和告警

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| POSTGRES_DB | aid_work_agent | 数据库名 |
| POSTGRES_USER | aid_user | 用户名 |
| POSTGRES_PASSWORD | Aid_2026 | 密码 |
| POSTGRES_PORT | 5432 | 端口 |