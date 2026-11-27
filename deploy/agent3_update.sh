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
#   7. 切换后把 dist 属主恢复为 ubuntu（node 容器以 root 编译，产物属主为 root）
#   8. 支持选择发布分支：不传参数时进入交互菜单（↑/↓ 或输入序号，回车确认，
#      默认 master），用于开发分支在线真机验收；也可 ./agent3_update.sh <远程分支名>
#      直接指定跳过菜单；checkout -f -B 切换/重建本地分支并对齐 origin/<分支>，
#      替代固定 reset --hard origin/master
#   9. 新增 git 凭据自愈：.git-credentials 两次被 root 身份的 git/写文件操作
#      覆盖属主为 root（600），ubuntu 读不到导致 git fetch 交互索要密码、
#      脚本 set -e 直接失败（2026-10-14 事故）。fetch 前检测属主/可读性异常
#      即从 /root/.git-credentials.bak（root:root 600 备份件，防连带被改）
#      恢复内容并 chown 回 ubuntu；凭据本体不入 git 仓库
#      （2026-10-19 加固：内容被清空为 0 字节时属主仍可能是 ubuntu，仅查属主会
#       漏判，改为同时检查 codeup 凭据是否存在，缺失即从备份件恢复）
#  10. 2026-11-14 新增 [5.1] 步：runner 容器（worker/heavy-tools/runner-api）的
#      /tmp 与 /home/appuser/tmp 权限兜底——原脚本只修 aid-agent-api3，
#      worker TMPDIR 权限在镜像未重建时缺兜底（2026-11-13 部署手工补）
#  11. 2026-11-20 [7] 步增量依赖安装由「仅主 API 容器」改为「本 compose project
#      内全部同镜像容器」：docker exec 的 pip 安装只落在单容器可写层，Runner
#      不继承主 API 的安装，导致 rfc8785 仅主 API 有、worker 全缺
#  13. 2026-11-26 对齐 agent2_update.sh 的三项提速：main() 包裹 + exec 自更新
#      重入（脚本随发布更新后自动用新版，不再需要跑两遍）；前端校验链双通道
#      并行（typecheck 门禁与 vite build 产出互不依赖）；pip 安装转后台与前端
#      等待并行（[5.2]/[7]）
#  12. 2026-11-26 两项调整（对齐 agent2_update.sh）：
#      a) 等待语义对齐生产：up --wait 只等主 API/background，runner 服务
#         （runner-api/worker/monitor/heavy-tools）单独 up --force-recreate 不阻塞
#         等待健康，发布后仅对 runner-api 做 /health 探测告警——此前 --wait 全量
#         等待把 runner-monitor（start_period 4m）等 healthcheck 串进发布总时长
#      b) 新增分段计时：每步结束打印「本段/累计」耗时
#  14. 2026-11-27 输出精简（与 agent2_update.sh 同款）：compose up 输出重定向
#      文件（成功零输出、失败回显错误尾部，不依赖 --progress）；git fetch/npm/
#      checkout 静默；docker update 输出抑制；删除 /tmp ls 回显；pip 加
#      --disable-pip-version-check、逐容器行改单行汇总
# ==============================================================================

