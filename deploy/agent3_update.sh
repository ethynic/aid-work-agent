#!/bin/bash

# ==============================================================================
# AI 数字员工系统（在线开发环境 agent3）- 快速更新脚本（不重建 Docker 镜像）
# 用途: 仅更新代码，快速重启服务
# ==============================================================================
# 变更记录：
#   1. 前端编译输出到 dist.new，编译期间 nginx 继续服务旧 dist，build 完成后
#      原子切换（两步 mv），消除原「rm -rf dist/* → npm run build」造成的 ~30s 前端空窗
#   2. 前端编译（后台）与后端重启（up）并行，总停机时间 ≈ 后端重启耗时
#   3. 保留「前端无变更跳过编译」判断；前端无变更时直接重启后端
#   4. 用 up --force-recreate --remove-orphans 替代 down + up，省去全停窗口
#   5. up 后用 docker update 施加 cgroup 资源限制（compose 非 swarm 会忽略
#      deploy.resources，资源值在脚本内维护，为唯一来源）
#   6. package-lock.json 未变化时跳过 npm install；npm install 挂命名卷缓存
#      并加 --no-audit --prefer-offline，消除全新容器重拉包元数据导致的数分钟卡顿
# ==============================================================================

set -e

START_TS=$(date +%s)

echo "=========================================="
echo "  AI 数字员工系统（在线开发） - 快速更新"
echo "=========================================="

FRONTEND_DIR="/var/www/agent3/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="/var/www/agent3/log/frontend-build.log"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory /var/www/agent3 2>/dev/null || true

# 1. 拉取代码
echo "[1] 拉取最新代码..."
cd "/var/www/agent3"
OLD_HEAD=$(git rev-parse HEAD)
echo "更新前版本: $(git log -1 --format='%cd %s' --date='format:%Y-%m-%d %H:%M:%S')"
git fetch --all
git reset --hard origin/master
echo "更新后版本: $(git log -1 --format='%cd %s' --date='format:%Y-%m-%d %H:%M:%S')"
NEW_HEAD=$(git rev-parse HEAD)
find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 1.1 清除 Python 字节码缓存（避免旧代码运行）
echo "[1.1] 清除 Python .pyc 缓存..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 2. 判断前端是否需要编译；需要时编译到 dist.new（后台，与后端重启并行）
FRONTEND_PID=""
if [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
    FRONTEND_CHANGED=$(git diff --name-only "$OLD_HEAD" "$NEW_HEAD" -- frontend/ | wc -l)
else
    FRONTEND_CHANGED=0
fi

if [ "$FRONTEND_CHANGED" -gt 0 ] || [ ! -d "$DIST_DIR" ]; then
    echo "[2] 前端有变更，编译到 dist.new（后台）..."
    # 依赖安装 + 类型/边界检查在前台（不产出 dist，服务不受影响；失败即中止避免白停）
    # npm 缓存用命名卷持久化（容器内 /root/.npm 每次销毁，否则全新容器需向 registry
    # 重新拉取全部包元数据，up to date 也会耗时数分钟）；--no-audit 跳过 audit 网络请求
    if git diff --quiet "$OLD_HEAD" "$NEW_HEAD" -- frontend/package-lock.json; then
        echo "[2.1] package-lock.json 未变化，跳过依赖安装"
    else
        echo "[2.1] 安装前端依赖..."
        docker run --rm \
            -v "$FRONTEND_DIR":/app \
            -v agent3_npm_cache:/root/.npm \
            -w /app node:22-alpine \
            npm install --no-audit --no-fund --prefer-offline
    fi
    docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck
    docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
        node scripts/check-dependency-boundaries.mjs --scope=web
    # vite build + 产物校验放后台，与后端重启并行
    rm -rf "$DIST_DIR.new"
    mkdir -p "$(dirname "$BUILD_LOG")"
    (
      docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
          npx vite build --outDir dist.new \
        && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
            node scripts/verify-web-build.mjs /app/dist.new
    ) > "$BUILD_LOG" 2>&1 &
    FRONTEND_PID=$!
else
    echo "[2] 前端代码无变更，跳过编译。"
fi

# 3. 后端重启（与前端编译并行；前端无变更时直接重启）
#    不先 down：up --force-recreate 会自动 stop→remove→create，避免全停窗口
echo "[3] 重启后端服务..."
docker compose -f docker-compose.dev.yml up -d --force-recreate --remove-orphans --wait

# 4. 施加资源限制（docker compose 非 swarm 会忽略 deploy.resources，改用 docker update
#    显式施加 cgroup 限制；资源值在本脚本内维护，为唯一来源；本环境无 background）
echo "[4] 施加容器资源限制..."
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-agent-api3

# 5. 修复容器内 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs 且默认 755，
#    Dockerfile 的 chmod 不生效，entrypoint 已处理；此处作为运行时兜底）
echo "[5] 修复容器 /tmp 权限..."
if ! docker exec -u root aid-agent-api3 chmod 1777 /tmp 2>/dev/null; then
  echo "  chmod 失败，尝试 mount remount..."
  docker exec -u root aid-agent-api3 mount -o remount,mode=1777 /tmp 2>/dev/null || \
      echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
docker exec -u root aid-agent-api3 ls -ld /tmp || true

# 6. 等待前端编译完成并原子切换（仅前端有变更时）
if [ -n "$FRONTEND_PID" ]; then
    echo "[6] 等待前端编译完成..."
    if ! wait "$FRONTEND_PID"; then
        echo "  错误：前端编译失败，详见 $BUILD_LOG"
        echo "  后端已更新但前端仍为旧版本，请检查后重跑本脚本"
        exit 1
    fi

    echo "[6.1] 原子切换前端 dist..."
    rm -rf "$DIST_DIR.old"
    [ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
    mv "$DIST_DIR.new" "$DIST_DIR"
    rm -rf "$DIST_DIR.old"
fi
chmod 777 "$DIST_DIR" # dist 目录需要 777 权限，否则无法ftp上传微信验证文件

# 7. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像，
#    新依赖不会自动安装；下次重建镜像后可移除此步骤）
echo "[7] 增量安装新增依赖..."
docker exec -u root aid-agent-api3 \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

echo ""
echo "更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
