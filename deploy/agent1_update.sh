#!/bin/bash

# ==============================================================================
# 仿真环境（staging, agent1）- 验证模式更新脚本（git 拉取 + 前端构建）
# 用途: 把 /var/www/agent1 重置到指定 git 版本并构建前端，用于验证修复后的代码
#       能正确工作（修复在仿真环境验收通过后再上生产）
# 用法: ./agent1_update.sh [git版本号]
#       不带参数 -> 更新到 origin/master 最新
#       带参数   -> 停留在指定提交/分支（如 ./agent1_update.sh fix-xxx 或 c43c65e）
# ==============================================================================
# 与生产 agent_update.sh 的差异：
#   - 目录 /var/www/agent1，容器 aid-agent-api1（docker-compose.sim.yml 仅 api 服务，
#     无 background 容器），健康检查走 localhost:8010
#   - 无 docker update 步骤（cpus/mem_limit 在 docker-compose.sim.yml 顶层声明，
#     docker compose v2 直接生效，非 deploy.resources 段）
#   - 无 /tmp 权限修复步骤（compose tmpfs 已带 mode=1777）
#   - npm 缓存命名卷独立为 agent1_npm_cache
#   - 末尾把 sim-base tag 前移到本次 HEAD：agent1_update.sh 与
#     sim.sh（复现模式，rsync 生产代码 + 重放仿真增量）互为整体替换，
#     重置后工作区即纯 master 代码、仿真增量为空，前移基准可避免下次数据同步误判
# ⚠️ 本脚本 git reset --hard 会丢弃工作区本地修改（含复现模式同步来的生产代码
#    与同步提交）；复现生产 bug 请改跑 sim.sh（默认附带代码同步）
# ==============================================================================

set -e

START_TS=$(date +%s)

echo "=========================================="
echo "  仿真环境（agent1）- 验证模式更新"
echo "=========================================="

SIM_DIR="/var/www/agent1"
FRONTEND_DIR="$SIM_DIR/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="$SIM_DIR/log/frontend-build.log"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory "$SIM_DIR" 2>/dev/null || true

# 1. 拉取代码（可选参数：目标 git 提交/分支，短哈希/长哈希/tag/分支名均可；
#    不传则更新到 origin/master 最新。指定修复分支/提交用于上线前验证）
echo "[1] 拉取代码..."
cd "$SIM_DIR"
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

# 1.1 前移 sim-base 基准（本脚本工作区 = 纯 git 代码，仿真增量视为空）
git tag -f sim-base HEAD >/dev/null

# 1.2 清除 Python 字节码缓存（避免旧代码运行；rsync 同步可能留下 root 属主残留，用 sudo）
echo "[1.2] 清除 Python .pyc 缓存..."
sudo find "$SIM_DIR" -name .git -prune -o -type f -name '*.pyc' -delete 2>/dev/null || true
sudo find "$SIM_DIR" -name .git -prune -o -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# 2. 前端依赖安装（node_modules 持久化在宿主机，增量安装；npm 缓存用命名卷持久化）
echo "[2] 安装前端依赖..."
docker run --rm \
    -v "$FRONTEND_DIR":/app \
    -v agent1_npm_cache:/root/.npm \
    -w /app node:22-alpine \
    npm install --no-audit --no-fund --prefer-offline

# 3. 前端类型检查 + 依赖边界检查（先于后端重启执行，错误在此中止，避免白白停一次服务）
echo "[3] 前端类型检查 + 边界检查..."
docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck
docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
    node scripts/check-dependency-boundaries.mjs --scope=web

# 4. 前端编译到 dist.new（后台执行，与后端重启并行，编译期间前端零空窗）
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

# 5. 重启仿真后端（up 会拉起未运行的容器；--force-recreate 保证 .env 最新）
echo "[5] 重启仿真后端 aid-agent-api1..."
docker compose -f "$SIM_DIR/docker-compose.sim.yml" up -d --force-recreate --wait

# 6. 等待前端编译完成
echo "[6] 等待前端编译完成..."
if ! wait "$FRONTEND_PID"; then
  echo "  错误：前端编译失败，详见 $BUILD_LOG"
  echo "  后端已更新但前端仍为旧版本，请检查后重跑本脚本"
  exit 1
fi

# 7. 原子切换 dist（同一文件系统内两步 mv；nginx 走 /index.html 兜底）
echo "[7] 原子切换前端 dist..."
rm -rf "$DIST_DIR.old"
[ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
mv "$DIST_DIR.new" "$DIST_DIR"
rm -rf "$DIST_DIR.old"
# dist 由 docker root 创建，脚本以 ubuntu 运行无法直接 chmod，须 sudo
sudo chmod 777 "$DIST_DIR"

# 8. 增量安装 requirements.txt 中新增的依赖（不重建镜像，新依赖需补装）
echo "[8] 增量安装新增依赖..."
docker exec -u root aid-agent-api1 \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

# 9. 健康检查（确认启动横幅「⚠️ 仿真环境（SIMULATION_MODE=1）」门控生效）
echo "[9] 健康检查..."
HEALTH_OK=0
for i in $(seq 1 24); do
    if curl -sf http://localhost:8010/health >/dev/null; then HEALTH_OK=1; break; fi
    sleep 5
done
if [ $HEALTH_OK -eq 1 ]; then
    echo "  健康检查通过 (localhost:8010/health)"
    docker logs aid-agent-api1 2>&1 | grep -m1 "仿真环境" || echo "  提示：未在日志中找到仿真环境横幅，请确认 SIMULATION_MODE 门控生效"
else
    echo "  错误：健康检查超时（120s），请查看 docker logs aid-agent-api1"
    exit 1
fi

echo ""
echo "=========================================="
echo "  仿真环境更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
