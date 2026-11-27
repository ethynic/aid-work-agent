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
#   - 等待语义与生产对齐（2026-11-26）：up --wait 只等主 api；runner-sim 服务
#     （runner-api-sim/runner-worker-sim/runner-monitor-sim）单独 up --force-recreate
#     不阻塞等待健康，发布后仅对 runner-api-sim 做 /health 探测告警
#   - 与 sim.sh（复现模式，纯 rsync 生产代码整体替换）互为整体替换，
#     仿真侧不保留任何自有代码改动
# ⚠️ 本脚本 git reset --hard 会丢弃工作区本地修改（含复现模式同步来的生产代码）；
#    复现生产 bug 请改跑 sim.sh（默认附带代码同步）
# ==============================================================================

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
# 的旧引擎（探测失败也不再有进度帧刷屏）
COMPOSE_LOG=/tmp/agent1_compose.log
compose_up() {
    local desc="$1"; shift
    if ! docker compose "${COMPOSE_ARGS[@]}" "${COMPOSE_QUIET[@]}" "$@" >"$COMPOSE_LOG" 2>&1; then
        echo "错误：$desc 失败，关键输出如下："
        grep -vE 'swap limit|Running [0-9]+/' "$COMPOSE_LOG" | tail -40
        return 1
    fi
}

echo "=========================================="
echo "  仿真环境（agent1）- 验证模式更新"
echo "=========================================="

SIM_DIR="/var/www/agent1"
FRONTEND_DIR="$SIM_DIR/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="$SIM_DIR/log/frontend-build.log"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory "$SIM_DIR" 2>/dev/null || true

# 0. 自愈 git 凭据：.git-credentials 曾两次被 root 身份的 git/写文件操作覆盖属主
#    为 root（600），ubuntu 读不到导致 git fetch 交互索要密码、脚本 set -e 直接
#    失败（2026-10-14 事故）。fetch 前检测属主/可读性异常即从
#    /root/.git-credentials.bak（root:root 600 备份件，防连带被改）恢复内容并
#    chown 回 ubuntu；凭据本体不入 git 仓库
#    （2026-10-19 加固：内容被清空为 0 字节时属主仍可能是 ubuntu，仅查属主会漏判，
#     改为同时检查 codeup 凭据是否存在，缺失即从备份件恢复）
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

# 1. 拉取代码（可选参数：目标 git 提交/分支，短哈希/长哈希/tag/分支名均可；
#    不传则更新到 origin/master 最新。指定修复分支/提交用于上线前验证）
echo "[1] 拉取代码..."
cd "$SIM_DIR"
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
# 自更新重入：脚本文件随本次检出变化 -> exec 新版（重入后再比较必然相同，
# 且 AID_DEPLOY_RERUN 守卫兜底，不会死循环）
NEW_SELF_HASH=$(sha256sum "$SCRIPT_PATH" 2>/dev/null | cut -d' ' -f1 || true)
if [ "${AID_DEPLOY_RERUN:-0}" != "1" ] && [ -n "$SCRIPT_SELF_HASH" ] \
   && [ "$NEW_SELF_HASH" != "$SCRIPT_SELF_HASH" ]; then
    echo "[1.2] 发布脚本已随本次更新变化，exec 重启以执行新版脚本（仅重入一次）..."
    AID_DEPLOY_RERUN=1 exec bash "$SCRIPT_PATH" "$@"
fi

# 1.1 清除 Python 字节码缓存（避免旧代码运行；rsync 同步可能留下 root 属主残留，用 sudo）
echo "[1.1] 清除 Python .pyc 缓存..."
sudo find "$SIM_DIR" -name .git -prune -o -type f -name '*.pyc' -delete 2>/dev/null || true
sudo find "$SIM_DIR" -name .git -prune -o -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
step_done "拉取+清缓存"

# 2. 前端依赖安装（node_modules 持久化在宿主机，增量安装；npm 缓存用命名卷持久化）
echo "[2] 安装前端依赖..."
docker run --rm \
    -v "$FRONTEND_DIR":/app \
    -v agent1_npm_cache:/root/.npm \
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
# dist.new/dist.old 若为上次 root 容器残留，ubuntu 身份 rm 会 Permission denied，须 sudo
sudo rm -rf "$DIST_DIR.new"
mkdir -p "$(dirname "$BUILD_LOG")"
(
  docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck \
  && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      node scripts/check-dependency-boundaries.mjs --scope=web
) > "$BUILD_LOG.a" 2>&1 &
LANE_A_PID=$!
(
  docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      npx vite build --outDir dist.new \
  && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
      node scripts/verify-web-build.mjs /app/dist.new
) > "$BUILD_LOG.b" 2>&1 &
LANE_B_PID=$!
step_done "前端后台"

# 5. 重启仿真后端（up 会拉起未运行的容器；--force-recreate 保证 .env 最新）
# compose 文件以仓库 deploy/ 版本为准（env_file: .env 相对路径要求部署在仿真根目录）
# runner overlay（docker-compose.agent-runner.sim.yml）存在于根级时一并叠加，
# 统一重建 runner-api-sim/runner-worker-sim（代码经 .:/app 挂载，重建保证进程加载新代码）
cp -f "$SIM_DIR/deploy/docker-compose.sim.yml" "$SIM_DIR/docker-compose.sim.yml"
COMPOSE_ARGS=(-f "$SIM_DIR/docker-compose.sim.yml")
if [[ -f "$SIM_DIR/deploy/docker-compose.agent-runner.sim.yml" ]]; then
  cp -f "$SIM_DIR/deploy/docker-compose.agent-runner.sim.yml" "$SIM_DIR/docker-compose.agent-runner.sim.yml"
  COMPOSE_ARGS+=(-f "$SIM_DIR/docker-compose.agent-runner.sim.yml")
