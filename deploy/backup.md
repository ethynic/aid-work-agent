# 备份与灾难恢复

> 生产（新机 243）每日备份与异地容灾的完整说明。脚本总览见 [服务器部署现状.md](服务器部署现状.md) §9。

## 1. 生产每日备份（2026-09-15 上线）

**脚本**：`deploy/backup_daily.sh`（部署于新机 `/var/www/agent/deploy/backup_daily.sh`，root crontab 每日 `30 1 * * *`，日志在 `/var/backups/agent/logs/`）

```bash
# 新机 root crontab
30 1 * * * /var/www/agent/deploy/backup_daily.sh >> /var/backups/agent/logs/backup.log 2>&1
```

| 备份对象 | 频率 | 本地保留 | 异地保留 |
|---------|------|---------|---------|
| 主库 `aid_work_agent`（pg_dump 整库 gzip，约 26M/天） | 每日 | 30 天（`/var/backups/agent/db/`） | 30 天 |
| 日志库 `aid_work_logs` | 每周日 | 30 天 | 30 天 |
| 当日新增附件（前一天 00:00 到当天 00:00，`agent_storage` + `agent_uploads`，排除 `tmp`，tar 增量包） | 每日 | 30 天（`/var/backups/agent/files/`） | **永久** |
| 关键配置（.env、docker-compose.prod.yml、gunicorn.conf.py、agent_update.sh、nginx conf + SSL 证书） | 每日 | 30 天 | 30 天 |

## 2. 异地存放（254 NAS 盘）

旧服务器 254 的 NFS NAS 盘 `/var/www/qb3_upload/backup_from_243/{db,files,config}/`（3T 盘，空间充裕）。

传输通道：243 root 专用密钥 `/root/.ssh/id_backup_ed25519`（注释名 `backup-243-to-254`），旧机 `authorized_keys` 中带 `from="129.211.65.243"` 限制，仅接受新机来源连接。**清理旧机授权键时勿删这条**（见 [生产环境部署.md](生产环境部署.md) §3 收尾清单）。

## 3. 附件全量基线 + 日增量

基线（2026-09-15 已同步，476M）+ 每日 tar 增量。异地恢复时 = `files_baseline/agent_storage/` 基线 + 逐日 `files_YYYYMMDD.tar.gz` 增量。基线重建命令见脚本头部注释。

## 4. 灾难恢复流程（243 不可用，恢复到 254）

1. 254 部署代码 + 构建前端（或从 git clone；镜像可从 243 导出，若 243 已损毁则 `docker build` 重建）
2. 恢复配置：解压最新 `config_*.tar.gz` 到 `/`（覆盖 `.env`、compose、nginx conf + SSL），`.env` 中 `DATABASE_URL`/`LOGS_DATABASE_URL`/`REDIS_HOST` 按本机环境修正
3. 恢复数据库：`gunzip -c db/aid_work_agent_*.sql.gz | docker exec -i aid-postgres psql -U aid_user aid_work_agent`；若装 TimescaleDB 的日志库，按 TimescaleDB 恢复流程（`timescaledb_pre_restore()` → pg_restore → `timescaledb_post_restore()`，见 [生产环境部署.md](生产环境部署.md) §1.3）
4. 恢复附件：解压 `files_baseline` + 所有 `files_*.tar.gz` 到 `/var/www/qb3_upload/`
5. DNS 把 `agent.aidingyi.cn` 切回 254 公网 IP，`docker compose up -d` 启动服务

## 5. 验证与抽查

```bash
# 手动跑一轮，检查日志无 ERROR 且 254 上三个目录均有当日文件
ssh -p 10167 ubuntu@129.211.65.243 "sudo bash /var/www/agent/deploy/backup_daily.sh"

# cron 凌晨执行后次日抽查
ssh -p 10167 ubuntu@124.222.3.254 "ls /var/www/qb3_upload/backup_from_243/db/ | tail"
```

## 6. 旧机测试/开发库备份

旧机（254）root crontab 的 `backup_postgres.sh` 仅备份测试库 `aid_work_agent2` / `aid_work_logs2`（原生产库已改名禁连，脚本已改为跳过，原文件备份于旧机 `/backup_postgres.sh.bak_20260915`，见 [生产环境部署.md](生产环境部署.md) §1.4）。
