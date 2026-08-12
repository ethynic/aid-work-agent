# AID Work Agent 生产环境部署文档

## 📋 部署概述

### 环境要求

- **操作系统**: Ubuntu 16.04+
- **软件环境**:
  - Docker 20.10+
  - Docker Compose Plugin（`docker compose`，无中划线）
  - Nginx 1.18+
- **网络要求**:
  - 域名已解析到服务器 IP
  - SSL 证书已配置
  - 端口开放: 80, 443

> ⚠️ **注意**：服务器为 Ubuntu 16.04，不支持独立的 `docker-compose` 命令（带中划线），
> 请使用 Docker 内置的 `docker compose`（空格，无中划线）。
> 下文所有命令均使用 `docker compose`。

### 部署架构

```
互联网用户
    ↓
Nginx (宿主机)
    ├── 静态文件 (frontend/dist) → 直接返回
    ├── API请求 (/api/*) → Docker容器 (8000端口)
    └── 文件上传 (/uploads/*) → 宿主机文件系统

Docker 容器内部
    Gunicorn (主进程)
        ├── UvicornWorker #0
        ├── UvicornWorker #1
        ├── ...
        └── UvicornWorker #8   ← 4核服务器默认 9 个 Worker
```

### 后端进程架构（Gunicorn + UvicornWorker）

生产环境使用 **Gunicorn 管理多个 UvicornWorker 进程**，替代单进程 uvicorn，以充分利用多核 CPU：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `WORKERS` | 9 | 当前服务器 4 核，2×4+1=9 |
| `WORKER_TIMEOUT` | 120s | LLM 调用耗时较长，不能设太小 |
| `SERVER_PORT` | 8000 | 监听端口 |
| `LOG_LEVEL` | info | 日志级别 |

运行参数统一在 `deploy/gunicorn.conf.py` 中管理，也可通过 `.env` 环境变量覆盖。

---

## 🚀 快速部署

### 方式一：手动部署（推荐，步骤清晰）

详见下方分步说明。

### 方式二：脚本部署

```bash
cd /var/www/agent/deploy
chmod +x deploy.sh
./deploy.sh
```

---

## 📦 分步部署指南

### 第一步：准备服务器环境

```bash
# 更新系统包
sudo apt-get update && sudo apt-get upgrade -y

# 安装 Docker（含内置 Compose Plugin）
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 验证 docker compose（注意：无中划线）
docker compose version
# 应输出类似：Docker Compose version v2.x.x

# 安装 Nginx
sudo apt-get install nginx -y

# 创建项目目录
sudo mkdir -p /var/www/agent
sudo mkdir -p /var/www/qb3_upload/agent_uploads
sudo chown -R $USER:$USER /var/www/agent
sudo chown -R www-data:www-data /var/www/qb3_upload

# 创建 SMB 共享目录挂载点（EAS 合同核对功能使用）
sudo mkdir -p /mnt/smb/AIUpload
sudo chown -R $USER:$USER /mnt/smb/AIUpload

# 验证安装
docker --version
docker compose version
nginx -v
```

### 第二步：拉取项目代码

```bash
cd /var/www/agent

# 首次部署：克隆仓库
git clone <仓库地址> .

# 后续更新：拉取最新代码
git pull

# 设置文件权限
chmod -R 755 .
chmod +x deploy/*.sh
```

### 第三步：配置环境变量

```bash
cd /var/www/agent

# 复制环境变量模板
cp deploy/.env.production.example .env

# 编辑配置文件
vim .env
```

**重要配置项说明**：

