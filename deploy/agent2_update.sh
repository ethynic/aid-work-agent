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
git config --global --add safe.directory /var/www/agent2 2>/dev/null || true

# 1. 拉取代码
echo "[1/5] 拉取最新代码..."
cd "/var/www/agent2"
OLD_HEAD=$(git rev-parse HEAD)
#git pull origin master
git fetch --all
git reset --hard origin/master
NEW_HEAD=$(git rev-parse HEAD)
# 设置需要写入权限的目录
sudo chmod -R 777 .
sudo find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 2. 判断前端是否需要编译
if [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
    FRONTEND_CHANGED=$(git diff --name-only "$OLD_HEAD" "$NEW_HEAD" -- frontend/ | wc -l)
else
    FRONTEND_CHANGED=0
fi

if [ "$FRONTEND_CHANGED" -gt 0 ]; then
    echo "[2/5] 更新前端（检测到前端代码变更）..."
    sudo rm -rf frontend/dist/*
    sudo docker run --rm -v /var/www/agent2/frontend:/app -w /app node:22-alpine npm install
    sudo docker run --rm -v /var/www/agent2/frontend:/app -w /app node:22-alpine npm run build
else
    echo "[2/5] 前端代码无变更，跳过编译。"
fi

# 3. 释放数据库连接（容器重启前清理残留连接，避免占满 max_connections）
echo "[3/5] 释放数据库连接..."
for DB_NAME in aid_work_agent2 aid_work_logs2; do
    sudo docker run --rm postgres:16-alpine psql \
        "postgresql://aid_user:Aid_2026@172.17.80.10:5433/${DB_NAME}" \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name IN ('aid-work-agent', 'aid-work-agent-logs') AND pid <> pg_backend_pid();" \
        2>/dev/null && echo "  已清理 ${DB_NAME} 应用连接" || echo "  跳过 ${DB_NAME}（不可达或无连接）"
done

# 4. 重启后端容器（代码已通过 volume 挂载，无需重建）
echo "[4/5] 重启后端服务..."
sudo docker compose -f docker-compose.test.yml down
sudo docker compose -f docker-compose.test.yml up -d

echo ""
echo "[5/5] 更新完成！"
echo "=========================================="
