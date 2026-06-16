#!/bin/bash
# PostgreSQL 数据库恢复脚本
# 用途：将 backup/ 下的两个 .sql.gz 备份恢复到本地 aid-postgres 容器
# 用法：
#   ./restore_postgres.sh                  # 恢复两个库（默认 dry-run，需 --confirm 才执行）
#   ./restore_postgres.sh --confirm        # 实际执行恢复
#   ./restore_postgres.sh --confirm --prod # 只恢复生产库
#   ./restore_postgres.sh --confirm --test # 只恢复测试库
#   ./restore_postgres.sh --list           # 列出可用的备份文件
#
# 注意：恢复操作会 DROP 目标库并重建，恢复前请确认当前 .env 没有连接这些库

set -euo pipefail

# ==================== 配置 ====================
CONTAINER_NAME="aid-postgres"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DIR="${PROJECT_DIR}/backup"
LOG_DIR="${SCRIPT_DIR}/logs"

# 库与用户（与 init-postgres.sql / .env 保持一致）
PROD_DB="aid_work_agent"
PROD_USER="aid_user"
PROD_PASS="Aid_2026"

TEST_DB="aid_work_agent2"
TEST_USER="aid_user"   # 当前 .env 用 aid_user 跑测试库，保持一致
TEST_PASS="Aid_2026"

# 时间戳
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/restore_${TIMESTAMP}.log"

mkdir -p "${LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

# ==================== 参数解析 ====================
CONFIRM=0
DO_PROD=0
DO_TEST=0
LIST_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --confirm) CONFIRM=1; shift ;;
        --prod)    DO_PROD=1; shift ;;
        --test)    DO_TEST=1; shift ;;
        --list)    LIST_ONLY=1; shift ;;
        -h|--help)
            sed -n '2,12p' "$0"; exit 0 ;;
        *)
            echo "未知参数: $1" >&2; exit 1 ;;
    esac
done

# 默认两个都恢复
if [[ $DO_PROD -eq 0 && $DO_TEST -eq 0 ]]; then
    DO_PROD=1
    DO_TEST=1
fi

# ==================== 前置检查 ====================
check_prereqs() {
    command -v docker >/dev/null 2>&1 || { echo "未安装 docker" >&2; exit 1; }
    command -v zcat   >/dev/null 2>&1 || { echo "未安装 zcat"   >&2; exit 1; }

    if ! docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
        echo "未找到 docker 容器: ${CONTAINER_NAME}" >&2; exit 1
    fi
    if ! docker exec "${CONTAINER_NAME}" pg_isready -U "${PROD_USER}" >/dev/null 2>&1; then
        echo "容器 ${CONTAINER_NAME} 未就绪" >&2; exit 1
    fi
}

# ==================== 工具函数 ====================
# 终止目标库的所有连接（在 drop 前调用）
# 用目标库的 owner 登录 postgres 库执行（容器里没有 postgres 超级用户）
terminate_connections() {
    local db="$1" user="$2" pass="$3"
    log "  终止数据库 ${db} 的活跃连接..."
    docker exec "${CONTAINER_NAME}" \
        env PGPASSWORD="${pass}" \
        psql -U "${user}" -d postgres -v ON_ERROR_STOP=0 -c "
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '${db}' AND pid <> pg_backend_pid();" \
        >/dev/null 2>&1 || true
}

# drop 并重建数据库（恢复前清空）
# 用目标库的 owner 登录 postgres 库执行（owner 拥有该库，且通常带 Create DB 权限）
# 关键：DROP/CREATE DATABASE 不能在事务块里执行，所以用两个独立 -c 命令
recreate_db() {
    local db="$1" user="$2" pass="$3"
    log "  重建数据库 ${db}（DROP + CREATE）..."
    docker exec "${CONTAINER_NAME}" \
        env PGPASSWORD="${pass}" \
        psql -U "${user}" -d postgres -v ON_ERROR_STOP=1 \
            -c "DROP DATABASE IF EXISTS \"${db}\";" \
        >/dev/null
    docker exec "${CONTAINER_NAME}" \
        env PGPASSWORD="${pass}" \
        psql -U "${user}" -d postgres -v ON_ERROR_STOP=1 \
            -c "CREATE DATABASE \"${db}\" OWNER \"${user}\";" \
        >/dev/null
}

