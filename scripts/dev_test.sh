#!/usr/bin/env bash
# 统一测试入口：自动探测运行环境
#
# 优先在 docker 容器 aid-agent-api 内执行（依赖、PostgreSQL 连接池、Redis、skill 加载链路都在容器里）；
# 容器未运行时降级到宿主机 python（同事本地直跑环境）。
#
# 用法:
#   ./scripts/dev_test.sh tests/unit/test_message_recall.py -p no:cacheprovider -q
#   ./scripts/dev_test.sh -m tools
#
# 默认排除 e2e 和 real_browser 测试（pytest.ini addopts）。
# real_browser 测试（真实浏览器/Node renderer，极慢且容器内可能 hang）单独运行：
#   ./scripts/dev_test.sh -m real_browser -p no:cacheprovider -q
# （CLI 的 -m 会覆盖 addopts 中的 -m）
#
# real_browser 测试中的 browser_redis fixture
# （tests/integration/agent_runner_service/browser_io.py）期望一个已运行的
# 任务专属 Redis helper（容器内 127.0.0.1，端口取 AID_TEST_BROWSER_REDIS_PORT，
# 默认 44635，appendonly no / save ''，fixture 不自行启动）。容器模式下本脚本
# 在调用参数选中 real_browser 测试时按需供给该 helper：一次性 sidecar 容器共享
# aid-agent-api 网络命名空间，测试结束即删除，不重启或修改任何既有容器；
# 外部已提供的可达 helper 直接复用、不接管清理。宿主机直跑模式不供给
# （fixture 报 'Task-owned real Redis helper is unavailable'，与原行为一致）。

set -e

CONTAINER_NAME="aid-agent-api"
BROWSER_REDIS_HELPER="aid-test-browser-redis"
BROWSER_REDIS_PORT="${AID_TEST_BROWSER_REDIS_PORT:-44635}"

# Global scheduler tests must not scan a developer's shared database. This mode
# creates and initializes a disposable database before starting a fresh pytest.
TEST_ENTRY=(-m pytest)
if [[ "${1:-}" == "--isolated-db" ]]; then
  shift
  TEST_ENTRY=(scripts/dev_test_isolated_db.py)
fi

RUN_IN_CONTAINER=0
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  RUN_IN_CONTAINER=1
fi

# Any pytest argument mentioning real_browser selects those tests (pytest.ini
# excludes them by default), so the helper is wanted exactly then. Supplying it
# for a stray `not real_browser` selection is harmless: nothing connects to it.
HELPER_WANTED=0
HELPER_STARTED=0
if [[ ${RUN_IN_CONTAINER} -eq 1 ]]; then
  for arg in "$@"; do
    case "${arg}" in *real_browser*) HELPER_WANTED=1 ;; esac
  done
fi

helper_reachable() {
  docker exec "${CONTAINER_NAME}" python -c \
    "import redis; c = redis.Redis(host='127.0.0.1', port=${BROWSER_REDIS_PORT}, db=0, socket_connect_timeout=1, socket_timeout=1); exit(0 if c.ping() else 1)" \
    >/dev/null 2>&1
}

start_helper() {
  if helper_reachable; then
    return 0  # Externally provided helper: reuse as-is, never adopt cleanup.
  fi
  if [[ ! "${BROWSER_REDIS_PORT}" =~ ^[0-9]+$ ]] || [[ "${BROWSER_REDIS_PORT}" == "6379" ]]; then
    echo "dev_test.sh: AID_TEST_BROWSER_REDIS_PORT must be a non-default numeric port (got '${BROWSER_REDIS_PORT}')" >&2
    exit 2
  fi
  if ! docker image inspect redis:7-alpine >/dev/null 2>&1; then
    echo "dev_test.sh: image redis:7-alpine is required for the task-owned Redis helper (real_browser tests); pull it first" >&2
    exit 2
  fi
  # The fixed helper name belongs to this disposable sidecar only; a leftover
  # from an aborted run (docker --rm cannot clean up after SIGKILL) is replaced.
  docker rm -f "${BROWSER_REDIS_HELPER}" >/dev/null 2>&1 || true
  echo "dev_test.sh: supplying disposable task-owned Redis helper on 127.0.0.1:${BROWSER_REDIS_PORT} (sidecar of container ${CONTAINER_NAME})"
  docker run -d --rm --name "${BROWSER_REDIS_HELPER}" \
    --network "container:${CONTAINER_NAME}" \
    redis:7-alpine redis-server --port "${BROWSER_REDIS_PORT}" --appendonly no --save "" >/dev/null
  for _ in $(seq 1 20); do
    if helper_reachable; then
      HELPER_STARTED=1
      return 0
    fi
    sleep 0.5
  done
  docker rm -f "${BROWSER_REDIS_HELPER}" >/dev/null 2>&1 || true
  echo "dev_test.sh: task-owned Redis helper did not become reachable on port ${BROWSER_REDIS_PORT}" >&2
  exit 2
}

stop_helper() {
  # Only a helper this script started is cleaned up; an externally provided
  # one keeps its owner's lifecycle.
  if [[ ${HELPER_STARTED} -eq 1 ]]; then
    docker rm -f "${BROWSER_REDIS_HELPER}" >/dev/null 2>&1 || true
  fi
}

STATUS=0
if [[ ${RUN_IN_CONTAINER} -eq 1 ]]; then
  if [[ ${HELPER_WANTED} -eq 1 ]]; then
    start_helper
  fi
  docker exec -i "${CONTAINER_NAME}" python "${TEST_ENTRY[@]}" "$@" || STATUS=$?
  if [[ ${HELPER_WANTED} -eq 1 ]]; then
    stop_helper
  fi
else
  exec python "${TEST_ENTRY[@]}" "$@"
fi
exit "${STATUS}"