```bash
# ========== LLM 配置 ==========
# 通义千问（推荐）
LLM_PROVIDER=qwen
API_KEYS=your_qwen_api_key_here

# 多 Key 池（可选，多个 key 用逗号分隔，有效利用免费额度）
# API_KEYS=key1,key2,key3

# 智谱GLM（备选）
# LLM_PROVIDER=zhipu\
# API_KEYS=key1,key2,key3

# ========== Gunicorn 配置 ==========
# 当前服务器 4 核，推荐 workers = 2×4+1 = 9（已为默认值，无需修改）
# WORKERS=9
# WORKER_TIMEOUT=120

# ========== 服务配置 ==========
API_PORT=8000
DEBUG=false

# 企业微信 / 钉钉 / 飞书渠道不再通过 .env 配置，请登录管理后台「渠道配置」页面录入

# ========== 工具配置 ==========
# 邮件服务
SMTP_SERVER=smtp.example.com
SMTP_PORT=465
SMTP_USER=your_email@example.com
SMTP_PASSWORD=your_password
IMAP_SERVER=imap.example.com
IMAP_PORT=993

# 网络搜索
TAVILY_API_KEY=your_tavily_key

# 百度OCR
BAIDU_OCR_API_KEY=your_ocr_key
BAIDU_OCR_SECRET_KEY=your_ocr_secret

# ========== EAS 合同核对 - SMB 共享目录配置 ==========
# EAS 合同归档 API
EAS_API_URL=https://dc.trendzone.com.cn/manage/api/eas_auto_contAttach

# SMB 服务器与共享目录
SMB_SERVER=192.168.200.10
SMB_SHARE_NAME=AIUpload
SMB_USERNAME=aiupload
SMB_PASSWORD=your_smb_password_here

# Linux 挂载点路径（容器内访问路径，需与宿主机挂载点一致）
SMB_MOUNT_POINT=/mnt/smb/AIUpload
```

### 第3.5步：挂载 SMB 共享目录（EAS 合同核对功能）

EAS 合同核对功能需要将文件上传到 Windows SMB 共享目录。在 Ubuntu 生产环境中，需在**宿主机**上预先挂载，再通过 Docker volumes 映射到容器内。

```bash
# 1. 安装 CIFS 工具
sudo apt-get install -y cifs-utils

# 2. 创建挂载点
sudo mkdir -p /mnt/smb/AIUpload

# 3. 创建凭据文件（避免密码出现在命令行和 fstab 中）
sudo tee /etc/smb-credentials.aiupload > /dev/null <<'EOF'
username=aiupload
password=your_smb_password_here
domain=
EOF
sudo chmod 600 /etc/smb-credentials.aiupload

# 4. 临时挂载（立即生效，重启后失效）
sudo mount -t cifs //192.168.200.10/AIUpload /mnt/smb/AIUpload \
  -o credentials=/etc/smb-credentials.aiupload,uid=$(id -u),gid=$(id -g),iocharset=utf8,vers=3.0

# 5. 验证挂载
ls /mnt/smb/AIUpload
# 应能看到共享目录内容

# 6. 配置开机自动挂载（添加到 /etc/fstab）
echo '//192.168.200.10/AIUpload /mnt/smb/AIUpload cifs credentials=/etc/smb-credentials.aiupload,uid=$(id -u),gid=$(id -g),iocharset=utf8,vers=3.0,_netdev 0 0' | sudo tee -a /etc/fstab

# 7. 验证 fstab 配置
sudo mount -a
# 无报错即为正确
```

> **注意事项**：
> - `vers=3.0` 指定 SMB 协议版本，如果服务器不支持 3.0 可改为 `vers=2.1` 或 `vers=1.0`
> - `_netdev` 确保网络就绪后才挂载，避免开机报错
> - Docker 容器通过 `docker-compose.prod.yml` 中的 volumes 配置访问此挂载点
> - 如果 SMB 服务器不可达，EAS 合同核对功能会返回明确的错误提示

### 第四步：构建前端

```bash
cd /var/www/agent/frontend

# 安装依赖
npm install

# 构建生产版本
npm run build

# 验证构建结果
ls -la dist/
```

### 第五步：部署 Docker 容器

```bash
cd /var/www/agent

# 构建并启动容器（注意：docker compose，无中划线）
docker compose -f docker-compose.prod.yml up -d --build

# 查看容器状态
docker compose -f docker-compose.prod.yml ps

# 查看启动日志（确认 gunicorn worker 全部启动）
docker compose -f docker-compose.prod.yml logs -f aid-agent-api
# 应能看到类似：
#   [INFO] Arbiter booted
#   [INFO] Worker with pid XXXX booted
#   ...（共 9 个 worker）
```

### 第六步：配置 Nginx

```bash
# 复制 Nginx 配置文件
sudo cp deploy/agent.aidingyi.cn.conf /etc/nginx/sites-available/

# 创建软链接
sudo ln -sf /etc/nginx/sites-available/agent.aidingyi.cn.conf /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重载 Nginx
sudo systemctl reload nginx

# 设置开机自启
sudo systemctl enable nginx
```