# 把 .sql.gz 解压后灌进目标库
restore_from_gz() {
    local gz_file="$1" db="$2" user_val="$3" pass_val="$4"

    log "  开始恢复: ${gz_file}"
    log "  目标数据库: ${db}（用户 ${user_val}）"

    # 流式过滤：本地镜像只装了 timescaledb，没有 timescaledb_toolkit，
    # 但备份里有 CREATE EXTENSION timescaledb_toolkit 会失败。
    # 备份里对 toolkit 的引用只有这两行（pg_dump 不导出 extension 内部对象），注释掉即可。
    # zcat | sed | psql；PG16 不识别 \restrict/\unrestrict 但会忽略（不影响导入）
    # 用 -v ON_ERROR_STOP=1 让真错误立刻中止
    zcat "${gz_file}" \
        | sed -E 's/^(CREATE[[:space:]]+EXTENSION[[:space:]]+IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+timescaledb_toolkit)/-- \1/i; s/^(COMMENT[[:space:]]+ON[[:space:]]+EXTENSION[[:space:]]+timescaledb_toolkit)/-- \1/i' \
        | docker exec -i "${CONTAINER_NAME}" \
            env PGPASSWORD="${pass_val}" \
            psql -U "${user_val}" -d "${db}" -v ON_ERROR_STOP=1 \
                 --no-psqlrc \
                 2> >(tee -a "${LOG_FILE}" >&2) \
        || { log "  恢复失败，详情见日志: ${LOG_FILE}"; return 1; }

    log "  恢复完成"
}

# 恢复后做一次基本校验
verify_restore() {
    local db="$1" user="$2" pass="$3"
    local table_count
    table_count=$(docker exec "${CONTAINER_NAME}" \
        env PGPASSWORD="${pass}" \
        psql -U "${user}" -d "${db}" -At -c \
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null)
    log "  校验: ${db} public schema 表数 = ${table_count}"
    if [[ -z "${table_count}" || "${table_count}" -eq 0 ]]; then
        log "  警告: ${db} 没有 public 表，恢复可能未生效"
    fi
}

# 执行单个库的恢复流程
do_restore() {
    local label="$1" db="$2" user="$3" pass="$4" gz_file="$5"

    log "==== [${label}] 恢复 ${db} ===="
    if [[ ! -f "${gz_file}" ]]; then
        log "  备份文件不存在: ${gz_file}"; return 1
    fi
    log "  备份文件: ${gz_file}（$(du -h "${gz_file}" | cut -f1)）"

    terminate_connections "${db}" "${user}" "${pass}"
    recreate_db "${db}" "${user}" "${pass}"

    restore_from_gz "${gz_file}" "${db}" "${user}" "${pass}"
    verify_restore "${db}" "${user}" "${pass}"
}

# ==================== 主流程 ====================
check_prereqs

# --list 模式：列出可用备份
if [[ $LIST_ONLY -eq 1 ]]; then
    echo "可用备份 (${BACKUP_DIR}):"
    ls -lh "${BACKUP_DIR}"/*.sql.gz 2>/dev/null || echo "  (无)"
    exit 0
fi

log "==== PostgreSQL 恢复任务开始 ===="
log "  容器: ${CONTAINER_NAME}"
log "  备份目录: ${BACKUP_DIR}"
log "  日志文件: ${LOG_FILE}"

# 解析最新的两个备份文件（按文件名前缀匹配）
PROD_GZ=$(ls -1t "${BACKUP_DIR}/aid_work_agent_"*.sql.gz 2>/dev/null | grep -v "aid_work_agent2_" | head -1 || true)
TEST_GZ=$(ls -1t "${BACKUP_DIR}/aid_work_agent2_"*.sql.gz 2>/dev/null | head -1 || true)

if [[ $DO_PROD -eq 1 && -z "${PROD_GZ:-}" ]]; then
    echo "找不到生产库备份: ${BACKUP_DIR}/aid_work_agent_*.sql.gz" >&2; exit 1
fi
if [[ $DO_TEST -eq 1 && -z "${TEST_GZ:-}" ]]; then
    echo "找不到测试库备份: ${BACKUP_DIR}/aid_work_agent2_*.sql.gz" >&2; exit 1
fi

echo ""
echo "将执行以下恢复操作："
[[ $DO_PROD -eq 1 ]] && echo "  生产库 ${PROD_DB}  <-  ${PROD_GZ}"
[[ $DO_TEST -eq 1 ]] && echo "  测试库 ${TEST_DB}  <-  ${TEST_GZ}"
echo ""
echo "注意：目标库将被 DROP 后重建，当前数据会丢失。"
echo ""

if [[ $CONFIRM -ne 1 ]]; then
    echo "当前为 dry-run 模式，未执行任何修改。"
    echo "确认要恢复，请加 --confirm 参数："
    echo "  $0 --confirm"
    exit 0
fi

[[ $DO_PROD -eq 1 ]] && do_restore "PROD" "${PROD_DB}" "${PROD_USER}" "${PROD_PASS}" "${PROD_GZ}"
[[ $DO_TEST -eq 1 ]] && do_restore "TEST" "${TEST_DB}" "${TEST_USER}" "${TEST_PASS}" "${TEST_GZ}"

log "==== 恢复任务结束 ===="
echo ""
echo "完成。日志: ${LOG_FILE}"
echo "下一步：检查 .env 中 DATABASE_URL 是否指向已恢复的库，必要时重启应用。"
