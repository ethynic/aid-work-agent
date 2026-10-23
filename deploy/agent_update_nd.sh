#!/bin/bash

# ==============================================================================
# AI 数字员工系统 - 快速更新脚本【济南诺度私有云适配版】
# 服务器：agent.jzzysd.com（139.129.48.30），用户 ady，仓库根 /var/www/agent/app
# 与 243 生产版（deploy/agent_update.sh）的差异：
#   1. 目录多一层 /app：仓库根 /var/www/agent/app，前端/日志均在 app 下
#   2. 运行用户 ubuntu -> ady，git 凭据 /home/ady/.git-credentials
#   3. ady 需 sudo 执行 docker（已配 NOPASSWD；ady 已加入 docker 组则 sudo 也兼容）
#   4. compose 为 docker-compose.main.yml + deploy/nuodu/docker-compose.agent-runner.yml
#      overlay（runner 内存 6g->3g 混部压缩版）；不能探测仓库根级
#      docker-compose.agent-runner.yml —— 那是 243 版 overlay，内存配置不同
#   5. aid-postgres 在独立 compose 项目（/var/www/agent/postgres），本脚本不触碰
#   6. npm 缓存卷名 agent_npm_cache（243 为 agent2_npm_cache）
# 其余流程（dist.new 原子切换、前后端并行、凭据自愈、/tmp 修复、增量 pip）
# 与 243 版保持一致，变更记录见 deploy/agent_update.sh。
# ==============================================================================

# 禁止以 root/sudo 运行：root 身份的 git/写文件操作会把仓库 .git 内部文件改成
# root 属主，导致 ady 后续免密 fetch 失败。脚本以 ady 用户直接运行。
if [ "$(id -u)" -eq 0 ]; then
    echo "错误：请勿用 sudo/root 运行本脚本（会污染 git 凭据与仓库属主）。" >&2
    echo "      请直接以 ady 用户执行，不要加 sudo：bash $0" >&2
    exit 1
fi

set -e

START_TS=$(date +%s)

echo "=========================================="
echo "  AI 数字员工系统 - 快速更新（济南诺度）"
echo "=========================================="

REPO_DIR="/var/www/agent/app"
FRONTEND_DIR="$REPO_DIR/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="$REPO_DIR/log/frontend-build.log"
RUN_USER="ady"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

# 0. 自愈 git 凭据（机制同 243 版：/root 下 root:root 600 备份件，防连带被改）
CREDS_FILE="/home/ady/.git-credentials"
CREDS_BAK="/root/.git-credentials.bak"
CREDS_MARKER="codeup.aliyun.com"
if ! sudo test -f "$CREDS_BAK"; then
    sudo install -m 600 -o root -g root /dev/null "$CREDS_BAK" 2>/dev/null || true
fi
# 触发条件：属主/可读性异常，或内容已丢失 codeup 凭据（文件被清空时属主可能仍正常）
if [ ! -r "$CREDS_FILE" ] || [ "$(stat -c %U "$CREDS_FILE" 2>/dev/null)" != "$RUN_USER" ] \
   || ! grep -q "$CREDS_MARKER" "$CREDS_FILE" 2>/dev/null; then
    echo "[0] 检测到 git 凭据文件异常（$(stat -c '%U:%G %a' "$CREDS_FILE" 2>/dev/null || echo '不可读')），自愈修复..."
    if sudo test -s "$CREDS_BAK"; then
        sudo cp "$CREDS_BAK" "$CREDS_FILE"
    fi
    sudo chown "$RUN_USER:$RUN_USER" "$CREDS_FILE"
    sudo chmod 600 "$CREDS_FILE"
    echo "    已恢复属主 $RUN_USER:$RUN_USER（600）"
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
ENV_SNAPSHOT_DIR="$(dirname "$REPO_DIR")/env_snapshots"
if [ ! -f "$REPO_DIR/.env" ]; then
    echo "错误：$REPO_DIR/.env 不存在，中止更新（环境未初始化？）。"
    exit 1
