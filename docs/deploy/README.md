# 部署文档索引

服务器部署相关**文档**统一放在本目录；`deploy/` 只保留服务器运行必需的配置文件与脚本（服务器开启了 sparse-checkout，`docs/` 不落工作区，见 [sparse-checkout-工作区瘦身.md](./sparse-checkout-工作区瘦身.md)）。

## 文档索引

| 文档 | 用途 |
|------|------|
| [服务器部署现状.md](./服务器部署现状.md) | 已部署环境运维入口：端口/目录/容器、日志、巡检、待办 |
| [生产发布备忘.md](./生产发布备忘.md) | 每批次发布的专项事项清单（随发布更新） |
| [生产环境部署.md](./生产环境部署.md) | 243 迁移历史档案（含存量库迁移踩坑、回滚步骤） |
| [生产环境部署指南.md](../guides/deployment/生产环境部署指南.md) | 通用新环境部署标准动作 |
| [sparse-checkout-工作区瘦身.md](./sparse-checkout-工作区瘦身.md) | 服务器 sparse-checkout 配置、回滚与踩坑 |
| [deploy-eas.md](./deploy-eas.md) | EAS（192.168.200.72）环境记录 |
| [web-push-agent2.md](./web-push-agent2.md) | agent2 Web Push 启用步骤 |
| [myhongtao-image-proxy.md](./myhongtao-image-proxy.md) | 宏陶图片反向代理说明 |
| [济南诺度私有云部署.md](./济南诺度私有云部署.md) | 济南诺度混部私有云实例记录 |
| [济南诺度私有云验收报告.md](./济南诺度私有云验收报告.md) | 济南诺度交付验收记录 |
| [liaoyi/聊易私有云部署.md](./liaoyi/聊易私有云部署.md) | 聊易私有云（agent.0635easy.com）部署记录 |
| [liaoyi/创蓝253短信通道开发任务.md](./liaoyi/创蓝253短信通道开发任务.md) | 聊易 253 短信通道交接与验收 |
| [liaoyi/错误日志排查_20261117.md](./liaoyi/错误日志排查_20261117.md) | 聊易上线首日错误日志排查 |
| [image-gen/README.md](./image-gen/README.md) | image-gen 生图推理容器（独立部署） |
| [redis/README.md](./redis/README.md) | Redis 配置说明 |

## deploy/ 运行时资产（不随文档迁移）

| 类别 | 文件 |
|------|------|
| Nginx 配置 | `agent.aidingyi.cn.conf`、`agent1/2/3.aidingyi.cn.conf`、`myhongtao.aidingyi.cn.conf`、`192.168.200.72.conf`、`liaoyi/`、`nuodu/` 下各 conf |
| Compose | `docker-compose.*.yml`、`liaoyi/`、`nuodu/`、`redis/`、`image-gen/` 下各 compose |
| 更新/部署脚本 | `agent_update*.sh`、`agent1/2/3_update.sh`、`deploy.sh`、`check_deployment.sh`、`sim.sh`、`test2dev.sh` |
| 备份/恢复 | `backup.sh`、`backup_daily.sh`、`backup_postgres.sh`、`restore_postgres.sh`、`nuodu/backup_daily_nd.sh`、`liaoyi/backup_daily_li.sh` |
| 启动/运行时 | `gunicorn.conf.py`、`fix_tmp.sh`、`kill_connections.py`、`env.sim.example` |
| 数据库 | `init-postgres.sql`、`init-postgres-logs.sql`、`db_update.yaml`、`nuodu/init-postgres*.sql`、一次性运维 SQL |

## 常用入口

- 日常运维、日志、巡检：先读 [服务器部署现状.md](./服务器部署现状.md)
- 每次发布前：过一遍 [生产发布备忘.md](./生产发布备忘.md)
- 本地开发环境：见 [开发环境手册](../guides/development/local-dev-environments.md)