### 第七步：验证部署

```bash
# 运行检查脚本
cd /var/www/agent/deploy
./check_deployment.sh

# 手动验证
# 1. 检查容器状态
docker ps

# 2. 确认 gunicorn worker 数量
docker exec aid-agent-api ps aux | grep gunicorn

# 3. 检查端口监听
sudo netstat -tlnp | grep -E ':(80|443|8000)'

# 4. 测试后端 API
curl http://localhost:8000/health

# 5. 测试前端访问
curl -I https://agent.aidingyi.cn

# 6. 检查日志
docker logs aid-agent-api --tail 100
```

---

## 🔧 运维管理

### 日志查看

```bash
# 查看容器日志（gunicorn + 应用日志混合输出）
docker logs aid-agent-api -f

# 查看应用日志文件
tail -f /var/www/agent/log/aid-work-agent.log
tail -f /var/www/agent/log/error.log

# 查看 Nginx 访问日志
sudo tail -f /var/log/nginx/access.log

# 查看 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

### 服务管理

```bash
# 启动服务
docker compose -f docker-compose.prod.yml start

# 停止服务
docker compose -f docker-compose.prod.yml stop

# 重启服务
docker compose -f docker-compose.prod.yml restart

# 重启 Nginx
sudo systemctl restart nginx
```

### 代码更新

```bash
# 1. 拉取最新代码
cd /var/www/agent
git pull

# 2. 如有前端变更，重新构建前端
cd frontend && npm run build && cd ..

# 3. 重建并重启容器（有依赖变更时用 --build，否则直接 restart）
docker compose -f docker-compose.prod.yml up -d --build

# 仅重启（无依赖变更）
docker compose -f docker-compose.prod.yml restart
```

### 调整 Gunicorn 参数

修改 `deploy/gunicorn.conf.py` 后重建容器：

```bash
# 例如：调整 worker 超时
vim deploy/gunicorn.conf.py

# 重建生效
docker compose -f docker-compose.prod.yml up -d --build
```

或者在 `.env` 中设置环境变量临时覆盖（无需重建，重启即可）：

```bash
# .env 中添加
WORKERS=5
WORKER_TIMEOUT=180

docker compose -f docker-compose.prod.yml restart
```

### 数据备份

```bash
# 备份数据目录
tar -czf agent_backup_$(date +%Y%m%d).tar.gz \
  /var/www/qb3_upload/agent_uploads \
  /var/www/agent/.env

# 定期备份（添加到 crontab）
# 0 2 * * * /var/www/agent/deploy/backup.sh
```

---

## 🔍 故障排查

### 常见问题

#### 1. 容器无法启动

```bash
# 查看详细日志
docker logs aid-agent-api

# 常见原因：
# - 环境变量未配置：检查 .env 文件
# - 端口冲突：修改 docker-compose.prod.yml 中的端口映射
# - 依赖问题：重新构建镜像
docker compose -f docker-compose.prod.yml build --no-cache
```

#### 2. Gunicorn Worker 启动失败

```bash
# 查看详细启动日志
docker logs aid-agent-api 2>&1 | head -100

# 常见原因：
# - asyncio lifespan 初始化报错（检查 LLM API Key 是否配置）
# - /dev/shm 不可用（注释掉 gunicorn.conf.py 中的 worker_tmp_dir）
```

#### 3. Nginx 502 错误

```bash
# 检查后端容器是否运行
docker ps | grep aid-agent-api

# 检查端口连通性
curl http://localhost:8000/health

# 检查 Nginx 配置
sudo nginx -t

# 查看 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

#### 4. 前端页面无法访问

```bash
# 检查前端构建文件
ls -la /var/www/agent/frontend/dist/

# 检查文件权限
sudo chown -R www-data:www-data /var/www/agent/frontend/dist/
```

#### 5. API 请求超时

```bash
# 检查容器资源使用
docker stats aid-agent-api

# 增加 gunicorn worker 超时（修改 .env）
WORKER_TIMEOUT=300

# 同时增加 Nginx 超时（修改 nginx 配置）
# proxy_read_timeout 300s;
# proxy_send_timeout 300s;

# 查看应用日志
docker logs aid-agent-api --tail 200
```

