# 全筑档案智能体部署

# 内网服务器信息
192.168.200.72
用户名：ubuntu
密码：K9#mP2$vL5@nQ8&w

---

## 📋 部署概述

- **目标服务器**: 192.168.200.72
- **操作系统**: Ubuntu 22.04
- **配置**: 4核8G，硬盘 100G+400G
- **部署方式**: Docker + Nginx

## 🚀 部署步骤

### 第一步：SSH 连接到服务器

```bash
ssh ubuntu@192.168.200.72
```

### 第二步：更新系统并安装基础软件

```bash
sudo timedatectl set-timezone Asia/Shanghai

# 更新系统包
sudo apt-get update && sudo apt-get upgrade -y

# 安装 Docker（包含 docker compose plugin）
# curl -fsSL https://get.docker.com | sh

# 1. 安装依赖
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# 2. 添加 Docker GPG key（阿里云镜像）
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# 3. 添加 Docker 仓库（阿里云镜像）
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 4. 安装 Docker
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 5. 启动并设置开机自启
sudo systemctl start docker
sudo systemctl enable docker

# 6. 验证安装
docker --version

# 7. 配置镜像加速
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://registry.docker-cn.com"
  ]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker


sudo usermod -aG docker $USER

# 验证 docker compose（Ubuntu 22.04 原生支持）
docker compose version

# 安装 Nginx
sudo apt-get install nginx -y

# 安装 CIFS 工具（SMB 挂载用）
sudo apt-get install -y cifs-utils
```

### 第三步：创建项目目录并拉取代码

```bash
# 创建项目目录
sudo mkdir -p /var/www/agent
sudo mkdir -p /home/ubuntu/aid_data/uploads
sudo mkdir -p /home/ubuntu/aid_data/storage
sudo mkdir -p /mnt/smb/AIUpload

# 设置权限
sudo chown -R $USER:$USER /var/www/agent
sudo chown -R www-data:www-data /home/ubuntu/aid_data

# 进入项目目录
cd /var/www/agent

# 克隆仓库（替换为实际仓库地址）
git clone https://codeup.aliyun.com/69d5bf06405bafb07e1278aa/shtulin/aid-work-agent.git .

# 设置权限
chmod -R 755 .
chmod +x deploy/*.sh
```

### 第四步：配置环境变量

```bash
cd /var/www/agent

# 复制环境变量模板
cp deploy/.env.production.example .env

# 编辑配置文件
vim .env
```

**重要配置项**：

```bash
# ========== LLM 配置 ==========
LLM_PROVIDER=qwen
API_KEYS=your_qwen_api_key_here

# ========== Gunicorn 配置 ==========
# 4核服务器: workers = 2×4+1 = 9
WORKERS=9
WORKER_TIMEOUT=120

# ========== 服务配置 ==========
API_PORT=8000
DEBUG=false

# 企业微信 / 钉钉 / 飞书渠道不再通过 .env 配置，请登录管理后台「渠道配置」页面录入

# ========== 工具配置 ==========
SMTP_SERVER=smtp.example.com
SMTP_PORT=465
SMTP_USER=your_email@example.com
SMTP_PASSWORD=your_password

TAVILY_API_KEY=your_tavily_key
BAIDU_OCR_API_KEY=your_ocr_key
BAIDU_OCR_SECRET_KEY=your_ocr_secret

# ========== EAS 合同核对 - SMB 配置 ==========
EAS_API_URL=https://dc.trendzone.com.cn/manage/api/eas_auto_contAttach
SMB_SERVER=192.168.200.10
SMB_SHARE_NAME=AIUpload
SMB_USERNAME=aiupload
SMB_PASSWORD=Ai@2025
SMB_MOUNT_POINT=/mnt/smb/AIUpload
```

### 第五步：挂载 SMB 共享目录

