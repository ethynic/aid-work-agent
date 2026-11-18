# Redis 配置

项目本地业务 Redis 的启动、连接和排查见[开发环境手册](../../docs/guides/development/local-dev-environments.md#2-复用或启动本地服务)。独立 Redis 的声明保存在 [Compose 文件](docker-compose.yml)。

## 生产环境

生产环境使用腾讯云 Redis，配置通过环境变量设置：

```bash
REDIS_ENABLED=true
REDIS_HOST=你的腾讯云Redis地址
REDIS_PORT=6379
REDIS_PASSWORD=你的密码
REDIS_DB=0
REDIS_SSL=true
```
