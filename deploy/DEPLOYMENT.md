# AID Work Agent 生产环境部署文档

## 📋 部署概述

### 环境要求

- **操作系统**: Ubuntu 16.04+
- **软件环境**:
  - Docker 20.10+
  - Docker Compose 2.0+
  - Nginx 1.18+
- **网络要求**:
  - 域名已解析到服务器IP
  - SSL证书已配置
  - 端口开放: 80, 443

### 部署架构

```
互联网用户
    ↓
Nginx (宿主机)
    ├── 静态文件 (frontend/dist) → 直接返回
    ├── API请求 (/api/*) → Docker容器 (8000端口)
    ├── 文件上传 (/uploads/*) → 宿主机文件系统
    └── 记忆文件 (/memories/*) → 宿主机文件系统
```

---

## 🚀 快速部署

### 方式一：一键部署（推荐）

```bash
# 1. 上传代码到服务器
# 通过 FTP 将整个项目上传到 /var/www/agent/

# 2. 进入部署目录
cd /var/www/agent/deploy

# 3. 配置环境变量
cp .env.production.example ../.env
vim ../.env  # 编辑配置文件

# 4. 执行部署脚本
chmod +x deploy.sh
./deploy.sh

# 5. 检查部署状态
./check_deployment.sh
```

### 方式二：手动部署

详见下方分步说明。

---

## 📦 分步部署指南

### 第一步：准备服务器环境

```bash
# 更新系统包
sudo apt-get update && sudo apt-get upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 安装 Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

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
docker-compose --version
nginx -v
```

### 第二步：上传项目代码

```bash
# 通过 FTP 工具（如 FileZilla）将项目上传到 /var/www/agent/
# 确保包含以下内容：
# - src/               后端源代码
# - frontend/          前端代码
# - configs/           配置文件
# - deploy/            部署脚本
# - requirements.txt   Python依赖
# - Dockerfile         Docker镜像构建文件
# - docker-compose.prod.yml  生产环境编排文件

# 设置文件权限
cd /var/www/agent
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
# ========== LLM配置 ==========
# 通义千问（推荐）
LLM_PROVIDER=qwen
QWEN_API_KEY=your_qwen_api_key_here

# 智谱GLM（备选）
# LLM_PROVIDER=zhipu
# ZHIPU_API_KEY=your_zhipu_api_key_here

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

# 构建并启动容器
docker-compose -f docker-compose.prod.yml up -d --build

# 查看容器状态
docker-compose -f docker-compose.prod.yml ps

# 查看日志
docker-compose -f docker-compose.prod.yml logs -f aid-agent-api
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

# 2. 检查端口监听
sudo netstat -tlnp | grep -E ':(80|443|8000)'

# 3. 测试后端API
curl http://localhost:8000/health

# 4. 测试前端访问
curl -I https://agent.aidingyi.cn

# 5. 检查日志
docker logs aid-agent-api --tail 100
```

---

## 🔧 运维管理

### 日志查看

```bash
# 查看容器日志
docker logs aid-agent-api -f

# 查看应用日志
tail -f /var/www/agent/logs/app.log

# 查看 Nginx 访问日志
sudo tail -f /var/log/nginx/access.log

# 查看 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

### 服务管理

```bash
# 启动服务
docker-compose -f docker-compose.prod.yml start

# 停止服务
docker-compose -f docker-compose.prod.yml stop

# 重启服务
docker-compose -f docker-compose.prod.yml restart

# 重启 Nginx
sudo systemctl restart nginx
```

### 代码更新

```bash
# 方式一：使用更新脚本（推荐）
cd /var/www/agent/deploy
./update_code.sh

# 方式二：手动更新
# 1. 上传新代码到服务器
# 2. 重新构建前端
cd /var/www/agent/frontend
npm run build

# 3. 重启容器（后端代码通过 volume 挂载会自动更新）
docker-compose -f docker-compose.prod.yml restart

# 4. 清理浏览器缓存并测试
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
# - 依赖问题：重新构建镜像 docker-compose build --no-cache
```

#### 2. Nginx 502 错误

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

#### 3. 前端页面无法访问

```bash
# 检查前端构建文件
ls -la /var/www/agent/frontend/dist/

# 检查文件权限
sudo chown -R www-data:www-data /var/www/agent/frontend/dist/

# 清理浏览器缓存
# Chrome: Ctrl+Shift+Delete
```

#### 4. API 请求超时

```bash
# 检查容器资源使用
docker stats aid-agent-api

# 增加超时时间（修改 nginx 配置）
# proxy_read_timeout 300s;

# 查看应用日志
docker logs aid-agent-api --tail 200
```

#### 5. 文件上传失败

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

### 1. Docker 资源限制

编辑 `docker-compose.prod.yml`：

```yaml
services:
  aid-agent-api:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

### 2. Nginx 优化

```nginx
# 在 http 块中添加
worker_processes auto;
worker_connections 1024;
keepalive_timeout 65;
gzip_comp_level 6;
```

### 3. 数据库连接池

```bash
# 如果使用外部数据库，配置连接池
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10
```

---

## 📞 联系支持

如遇问题，请查看：
1. 项目文档：`/var/www/agent/docs/`
2. 日志文件：`/var/www/agent/logs/`
3. 技术支持：联系项目负责人

---

**部署清单**：
- [ ] 服务器环境准备完成
- [ ] 项目代码上传完成
- [ ] 环境变量配置完成
- [ ] 前端构建完成
- [ ] Docker 容器运行正常
- [ ] Nginx 配置生效
- [ ] SSL 证书配置完成
- [ ] 健康检查通过
- [ ] 备份脚本配置完成