main() {
set -e

# 用法: ./agent3_update.sh [远程分支名]
#   不传参数：进入交互菜单选择发布分支（↑/↓ 移动或输入序号，回车确认，默认 master）；
#   传参数  ：跳过菜单直接发布该远程分支（便于自动化调用）。
TARGET_BRANCH="${1:-}"
if [ "$TARGET_BRANCH" = "-h" ] || [ "$TARGET_BRANCH" = "--help" ]; then
    echo "用法: $0 [远程分支名]"
    echo "  不传参数时进入交互菜单（↑/↓ 或输入序号，回车确认，默认 master）；"
    echo "  传远程分支名则跳过菜单直接发布，如：$0 feature/unified-agent-run-p0"
    exit 0
fi
# 分支名白名单：字母/数字开头，仅含字母/数字/._-/（远程分支名含 / 是合法的），
# 拒绝 ..、尾斜杠与特殊字符；更严的合法性最终由 show-ref 存在性校验兜底
if [ -n "$TARGET_BRANCH" ] && { [[ ! "$TARGET_BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]+$ ]] || [[ "$TARGET_BRANCH" == *..* ]] || [[ "$TARGET_BRANCH" == */ ]]; }; then
    echo "错误：非法分支名 '$TARGET_BRANCH'"
    exit 1
fi

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
COMPOSE_LOG=/tmp/agent3_compose.log
compose_up() {
    local desc="$1"; shift
    if ! docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_QUIET[@]}" "$@" >"$COMPOSE_LOG" 2>&1; then
        echo "错误：$desc 失败，关键输出如下："
        grep -vE 'swap limit|Running [0-9]+/' "$COMPOSE_LOG" | tail -40
        return 1
    fi
}

echo "=========================================="
echo "  AI 数字员工系统（在线开发） - 快速更新"
echo "=========================================="

FRONTEND_DIR="/var/www/agent3/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="/var/www/agent3/log/frontend-build.log"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory /var/www/agent3 2>/dev/null || true

# 0. 自愈 git 凭据（见变更记录 9）
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

# ─── 交互选择发布分支：↑/↓ 移动或输入序号，回车确认；默认 master；q 取消 ───
# 选中结果写入全局 TARGET_BRANCH；菜单阶段脚本尚未做任何变更，取消直接退出。
# master 固定第一项且为默认；其余按最近推送时间排序，只列前 15 个（更多用参数指定）。
interactive_select_branch() {
    local -a BRANCHES=("master")
    local b i key tail sel=0 num="" drawn=0
    while IFS= read -r b; do
        BRANCHES+=("$b")
    done < <(git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/remotes/origin/ \
             | grep -v -e '^origin/HEAD$' -e '^origin/master$' \
             | sed -e 's|^origin/||' | head -n 15)
    local count=${#BRANCHES[@]}
    echo "选择要发布的分支："
    while true; do
        # 重绘前把光标移回菜单首行，覆盖上一次输出（首次绘制不回退）
        if [ "$drawn" -gt 0 ]; then printf '\033[%dA' "$count"; fi
        i=0
        while [ "$i" -lt "$count" ]; do
            if [ "$i" -eq "$sel" ]; then
                printf '\r\033[K  \033[1;36m❯ %2d) %s\033[0m\n' "$((i+1))" "${BRANCHES[$i]}"
            else
                printf '\r\033[K    %2d) %s\n' "$((i+1))" "${BRANCHES[$i]}"
            fi
            i=$((i+1))
        done
        printf '\r\033[K  ↑/↓ 移动 / 输入序号 / 回车确认（默认 master）/ q 取消  %s' "$num"
        drawn=1
        if ! read -rsn1 key; then
            printf '\n'
            echo "已取消（输入结束），未做任何变更。"
            exit 1
        fi
        case "$key" in
            $'\x1b')  # 方向键为 ESC [ A/B 三字节序列，一次读完剩余 2 字节
                if read -rsn2 -t 1 tail; then
                    case "$tail" in
                        '[A') sel=$(((sel - 1 + count) % count)); num="" ;;
                        '[B') sel=$(((sel + 1) % count)); num="" ;;
                    esac
                fi ;;
            '')  # 回车：已输入序号则按序号（越界忽略），否则取当前高亮项（初始即 master）
                if [ -n "$num" ] && [ "$num" -ge 1 ] && [ "$num" -le "$count" ]; then
                    sel=$((num - 1))
                fi
                TARGET_BRANCH="${BRANCHES[$sel]}"
                printf '\n'
                return ;;
            [0-9])
                if [ "${#num}" -lt 2 ]; then num="${num}${key}"; fi ;;
            q|Q)
                printf '\n'
                echo "已取消，未做任何变更。"
                exit 0 ;;
            *)
                num="" ;;
        esac
    done
}

# 1. 拉取代码（发布分支由参数或交互菜单确定，默认 master）
echo "[1] 拉取最新代码..."
cd "/var/www/agent3"
echo "当前分支: $(git rev-parse --abbrev-ref HEAD)"
OLD_HEAD=$(git rev-parse HEAD)
echo "更新前版本: $(git log -1 --format='%cd %s' --date='format:%Y-%m-%d %H:%M:%S')"
git fetch --all --quiet
# 参数优先；未传参数时交互选择；stdin 非终端（管道/cron）回退默认 master
# 自更新重入时沿用首轮选择的分支（否则交互菜单会再弹一次）
if [ -z "$TARGET_BRANCH" ] && [ -n "${AID_DEPLOY_BRANCH:-}" ]; then
    echo "自更新重入：沿用首轮选择的分支 $AID_DEPLOY_BRANCH"
    TARGET_BRANCH="$AID_DEPLOY_BRANCH"
fi
if [ -z "$TARGET_BRANCH" ]; then
    if [ -t 0 ]; then
        interactive_select_branch
    else
        echo "stdin 非交互终端，默认发布 master"
        TARGET_BRANCH="master"
    fi
