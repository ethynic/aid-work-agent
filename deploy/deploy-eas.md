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
sudo mkdir -p /home/ubuntu/aid_data/memories
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
QWEN_API_KEYS=your_qwen_api_key_here

# ========== Gunicorn 配置 ==========
# 4核服务器: workers = 2×4+1 = 9
WORKERS=9
WORKER_TIMEOUT=120

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

---

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
mkdir -p /home/ubuntu/aid_data/memories
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