fi
mkdir -p "$ENV_SNAPSHOT_DIR"
cp "$REPO_DIR/.env" "$ENV_SNAPSHOT_DIR/.env_$(date +%Y%m%d_%H%M%S)"
ls -1t "$ENV_SNAPSHOT_DIR"/.env_* 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "[0.5] .env 已快照到 $ENV_SNAPSHOT_DIR（保留最近 10 份）"

# 1. 拉取代码（可选参数：目标 git 提交版本号，短哈希/长哈希/tag 均可；
#    不传则更新到 origin/master 最新）
echo "[1] 拉取代码..."
cd "$REPO_DIR"
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

# 2. 前端依赖安装（node_modules 持久化在宿主机，增量安装）
#    npm 缓存用命名卷持久化，--no-audit 跳过国内极慢的 audit 端点
echo "[2] 安装前端依赖..."
sudo docker run --rm \
    -v "$FRONTEND_DIR":/app \
    -v agent_npm_cache:/root/.npm \
    -w /app node:22-alpine \
    npm install --no-audit --no-fund --prefer-offline

# 3. 前端类型检查 + 依赖边界检查（不产出 dist，nginx 服务不受影响；
#    先于后端重启执行，类型/边界错误能在此中止，避免白白停一次服务）
echo "[3] 前端类型检查 + 边界检查..."
sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck
sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
    node scripts/check-dependency-boundaries.mjs --scope=web

# 4. 前端编译到 dist.new（后台执行，与后端重启并行）。
#    旧 dist 一直保留到原子切换，编译期间前端零空窗。
echo "[4] 前端编译 dist.new（后台）..."
sudo rm -rf "$DIST_DIR.new"
mkdir -p "$(dirname "$BUILD_LOG")"
(
  sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      npx vite build --outDir dist.new \
    && sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
        node scripts/verify-web-build.mjs /app/dist.new
) > "$BUILD_LOG" 2>&1 &
FRONTEND_PID=$!

# 4.5 发布前自检：容器间数据库连通性（2026-10-23 事故）。
#    服务器重启后 dockerd 偶发未能编程 iptables（FORWARD 空、DOCKER nat 链缺失），
#    容器访问 postgres:5432 全超时——此时重建 aid-agent-api 必然起不来，全站 404。
#    用现有 api 容器实测 DATABASE_URL（服务名直连）连通性，异常即中止并给出修复指引。
echo "[4.5] 发布前自检：容器间数据库连通性..."
if sudo docker ps --format '{{.Names}}' | grep -qx 'aid-agent-api'; then
    if ! sudo docker exec aid-agent-api python -c "
import os, sys, psycopg2
try:
    psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5).close()
except Exception as e:
    print(f'PG 连接失败: {e}', file=sys.stderr)
    sys.exit(1)
"; then
        echo "错误：容器内无法连接 PostgreSQL（典型原因：服务器重启后 docker iptables 规则丢失）。" >&2
        echo "  修复：sudo systemctl restart docker（重建 iptables 并自动拉起容器），完成后重跑本脚本。" >&2
        sudo kill "$FRONTEND_PID" 2>/dev/null || true
        exit 1
    fi
    echo "    PG 连通性正常"
else
    echo "  警告：aid-agent-api 容器未运行，跳过连通性自检（首次部署或服务未启动？）"
fi

# 5. 后端重启（与前端编译并行）
#    显式列出 api/background 两个服务，避免连 redis 一起重建造成 ~10s 不可用窗口；
#    runner overlay 固定取 deploy/nuodu/ 版本（仓库根级同名文件是 243 版，勿用），
#    不叠加会被 --remove-orphans 把 aid-runner-api/aid-runner-worker 删掉
echo "[5] 重启后端服务..."
RUNNER_OVERLAY="deploy/nuodu/docker-compose.agent-runner.yml"
COMPOSE_ARGS=(-f docker-compose.main.yml)
if [ -f "$RUNNER_OVERLAY" ]; then
    COMPOSE_ARGS+=(-f "$RUNNER_OVERLAY")
    echo "    已叠加 $RUNNER_OVERLAY"
