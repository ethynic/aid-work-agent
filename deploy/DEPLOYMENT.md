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
    ├── 文件上传 (/uploads/*) → 宿主机文件系统
    └── 记忆文件 (/memories/*) → 宿主机文件系统

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
sudo mkdir -p /var/www/qb3_upload/agent_memories
sudo chown -R $USER:$USER /var/www/agent
sudo chown -R www-data:www-data /var/www/qb3_upload

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
QWEN_API_KEY=your_qwen_api_key_here

# 多 Key 池（可选，多个 key 用逗号分隔，有效利用免费额度）
# QWEN_API_KEYS=key1,key2,key3

# 智谱GLM（备选）
# LLM_PROVIDER=zhipu
# ZHIPU_API_KEY=your_zhipu_api_key_here
# ZHIPU_API_KEYS=key1,key2,key3

# ========== Gunicorn 配置 ==========
# 当前服务器 4 核，推荐 workers = 2×4+1 = 9（已为默认值，无需修改）
# WORKERS=9
# WORKER_TIMEOUT=120

# ========== 服务配置 ==========
API_PORT=8000
DEBUG=false

# ========== 企业微信配置 ==========
WECOM_ENABLED=true
WECOM_CORP_ID=your_corp_id
WECOM_AGENT_ID=your_agent_id
WECOM_SECRET=your_secret
WECOM_TOKEN=your_token
WECOM_ENCODING_AES_KEY=your_aes_key

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
```

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
  /var/www/qb3_upload/agent_memories \
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
QWEN_API_KEYS=key1,key2,key3

# 每个 Key 默认并发 2，3 个 Key 总并发 = 6
# 配置在 configs/config.yaml 中可调整每 Key 并发上限
```

---

## 📞 联系支持

如遇问题，请查看：
1. 项目文档：`/var/www/agent/docs/`
2. 应用日志：`/var/www/agent/log/`
3. 容器日志：`docker logs aid-agent-api`
4. 技术支持：联系项目负责人

---

**部署清单**：
- [ ] 服务器环境准备完成（Docker、docker compose、Nginx）
- [ ] 项目代码拉取完成
- [ ] 环境变量配置完成（`.env`）
- [ ] 前端构建完成
- [ ] Docker 容器运行正常（确认 9 个 gunicorn worker 启动）
- [ ] Nginx 配置生效
- [ ] SSL 证书配置完成
- [ ] 健康检查通过（`curl http://localhost:8000/health`）
- [ ] 备份脚本配置完成