```bash
# 创建凭据文件
sudo tee /etc/smb-credentials.aiupload > /dev/null <<'EOF'
username=aiupload
password=Ai@2025
domain=
EOF
sudo chmod 600 /etc/smb-credentials.aiupload

# 临时挂载
sudo mount -t cifs //192.168.200.10/AIUpload /mnt/smb/AIUpload \
  -o credentials=/etc/smb-credentials.aiupload,uid=1000,gid=1000,iocharset=utf8,vers=3.0

# 验证挂载
ls /mnt/smb/AIUpload

# 配置开机自动挂载
echo '//192.168.200.10/AIUpload /mnt/smb/AIUpload cifs credentials=/etc/smb-credentials.aiupload,uid=1000,gid=1000,iocharset=utf8,vers=3.0,_netdev 0 0' | sudo tee -a /etc/fstab

# 验证 fstab
sudo mount -a
```

### 第六步：构建前端

```bash
# nvm 升级
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
source ~/.bashrc
nvm install 18
nvm use 18
nvm alias default 18

cd /var/www/agent/frontend
npm install
npm run build
```

### 第七步：启动 Docker 容器

```bash
cd /var/www/agent

# 首次构建并启动。如果构建失败，可以尝试将本机的 python:3.11-slim 导出（docker save -o python311slim.tar python:3.11-slim），到服务器上导入（docker load -i python311slim.tar），然后再构建
docker compose -f docker-compose.prod-eas.yml up -d --build

# 查看容器状态
docker compose -f docker-compose.prod-eas.yml ps

# 查看日志
docker compose -f docker-compose.prod-eas.yml logs -f aid-agent-api

# 部署 postgresql 数据库容器
cd /var/www/agent/deploy
docker compose -f docker-compose.postgres.yml up -d --build
# 以上命令报错，用本地导出、服务器导入镜像的方法
# 导出两个镜像
docker save -o aid_pgadmin.tar dpage/pgadmin4:latest
docker save -o aid_pgvector.tar pgvector/pgvector:pg16
docker load -i aid_pgadmin.tar
docker load -i aid_pgvector.tar
# 启动 postgres（不需要 --build）
docker compose -f docker-compose.postgres.yml up -d

```

**重要说明**：
- 容器内的 `appuser` 用户 uid=1000，与宿主机 SMB 挂载时指定的 `uid=1000`（ubuntu 用户）保持一致
- 这样可以确保容器内的应用对 SMB 共享目录有正确的读写权限

**验证 SMB 共享目录写入权限**：
```bash
# 验证容器内用户
docker exec -it aid-agent-api id
# 应显示：uid=1000(appuser) gid=999(appgroup)

# 测试写入
docker exec -it aid-agent-api bash -c "touch /mnt/smb/AIUpload/.write_test && rm /mnt/smb/AIUpload/.write_test && echo 'SMB write OK'"
# 应返回：SMB write OK
```

### 第八步：配置 Nginx

```bash
# 复制 Nginx 配置
sudo cp deploy/agent.aidingyi.cn.conf /etc/nginx/sites-available/

# 创建软链接
sudo ln -sf /etc/nginx/sites-available/agent.aidingyi.cn.conf /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重载 Nginx
sudo systemctl reload nginx
sudo systemctl enable nginx
```

### 第九步：验证部署

```bash
# 检查容器状态
docker ps

# 检查端口
sudo netstat -tlnp | grep -E ':(80|443|8000)'

# 测试后端 API
curl http://localhost:8000/health

# 测试前端
curl -I http://localhost

# 查看日志
docker logs aid-agent-api --tail 50
```

### 第十步：配置备份任务

```bash
# 创建备份目录
sudo mkdir -p /home/ubuntu/aid_backup/postgres
sudo mkdir -p /home/ubuntu/aid_backup/uploads
sudo chown -R ubuntu:ubuntu /home/ubuntu/aid_backup

# 安装 cron（如果没有）
sudo apt-get install -y cron

# 编辑 crontab
crontab -e
```

**在 crontab 中添加以下内容**：

```cron
# 每天 1:00 全量备份 PostgreSQL 数据库（zip 压缩），保留 180 天
0 1 * * * docker exec aid-postgres-1 pg_dump -U postgres -d aid_agent | zip > /home/ubuntu/aid_backup/postgres/backup_$(date +\%Y\%m\%d).sql.zip && find /home/ubuntu/aid_backup/postgres/ -name "backup_*.sql.zip" -mtime +180 -delete

# 每天 0:01 备份前一天的上传文件（增量备份，打包为 zip，全部保留）
1 0 * * * cd /home/ubuntu/aid_data/uploads && find . -type f -mtime 1 | zip -@ /home/ubuntu/aid_backup/uploads/backup_$(date +\%Y\%m\%d).zip 2>/dev/null || true
```

