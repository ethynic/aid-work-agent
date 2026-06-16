#!/bin/bash

# ==============================================================================
# AI 数字员工系统 - 快速更新脚本（不重建 Docker 镜像）
# 用途: 仅更新代码，快速重启服务
# ==============================================================================

set -e

echo "=========================================="
echo "  AI 数字员工系统 - 快速更新"
echo "=========================================="

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory /var/www/agent 2>/dev/null || true

# 1. 拉取代码
echo "[1] 拉取最新代码..."
cd "/var/www/agent"
OLD_HEAD=$(git rev-parse HEAD)
git fetch --all
git reset --hard origin/master
NEW_HEAD=$(git rev-parse HEAD)
sudo chmod -R 777 .
sudo find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 2. 前端编译
echo "[2] 更新前端（检测到前端代码变更）..."
sudo rm -rf frontend/dist/*
sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm install
sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm run build


# 3. 停止旧容器（释放数据库连接）
echo "[3] 停止旧容器..."
sudo docker compose -f docker-compose.prod.yml down

# 4. 启动新容器
echo "[4] 启动后端服务..."
sudo docker compose -f docker-compose.prod.yml up -d

# 5. 修复容器内 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs 且默认 755，
#    Dockerfile 的 chmod 不生效，entrypoint 已处理；此处作为运行时兜底）
#    -u root：必须以 root 身份执行，否则 appuser 在 tmpfs 上无权限改 /tmp
#    兜底链：先尝试 chmod（多数 tmpfs 上 root 可改），失败则 mount remount
echo "[5] 修复容器 /tmp 权限..."
if ! sudo docker exec -u root aid-agent-api chmod 1777 /tmp 2>/dev/null; then
    echo "  chmod 失败，尝试 mount remount..."
    sudo docker exec -u root aid-agent-api mount -o remount,mode=1777 /tmp 2>/dev/null || \
        echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
sudo docker exec -u root aid-agent-api ls -ld /tmp || true

echo ""
echo "=========================================="
echo "  更新完成！"
echo "=========================================="

/agent2_update.sh
