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
#   7. 2026-11-26 新增分段计时（step_done，与 243 版变更记录 10 同款）
# 其余流程（dist.new 原子切换、前后端并行、凭据自愈、/tmp 修复、增量 pip）
# 与 243 版保持一致，变更记录见 deploy/agent_update.sh。
#
# 用法：bash agent_update_nd.sh [recreate|提交哈希]
#   recreate = 仅重建容器使修改后的 .env 生效（不拉代码、不编译前端；
#              只做 PG 连通自检 + 后端/runner 容器重建 + CPU 限制 + /tmp 权限
#              兜底 + 依赖补装）。docker restart 不会重读 env，必须 force-recreate
#              重建；重建会丢弃容器可写层里的 pip 安装，故依赖补装必须执行
#   提交哈希 = 更新到指定版本；不传 = 更新到 origin/master 最新
# ==============================================================================

# 禁止以 root/sudo 运行：root 身份的 git/写文件操作会把仓库 .git 内部文件改成
# root 属主，导致 ady 后续免密 fetch 失败。脚本以 ady 用户直接运行。
if [ "$(id -u)" -eq 0 ]; then
    echo "错误：请勿用 sudo/root 运行本脚本（会污染 git 凭据与仓库属主）。" >&2
    echo "      请直接以 ady 用户执行，不要加 sudo：bash $0" >&2
    exit 1
fi

