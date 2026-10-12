#!/usr/bin/env bash
# 本地重放 data-analysis 对话：本地代码 + agent1(243) 仿真数据库
#
# 与容器重放（docker exec aid-agent-api1 python /app/scripts/e2e_dogfood_replay.py）的差别：
#   - 代码用本地工作区（改完即跑，无需 rsync 同步 agent1）
#   - 数据库经 SSH 隧道连 243 的 aid_work_agent1 / aid_work_logs1（同一仿真库，
#     chat_records 按 session_replay_<ts> 会话前缀区分，与容器重放互不污染）
#   - LLM 模型配置强制对齐 agent1 .env（成本对比要求同模型）
#   - 表源文件 /app/storage/... 由重放脚本内的路径 shim 映射到 <repo>/storage/ 镜像
#
# 前置（一次性）：
#   1. ssh 免密：ssh -p 10167 ubuntu@129.211.65.243
#   2. agent1 连接串存于 ~/.aid-work-agent/agent1_replay.env（DATABASE_URL/
#      LOGS_DATABASE_URL/LLM_PROVIDER/DEEPSEEK_MODEL_CODE/QWEN_MODEL_CODE/ZHIPU_MODEL_CODE，
#      从 243:/var/www/agent1/.env 摘取，chmod 600）
#   3. 表源文件镜像：<repo>/storage/tenants/c148f4efb4dc/data_sources/（从
#      243:/var/www/qb3_upload/agent_storage/tenants/... tar 拉取）
#
# 用法：
#   bash scripts/e2e_dogfood_replay_local.sh [--max-continuations 3] [--max-seconds 3600]
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${AGENT1_ENV_FILE:-$HOME/.aid-work-agent/agent1_replay.env}"

if [ ! -f "$ENV_FILE" ]; then
    echo "缺少 $ENV_FILE（agent1 的 DATABASE_URL 等配置，见脚本头注释）" >&2
    exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

# 经本地 SSH 隧道访问：容器内地址 172.17.0.9 换成 127.0.0.1（隧道 -L 10864）
export DATABASE_URL="${DATABASE_URL/172.17.0.9/127.0.0.1}"
export LOGS_DATABASE_URL="${LOGS_DATABASE_URL/172.17.0.9/127.0.0.1}"
# 模型配置对齐 agent1（覆盖本地 .env 的差异值，保证成本对比同模型）
export LLM_PROVIDER DEEPSEEK_MODEL_CODE QWEN_MODEL_CODE ZHIPU_MODEL_CODE
# 积分系数对齐 agent1（agent1 .env USAGE_FACTOR=50，config.yaml 默认 100；
# 不导出则本地按 2 倍系数计费，2026-10-12 实测 275.4 vs 折算 137.7）
export USAGE_FACTOR

# 隧道确保存活：10864 已监听则复用，否则临时建一条并在退出时回收
if ! python -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(('127.0.0.1',10864))==0 else 1)" 2>/dev/null; then
    echo "[tunnel] 10864 未监听，建立 SSH 隧道 -> 243..." >&2
    ssh -p 10167 -N -L 10864:172.17.0.9:10864 \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
        -o ExitOnForwardFailure=yes -o BatchMode=yes \
        ubuntu@129.211.65.243 &
    TUNNEL_PID=$!
    sleep 3
    if ! python -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(('127.0.0.1',10864))==0 else 1)" 2>/dev/null; then
        echo "[tunnel] 隧道建立失败（检查 ssh 免密）" >&2
        exit 1
    fi
fi

cd "$REPO_ROOT"
python scripts/e2e_dogfood_replay.py "$@"
RC=$?
if [ -n "${TUNNEL_PID:-}" ]; then
    kill "$TUNNEL_PID" 2>/dev/null || true
fi
exit $RC
