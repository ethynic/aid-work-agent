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
git fetch --all
git reset --hard origin/master
sudo chmod -R 777 .
sudo chmod -R 777 log
sudo find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 2. 判断前端是否需要编译
if [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
    FRONTEND_CHANGED=$(git diff --name-only "$OLD_HEAD" "$NEW_HEAD" -- frontend/ | wc -l)
else
    FRONTEND_CHANGED=0
fi

if [ "$FRONTEND_CHANGED" -gt 0 ]; then
    echo "[2] 更新前端（检测到前端代码变更）..."
    sudo rm -rf frontend/dist/*
    sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm install
    sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm run build
else
    echo "[2] 前端代码无变更，跳过编译。"
fi

# 3. 停止旧容器（释放数据库连接）
echo "[3] 停止旧容器..."
sudo docker compose -f docker-compose.prod.yml down

# 4. 启动新容器
echo "[4] 启动后端服务..."
sudo docker compose -f docker-compose.prod.yml up -d

echo ""
echo "=========================================="
echo "  更新完成！"
echo "=========================================="

/agent2_update.sh