**说明**：
- PostgreSQL 备份：每天 1:00 执行全量备份，自动删除 180 天前的旧备份
- 上传文件备份：每天 0:01 备份前一天新增的上传文件，保留全部历史

**验证备份**：
```bash
# 查看备份目录
ls -la /home/ubuntu/aid_backup/postgres/
ls -la /home/ubuntu/aid_backup/uploads/

# 手动测试 PostgreSQL 备份
docker exec aid-postgres-1 pg_dump -U postgres -d aid_agent > /tmp/test_backup.sql
```

## 🔧 运维命令

```bash
# 启动服务
docker compose -f docker-compose.prod-eas.yml start

# 停止服务
docker compose -f docker-compose.prod-eas.yml stop

# 重启服务
docker compose -f docker-compose.prod-eas.yml restart

# 重启 Nginx
sudo systemctl restart nginx

# 查看日志
docker logs aid-agent-api -f

# 代码更新
cd /var/www/agent
git pull
cd frontend && npm run build && cd ..
docker compose -f docker-compose.prod-eas.yml up -d --build
```

---

## 📝 一键部署脚本

```bash
#!/bin/bash
# AID Work Agent 部署脚本 - Ubuntu 22.04

set -e

PROJECT_DIR="/var/www/agent"
DEPLOY_DIR="$PROJECT_DIR/deploy"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }

echo "========================================"
echo "  AID Work Agent 部署 - Ubuntu 22.04"
echo "========================================"

# 1. 环境检查
log_info "检查 Docker 和 Nginx..."
docker compose version
nginx -v

# 2. 进入项目目录
cd $PROJECT_DIR

# 3. 检查 .env
if [ ! -f ".env" ]; then
    cp $DEPLOY_DIR/.env.production.example .env
    echo "请先编辑 .env 文件配置环境变量"
    exit 1
fi

# 4. 创建必要目录
mkdir -p log
mkdir -p /home/ubuntu/aid_data/uploads
sudo chown -R www-data:www-data /home/ubuntu/aid_data

# 5. 启动 Docker 容器
log_info "启动 Docker 容器..."
docker compose -f docker-compose.prod-eas.yml down 2>/dev/null || true
docker compose -f docker-compose.prod-eas.yml up -d --build

# 6. 等待启动
sleep 10

# 7. 验证
if docker compose -f docker-compose.prod-eas.yml ps | grep -q "Up"; then
    log_success "部署完成！"
    echo "访问地址: https://agent.aidingyi.cn"
else
    echo "部署失败，查看日志: docker compose -f docker-compose.prod-eas.yml logs"
fi
```

---

## ✅ 部署检查清单

- [ ] SSH 连接到服务器成功
- [ ] Docker 和 docker compose 已安装
- [ ] Nginx 已安装
- [ ] 项目代码已拉取到 `/var/www/agent`
- [ ] `.env` 文件已配置（重点：LLM API Key）
- [ ] SMB 共享目录已挂载（`/mnt/smb/AIUpload`）
- [ ] 前端已构建（`frontend/dist/` 存在）
- [ ] Docker 容器运行正常（9 个 gunicorn worker 启动）
- [ ] Nginx 配置生效
- [ ] 健康检查通过：`curl http://localhost:8000/health`

## ⚠️ 常见问题
# 服务器重启后，共享目录没有写入权限
报错类似于： 附件上传失败,原因是没有共享目录的写入权限。目标路径为:/mnt/smb/AlUpload/212321SG073-XMCG-004,请确认共享路径可访问且账户有写入权限
目录 /mnt/smb/AIUpload 的所有权是 root，说明是本地目录，没有被 CIFS 挂载覆盖。而且 /mnt/smb/AIUpload 目录是空的，原来上传的文件看不到。

可以手动重新挂载
sudo mount -t cifs //192.168.200.10/AIUpload /mnt/smb/AIUpload -o credentials=/etc/smb-credentials.aiupload,uid=1000,gid=1000,iocharset=utf8,vers=3.0

然后重启容器
docker compose -f docker-compose.prod-eas.yml down
docker compose -f docker-compose.prod-eas.yml up -d