#### 6. 文件上传失败

```bash
# 检查上传目录权限
ls -la /var/www/qb3_upload/agent_uploads/

# 修复权限
sudo chown -R www-data:www-data /var/www/qb3_upload/agent_uploads/
sudo chmod -R 755 /var/www/qb3_upload/agent_uploads/

# 检查磁盘空间
df -h
```

---

## 🛡️ 安全加固

### 1. 防火墙配置

```bash
# 安装 UFW
sudo apt-get install ufw

# 配置规则
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# 启用防火墙
sudo ufw enable
```

### 2. SSL 证书配置

```bash
# 使用 Let's Encrypt 免费证书
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d agent.aidingyi.cn

# 自动续期
sudo certbot renew --dry-run
```

### 3. 限制访问

```bash
# 在 nginx 配置中添加 IP 白名单（可选）
# allow 192.168.1.0/24;
# deny all;
```

---

## 📊 性能优化

### 1. Gunicorn Workers 调优

当前服务器 **4 核**，已配置最优值：

```bash
# 公式：2 × CPU核数 + 1
# 4核 → workers = 9（已为默认值）
WORKERS=9
```

如果内存不足（每个 worker 约占 100-200MB），可适当减小：

```bash
WORKERS=5  # 内存受限时
```

### 2. Docker 资源限制

编辑 `docker-compose.prod.yml`：

```yaml
services:
  aid-agent-api:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 4G
        reservations:
          cpus: '2'
          memory: 2G
```

### 3. Nginx 优化

```nginx
# 在 http 块中添加
worker_processes auto;
worker_connections 1024;
keepalive_timeout 65;
gzip on;
gzip_comp_level 6;

# API 代理超时（LLM 响应较慢，需适当放宽）
proxy_read_timeout 180s;
proxy_send_timeout 180s;
```

### 4. LLM Key 池

配置多个 API Key 并行，提升并发处理能力：

```bash
# .env 中配置 Key 池（逗号分隔）
API_KEYS=key1,key2,key3

# 每个 Key 默认并发 2，3 个 Key 总并发 = 6
# 配置在 configs/config.yaml 中可调整每 Key 并发上限
```

---

## Docker 镜像仓库

自行部署镜像仓库在 124.222.3.254:5005 端口

```
docker pull registry:2
docker run -d -p 5005:5000 --restart=always --name registry -v /data/docker/registry:/var/lib/registry registry:2
```

### 本地编译镜像并推送

在**本地开发机**上完成镜像构建并推送到镜像仓库：

```bash
# 1. 进入项目目录
cd /path/to/aid-work-agent

# 2. 构建镜像（使用生产 Dockerfile）
docker build -t 124.222.3.254:5005/aid-agent-api:latest .

# 3. 添加版本标签
docker tag aid-agent-api:latest 124.222.3.254:5005/aid-agent-api:latest

# 4. 推送镜像到仓库（如果 registry 不支持 HTTPS，需先配置 insecure-registries）
docker push 124.222.3.254:5005/aid-agent-api:latest
```

**注意**：若镜像仓库未配置 HTTPS，Docker 默认拒绝推送。请在 `/etc/docker/daemon.json` 中添加：
```json
{
  "insecure-registries": ["124.222.3.254:5005"]
}
```

然后执行 `sudo systemctl restart docker` 使配置生效。

### 服务器上拉取镜像并部署

在**生产服务器**上拉取已推送的镜像并启动服务：

```bash
# 1. SSH 登录到生产服务器
ssh user@your-server-ip

# 2. 进入项目目录
cd /var/www/agent

# 3. 拉取最新镜像（如果 registry 不支持 HTTPS，同样需要配置 insecure-registries）
docker pull 124.222.3.254:5005/aid-agent-api:latest
docker pull 172.17.80.10:5005/aid-agent-api:latest

# 4. 停止旧容器
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.test.yml down

# 5. 启动新容器（使用已拉取的镜像，无需 --build）
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.test.yml up -d

# 6. 查看容器状态
docker compose -f docker-compose.prod.yml ps

# 7. 查看启动日志
docker compose -f docker-compose.prod.yml logs -f aid-agent-api
```