fi
echo "目标分支: origin/$TARGET_BRANCH"
if ! git show-ref --verify --quiet "refs/remotes/origin/$TARGET_BRANCH"; then
    echo "错误：远程分支 origin/$TARGET_BRANCH 不存在，可用远程分支："
    git branch -r | grep -v 'HEAD' | sed 's/^/  /'
    exit 1
fi
if [ "$TARGET_BRANCH" != "master" ]; then
    echo "⚠️  正在发布非 master 分支 '$TARGET_BRANCH'（开发/验收用途）"
fi
# checkout -f -B：切换或重建本地分支并强制对齐 origin/<分支>；
# -f 丢弃本地改动与挡路的未跟踪文件，与原 reset --hard origin/master 的部署语义一致
git checkout -f -B --quiet "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
echo "更新后版本: $(git log -1 --format='%cd %s' --date='format:%Y-%m-%d %H:%M:%S')"
NEW_HEAD=$(git rev-parse HEAD)
# 自更新重入：脚本文件随本次检出变化 -> exec 新版（重入后再比较必然相同，
# 且 AID_DEPLOY_RERUN 守卫兜底，不会死循环）
NEW_SELF_HASH=$(sha256sum "$SCRIPT_PATH" 2>/dev/null | cut -d' ' -f1 || true)
if [ "${AID_DEPLOY_RERUN:-0}" != "1" ] && [ -n "$SCRIPT_SELF_HASH" ] \
   && [ "$NEW_SELF_HASH" != "$SCRIPT_SELF_HASH" ]; then
    echo "[1.2] 发布脚本已随本次更新变化，exec 重启以执行新版脚本（仅重入一次）..."
    # 交互菜单选中的分支经环境变量带入重入轮次，避免菜单再弹一次
    AID_DEPLOY_RERUN=1 AID_DEPLOY_BRANCH="$TARGET_BRANCH" exec bash "$SCRIPT_PATH" "$@"
fi
find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 1.1 清除 Python 字节码缓存（避免旧代码运行）
echo "[1.1] 清除 Python .pyc 缓存..."
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
step_done "拉取+清缓存"

# 2. 判断前端是否需要编译；需要时编译到 dist.new（后台，与后端重启并行）
LANE_A_PID=""; LANE_B_PID=""
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
            npm install --no-audit --no-fund --prefer-offline --loglevel=error
    fi
    # 双通道并行（2026-11-26）：通道 A = typecheck/边界检查（门禁，不产出 dist），
    # 通道 B = vite build/产物校验（产出 dist.new），互不依赖，墙钟时间 = max(两通道)；
    # 两通道都通过才允许切换 dist（门禁不放松）
    (
      docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine npm run typecheck \
      && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
          node scripts/check-dependency-boundaries.mjs --scope=web
    ) > "$BUILD_LOG.a" 2>&1 &
    LANE_A_PID=$!
    # 通道 B：vite build + 产物校验
    sudo rm -rf "$DIST_DIR.new"
    mkdir -p "$(dirname "$BUILD_LOG")"
    (
      docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
          npx vite build --outDir dist.new \
        && docker run --rm -v "$FRONTEND_DIR":/app -w /app node:22-alpine \
            node scripts/verify-web-build.mjs /app/dist.new
    ) > "$BUILD_LOG.b" 2>&1 &
    LANE_B_PID=$!
else
    echo "[2] 前端代码无变更，跳过编译。"
fi
step_done "前端准备"

# 3. 后端重启（与前端编译并行；前端无变更时直接重启）
#    不先 down：up --force-recreate 会自动 stop→remove→create，避免全停窗口
#    AgentRunner overlay：分支含 docker-compose.agent-runner.yml 时叠加一并重建
#    （runner-api/runner-worker/heavy-tools/runner-monitor）。必须与主 compose 一起传给同一条 up，否则
#    --remove-orphans 会把 overlay 服务当孤儿容器删除。KF 恢复原主 API 渠道入口；
#    发布前须按 docs/guides/deployment/agent-runner-deploy-checklist.md 完成旧 KF 存量/送达核对与 cursor 交接，
#    停止旧 KF 消费者及仍读写 gate 的旧 Runner API/worker，清理专属凭据；
#    主 API 启动会执行 gate 退役迁移，不能只等孤儿清理。
#    --remove-orphans 只移除同 compose project 的旧 KF 容器，发布后须核对实际残留。
#    overlay 缺失（如早期分支）自动退回纯主 compose。
echo "[3] 重启后端服务..."
COMPOSE_FILES=(-f docker-compose.dev.yml)
if [ -f docker-compose.agent-runner.yml ]; then
    COMPOSE_FILES+=(-f docker-compose.agent-runner.yml)
    echo "    叠加 AgentRunner overlay（runner-api/runner-worker/heavy-tools/runner-monitor）"
