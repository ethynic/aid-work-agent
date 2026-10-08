#!/bin/bash

# ==============================================================================
# AI 数字员工系统（测试环境 agent2）- 快速更新脚本（不重建 Docker 镜像）
# 用途: 仅更新代码，快速重启服务
# ==============================================================================
# 变更记录：
#   1. 前端编译输出到 dist.new，编译期间 nginx 继续服务旧 dist，build 完成后
#      原子切换（两步 mv），消除原「rm -rf dist/* → npm run build」造成的 ~30s 前端空窗
#   2. 前端编译（后台）与后端重启（up）并行，总停机时间 ≈ 后端重启耗时
#   3. 用 up --force-recreate --remove-orphans 替代 down + up，省去全停窗口
#   4. up 后用 docker update 施加 cgroup 资源限制（compose 非 swarm 会忽略
#      deploy.resources，资源值在脚本内维护，为唯一来源）
#   5. npm install 挂命名卷缓存
#      并加 --no-audit --prefer-offline，消除全新容器重拉包元数据导致的数分钟卡顿
#   6. 原子切换后把 dist 属主恢复为 ubuntu（node 容器以 root 编译，产物属主为 root）
#   7. 新增 Node PPT 渲染器构建步骤：renderer-node 的 node_modules/dist 是
#      gitignore 覆盖的 untracked 构建产物（git reset --hard 不会删除），用
#      node:22-alpine 侧车容器 npm ci + tsc 构建，产物留在宿主机挂载目录，
#      容器内 node 直接执行；dist 比 package-lock 新且依赖齐全时跳过构建
# ==============================================================================

set -e

START_TS=$(date +%s)

echo "=========================================="
echo "  AI 数字员工系统（测试） - 快速更新"
echo "=========================================="

FRONTEND_DIR="/var/www/agent2/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="/var/www/agent2/log/frontend-build.log"
PPT_RENDERER_DIR="/var/www/agent2/src/tools/ppt/renderer-node"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory /var/www/agent2 2>/dev/null || true

# 1. 拉取代码
echo "[1] 拉取最新代码..."
cd "/var/www/agent2"
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


# 3. 前端类型检查 + 边界检查（不产出 dist，nginx 服务不受影响；
#    先于后端重启执行，类型/边界错误能在此中止，避免白白停一次服务）
echo "[3] 前端类型检查 + 边界检查..."
docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck
docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
    node scripts/check-dependency-boundaries.mjs --scope=web

# 4. 前端编译到 dist.new（后台执行，与后端重启并行）。
#    旧 dist 一直保留到原子切换，编译期间前端零空窗。
#    校验链拆开跑：typecheck/boundary 已在 [3]，build 用 npx vite build --outDir
echo "[4] 前端编译 dist.new（后台）..."
sudo rm -rf "$DIST_DIR.new"
mkdir -p "$(dirname "$BUILD_LOG")"
(
  docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      npx vite build --outDir dist.new \
    && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
        node scripts/verify-web-build.mjs /app/dist.new
) > "$BUILD_LOG" 2>&1 &
FRONTEND_PID=$!

# 5. 构建 Node PPT 渲染器（PptxGenJS）。容器挂载整个项目目录，镜像内产物会被
#    挂载遮住，node_modules/dist 必须落在宿主机检出目录（untracked，git reset
#    不会删除）。dist/render.js 比 package-lock.json 和全部 .ts 源码新且
#    pptxgenjs 已安装时跳过构建（只改 .ts 不动依赖也要重建，否则沿用旧 dist）；
#    npm 缓存复用前端的命名卷，避免重复拉包元数据
echo "[5] 构建 Node PPT 渲染器..."
if [ -f "$PPT_RENDERER_DIR/dist/render.js" ] && \
   [ "$PPT_RENDERER_DIR/dist/render.js" -nt "$PPT_RENDERER_DIR/package-lock.json" ] && \
   [ -d "$PPT_RENDERER_DIR/node_modules/pptxgenjs" ] && \
   ! find "$PPT_RENDERER_DIR/src" -name '*.ts' -newer "$PPT_RENDERER_DIR/dist/render.js" | grep -q .; then
  echo "  dist/render.js 已是最新，跳过构建"
else
  docker run --rm \
      -v "$PPT_RENDERER_DIR":/app \
      -v agent2_npm_cache:/root/.npm \
      -w /app node:22-alpine \
      sh -c "npm ci --no-audit --no-fund --prefer-offline && npm run build"
  # 构建后校验产物存在：docker run 成功但产物缺失时明确报错，避免带病重启后端
  if [ ! -f "$PPT_RENDERER_DIR/dist/render.js" ]; then
    echo "  错误：Node PPT 渲染器构建失败，dist/render.js 不存在"
    exit 1
  fi
  # 侧车以 root 构建，产物属主为 root；恢复为 ubuntu，避免 root 属主文件
  # 在后续 git 更新/排查时造成 Permission denied 干扰（同第 [10] 步 dist 处理）
  sudo chown -R ubuntu:ubuntu "$PPT_RENDERER_DIR/dist" "$PPT_RENDERER_DIR/node_modules"
fi

