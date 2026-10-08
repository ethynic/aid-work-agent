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
#   7. 切换后把 dist 属主恢复为 ubuntu（node 容器以 root 编译，产物属主为 root）
#   8. 支持选择发布分支：不传参数时进入交互菜单（↑/↓ 或输入序号，回车确认，
#      默认 master），用于开发分支在线真机验收；也可 ./agent3_update.sh <远程分支名>
#      直接指定跳过菜单；checkout -f -B 切换/重建本地分支并对齐 origin/<分支>，
#      替代固定 reset --hard origin/master
# ==============================================================================

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

echo "=========================================="
echo "  AI 数字员工系统（在线开发） - 快速更新"
echo "=========================================="

FRONTEND_DIR="/var/www/agent3/frontend"
DIST_DIR="$FRONTEND_DIR/dist"
BUILD_LOG="/var/www/agent3/log/frontend-build.log"

# 配置 Git 安全目录（避免所有权检查错误）
git config --global --add safe.directory /var/www/agent3 2>/dev/null || true

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
git fetch --all
# 参数优先；未传参数时交互选择；stdin 非终端（管道/cron）回退默认 master
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
git checkout -f -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
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
    sudo rm -rf "$DIST_DIR.new"
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
#    AgentRunner overlay：分支含 docker-compose.agent-runner.yml 时叠加一并重建
#    （runner-api/runner-worker/kf-ingress/kf-admission）。必须与主 compose 一起传给
#    同一条 up，否则 --remove-orphans 会把 overlay 服务当孤儿容器删除；KF 服务随
#    --profile wecom-kf 纳入（agent3 已启用微信客服 native）。overlay 缺失（如早期
#    分支）自动退回纯主 compose。
echo "[3] 重启后端服务..."
COMPOSE_FILES=(-f docker-compose.dev.yml)
COMPOSE_PROFILES=()
if [ -f docker-compose.agent-runner.yml ]; then
    COMPOSE_FILES+=(-f docker-compose.agent-runner.yml)
    COMPOSE_PROFILES=(--profile wecom-kf)
    echo "    叠加 AgentRunner overlay（含 wecom-kf profile）"
else
    echo "    未发现 AgentRunner overlay，仅主 compose"
fi
docker compose "${COMPOSE_FILES[@]}" "${COMPOSE_PROFILES[@]}" up -d --force-recreate --remove-orphans --wait

# 4. 施加资源限制（docker compose 非 swarm 会忽略 deploy.resources，改用 docker update
#    显式施加 cgroup 限制；资源值在本脚本内维护，为唯一来源；本环境无 background）
#    runner-api/kf 两类为轻量 IO 进程给 0.5C/512M；runner-worker 执行负载给 1C/1G。
#    容器不存在时（未启用 overlay 的分支）docker update 报错忽略。
echo "[4] 施加容器资源限制..."
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-agent-api3
for c in aid-runner-api aid-kf-ingress aid-kf-admission; do
    docker update --cpus 0.5 --memory 512M --memory-reservation 256M "$c" 2>/dev/null \
        || echo "  跳过 $c（不存在）"
done
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-runner-worker 2>/dev/null \
    || echo "  跳过 aid-runner-worker（不存在）"

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
    sudo rm -rf "$DIST_DIR.old"
    [ -d "$DIST_DIR" ] && mv "$DIST_DIR" "$DIST_DIR.old"
    mv "$DIST_DIR.new" "$DIST_DIR"
    sudo rm -rf "$DIST_DIR.old"
fi
# dist 由 docker root 容器创建，属主为 root；恢复为 ubuntu，避免残留 root 属主文件
# 在后续 git 更新/排查时造成 Permission denied 干扰（2026-09-20 事故）
# 用 sudo：dist 为 root 属主时无 sudo 的 chmod 会直接失败
sudo chown -R ubuntu:ubuntu "$DIST_DIR"
sudo chmod 777 "$DIST_DIR" # dist 目录需要 777 权限，否则无法ftp上传微信验证文件

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