else
    echo "    未发现 AgentRunner overlay，仅主 compose"
fi
# --wait 只等主 API/background（2026-11-26 对齐生产 agent_update.sh 语义）：
# runner 服务（runner-api/runner-worker/runner-monitor/heavy-tools）的
# healthcheck（monitor start_period 4m、heavy-tools 60s）不再串进发布总时长
compose_up "后端主服务重启（api/background）" up -d --force-recreate --remove-orphans --wait \
    aid-agent-api aid-agent-background
echo "    主服务已就绪（api/background Healthy）"

# runner 服务重建使新代码生效；不带 --wait（与生产对齐，就绪靠下方 /health 探测告警）
if [ -f docker-compose.agent-runner.yml ]; then
    RUNNER_SERVICES=$(docker compose "${COMPOSE_FILES[@]}" config --services 2>/dev/null \
        | grep -E '^runner|^heavy-tools' || true)
    if [ -n "$RUNNER_SERVICES" ]; then
        # shellcheck disable=SC2086
        compose_up "runner 服务重建" up -d --force-recreate $RUNNER_SERVICES
        echo "    runner 服务已重建（不等待健康）: $(echo $RUNNER_SERVICES | tr '\n' ' ')"
    fi
    # runner-api 可达性探测（失败仅告警不阻断，重建后异步就绪）
    if docker exec aid-agent-api3 python -c \
        "import urllib.request; urllib.request.urlopen('http://runner-api:8091/health', timeout=5)" \
        2>/dev/null; then
        echo "    runner-api 健康检查通过"
    else
        echo "  警告：runner-api /health 暂不可达（重建后异步就绪，稍后 docker ps 核对），渠道对话可能短暂 RUNNER_SERVICE_UNAVAILABLE"
    fi
fi
step_done "重启后端"

# 4. 施加资源限制（docker compose 非 swarm 会忽略 deploy.resources，改用 docker update
#    显式施加 cgroup 限制；资源值在本脚本内维护，为唯一来源）
#    runner-api 容器名从 .env 读取；worker 按 Compose 副本命名，限额由 overlay 配置。
#    runner-api 为轻量 IO 进程给 0.5C/512M。
#    容器不存在时（未启用 overlay 的分支）docker update 报错忽略。
echo "[4] 施加容器资源限制..."
RUNNER_API_CONTAINER="$(grep -E '^AGENT_RUNNER_API_CONTAINER=' .env 2>/dev/null | cut -d= -f2- || true)"
RUNNER_API_CONTAINER="${RUNNER_API_CONTAINER:-aid-runner-api}"
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-agent-api3 >/dev/null
docker update --cpus 0.5 --memory 512M --memory-reservation 256M "$RUNNER_API_CONTAINER" >/dev/null 2>/dev/null \
    || echo "  跳过 $RUNNER_API_CONTAINER（不存在）"
step_done "资源限制"

# 5. 修复容器内 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs 且默认 755，
#    Dockerfile 的 chmod 不生效，entrypoint 已处理；此处作为运行时兜底）
echo "[5] 修复容器 /tmp 权限..."
if ! docker exec -u root aid-agent-api3 chmod 1777 /tmp 2>/dev/null; then
  echo "  chmod 失败，尝试 mount remount..."
  docker exec -u root aid-agent-api3 mount -o remount,mode=1777 /tmp 2>/dev/null || \
      echo "  警告：两种方式均失败，appuser 可能无法写入 /tmp"
fi
step_done "权限修复"

