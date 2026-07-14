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
git config --global --add safe.directory /var/www/agent3 2>/dev/null || true

# 1. 拉取代码
echo "[1] 拉取最新代码..."
cd "/var/www/agent3"
OLD_HEAD=$(git rev-parse HEAD)
git fetch --all
git reset --hard origin/master
NEW_HEAD=$(git rev-parse HEAD)
# chmod -R 777 .
find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 清除 Python 字节码缓存（避免旧代码运行）
echo "[1.1] 清除 Python .pyc 缓存..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 2. 判断前端是否需要编译
if [ "$OLD_HEAD" != "$NEW_HEAD" ] || [ ! -d "frontend/dist" ]; then
   FRONTEND_CHANGED=1
else
   FRONTEND_CHANGED=0
fi

if [ "$FRONTEND_CHANGED" -gt 0 ]; then
    echo "[2] 前端编译..."
    rm -rf frontend/dist/*
    docker run --rm -v /var/www/agent3/frontend:/app -w /app node:22-alpine npm install
    docker run --rm -v /var/www/agent3/frontend:/app -w /app node:22-alpine npm run build
else
   echo "[2] 前端代码无变更，跳过编译。"
fi

# 3. 停止旧容器（释放数据库连接）
echo "[3] 停止旧容器..."
docker compose -f docker-compose.dev.yml down --remove-orphans

# 4. 启动新容器
#    --force-recreate：强制走"删了重建"路径，避免 compose 协调器在 down 之后偶发误报
#                       container name conflict（容器最终会被正确拉起，但脚本会中断）
#    --wait：等所有容器 healthy 才返回，与 set -e 配合更可预测
echo "[4] 启动后端服务..."
docker compose -f docker-compose.dev.yml up -d --force-recreate

# 5. 修复容器内 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs 且默认 755，
#    Dockerfile 的 chmod 不生效，entrypoint 已处理；此处作为运行时兜底）
#    -u root：必须以 root 身份执行，否则 appuser 在 tmpfs 上无权限改 /tmp
#    兜底链：先尝试 chmod（多数 tmpfs 上 root 可改），失败则 mount remount
echo "[5] 修复容器 /tmp 权限..."
if ! docker exec -u root aid-agent-api3 chmod 1777 /tmp 2>/dev/null; then
    echo "  chmod 失败，尝试 mount remount..."
    docker exec -u root aid-agent-api3 mount -o remount,mode=1777 /tmp 2>/dev/null || \
        echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
docker exec -u root aid-agent-api3 ls -ld /tmp || true

# 6. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像，
#    新依赖不会自动安装；下次重建镜像后可移除此步骤）
echo "[6] 增量安装新增依赖..."
docker exec -u root aid-agent-api3 \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

echo ""
echo "更新完成！"
echo "=========================================="
