# PostgreSQL 部署与连接

> 两台服务器各一套 `aid-postgres` 容器（同名不同机）。部署分步指南见 [POSTGRES_DEPLOY.md](POSTGRES_DEPLOY.md)。

## 1. 两套实例

| 项目 | 生产（新机 129.211.65.243） | 测试/开发（旧机 124.222.3.254） |
|------|---------------------------|------------------------------|
| 容器名 | `aid-postgres` | `aid-postgres`（同名，不同机） |
| 镜像 | `timescale/timescaledb:latest-pg16`（2.27.2，含 pgvector） | 同左 |
| 对外端口 | **10864** → 5432（`POSTGRES_PORT`） | 5433 → 5432 |
| compose | `/var/www/agent/deploy/docker-compose.postgres.yml` | `/var/www/agent2/deploy/docker-compose.postgres.yml` |
| 数据卷 | `deploy_postgres_data`（2026-09-15 全量 restore 自旧机） | 同名卷（原始生产数据） |
| 网络可达性 | 公网入站已被腾讯云安全组拦截（2026-09-16 实测）；容器绑 `0.0.0.0` 待改 `127.0.0.1`；容器内应用经 `172.17.0.9:10864` 访问 | 仅内网/容器网络 |

## 2. 数据库清单

**生产库**（新机，2026-09-15 切换时行数核对一致）：

| 库名 | 用户名 | 用途 | 切换时行数基准 |
|------|--------|------|---------------|
| `aid_work_agent` | `aid_user` | 生产主库（124 表） | `chat_records=2748`（最新 2026-09-15 02:30） |
| `aid_work_logs` | `aid_user` | 生产日志库（TimescaleDB） | `obs_spans=9348` / `obs_traces=2455` |

**测试/开发库**（旧机）：

| 库名 | 用户名 | 用途 |
|------|--------|------|
| `aid_work_agent2` | `aid_user`（容器 env 实测，非文档旧记的 `aid_user2`） | 测试/在线开发主库 |
| `aid_work_logs2` | `aid_user` | 测试/在线开发日志库 |

> **旧生产库已禁用（2026-09-15）**：旧机上的原生产库已改名 `aid_work_agent_rollback_20260915` / `aid_work_logs_rollback_20260915`，`datallowconn=false` + `CONNECTION LIMIT 0`（数据完整保留，防误操作/误写入）。回滚恢复步骤见 [生产环境部署.md](生产环境部署.md) §3。注意：`aid_user` 在旧库容器中是**超级用户**，`REVOKE CONNECT` / `CONNECTION LIMIT` 对其无效，必须用 `ALLOW_CONNECTIONS false` 才能彻底禁连。

## 3. 扩展

- `pgvector` — 向量搜索（`chunks_vec` 表，HNSW + cosine）
- `uuid-ossp` — UUID 生成
- `timescaledb` — Hypertable 分区（两个库均启用；日志库的 `obs_*` 表为核心使用者）

## 4. 连接方式

> 密码通过服务器 `.env` 配置，**不要写入文档或代码**。

```bash
# 容器内 psql（生产主库，新机）
ssh -p 10167 ubuntu@129.211.65.243
sudo docker exec -it aid-postgres psql -U aid_user -d aid_work_agent

# 容器内 psql（测试库，旧机）
sudo docker exec -it aid-postgres psql -U aid_user -d aid_work_agent2

# 验证 pgvector 扩展
sudo docker exec -it aid-postgres psql -U aid_user -d aid_work_agent \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
```

应用侧连接串（真实值见服务器 `.env`）：

```
# 生产（新机）
DATABASE_URL=postgresql://aid_user:***@172.17.0.9:10864/aid_work_agent
LOGS_DATABASE_URL=postgresql://aid_user:***@172.17.0.9:10864/aid_work_logs

# 测试/开发（旧机，公网 5433 可直连）
DATABASE_URL=postgresql://aid_user:***@124.222.3.254:5433/aid_work_agent2
```

WSL 本机调试：

```bash
# 测试库（旧机，公网 5433）
psql "postgresql://aid_user:***@124.222.3.254:5433/aid_work_agent2" \
  -c "SELECT count(*) FROM chat_sessions;"

# 生产库（新机）：走 SSH 隧道
# ssh -p 10167 -L 10864:172.17.0.9:10864 ubuntu@129.211.65.243
# psql "postgresql://aid_user:***@localhost:10864/aid_work_agent"
```

## 5. 连接数排查

```bash
# 查看当前连接数
sudo docker exec -it aid-postgres psql -U aid_user -d postgres \
  -c "SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname;"

# 强制断开某库所有连接（谨慎）
sudo docker exec -it aid-postgres psql -U aid_user -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='aid_work_agent' AND pid <> pg_backend_pid();"
```

## 6. 备份与恢复

见 [backup.md](backup.md)。
