#!/usr/bin/env bash
# 统一测试入口：自动探测运行环境
#
# 优先在 docker 容器 aid-agent-api 内执行（依赖、PostgreSQL 连接池、Redis、skill 加载链路都在容器里）；
# 容器未运行时降级到宿主机 python（同事本地直跑环境）。
#
# 用法：
#   ./scripts/dev_test.sh tests/unit/test_message_recall.py -p no:cacheprovider -q
#   ./scripts/dev_test.sh -m tools

set -e

CONTAINER_NAME="aid-agent-api"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  exec docker exec -i "${CONTAINER_NAME}" python -m pytest "$@"
else
  exec python -m pytest "$@"
fi