> **提示**：需要在 `docker-compose.prod.yml` 中将 `build` 替换为 `image`，或同时保留两者（Docker Compose 会优先使用本地镜像），以确保使用拉取的镜像而非本地构建：
> ```yaml
> services:
>   aid-agent-api:
>     image: 124.222.3.254:5005/aid-agent-api:latest
>     # build: .   ← 可注释掉，避免每次重建
>     ...
> ```

### 一键更新脚本（服务器端）

将以下脚本保存为 `deploy/update-from-registry.sh`，便于快速更新：

```bash
#!/bin/bash
# 从镜像仓库拉取最新镜像并重启服务

set -e

REGISTRY="124.222.3.254:5005"
IMAGE="${REGISTRY}/aid-agent-api:latest"
PROJECT_DIR="/var/www/agent"

echo "=== 拉取最新镜像 ==="
docker pull ${IMAGE}

echo "=== 停止旧容器 ==="
cd ${PROJECT_DIR}
docker compose -f docker-compose.prod.yml down

echo "=== 启动新容器 ==="
docker compose -f docker-compose.prod.yml up -d

echo "=== 清理旧镜像（可选） ==="
docker image prune -f

echo "=== 检查服务状态 ==="
sleep 5
docker compose -f docker-compose.prod.yml ps
curl -s http://localhost:8000/health || echo "健康检查失败，请检查日志"

echo "=== 更新完成 ==="
```

使用方法：
```bash
cd /var/www/agent
chmod +x deploy/update-from-registry.sh
./deploy/update-from-registry.sh
```

## postgresql 数据库升级 timescale 扩展

```bash
1. 备份数据（安全第一）
# 在服务器上执行，备份当前 PG 数据卷
docker run --rm -v aid-postgres_postgres_data:/data -v $(pwd):/backup alpine tar czf /backup/pg_backup_$(date +%Y%m%d).tar.gz -C /data .

2. 切换镜像
cd /path/to/deploy
docker compose -f docker-compose.postgres.yml down
docker compose -f docker-compose.postgres.yml up -d

3. 验证 pgvector 仍可用
docker exec -it aid-postgres psql -U aid_user -d aid_work_agent2 -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"

4. 创建日志库并启用 TimescaleDB
# 创建数据库
docker exec -it aid-postgres psql -U aid_user -d postgres -c "CREATE DATABASE aid_work_logs OWNER aid_user;"

# 启用 TimescaleDB 扩展
docker exec -it aid-postgres psql -U aid_user -d aid_work_logs -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

5. 验证
docker exec -it aid-postgres psql -U aid_user -d aid_work_logs -c "SELECT extname, extversion FROM pg_extension;"
应该看到 timescaledb 和 vector（如果日志库也装了）都已就绪。之后部署应用时，init_logs_tables() 会自动建表并配置 Hypertable 分区策略。

数据恢复步骤

1. 确认目标数据库存在
docker exec -it aid-postgres psql -U aid_user -d postgres -c "\l"

如果缺了哪个，先创建
docker exec -it aid-postgres psql -U aid_user -d postgres -c "CREATE DATABASE aid_work_agent2 OWNER aid_user;"

# 1. 断开所有连接
docker exec -it aid-postgres psql -U aid_user -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='aid_work_agent' AND pid <> pg_backend_pid();"

# 2. 删除并重建空库
docker exec -it aid-postgres psql -U aid_user -d postgres -c "DROP DATABASE aid_work_agent;"
docker exec -it aid-postgres psql -U aid_user -d postgres -c "CREATE DATABASE aid_work_agent OWNER aid_user;"

# 3. 恢复备份
gunzip -c /backups/202606/aid_work_agent_20260605_120310.sql.gz | docker exec -i aid-postgres psql -U aid_user -d aid_work_agent

# === aid_work_agent2 ===
docker exec -it aid-postgres psql -U aid_user -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='aid_work_agent2' AND pid <> pg_backend_pid();"
docker exec -it aid-postgres psql -U aid_user -d postgres -c "DROP DATABASE aid_work_agent2;"
docker exec -it aid-postgres psql -U aid_user -d postgres -c "CREATE DATABASE aid_work_agent2 OWNER aid_user;"
gunzip -c /backups/202606/aid_work_agent2_20260605_120310.sql.gz | docker exec -i aid-postgres psql -U aid_user -d aid_work_agent2

```

