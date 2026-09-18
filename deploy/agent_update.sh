#!/bin/bash

# ==============================================================================
# AI 数字员工系统 - 快速更新脚本（不重建 Docker 镜像）
# 用途: 仅更新代码，快速重启服务
# 用法: ./agent_update.sh [git版本号]
#       不带参数 -> 更新到 origin/master 最新
#       带参数   -> 停留在指定提交（如 ./agent_update.sh c43c65e）
# ==============================================================================
# 变更记录：
#   1. 前端编译输出到 dist.new，编译期间 nginx 继续服务旧 dist，build 完成后
#      原子切换（两步 mv），消除原「rm -rf dist/* → npm run build」造成的 ~30s 前端空窗
#   2. 前端编译（后台）与后端重启（down/up）并行，总停机时间 ≈ 后端重启耗时
#   3. up 后用 docker update 施加 cgroup 资源限制 —— docker compose 非 swarm 模式
#      会静默忽略 deploy.resources 段，需显式施加（资源值在脚本内维护，为唯一来源）
#   4. npm install 挂命名卷缓存
#      并加 --no-audit --prefer-offline，消除全新容器重拉包元数据导致的数分钟卡顿
#   5. 内存上限移入 docker-compose.prod.yml 的 mem_limit（api 8G/background 4G/redis 1G），
#      第 6 步只保留 docker update --cpus，不再用 docker update 覆盖内存
# ==============================================================================

set -e

START_TS=$(date +%s)

echo "=========================================="
echo "  AI 数字员工系统 - 快速更新"
echo "=========================================="

FRONTEND_DIR="/var/www/agent/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="/var/www/agent/log/frontend-build.log"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory /var/www/agent 2>/dev/null || true

# 1. 拉取代码（可选参数：目标 git 提交版本号，短哈希/长哈希/tag 均可；
#    不传则更新到 origin/master 最新。指定旧版本用于回滚或停留在未全部
#    合入测试通过的提交）
echo "[1] 拉取代码..."
cd "/var/www/agent"
OLD_HEAD=$(git rev-parse HEAD)
echo "更新前版本: $(git log -1 --format='%cd %s' --date=format:'%Y-%m-%d %H:%M:%S')"
git fetch --all
if [ -n "$1" ]; then
    if ! git cat-file -e "$1^{commit}" 2>/dev/null; then
        echo "错误：仓库中不存在提交 $1（先确认已 fetch，且版本号正确）"
        exit 1
    fi
    TARGET_REF="$1"
else
    TARGET_REF="origin/master"
fi
git reset --hard "$TARGET_REF"
echo "更新后版本: $(git log -1 --format='%cd %s' --date=format:'%Y-%m-%d %H:%M:%S')"
NEW_HEAD=$(git rev-parse HEAD)
find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 1.1 清除 Python 字节码缓存（避免旧代码运行）
echo "[1.1] 清除 Python .pyc 缓存..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 2. 前端依赖安装（node_modules 已持久化在宿主机，增量安装，通常很快）
#    npm 缓存用命名卷持久化（容器内 /root/.npm 每次销毁，
#    否则全新容器需向 registry 重新拉取全部包元数据，up to date 也会耗时数分钟）；
#    --no-audit 跳过 audit 网络请求（国内直连 registry.npmjs.org 的 audit 端点极慢）
echo "[2] 安装前端依赖..."
docker run --rm \
    -v "$FRONTEND_DIR":/app \
    -v agent2_npm_cache:/root/.npm \
    -w /app node:22-alpine \
    npm install --no-audit --no-fund --prefer-offline


# 3. 前端类型检查 + 依赖边界检查（不产出 dist，nginx 服务不受影响；
#    先于后端重启执行，类型/边界错误能在此中止，避免白白停一次服务）
echo "[3] 前端类型检查 + 边界检查..."
docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck
docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
    node scripts/check-dependency-boundaries.mjs --scope=web

# 4. 前端编译到 dist.new（后台执行，与后端重启并行）。
#    旧 dist 一直保留到原子切换，编译期间前端零空窗。
#    校验链拆开跑：typecheck/boundary 已在 [3]，build 用 npx vite build --outDir
#    （npm run build 是复合命令，追加 --outDir 会透传给最后的 verify 脚本导致误解析）
echo "[4] 前端编译 dist.new（后台）..."
rm -rf "$DIST_DIR.new"
mkdir -p "$(dirname "$BUILD_LOG")"
(
  docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      npx vite build --outDir dist.new \
    && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
        node scripts/verify-web-build.mjs /app/dist.new
) > "$BUILD_LOG" 2>&1 &
FRONTEND_PID=$!

# 5. 后端重启（与前端编译并行）
#    不先 down：up --force-recreate 会自动 stop→remove→create，省去 down 后到 up 之间
#    所有容器同时停止的全停窗口；api 与 background 由 compose 串行错开重建（api 先起时
#    background 仍运行），后台任务/心跳中断更少、DB 连接释放重建更平滑
#    显式列出 api/background 两个服务：--force-recreate 不带服务名会连 redis 一起
#    重建，导致每次发版 redis 都有 ~10s 不可用窗口（2026-09-18 实证会触发 api worker
#    首连 DNS 失败降级进内存、跨 worker 状态漂移）。redis 配置/镜像变更时需手动
#    单独执行：docker compose -f docker-compose.prod.yml up -d --force-recreate --wait aid-redis
echo "[5] 重启后端服务..."
docker compose -f docker-compose.prod.yml up -d --force-recreate --remove-orphans --wait \
    aid-agent-api aid-agent-background

# 6. 施加 CPU 限制（docker compose 非 swarm 会忽略 deploy.resources；
#    内存上限已在 docker-compose.prod.yml 用 mem_limit 声明（api 8G/background 4G/redis 1G），
#    此处不再用 docker update 覆盖内存，避免与 compose 打架）
echo "[6] 施加容器 CPU 限制..."
docker update --cpus 2 aid-agent-api
docker update --cpus 1 aid-agent-background

# 7. 修复容器内 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs 且默认 755，
#    Dockerfile 的 chmod 不生效，entrypoint 已处理；此处作为运行时兜底）
echo "[7] 修复容器 /tmp 权限..."
if ! docker exec -u root aid-agent-api chmod 1777 /tmp 2>/dev/null; then
  echo "  chmod 失败，尝试 mount remount..."
  docker exec -u root aid-agent-api mount -o remount,mode=1777 /tmp 2>/dev/null || \
      echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
docker exec -u root aid-agent-api ls -ld /tmp || true

# 8. 等待前端编译完成
echo "[8] 等待前端编译完成..."
if ! wait "$FRONTEND_PID"; then
  echo "  错误：前端编译失败，详见 $BUILD_LOG"
  echo "  后端已更新但前端仍为旧版本，请检查后重跑本脚本"
  exit 1
fi

# 9. 原子切换 dist（同一文件系统内两步 mv，切换瞬间旧→新；nginx 走 /index.html 兜底）
echo "[9] 原子切换前端 dist..."
rm -rf "$DIST_DIR.old"
[ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
mv "$DIST_DIR.new" "$DIST_DIR"
rm -rf "$DIST_DIR.old"
# dist 由 docker root 创建，脚本以 ubuntu 运行无法直接 chmod，须 sudo
sudo chmod 777 "$DIST_DIR" # dist 目录需要 777 权限，否则无法ftp上传微信验证文件

# 10. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像，
#     新依赖不会自动安装；下次重建镜像后可移除此步骤）
echo "[10] 增量安装新增依赖..."
docker exec -u root aid-agent-api \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

echo ""
echo "=========================================="
echo "  更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
