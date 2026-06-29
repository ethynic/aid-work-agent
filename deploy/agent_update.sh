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
# sudo chmod -R 777 .
sudo find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 2. 前端编译
echo "[2] 前端编译..."
sudo rm -rf frontend/dist/*
sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm install
sudo docker run --rm -v /var/www/agent/frontend:/app -w /app node:22-alpine npm run build

# 3. 停止旧容器（释放数据库连接）
echo "[3] 停止旧容器..."
sudo docker compose -f docker-compose.prod.yml down --remove-orphans

# 4. 启动新容器
#    --force-recreate：强制走"删了重建"路径，避免 compose 协调器在 down 之后偶发误报
#                       container name conflict（容器最终会被正确拉起，但脚本会中断）
#    --wait：等所有容器 healthy 才返回，与 set -e 配合更可预测
echo "[4] 启动后端服务..."
sudo docker compose -f docker-compose.prod.yml up -d --wait

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

# 6. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像，
#    新依赖不会自动安装；下次重建镜像后可移除此步骤）
echo "[6] 增量安装新增依赖..."
sudo docker exec -u root aid-agent-api \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

# 7. 清理已移除的 zhipuai 包（requirements.txt 已不再包含，
#    旧镜像里残留会拉低 pyjwt 到 <2.9，与 mcp 冲突）
#    卸载后需确认 pyjwt 仍在 >=2.10.1，否则补装一次
echo "[7] 清理已移除的 zhipuai（如存在）..."
if sudo docker exec -u root aid-agent-api pip show zhipuai >/dev/null 2>&1; then
    sudo docker exec -u root aid-agent-api pip uninstall -y zhipuai >/dev/null
    echo "  已卸载 zhipuai"
    # zhipuai 可能把 pyjwt 锁在 <2.9，卸载后显式升回
    sudo docker exec -u root aid-agent-api \
        pip install --no-cache-dir --upgrade 'pyjwt>=2.10.1' \
        -i https://mirrors.cloud.tencent.com/pypi/simple \
        --quiet || echo "  警告：pyjwt 升级失败，mcp 可能不可用"
else
    echo "  zhipuai 未安装，跳过"
fi

echo ""
echo "更新完成！"
echo "=========================================="