main() {
set -e

START_TS=$(date +%s)

# 发布脚本自更新机制（2026-11-26）：检出目标版本后若脚本自身内容已变化，
# exec 重启自身执行新版——避免整轮发布跑在旧解析代码上、需要手动跑两遍。
# AID_DEPLOY_RERUN=1 守卫只允许重入一次，杜绝死循环；脚本未变化不重入。
# （main() 包裹保证检出发生时整份脚本已解析完毕，偏移错乱不会发生）
SCRIPT_PATH=$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")
SCRIPT_SELF_HASH=$(sha256sum "$SCRIPT_PATH" 2>/dev/null | cut -d' ' -f1 || true)
LAST_TS=$START_TS
TIMING_LIST=""
step_done() {
    local NOW LBL="${1:-}"
    NOW=$(date +%s)
    echo "  [计时] 本段 $(( NOW - LAST_TS ))s / 累计 $(( NOW - START_TS ))s"
    # 无标签调用（理论不存在，兜底）只计时不进汇总
    [ -n "$LBL" ] && TIMING_LIST="${TIMING_LIST:+$TIMING_LIST + }${LBL}$(( NOW - LAST_TS ))s"
    LAST_TS=$NOW
}

# compose 支持 --progress 时静默进度输出（TTY 进度帧重绘会灌出数千行发布日志）
if docker compose up --help 2>&1 | grep -q -- '--progress'; then
    COMPOSE_QUIET=(--progress quiet)
else
    COMPOSE_QUIET=()
fi

# compose 输出收口（2026-11-27，与 agent2_update.sh 同款）：成功时全量重定向到
# 文件，正常路径零输出；失败时回显过滤噪音后的错误尾部。兼容不支持 --progress
# 的旧引擎（探测失败也不再有进度帧刷屏）。诺度环境 docker 需 sudo
COMPOSE_LOG=/tmp/agent_nd_compose.log
compose_up() {
    local desc="$1"; shift
    if ! sudo docker compose "${COMPOSE_ARGS[@]}" "${COMPOSE_QUIET[@]}" "$@" >"$COMPOSE_LOG" 2>&1; then
        echo "错误：$desc 失败，关键输出如下："
        grep -vE 'swap limit|Running [0-9]+/' "$COMPOSE_LOG" | tail -40
        return 1
    fi
}

echo "=========================================="
echo "  AI 数字员工系统 - 快速更新（济南诺度）"
echo "=========================================="

# 模式参数：recreate = 仅重建容器使 .env 生效（不拉代码、不编译前端）
RECREATE_ONLY=0
if [ "${1:-}" = "recreate" ]; then
    RECREATE_ONLY=1
    echo "[模式] recreate：仅重建容器使 .env 生效（跳过拉代码/前端编译/依赖安装）"
fi

REPO_DIR="/var/www/agent/app"
FRONTEND_DIR="$REPO_DIR/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="$REPO_DIR/log/frontend-build.log"
RUN_USER="ady"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

# ── 以下凭据自愈/拉代码/前端编译段仅在标准更新模式执行（recreate 模式跳到第 4.5 步）──
if [ "$RECREATE_ONLY" -eq 0 ]; then

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
git fetch --all --quiet
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
# 自更新重入：脚本文件随本次检出变化 -> exec 新版（重入后再比较必然相同，
# 且 AID_DEPLOY_RERUN 守卫兜底，不会死循环）
NEW_SELF_HASH=$(sha256sum "$SCRIPT_PATH" 2>/dev/null | cut -d' ' -f1 || true)
if [ "${AID_DEPLOY_RERUN:-0}" != "1" ] && [ -n "$SCRIPT_SELF_HASH" ] \
   && [ "$NEW_SELF_HASH" != "$SCRIPT_SELF_HASH" ]; then
    echo "[1.2] 发布脚本已随本次更新变化，exec 重启以执行新版脚本（仅重入一次）..."
    AID_DEPLOY_RERUN=1 exec bash "$SCRIPT_PATH" "$@"
fi
find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 1.1 清除 Python 字节码缓存（避免旧代码运行）
echo "[1.1] 清除 Python .pyc 缓存..."
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
step_done "拉取+清缓存"

# 2. 前端依赖安装（node_modules 持久化在宿主机，增量安装）
#    npm 缓存用命名卷持久化，--no-audit 跳过国内极慢的 audit 端点
echo "[2] 安装前端依赖..."
sudo docker run --rm \
    -v "$FRONTEND_DIR":/app \
    -v agent_npm_cache:/root/.npm \
    -w /app node:22-alpine \
    npm install --no-audit --no-fund --prefer-offline --loglevel=error
step_done "npm"

# 3. 前端校验 + 编译到 dist.new（后台执行，与后端重启并行，2026-11-24 优化）。
#    typecheck/边界检查不产出 dist、不影响 nginx，只需在 dist 原子切换前通过；
#    移入后台校验链与后端重启并行（原串行卡在重启前约 2 分钟）。
#    校验链任一失败都不会生成 dist.new，等待段会在切换前中止，前端零空窗；
#    代价：类型/边界错误时后端已重启（多一次服务重启），属可接受取舍。
# 双通道并行（2026-11-26）：typecheck/边界检查是门禁（不产出 dist），
# vite build/产物校验产出 dist.new——两通道互不依赖，墙钟时间 = max(两通道)。
# 两通道都必须通过才允许切换 dist（门禁不放松）；代价：typecheck 失败时
# build 已跑完（白编译一次），属可接受取舍。
echo "[3] 前端校验 + 编译 dist.new（双通道后台，与后端重启并行）..."
sudo rm -rf "$DIST_DIR.new"
mkdir -p "$(dirname "$BUILD_LOG")"
(
  sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck \
  && sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      node scripts/check-dependency-boundaries.mjs --scope=web
) > "$BUILD_LOG.a" 2>&1 &
LANE_A_PID=$!
(
  sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      npx vite build --outDir dist.new \
  && sudo docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      node scripts/verify-web-build.mjs /app/dist.new
) > "$BUILD_LOG.b" 2>&1 &
LANE_B_PID=$!
step_done "前端后台"

fi  # 结束：标准更新模式专属段（凭据自愈/快照/拉代码/前端编译）

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
        sudo kill "$LANE_A_PID" "$LANE_B_PID" 2>/dev/null || true
        exit 1
    fi
    echo "    PG 连通性正常"
else
    echo "  警告：aid-agent-api 容器未运行，跳过连通性自检（首次部署或服务未启动？）"
fi

# 5. 后端重启（与前端编译并行）
#    显式列出 api/background 两个服务，避免连 redis 一起重建造成 ~10s 不可用窗口；
#    runner overlay 固定取 deploy/nuodu/ 版本（仓库根级同名文件是 243 版，勿用），
#    不叠加会被 --remove-orphans 把 runner-api/runner-worker/runner-monitor 删掉
echo "[5] 重启后端服务..."
RUNNER_OVERLAY="deploy/nuodu/docker-compose.agent-runner.yml"
COMPOSE_ARGS=(-f docker-compose.main.yml)
if [ -f "$RUNNER_OVERLAY" ]; then
    COMPOSE_ARGS+=(-f "$RUNNER_OVERLAY")
    echo "    已叠加 $RUNNER_OVERLAY"
else
    echo "  警告：未找到 $RUNNER_OVERLAY，将仅按 docker-compose.main.yml 重建（runner 会被 --remove-orphans 删除！）"
fi
compose_up "后端主服务重启（api/background）" up -d --force-recreate --remove-orphans --wait \
    aid-agent-api aid-agent-background
echo "    主服务已就绪（api/background Healthy）"

# runner 服务重建使新代码生效；服务名 runner-api / runner-worker
if [ -f "$RUNNER_OVERLAY" ]; then
    RUNNER_SERVICES=$(sudo docker compose "${COMPOSE_ARGS[@]}" config --services 2>/dev/null \
        | grep -E '^runner' || true)
    if [ -n "$RUNNER_SERVICES" ]; then
        # shellcheck disable=SC2086
        compose_up "runner 服务重建" up -d --force-recreate $RUNNER_SERVICES
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
step_done "重启后端"

# 6. 施加 CPU 限制（docker compose 非 swarm 会忽略 deploy.resources；
#    内存上限已在 docker-compose.main.yml 用 mem_limit 声明（混部压缩版），
#    此处不再用 docker update 覆盖内存）
echo "[6] 施加容器 CPU 限制..."
sudo docker update --cpus 2 aid-agent-api >/dev/null
sudo docker update --cpus 1 aid-agent-background >/dev/null
step_done "资源限制"

# 7. 修复容器内 /tmp 权限（运行时兜底）
echo "[7] 修复容器 /tmp 权限..."
if ! sudo docker exec -u root aid-agent-api chmod 1777 /tmp 2>/dev/null; then
  echo "  chmod 失败，尝试 mount remount..."
  sudo docker exec -u root aid-agent-api mount -o remount,mode=1777 /tmp 2>/dev/null || \
      echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
step_done "权限修复"

# 增量依赖安装转后台（2026-11-26）：与前端编译等待并行，两段互不依赖。
#     整个安装循环放进子壳——wait -n 在子壳内只可见 pip 任务，不会误等前台
#     任务。recreate 模式同样执行（重建丢层必须补装）。
#     注意：必须对本 compose project 全部同镜像容器逐个安装——docker exec 的
#     安装只落在单容器可写层，其它容器不会继承；--force-recreate 又会丢弃
#     容器内既有安装（2026-11-20 rfc8785 事故）。彻底解法仍是定期重建镜像。
echo "[7.5] 启动增量依赖安装（后台，与前端编译等待并行）..."
PIP_PROJECT=$(sudo docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' aid-agent-api 2>/dev/null || true)
[ -z "$PIP_PROJECT" ] && PIP_PROJECT="${PWD##*/}"
PIP_CONTAINERS=$(sudo docker ps --filter "label=com.docker.compose.project=$PIP_PROJECT" \
    --format '{{.Image}} {{.Names}}' | awk '$1 == "aid-agent-api:latest" {print $2}')
if [ -z "$PIP_CONTAINERS" ]; then
    echo "  警告：未匹配到同镜像容器，退回仅安装 aid-agent-api"
    PIP_CONTAINERS="aid-agent-api"
fi
PIP_TMPDIR=$(mktemp -d)
echo "    目标 $(echo $PIP_CONTAINERS | wc -w) 个容器（并发 4，后台）: $(echo $PIP_CONTAINERS | tr '\n' ' ')"
(
    PIP_RUNNING=0
    for c in $PIP_CONTAINERS; do
        ( sudo docker exec -u root "$c" \
              pip install --no-cache-dir -r /app/requirements.txt \
              -i https://mirrors.cloud.tencent.com/pypi/simple \
              --quiet --disable-pip-version-check || echo "$c" >> "$PIP_TMPDIR/failed" ) &
        PIP_RUNNING=$((PIP_RUNNING + 1))
        # 限制并发 4 个：多副本同时跑 pip 的内存峰值偏高
        if [ "$PIP_RUNNING" -ge 4 ]; then
            wait -n || true
            PIP_RUNNING=$((PIP_RUNNING - 1))
        fi
    done
    wait || true
) &
PIP_PID=$!
step_done "pip后台启动"

# ── 以下前端切换段仅在标准更新模式执行 ──
if [ "$RECREATE_ONLY" -eq 0 ]; then

# 8. 等待前端编译完成
echo "[8] 等待前端校验与编译完成..."
# 两个通道都要等（避免遗留孤儿进程），任一失败即中止且不切换 dist
LANE_FAIL=""
if ! wait "$LANE_A_PID"; then LANE_FAIL="typecheck/边界检查"; fi
if ! wait "$LANE_B_PID"; then LANE_FAIL="${LANE_FAIL:+$LANE_FAIL }vite构建/产物校验"; fi
if [ -n "$LANE_FAIL" ]; then
  echo "  错误：前端失败环节 [$LANE_FAIL]，详见 $BUILD_LOG.a / $BUILD_LOG.b"
  echo "  后端已更新但前端仍为旧版本，请检查后重跑本脚本"
  exit 1
fi
step_done "等待前端"

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
step_done "dist切换"

fi  # 结束：标准更新模式专属段（前端切换）

# 10. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像）
#     两种模式都必须执行：recreate 同样会 --force-recreate，丢弃容器可写层里的
#     既有安装；标准模式还可能引入新依赖。
#     注意：api/background/runner-api/runner-worker/heavy-tools/runner-monitor 共用
#     同一镜像，docker exec 的安装只落在单个容器的可写层，其它容器不会继承；
#     因此必须对本 compose project 内所有同镜像容器逐个安装，否则 Runner 侧
#     运行时 import 新依赖直接失败（2026-11-20 事故：仅主 API 装了 rfc8785，
#     Runner worker 全部缺包）。彻底解法仍是定期重建镜像，把依赖固化进镜像层。
echo "[10] 等待依赖安装完成..."
if ! wait "$PIP_PID"; then
    echo "  警告：pip 后台任务异常退出，见上方输出"
fi
if [ -s "$PIP_TMPDIR/failed" ]; then
    echo "  警告：以下容器依赖安装失败，对应功能可能不可用：$(tr '\n' ' ' < "$PIP_TMPDIR/failed")"
else
    echo "    全部容器依赖安装完成"
fi
rm -rf "$PIP_TMPDIR"
step_done "pip收尾"

echo ""
echo "=========================================="
if [ "$RECREATE_ONLY" -eq 1 ]; then
echo "  容器重建完成（.env 已生效，代码未更新）"
else
echo "  更新完成！"
fi
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
echo "分段耗时: $TIMING_LIST | 总计 ${ELAPSED}s"
}

{ main "$@"; exit $?; }
