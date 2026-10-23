#!/bin/bash

# 禁止以 root/sudo 运行（2026-10-19 事故防护）：
# sudo 会保留 HOME=/home/ubuntu，root 身份的 git/写文件操作还会把仓库 .git 内部
# 文件改成 root 属主，导致 ubuntu 后续免密 fetch 失败、git 反复索要密码。
# 本脚本设计为以 ubuntu 用户直接运行（需要提权处已自带 sudo，ubuntu 已配 NOPASSWD）。
if [ "$(id -u)" -eq 0 ]; then
    echo "错误：请勿用 sudo/root 运行本脚本（会污染 git 凭据与仓库属主）。" >&2
    echo "      请直接以 ubuntu 用户执行，不要加 sudo：bash $0" >&2
    exit 1
fi

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
#   6. 原子切换后把 dist 属主恢复为 ubuntu（node 容器以 root 编译，产物属主为 root）
#   7. 新增 git 凭据自愈：.git-credentials 两次被 root 身份的 git/写文件操作
#      覆盖属主为 root（600），ubuntu 读不到导致 git fetch 交互索要密码、
#      脚本 set -e 直接失败（2026-10-14 事故）。fetch 前检测属主/可读性异常
#      即从 /root/.git-credentials.bak（root:root 600 备份件，防连带被改）
#      恢复内容并 chown 回 ubuntu；凭据本体不入 git 仓库
#      （2026-10-19 加固：内容被清空为 0 字节时属主仍可能是 ubuntu，仅查属主会
#       漏判，改为同时检查 codeup 凭据是否存在，缺失即从备份件恢复）
#   8. runner overlay 自动叠加：服务器根级存在 docker-compose.agent-runner.yml
#      副本时自动叠加到 compose 命令，避免 --remove-orphans 把 aid-runner-api/
#      aid-runner-worker 当孤儿容器删除（2026-10-15 生产事故：发布后渠道消息
#      全部 RUNNER_SERVICE_UNAVAILABLE）；并重建 runner 服务使新代码生效。
#      删除根级副本即回到仅 api 形态（与 agent1 sim.sh 同模式）
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

# 0. 自愈 git 凭据（见变更记录 7）
CREDS_FILE="/home/ubuntu/.git-credentials"
CREDS_BAK="/root/.git-credentials.bak"
CREDS_MARKER="codeup.aliyun.com"
if ! sudo test -f "$CREDS_BAK"; then
    sudo install -m 600 -o root -g root /dev/null "$CREDS_BAK" 2>/dev/null || true
fi
# 触发条件：属主/可读性异常，或内容已丢失 codeup 凭据。文件被清空为 0 字节时
# 属主仍可能是 ubuntu，仅查属主会漏判（2026-10-19 事故加固）
if [ ! -r "$CREDS_FILE" ] || [ "$(stat -c %U "$CREDS_FILE" 2>/dev/null)" != "ubuntu" ] \
   || ! grep -q "$CREDS_MARKER" "$CREDS_FILE" 2>/dev/null; then
    echo "[0] 检测到 git 凭据文件异常（$(stat -c '%U:%G %a' "$CREDS_FILE" 2>/dev/null || echo '不可读')），自愈修复..."
    if sudo test -s "$CREDS_BAK"; then
        sudo cp "$CREDS_BAK" "$CREDS_FILE"
    fi
    sudo chown ubuntu:ubuntu "$CREDS_FILE"
    sudo chmod 600 "$CREDS_FILE"
    echo "    已恢复属主 ubuntu:ubuntu（600）"
fi
# 备份件为空但当前凭据有效时回填，保证下次损坏可自愈（首次部署 / 备份丢失场景）
if ! sudo test -s "$CREDS_BAK" && grep -q "$CREDS_MARKER" "$CREDS_FILE" 2>/dev/null; then
    sudo cp "$CREDS_FILE" "$CREDS_BAK"
    sudo chown root:root "$CREDS_BAK"
    sudo chmod 600 "$CREDS_BAK"
fi
if ! grep -q "$CREDS_MARKER" "$CREDS_FILE" 2>/dev/null; then
    echo "错误：$CREDS_FILE 缺少 codeup.aliyun.com 凭据且备份件为空，无法自愈。"
    echo "  请手工修复凭据文件后，执行：sudo install -m 600 -o root -g root $CREDS_FILE $CREDS_BAK"
    exit 1
fi

# 0.5 .env 快照（防覆盖：2026-10-22 诺度 .env 被覆盖丢失客户 key 事故。
#    快照存仓库外目录，git 操作不波及；保留最近 10 份；.env 缺失直接中止，
#    防止把更新发到未初始化环境）
ENV_SNAPSHOT_DIR="/var/www/env_snapshots"
if [ ! -f "/var/www/agent/.env" ]; then
    echo "错误：/var/www/agent/.env 不存在，中止更新（环境未初始化？）。"
    exit 1