fi
echo "[5] 重启仿真后端 aid-agent-api1（及已启用的 runner 容器）..."
# --wait 只等主 api（2026-11-26 对齐生产语义）：runner-sim 服务的 healthcheck
# （monitor-sim start_period 4m）不再串进发布总时长
# --remove-orphans：overlay 服务名变更后，旧容器占用同名 container_name 会导致建容器冲突
compose_up "仿真后端重启（api）" up -d --remove-orphans --force-recreate --wait api
echo "    主服务已就绪（api Healthy）"

# runner-sim 服务重建使新代码生效（代码经 .:/app 挂载，重建保证进程加载新代码）；
# 不带 --wait（与生产对齐，就绪靠下方 /health 探测告警）
RUNNER_SERVICES=$(docker compose "${COMPOSE_ARGS[@]}" config --services 2>/dev/null \
    | grep -E '^runner' || true)
if [ -n "$RUNNER_SERVICES" ]; then
    # shellcheck disable=SC2086
    compose_up "runner-sim 服务重建" up -d --force-recreate $RUNNER_SERVICES
    echo "    runner 服务已重建（不等待健康）: $(echo $RUNNER_SERVICES | tr '\n' ' ')"
    # runner-api-sim 可达性探测（失败仅告警不阻断，重建后异步就绪）
    if docker exec aid-agent-api1 python -c \
        "import urllib.request; urllib.request.urlopen('http://runner-api-sim:8091/health', timeout=5)" \
        2>/dev/null; then
        echo "    runner-api-sim 健康检查通过"
    else
        echo "  警告：runner-api-sim /health 暂不可达（重建后异步就绪，稍后 docker ps 核对）"
    fi
fi
step_done "重启后端"

# 增量依赖安装转后台（2026-11-26）：与前端编译等待并行，两段互不依赖。
#     整个安装循环放进子壳——wait -n 在子壳内只可见 pip 任务，不会误等前台
#     任务。
#     注意：必须对本 compose project 全部同镜像容器逐个安装——docker exec 的
#     安装只落在单容器可写层，其它容器不会继承；--force-recreate 又会丢弃
#     容器内既有安装（2026-11-20 rfc8785 事故）。彻底解法仍是定期重建镜像。
echo "[5.5] 启动增量依赖安装（后台，与前端编译等待并行）..."
PIP_PROJECT=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' aid-agent-api1 2>/dev/null || true)
[ -z "$PIP_PROJECT" ] && PIP_PROJECT="${PWD##*/}"
PIP_CONTAINERS=$(docker ps --filter "label=com.docker.compose.project=$PIP_PROJECT" \
    --format '{{.Image}} {{.Names}}' | awk '$1 == "aid-agent-api:latest" {print $2}')
if [ -z "$PIP_CONTAINERS" ]; then
    echo "  警告：未匹配到同镜像容器，退回仅安装 aid-agent-api1"
    PIP_CONTAINERS="aid-agent-api1"
fi
PIP_TMPDIR=$(mktemp -d)
echo "    目标 $(echo $PIP_CONTAINERS | wc -w) 个容器（并发 4，后台）: $(echo $PIP_CONTAINERS | tr '\n' ' ')"
(
    PIP_RUNNING=0
    for c in $PIP_CONTAINERS; do
        ( docker exec -u root "$c" \
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


# 6. 等待前端编译完成
echo "[6] 等待前端校验与编译完成..."
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

# 7. 原子切换 dist（同一文件系统内两步 mv；nginx 走 /index.html 兜底）
echo "[7] 原子切换前端 dist..."
sudo rm -rf "$DIST_DIR.old"
[ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
mv "$DIST_DIR.new" "$DIST_DIR"
# 保留企微可信域名校验文件：企微不定期复核，文件丢失会导致可信域名回退为待审核
ls "$DIST_DIR.old"/WW_verify_*.txt >/dev/null 2>&1 && \
    sudo cp "$DIST_DIR.old"/WW_verify_*.txt "$DIST_DIR"/
sudo rm -rf "$DIST_DIR.old"
# dist 由 docker root 创建，chown/chmod 归位到 ubuntu（下次 git/rm 不再被 root 属主阻断）
sudo chown -R ubuntu:ubuntu "$DIST_DIR"
sudo chmod 777 "$DIST_DIR"
step_done "dist切换"

# 8. 增量安装 requirements.txt 中新增的依赖（不重建镜像，新依赖需补装）
#    注意：api/runner-api/runner-worker 共用同一镜像，docker exec 的安装只落在
#    单个容器的可写层，其它容器不会继承；而 [5] 的 --force-recreate 又会丢弃
#    容器内既有安装。因此必须对本 compose project 内所有同镜像容器逐个安装，
#    否则 Runner 侧运行时 import 新依赖直接失败（2026-11-20 rfc8785 事故）。
echo "[8] 等待依赖安装完成..."
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
step_done "健康检查"

echo ""
echo "=========================================="
echo "  仿真环境更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
echo "分段耗时: $TIMING_LIST | 总计 ${ELAPSED}s"
}

{ main "$@"; exit $?; }
