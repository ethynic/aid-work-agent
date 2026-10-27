#!/bin/bash

# 先解析完整函数体，避免检出目标版本覆盖本脚本后，Bash 从新文件的旧偏移继续读取。
main() {
# 参数必须在身份检查、Git 配置和凭据修复之前验证。
DEPLOY_BRANCH="master"
BRANCH_SET=false
SHOW_HELP=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --branch)
            if [ "$BRANCH_SET" = true ] || [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == -* ]]; then
                echo "错误：--branch 只能指定一次，且必须提供 origin 分支名。" >&2
                return 1
            fi
            DEPLOY_BRANCH="$2"
            BRANCH_SET=true
            shift 2
            ;;
        --help|-h)
            SHOW_HELP=true
            shift
            ;;
        *)
            echo "错误：未知参数：$1。使用 --help 查看用法。" >&2
            return 1
            ;;
    esac
done
if [[ "$DEPLOY_BRANCH" == refs/* ]] || ! git check-ref-format --branch "$DEPLOY_BRANCH" >/dev/null 2>&1 \
   || ! git check-ref-format "refs/heads/$DEPLOY_BRANCH" >/dev/null 2>&1; then
    echo "错误：请提供合法的 origin 分支名，不接受 tag、SHA 或完整 ref。" >&2
    return 1
fi
if [ "$SHOW_HELP" = true ]; then
    echo "用法：bash $0 [--branch <origin分支名>] [--help]"
    echo "默认发布 origin/master；例如 --branch feature/ask-user-question-card。"
    return 0
fi

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
#   8. 新增 git 凭据自愈：.git-credentials 两次被 root 身份的 git/写文件操作
#      覆盖属主为 root（600），ubuntu 读不到导致 git fetch 交互索要密码、
#      脚本 set -e 直接失败（2026-10-14 事故）。fetch 前检测属主/可读性异常
#      即从 /root/.git-credentials.bak（root:root 600 备份件，防连带被改）
#      恢复内容并 chown 回 ubuntu；凭据本体不入 git 仓库
#      （2026-10-19 加固：内容被清空为 0 字节时属主仍可能是 ubuntu，仅查属主会
#       漏判，改为同时检查 codeup 凭据是否存在，缺失即从备份件恢复）
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

# 0. 自愈 git 凭据（见变更记录 8）
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

# 1. 拉取代码
echo "[1] 拉取最新代码..."
cd "/var/www/agent2"
OLD_HEAD=$(git rev-parse HEAD)
echo "更新前版本: $(git log -1 --format='%cd %s' --date='format:%Y-%m-%d %H:%M:%S')"
# 不自动 stash/clean/reset，避免覆盖人工修改；忽略文件由 checkout 的保护检查处理。
if ! WORKTREE_STATUS=$(git status --porcelain --untracked-files=all); then
    echo "错误：无法检查发布目录状态，发布已中止。" >&2
    exit 1
fi
if [ -n "$WORKTREE_STATUS" ]; then
    echo "错误：发布目录存在未提交修改或未跟踪文件，请先人工处理。" >&2
    exit 1
fi
echo "发布分支: origin/$DEPLOY_BRANCH"
# 显式请求源 ref：远端已删除但本地仍有缓存时也必须失败，绝不回退 master。
if ! git fetch --no-tags origin "+refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH"; then
    echo "错误：无法获取 origin/$DEPLOY_BRANCH，发布已中止。" >&2
    exit 1
fi
if ! TARGET_SHA=$(git rev-parse --verify "refs/remotes/origin/$DEPLOY_BRANCH^{commit}"); then
    echo "错误：远程分支提交不可解析，发布已中止。" >&2
    exit 1
fi
echo "发布提交: $TARGET_SHA"
# detached HEAD 保留原本地分支指针；不覆盖目标版本路径上的 ignored 文件。
if ! git checkout --detach --no-overwrite-ignore "$TARGET_SHA"; then
    echo "错误：无法安全检出目标提交，发布已中止。" >&2
    exit 1
fi
echo "更新后版本: $(git log -1 --format='%cd %s' --date='format:%Y-%m-%d %H:%M:%S')"
NEW_HEAD=$(git rev-parse HEAD)
find . -type d -name "__pycache__" -exec chmod -R 777 {} + 2>/dev/null || true

# 1.1 清除 Python 字节码缓存（避免旧代码运行）
echo "[1.1] 清除 Python .pyc 缓存..."
find . -type f -name "*.pyc" -delete 2>/dev/null || true
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
#    （runner-api/runner-worker）。必须与主 compose 一起传给同一条 up，否则
#    --remove-orphans 会把 overlay 服务当孤儿容器删除。KF 恢复原主 API 渠道入口；
#    发布前须按 agent-runner-deploy-checklist.md 完成旧 KF 存量/送达核对与 cursor 交接，
#    停止旧 KF 消费者及仍读写 gate 的旧 Runner API/worker，清理专属凭据；
#    主 API 启动会执行 gate 退役迁移，不能只等孤儿清理。
#    --remove-orphans 只移除同 compose project 的旧 KF 容器，发布后须核对实际残留。
#    overlay 缺失（如早期分支）自动退回纯主 compose。
#    agent2 特有：与 agent3 共库 aid_work_agent2 且同宿主机（共享 uploads/storage
#    目录，overlay 容器名固定无环境后缀），同一时刻只允许一个环境常驻 Runner
#    容器——叠加前做互斥预检，发现 Runner 容器被其他 compose project（agent3）占用即
#    报错退出；三个宿主变量为 test 环境专属值（overlay 默认值对齐生产，不注入会
#    接错网络/目录），在此 export 为唯一来源，不依赖服务器 .env 是否配置。
echo "[6] 重启后端服务..."
COMPOSE_FILES=(-f docker-compose.test.yml)
if [ -f docker-compose.agent-runner.yml ]; then
    COMPOSE_FILES+=(-f docker-compose.agent-runner.yml)
    export AGENT_RUNNER_NETWORK=aid-network2
    export AGENT_RUNNER_UPLOADS_HOST_DIR=/var/www/qb3_upload/agent2_uploads
    export AGENT_RUNNER_STORAGE_HOST_DIR=/var/www/qb3_upload/agent2_storage
    echo "    叠加 AgentRunner overlay（runner-api/runner-worker）"
    # 互斥预检：用本环境主 API 容器的 compose project 标签锚定当前 project 名
    # （首次部署容器不存在时退回目录名，与 compose 默认 project 命名一致）
    CURRENT_PROJECT=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' aid-agent-api2 2>/dev/null || true)
    [ -z "$CURRENT_PROJECT" ] && CURRENT_PROJECT="${PWD##*/}"
    for c in aid-runner-api aid-runner-worker; do
        OWNER=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$c" 2>/dev/null || true)
        if [ -n "$OWNER" ] && [ "$OWNER" != "$CURRENT_PROJECT" ]; then
            echo "错误：容器 $c 已被 compose project '$OWNER'（另一环境）占用。"
            echo "  agent2/agent3 共库 aid_work_agent2，同一时刻只允许一个环境常驻"
            echo "  Runner 容器（lease 池跨环境抢任务 + 同名容器冲突）。请先在该"
            echo "  环境移除 Runner 容器后再发布："
            echo "    docker rm -f aid-runner-api aid-runner-worker"
            exit 1
        fi
    done
else
    echo "    未发现 AgentRunner overlay，仅主 compose"
fi
docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate --remove-orphans --wait

# 7. 施加资源限制（docker compose 非 swarm 会忽略 deploy.resources，改用 docker update
#    显式施加 cgroup 限制；资源值在本脚本内维护，为唯一来源）
#    runner-api 为轻量 IO 进程给 0.5C/512M；runner-worker 执行负载给 1C/1G。
#    容器不存在时（未启用 overlay 的分支）docker update 报错忽略。
echo "[7] 施加容器资源限制..."
docker update --cpus 1 --memory 1G --memory-reservation 512M aid-agent-api2
docker update --cpus 0.5 --memory 512M --memory-reservation 256M aid-agent-background2
for c in aid-runner-api; do
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
# 保留企微可信域名校验文件：企微不定期复核，文件丢失会导致可信域名回退为待审核
ls "$DIST_DIR.old"/WW_verify_*.txt >/dev/null 2>&1 && \
    sudo cp "$DIST_DIR.old"/WW_verify_*.txt "$DIST_DIR"/
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

}

{ main "$@"; exit $?; }