fi
mkdir -p "$ENV_SNAPSHOT_DIR"
cp "/var/www/agent/.env" "$ENV_SNAPSHOT_DIR/.env_$(date +%Y%m%d_%H%M%S)"
ls -1t "$ENV_SNAPSHOT_DIR"/.env_* 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "[0.5] .env 已快照到 $ENV_SNAPSHOT_DIR（保留最近 10 份）"

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
# dist.new/dist.old 若为上次 root 容器残留，ubuntu 身份 rm 会 Permission denied，须 sudo
sudo rm -rf "$DIST_DIR.new"
mkdir -p "$(dirname "$BUILD_LOG")"
(
  docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      npx vite build --outDir dist.new \
    && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
        node scripts/verify-web-build.mjs /app/dist.new
) > "$BUILD_LOG" 2>&1 &
FRONTEND_PID=$!

# 4.5 发布前自检：容器间数据库连通性（2026-10-23 诺度同型事故）。
#    服务器重启后 dockerd 偶发未能编程 iptables（FORWARD 空、DOCKER nat 链缺失），
#    容器访问 postgres:5432 全超时——此时重建 aid-agent-api 必然起不来，全站 404。
#    用现有 api 容器实测 DATABASE_URL 连通性，异常即中止并给出修复指引。
echo "[4.5] 发布前自检：容器间数据库连通性..."
if docker ps --format '{{.Names}}' | grep -qx 'aid-agent-api'; then
    if ! docker exec aid-agent-api python -c "
import os, sys, psycopg2
try:
    psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5).close()
except Exception as e:
    print(f'PG 连接失败: {e}', file=sys.stderr)
    sys.exit(1)
"; then
        echo "错误：容器内无法连接 PostgreSQL（典型原因：服务器重启后 docker iptables 规则丢失）。" >&2
        echo "  修复：sudo systemctl restart docker（重建 iptables 并自动拉起容器），完成后重跑本脚本。" >&2
        kill "$FRONTEND_PID" 2>/dev/null || true
        exit 1
    fi
    echo "    PG 连通性正常"
else
    echo "  警告：aid-agent-api 容器未运行，跳过连通性自检（首次部署或服务未启动？）"
fi

# 5. 后端重启（与前端编译并行）
#    不先 down：up --force-recreate 会自动 stop→remove→create，省去 down 后到 up 之间
#    所有容器同时停止的全停窗口；api 与 background 由 compose 串行错开重建（api 先起时
#    background 仍运行），后台任务/心跳中断更少、DB 连接释放重建更平滑
#    显式列出 api/background 两个服务：--force-recreate 不带服务名会连 redis 一起
#    重建，导致每次发版 redis 都有 ~10s 不可用窗口（2026-09-18 实证会触发 api worker
#    首连 DNS 失败降级进内存、跨 worker 状态漂移）。redis 配置/镜像变更时需手动
#    单独执行：docker compose -f docker-compose.prod.yml up -d --force-recreate --wait aid-redis
echo "[5] 重启后端服务..."
# runner overlay 副本存在则自动叠加：--remove-orphans 只认本次命令文件集内的服务，
# 不叠加会把 aid-runner-api / aid-runner-worker 当孤儿容器删除（见变更记录 8）
COMPOSE_ARGS=(-f docker-compose.prod.yml)
if [ -f docker-compose.agent-runner.yml ]; then
    COMPOSE_ARGS+=(-f docker-compose.agent-runner.yml)
    echo "    检测到 runner overlay，已叠加 docker-compose.agent-runner.yml"
fi
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate --remove-orphans --wait \
    aid-agent-api aid-agent-background

# runner 服务重建使新代码生效；服务名以 overlay 实际定义为准，仅重建存在的服务
if [ -f docker-compose.agent-runner.yml ]; then
    RUNNER_SERVICES=$(docker compose "${COMPOSE_ARGS[@]}" config --services 2>/dev/null \
        | grep -E '^runner' || true)
    if [ -n "$RUNNER_SERVICES" ]; then
        # shellcheck disable=SC2086
        docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate $RUNNER_SERVICES
        echo "    runner 服务已重建: $(echo $RUNNER_SERVICES | tr '\n' ' ')"
    else
        echo "  警告：overlay 中未找到 runner-* 服务定义，未重建 runner"
    fi
    # 从主 API 容器内验证 runner-api 可达（DNS + /health），失败仅告警不阻断
    if docker exec aid-agent-api python -c \
        "import urllib.request; urllib.request.urlopen('http://runner-api:8091/health', timeout=5)" \
        2>/dev/null; then
        echo "    runner-api 健康检查通过"
    else
        echo "  警告：runner-api /health 不可达（渠道对话将报 RUNNER_SERVICE_UNAVAILABLE），请检查 runner 容器"
    fi
fi

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
sudo rm -rf "$DIST_DIR.old"
[ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
mv "$DIST_DIR.new" "$DIST_DIR"
# 保留企微可信域名校验文件：企微不定期复核，文件丢失会导致可信域名回退为待审核
ls "$DIST_DIR.old"/WW_verify_*.txt >/dev/null 2>&1 && \
    sudo cp "$DIST_DIR.old"/WW_verify_*.txt "$DIST_DIR"/
sudo rm -rf "$DIST_DIR.old"
# dist 由 docker root 容器创建，属主为 root；恢复为 ubuntu，避免残留 root 属主文件
# 在后续 git 更新/排查时造成 Permission denied 干扰（2026-09-20 事故）
sudo chown -R ubuntu:ubuntu "$DIST_DIR"
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
