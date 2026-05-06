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
echo "[1/3] 拉取最新代码..."
cd "/var/www/agent"
#git pull origin master
git fetch --all
git reset --hard origin/master
# 设置需要写入权限的目录
sudo chmod -R 777 .
sudo chmod -R 777 log
sudo find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 2. 更新前端
echo "[2/3] 更新前端..."
# 使用 Docker 中的 node:18-alpine 构建
sudo rm -rf frontend/dist/*

sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm install
sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm run build

# 3. 重启后端容器（代码已通过 volume 挂载，无需重建）
echo "[3/3] 重启后端服务..."
sudo docker compose -f docker-compose.prod.yml up -d

echo ""
echo "=========================================="
echo "  更新完成！"
echo "=========================================="

/agent2_update.sh