# 6. 后端重启（与前端编译并行）。
#    不先 down：up --force-recreate 会自动 stop→remove→create，api 与 background
#    由 compose 串行错开重建，避免所有容器同时停止的全停窗口
#    AgentRunner overlay：分支含 docker-compose.agent-runner.yml 时叠加一并重建
#    （runner-api/runner-worker/kf-ingress/kf-admission）。必须与主 compose 一起传给
#    同一条 up，否则 --remove-orphans 会把 overlay 服务当孤儿容器删除；KF 服务随
#    --profile wecom-kf 纳入。overlay 缺失（如早期分支）自动退回纯主 compose。
#    agent2 特有：与 agent3 共库 aid_work_agent2 且同宿主机（共享 uploads/storage
#    目录，overlay 容器名固定无环境后缀），同一时刻只允许一个环境常驻 runner/KF
#    容器——叠加前做互斥预检，发现四容器被其他 compose project（agent3）占用即
#    报错退出；三个宿主变量为 test 环境专属值（overlay 默认值对齐生产，不注入会
#    接错网络/目录），在此 export 为唯一来源，不依赖服务器 .env 是否配置。
echo "[6] 重启后端服务..."
COMPOSE_FILES=(-f docker-compose.test.yml)
COMPOSE_PROFILES=()
if [ -f docker-compose.agent-runner.yml ]; then
    COMPOSE_FILES+=(-f docker-compose.agent-runner.yml)
    COMPOSE_PROFILES=(--profile wecom-kf)
    export AGENT_RUNNER_NETWORK=aid-network2
    export AGENT_RUNNER_UPLOADS_HOST_DIR=/var/www/qb3_upload/agent2_uploads
    export AGENT_RUNNER_STORAGE_HOST_DIR=/var/www/qb3_upload/agent2_storage
    echo "    叠加 AgentRunner overlay（含 wecom-kf profile）"
    # 互斥预检：用本环境主 API 容器的 compose project 标签锚定当前 project 名
    # （首次部署容器不存在时退回目录名，与 compose 默认 project 命名一致）
    CURRENT_PROJECT=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' aid-agent-api2 2>/dev/null || true)
    [ -z "$CURRENT_PROJECT" ] && CURRENT_PROJECT="${PWD##*/}"
    for c in aid-runner-api aid-runner-worker aid-kf-ingress aid-kf-admission; do
        OWNER=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$c" 2>/dev/null || true)
        if [ -n "$OWNER" ] && [ "$OWNER" != "$CURRENT_PROJECT" ]; then
            echo "错误：容器 $c 已被 compose project '$OWNER'（另一环境）占用。"
            echo "  agent2/agent3 共库 aid_work_agent2，同一时刻只允许一个环境常驻"
            echo "  runner/KF 容器（lease 池跨环境抢任务 + 同名容器冲突）。请先在该"
            echo "  环境移除四容器后再发布："
            echo "    docker rm -f aid-runner-api aid-runner-worker aid-kf-ingress aid-kf-admission"
            exit 1
        fi
    done
else
    echo "    未发现 AgentRunner overlay，仅主 compose"
fi
docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILES[@]}" up -d --force-recreate --remove-orphans --wait

# 7. 施加资源限制（docker compose 非 swarm 会忽略 deploy.resources，改用 docker update
#    显式施加 cgroup 限制；资源值在本脚本内维护，为唯一来源）
#    runner-api/kf 两类为轻量 IO 进程给 0.5C/512M；runner-worker 执行负载给 1C/1G。
#    容器不存在时（未启用 overlay 的分支）docker update 报错忽略。
echo "[7] 施加容器资源限制..."
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-agent-api2
docker update --cpus 0.5 --memory 512M --memory-reservation 256M aid-agent-background2
for c in aid-runner-api aid-kf-ingress aid-kf-admission; do
    docker update --cpus 0.5 --memory 512M --memory-reservation 256M "$c" 2>/dev/null \
        || echo "  跳过 $c（不存在）"
done
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-runner-worker 2>/dev/null \
    || echo "  跳过 aid-runner-worker（不存在）"

# 8. 修复容器内 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs 且默认 755，
#    Dockerfile 的 chmod 不生效，entrypoint 已处理；此处作为运行时兜底）
echo "[8] 修复容器 /tmp 权限..."
if ! docker exec -u root aid-agent-api2 chmod 1777 /tmp 2>/dev/null; then
  echo "  chmod 失败，尝试 mount remount..."
  docker exec -u root aid-agent-api2 mount -o remount,mode=1777 /tmp 2>/dev/null || \
      echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
docker exec -u root aid-agent-api2 ls -ld /tmp || true

# 9. 等待前端编译完成
echo "[9] 等待前端编译完成..."
if ! wait "$FRONTEND_PID"; then
  echo "  错误：前端编译失败，详见 $BUILD_LOG"
  echo "  后端已更新但前端仍为旧版本，请检查后重跑本脚本"
  exit 1
fi

# 10. 原子切换 dist（同一文件系统内两步 mv，切换瞬间旧→新；nginx 走 /index.html 兜底）
echo "[10] 原子切换前端 dist..."
sudo rm -rf "$DIST_DIR.old"
[ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
mv "$DIST_DIR.new" "$DIST_DIR"
sudo rm -rf "$DIST_DIR.old"
# dist 由 docker root 容器创建，属主为 root；恢复为 ubuntu，避免残留 root 属主文件
# 在后续 git 更新/排查时造成 Permission denied 干扰（2026-09-20 事故）
sudo chown -R ubuntu:ubuntu "$DIST_DIR"
sudo chmod 777 "$DIST_DIR" # dist 目录需要 777 权限，否则无法ftp上传微信验证文件

# 11. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像，
#     新依赖不会自动安装；下次重建镜像后可移除此步骤）
echo "[11] 增量安装新增依赖..."
docker exec -u root aid-agent-api2 \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

echo ""
echo "更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