# 5.1 修复 runner 容器 /tmp 与 /home/appuser/tmp 权限（与 agent2_update.sh [8] 同款）。
#    worker 是执行体所在容器：/home/appuser/tmp 是其 TMPDIR，tmpfs mode 在本机引擎
#    不可靠（2026-11-12 Playwright EACCES 事故），镜像内 fix_tmp.sh 为权威兜底，
#    此处兜底「代码已更新但镜像未重建」的容器；旧镜像场景下按容器逐一修复并自检。
#    2026-11-14 补：池路由退役（B1）后 worker 按 Compose 副本序号命名
#    agent3-runner-worker-1..N；heavy-tools 在 agent3 不部署（REPLICAS=0），
#    保留在名单中便于未来拉起时同样兜底。
echo "[5.1] 修复 runner 容器 /tmp 与 /home/appuser/tmp 权限..."
for c in $(docker ps --format '{{.Names}}' | grep -E '^agent3-runner-worker-[0-9]+$|^aid-heavy-tools$|^aid-runner-api3$'); do
    if docker exec -u root "$c" sh -c "chmod 1777 /tmp && chown appuser:appgroup /home/appuser/tmp" 2>/dev/null; then
        # 以 appuser 身份自检可写性，失败时输出可 grep 的警告
        docker exec -u 1000 "$c" sh -c "touch /home/appuser/tmp/.perm_probe && rm -f /home/appuser/tmp/.perm_probe" 2>/dev/null \
            || echo "  警告：$c appuser 无法写 /home/appuser/tmp，请核对镜像内 fix_tmp.sh 版本"
    else
        echo "  警告：$c 权限修复失败（容器不存在或 exec 异常）"
    fi
done
step_done "runner权限"

# 5.2 增量依赖安装转后台（2026-11-26）：与前端编译等待并行，两段互不依赖。
#     整个安装循环放进子壳——wait -n 在子壳内只可见 pip 任务，不会误等前台
#     任务。注意：必须对本 compose project 全部同镜像容器逐个安装——docker
#     exec 的安装只落在单容器可写层，其它容器不会继承；--force-recreate 又会
#     丢弃容器内既有安装（2026-11-20 rfc8785 事故）。彻底解法仍是定期重建镜像。
echo "[5.2] 启动增量依赖安装（后台，与前端编译等待并行）..."
PIP_PROJECT=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' aid-agent-api3 2>/dev/null || true)
[ -z "$PIP_PROJECT" ] && PIP_PROJECT="${PWD##*/}"
PIP_CONTAINERS=$(docker ps --filter "label=com.docker.compose.project=$PIP_PROJECT" \
    --format '{{.Image}} {{.Names}}' | awk '$1 == "aid-agent-api:latest" {print $2}')
if [ -z "$PIP_CONTAINERS" ]; then
    echo "  警告：未匹配到同镜像容器，退回仅安装 aid-agent-api3"
    PIP_CONTAINERS="aid-agent-api3"
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

# 6. 等待前端编译完成并原子切换（仅前端有变更时）
if [ -n "$LANE_A_PID" ]; then
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

    echo "[6.1] 原子切换前端 dist..."
    sudo rm -rf "$DIST_DIR.old"
    [ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
    mv "$DIST_DIR.new" "$DIST_DIR"
    # 保留企微可信域名校验文件：企微不定期复核，文件丢失会导致可信域名回退为待审核
    ls "$DIST_DIR.old"/WW_verify_*.txt >/dev/null 2>&1 && \
        sudo cp "$DIST_DIR.old"/WW_verify_*.txt "$DIST_DIR"/
    sudo rm -rf "$DIST_DIR.old"
fi
# dist 由 docker root 容器创建，属主为 root；恢复为 ubuntu，避免残留 root 属主文件
# 在后续 git 更新/排查时造成 Permission denied 干扰（2026-09-20 事故）
# 用 sudo：dist 为 root 属主时无 sudo 的 chmod 会直接失败
sudo chown -R ubuntu:ubuntu "$DIST_DIR"
sudo chmod 777 "$DIST_DIR" # dist 目录需要 777 权限，否则无法ftp上传微信验证文件
step_done "前端切换"

# 7. 增量安装 requirements.txt 中新增的依赖（快速更新脚本不重建镜像，
#    新依赖不会自动安装；下次重建镜像后可移除此步骤）
#    注意：api/background/runner-api/runner-worker/heavy-tools/runner-monitor 共用
#    同一镜像，docker exec 的安装只落在单个容器的可写层，其它容器不会继承；而
#    [3] 的 --force-recreate 又会丢弃容器内既有安装。因此必须对本 compose project
#    内所有同镜像容器逐个安装，否则 Runner 侧运行时 import 新依赖直接失败
#    （2026-11-20 事故：仅主 API 装了 rfc8785，4 个 Runner worker 全部缺包）。
#    彻底解法仍是定期重建镜像，使依赖固化进镜像层。
echo "[7] 等待依赖安装完成..."
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
echo "更新完成！"
echo "=========================================="
ELAPSED=$(( $(date +%s) - START_TS ))
echo "总耗时: $(( ELAPSED / 60 )) 分 $(( ELAPSED % 60 )) 秒"
echo "分段耗时: $TIMING_LIST | 总计 ${ELAPSED}s"
}

{ main "$@"; exit $?; }
