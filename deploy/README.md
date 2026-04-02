# AID Work Agent 部署脚本说明

本目录包含生产环境部署所需的所有脚本和配置文件。

## 📁 文件清单

### 部署脚本

| 文件名 | 用途 | 使用方法 |
|--------|------|---------|
| `deploy.sh` | 一键部署脚本 | `./deploy.sh` |
| `update_code.sh` | 代码更新脚本 | `./update_code.sh` |
| `check_deployment.sh` | 部署检查脚本 | `./check_deployment.sh` |
| `backup.sh` | 数据备份脚本 | `./backup.sh` |

### 配置文件

| 文件名 | 用途 | 说明 |
|--------|------|------|
| `agent.aidingyi.cn.conf` | Nginx 配置文件 | 需复制到 `/etc/nginx/sites-available/` |
| `.env.production.example` | 环境变量模板 | 复制为 `.env` 并填写实际配置 |
| `docker-compose.prod.yml` | 生产环境 Docker Compose | 位于项目根目录 |

### 文档

| 文件名 | 用途 |
|--------|------|
| `DEPLOYMENT.md` | 完整部署文档 |
| `README.md` | 本文件 |

## 🚀 快速开始

### 1. 首次部署

```bash
# 1. 上传项目到服务器
# 通过 FTP 将整个项目上传到 /var/www/agent/

# 2. 进入部署目录
cd /var/www/agent/deploy

# 3. 配置环境变量
cp .env.production.example ../.env
vim ../.env

# 4. 执行部署
chmod +x *.sh
./deploy.sh

# 5. 检查部署状态
./check_deployment.sh
```

### 2. 代码更新

```bash
cd /var/www/agent/deploy
./update_code.sh
```

### 3. 数据备份

```bash
cd /var/www/agent/deploy
./backup.sh

# 建议添加到 crontab 实现自动备份
# 0 2 * * * /var/www/agent/deploy/backup.sh >> /var/log/agent_backup.log 2>&1
```

## 📝 脚本详细说明

### deploy.sh - 一键部署脚本

自动化执行以下步骤：

1. ✅ 检查系统环境（Docker、Nginx）
2. ✅ 检查项目目录
3. ✅ 检查环境变量配置
4. ✅ 创建必要的目录
5. ✅ 构建前端
6. ✅ 启动 Docker 容器
7. ✅ 配置 Nginx

**前提条件**：
- 已安装 Docker 和 Docker Compose
- 已安装 Nginx
- 已配置 SSL 证书
- 项目代码已上传到 `/var/www/agent/`

### update_code.sh - 代码更新脚本

自动化执行以下步骤：

1. ✅ 备份当前配置
2. ✅ 更新前端代码
3. ✅ 重启服务（可选）
4. ✅ 健康检查

**适用场景**：
- 前端代码更新
- 后端代码更新（通过 volume 挂载自动生效）
- 配置文件更新

### check_deployment.sh - 部署检查脚本

检查以下项目：

- [x] 项目目录存在
- [x] 配置文件存在
- [x] 前端构建文件存在
- [x] Docker 容器运行状态
- [x] 容器健康状态
- [x] 后端 API 健康检查
- [x] 端口监听状态
- [x] Nginx 配置正确
- [x] Nginx 运行状态
- [x] 上传目录权限
- [x] 记忆目录权限
- [x] HTTPS 访问
- [x] 日志目录权限
- [x] 磁盘空间
- [x] 容器资源使用

### backup.sh - 数据备份脚本

备份以下内容：

- ✅ 环境配置文件 (`.env`)
- ✅ 上传文件目录 (`agent_uploads/`)
- ✅ 记忆文件目录 (`agent_memories/`)
- ✅ 日志文件目录 (`log/`)

**备份策略**：
- 备份文件存储在 `/var/backups/agent/`
- 自动清理超过 7 天的旧备份
- 备份文件命名格式：`YYYYMMDD_HHMMSS.tar.gz`

## 🔧 常用命令

### Docker 管理

```bash
# 查看容器状态
docker-compose -f docker-compose.prod.yml ps

# 查看日志
docker-compose -f docker-compose.prod.yml logs -f

# 重启服务
docker-compose -f docker-compose.prod.yml restart

# 停止服务
docker-compose -f docker-compose.prod.yml down

# 重新构建
docker-compose -f docker-compose.prod.yml up -d --build
docker-compose -f docker-compose.prod.yml up -d --build --no-cache
```

### Nginx 管理

```bash
# 测试配置
sudo nginx -t

# 重载配置
sudo systemctl reload nginx

# 重启 Nginx
sudo systemctl restart nginx

# 查看状态
sudo systemctl status nginx

# 查看日志
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

## 📞 故障排查

### 常见问题

#### 1. 容器无法启动

```bash
# 查看容器日志
docker logs aid-agent-api --tail 100

# 常见原因：
# - 环境变量未配置：检查 .env 文件
# - 端口冲突：修改 docker-compose.prod.yml 中的端口
# - 依赖问题：重新构建镜像
```

#### 2. Nginx 502 错误

```bash
# 检查后端容器是否运行
docker ps | grep aid-agent-api

# 检查端口连通性
curl http://localhost:8000/health

# 检查 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

#### 3. 前端页面空白

```bash
# 检查前端构建文件
ls -la /var/www/agent/frontend/dist/

# 检查文件权限
sudo chown -R www-data:www-data /var/www/agent/frontend/dist/

# 清理浏览器缓存
```

#### 4. 文件上传失败

```bash
# 检查目录权限
ls -la /var/www/qb3_upload/

# 修复权限
sudo chown -R www-data:www-data /var/www/qb3_upload/
sudo chmod -R 755 /var/www/qb3_upload/
```

## 🛡️ 安全建议

1. **定期更新系统**
   ```bash
   sudo apt-get update && sudo apt-get upgrade -y
   ```

2. **配置防火墙**
   ```bash
   sudo ufw allow ssh
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```

3. **定期备份数据**
   ```bash
   # 添加到 crontab
   crontab -e
   # 每天凌晨 2 点备份
   0 2 * * * /var/www/agent/deploy/backup.sh
   ```

4. **监控日志**
   ```bash
   # 定期检查错误日志
   sudo tail -f /var/log/nginx/error.log
   docker logs aid-agent-api --tail 100 -f
   ```

## 📊 性能优化

### Docker 资源限制

编辑 `docker-compose.prod.yml`，调整资源限制：

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
```

### Nginx 优化

编辑 `/etc/nginx/nginx.conf`：

```nginx
worker_processes auto;
worker_connections 1024;
keepalive_timeout 65;
```

## 📝 更新日志

- **2026-03-23**: 初始版本，包含完整部署脚本和文档

## 📞 联系支持

如遇问题，请查看：
1. 完整部署文档：`DEPLOYMENT.md`
2. 项目文档：`/var/www/agent/docs/`
3. 日志文件：`/var/www/agent/log/`
