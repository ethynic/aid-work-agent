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
set -e

CONTAINER_NAME="aid-agent-api"

# Global scheduler tests must not scan a developer's shared database. This mode
# creates and initializes a disposable database before starting a fresh pytest.
TEST_ENTRY=(-m pytest)
ISOLATED_DB=0
if [[ "${1:-}" == "--isolated-db" ]]; then
  shift
  ISOLATED_DB=1
  TEST_ENTRY=(scripts/dev_test_isolated_db.py)
fi

RUN_IN_CONTAINER=0
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  RUN_IN_CONTAINER=1
fi

# An old running container can otherwise execute its unguarded business-DB
# conftest. Compare only the three safety entry files, without changing it.
if [[ ${RUN_IN_CONTAINER} -eq 1 ]]; then
  SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  if ! AID_TEST_SOURCE_HASHES="$(python "${SCRIPT_DIRECTORY}/testing_db_guard.py" --source-hashes)"; then
    echo 'dev_test.sh: host Python and current test-safety sources are required.' >&2
    exit 2
  fi
  export AID_TEST_SOURCE_HASHES
  if ! docker exec -i -e AID_TEST_SOURCE_HASHES "${CONTAINER_NAME}" python -c \
    'import hashlib,json,os,pathlib,sys; expected=json.loads(os.environ["AID_TEST_SOURCE_HASHES"]); ok=all(pathlib.Path(p).is_file() and hashlib.sha256(pathlib.Path(p).read_bytes().replace(b"\r\n",b"\n")).hexdigest()==h for p,h in expected.items()); sys.exit(0 if ok else 2)'; then
    echo 'dev_test.sh: container test-safety files differ from this checkout; use the current workspace. No tests or helpers started.' >&2
    exit 2
  fi
  if [[ ${ISOLATED_DB} -eq 1 ]]; then
    python "${SCRIPT_DIRECTORY}/testing_db_guard.py" --validate-admin || exit 2
    export DATABASE_URL
  else
    # Normal container tests are deliberately DB-free; never inherit the
    # container's business DSN. SQL tests use explicit --isolated-db instead.
    export DATABASE_URL=''
  fi
  export LOGS_DATABASE_URL=''
fi

STATUS=0
if [[ ${RUN_IN_CONTAINER} -eq 1 ]]; then
  docker exec -i -e DATABASE_URL -e LOGS_DATABASE_URL "${CONTAINER_NAME}" python "${TEST_ENTRY[@]}" "$@" || STATUS=$?
else
  exec python "${TEST_ENTRY[@]}" "$@"
fi
exit "${STATUS}"