else
    echo "  警告：未找到 $RUNNER_OVERLAY，将仅按 docker-compose.main.yml 重建（runner 会被 --remove-orphans 删除！）"
fi
sudo docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate --remove-orphans --wait \
    aid-agent-api aid-agent-background

# runner 服务重建使新代码生效；服务名 runner-api / runner-worker
if [ -f "$RUNNER_OVERLAY" ]; then
    RUNNER_SERVICES=$(sudo docker compose "${COMPOSE_ARGS[@]}" config --services 2>/dev/null \
        | grep -E '^runner' || true)
    if [ -n "$RUNNER_SERVICES" ]; then
        # shellcheck disable=SC2086
        sudo docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate $RUNNER_SERVICES
        echo "    runner 服务已重建: $(echo $RUNNER_SERVICES | tr '\n' ' ')"
    else
        echo "  警告：overlay 中未找到 runner-* 服务定义，未重建 runner"
    fi
    # 从主 API 容器内验证 runner-api 可达（DNS + /health），失败仅告警不阻断
    if sudo docker exec aid-agent-api python -c \
        "import urllib.request; urllib.request.urlopen('http://runner-api:8091/health', timeout=5)" \
        2>/dev/null; then
        echo "    runner-api 健康检查通过"
    else
        echo "  警告：runner-api /health 不可达（渠道对话将报 RUNNER_SERVICE_UNAVAILABLE），请检查 runner 容器"
    fi
fi

# 6. 施加 CPU 限制（docker compose 非 swarm 会忽略 deploy.resources；
#    内存上限已在 docker-compose.main.yml 用 mem_limit 声明（混部压缩版），
#    此处不再用 docker update 覆盖内存）
echo "[6] 施加容器 CPU 限制..."
sudo docker update --cpus 2 aid-agent-api
sudo docker update --cpus 1 aid-agent-background

# 7. 修复容器内 /tmp 权限（运行时兜底）
echo "[7] 修复容器 /tmp 权限..."
if ! sudo docker exec -u root aid-agent-api chmod 1777 /tmp 2>/dev/null; then
  echo "  chmod 失败，尝试 mount remount..."
  sudo docker exec -u root aid-agent-api mount -o remount,mode=1777 /tmp 2>/dev/null || \
      echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
sudo docker exec -u root aid-agent-api ls -ld /tmp || true

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
[ -d "$DIST_DIR" ] && sudo mv "$DIST_DIR" "$DIST_DIR.old"
sudo mv "$DIST_DIR.new" "$DIST_DIR"
# 保留企微可信域名校验文件：企微不定期复核，文件丢失会导致可信域名回退为待审核
sudo ls "$DIST_DIR.old"/WW_verify_*.txt >/dev/null 2>&1 && \
    sudo cp "$DIST_DIR.old"/WW_verify_*.txt "$DIST_DIR"/
sudo rm -rf "$DIST_DIR.old"
# dist 由 root 容器创建，属主为 root；恢复为 ady，避免 root 属主文件干扰后续 git 更新
sudo chown -R "$RUN_USER:$RUN_USER" "$DIST_DIR"
sudo chmod 777 "$DIST_DIR" # dist 目录需要 777 权限，否则无法ftp上传微信验证文件

# 10. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像）
echo "[10] 增量安装新增依赖..."
sudo docker exec -u root aid-agent-api \
    pip install --no-cache-dir -r /app/requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --quiet || echo "  警告：依赖安装失败，部分新功能可能不可用"

echo ""
echo "=========================================="
echo "  更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
