#!/bin/bash

# ==============================================================================
# AI 数字员工系统 - 镜像重建脚本【聊易私有云适配版】
# 服务器：agent.0635easy.com（47.104.107.85），用户 ubuntu，仓库根 /var/www/agent/app
#
# 目的（2026-11-24，发布提速优化 3）：把 requirements.txt 依赖固化进
# aid-agent-api:latest 镜像层。agent_update_li.sh 是"快速更新"（不重建镜像），
# 依赖靠步骤 [10] 逐容器增量 pip 安装到容器可写层；重建镜像后步骤 [10] 退化为
# 秒级满足性检查。
#
# 何时运行（定期，二选一触发）：
#   1. requirements.txt 变更后的下一次发布之前（deps 落镜像后，[10] 不再逐容器下载）
#   2. 每月定期一次（顺带刷新基础镜像的安全补丁层）
# 构建不产生停机：构建期间容器照常运行；下次 agent_update_li.sh 发布时
# --force-recreate 自动改用新镜像，无需手动重建容器。
#
# 预计耗时：30-60 分钟（requirements.txt 变更会使 venv 层及之后的 Playwright
# Chromium 层缓存失效重新下载；apt 层不变则命中缓存）。日志：
# /var/www/agent/app/log/image_build_<date>.log
#
# 用法：bash agent_rebuild_image_li.sh（禁止 sudo/root 运行，同更新脚本）
# ==============================================================================

if [ "$(id -u)" -eq 0 ]; then
    echo "错误：请勿用 sudo/root 运行本脚本。请直接以 ubuntu 用户执行：bash $0" >&2
    exit 1
fi

set -e

REPO_DIR="/var/www/agent/app"
cd "$REPO_DIR"

# 构建上下文安全检查：确认 .env 不会进镜像（.dockerignore 已排除，此处兜底校验）
if ! grep -qE '^\.env$' .dockerignore; then
    echo "错误：.dockerignore 未排除 .env，构建会把敏感配置打进镜像，中止。" >&2
    exit 1
fi

TS=$(date +%Y%m%d_%H%M%S)
BUILD_LOG="log/image_build_${TS}.log"
mkdir -p log

echo "构建前镜像：$(sudo docker images aid-agent-api --format '{{.ID}} {{.CreatedAt}}' | head -1)"
echo "日志：$REPO_DIR/$BUILD_LOG"

# --progress=plain：日志可读；构建缓存沿用服务器已有缓存（apt 层长期命中）
sudo docker build --progress=plain -t aid-agent-api:latest . > "$BUILD_LOG" 2>&1
BUILD_RC=$?

if [ "$BUILD_RC" -ne 0 ]; then
    echo "错误：镜像构建失败（exit=$BUILD_RC），详见 $BUILD_LOG 末尾：" >&2
    tail -30 "$BUILD_LOG" >&2
    exit "$BUILD_RC"
fi

echo "构建后镜像：$(sudo docker images aid-agent-api --format '{{.ID}} {{.Size}} {{.CreatedAt}}' | head -1)"

# 验证新镜像内依赖完整（对应 Dockerfile 关键校验 + 抽查新依赖）
sudo docker run --rm aid-agent-api:latest python -c "
import importlib.metadata as m
for pkg in ('apscheduler', 'redis', 'pdf2docx'):
    print(pkg, 'OK', m.version(pkg))
"

echo ""
echo "镜像重建完成。依赖已固化进镜像层；"
echo "下次 agent_update_li.sh 发布时 --force-recreate 自动启用新镜像，"
echo "步骤 [10] 的逐容器 pip 将退化为秒级满足性检查。"
