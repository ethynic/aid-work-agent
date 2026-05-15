# Redis 本地开发环境

本目录提供本地开发所需的 Redis 容器化部署。

## 前提条件

- 已安装 Docker 和 Docker Compose
- 端口 6379 未被占用

## 快速开始

### 启动 Redis

```bash
cd deploy/redis
docker-compose up -d
```

### 检查状态

```bash
docker-compose ps
docker-compose logs -f
```

### 连接 Redis

```bash
# 使用 redis-cli
docker exec -it aid-agent-redis redis-cli

# 或从外部连接
redis-cli -h localhost -p 6379
```

### 停止 Redis

```bash
docker-compose down
```

### 重启 Redis

```bash
docker-compose restart
```

## 配置参数

| 参数 | 值 | 说明 |
|------|------|------|
| host | `localhost` | 连接地址 |
| port | `6379` | 连接端口 |
| db | `0` | 默认数据库 |
| password | 空 | 本地开发无密码 |

在项目的 `configs/config.yaml` 中已包含上述默认配置，无需额外修改即可连接。

## 故障排查

### 端口被占用

```bash
# 查找占用 6379 的进程
lsof -i :6379
# 或
netstat -tlnp | grep 6379

# 解决：停掉本地 Redis 服务或修改 docker-compose.yml 中的端口映射
```

### 卷权限问题

```bash
# 检查 Redis 数据卷权限
docker volume inspect deploy_redis_data

# 如果需要清理数据（注意：会丢失所有缓存）
docker-compose down -v
docker-compose up -d
```

### 容器无法启动

```bash
# 查看容器日志
docker logs aid-agent-redis

# 常见原因：内存不足或磁盘空间不足
docker system df
```

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
