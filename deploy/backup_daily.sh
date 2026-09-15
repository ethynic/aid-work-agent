#!/bin/bash
# PostgreSQL 数据库 + 附件 + 配置 每日备份脚本（生产服务器 129.211.65.243 专用）
#
# 功能：
#   1. 主库 aid_work_agent 每日完整备份；日志库 aid_work_logs 每周日备份
#   2. 当日（前一天 00:00 至当天 00:00）新上传附件打包备份
#   3. 恢复所需关键配置打包（.env、docker-compose、gunicorn、nginx）
#   4. 全部备份 rsync 到旧服务器 124.222.3.254（NAS 盘）异地存放
#   5. 本地与远端数据库/配置备份保留 30 天；附件远端永久保留
#
# 首次部署时需手动做一次附件全量基线同步（此后每日 tar 均为增量）：
#   sudo rsync -az --exclude 'tmp' -e "ssh -i /root/.ssh/id_backup_ed25519" \
#     /var/www/qb3_upload/agent_storage/ \
#     ubuntu@124.222.3.254:/var/www/qb3_upload/backup_from_243/files_baseline/agent_storage/
#
# Crontab（root）：
#   30 1 * * * /var/www/agent/deploy/backup_daily.sh >> /var/backups/agent/logs/cron.log 2>&1

set -uo pipefail

# ==================== 配置 ====================
PROJECT_DIR="/var/www/agent"
CONTAINER_NAME="aid-postgres"

PROD_DB="aid_work_agent"
LOGS_DB="aid_work_logs"
DB_USER="aid_user"

# 本地备份根目录
BACKUP_ROOT="/var/backups/agent"

# 附件目录（挂载进容器的宿主机路径）
ATTACH_DIR="/var/www/qb3_upload"
# 打包时排除的附件子目录
ATTACH_EXCLUDE="agent_storage/tmp"

# 异地备份（旧服务器，NAS 盘）
REMOTE_HOST="ubuntu@124.222.3.254"
REMOTE_KEY="/root/.ssh/id_backup_ed25519"
REMOTE_PORT="22"
REMOTE_BASE="/var/www/qb3_upload/backup_from_243"

# 保留天数（附件异地永久保留，不参与清理）
RETENTION_DAYS=30

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DATE="$(date +%Y%m%d)"
WEEKDAY="$(date +%u)"   # 1=周一 ... 7=周日

BACKUP_DB_DIR="${BACKUP_ROOT}/db"
BACKUP_FILES_DIR="${BACKUP_ROOT}/files"
BACKUP_CONFIG_DIR="${BACKUP_ROOT}/config"
BACKUP_LOG_DIR="${BACKUP_ROOT}/logs"

LOG_FILE="${BACKUP_LOG_DIR}/backup_${DATE}.log"
HAS_ERROR=0

mkdir -p "${BACKUP_DB_DIR}" "${BACKUP_FILES_DIR}" "${BACKUP_CONFIG_DIR}" "${BACKUP_LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -p "${REMOTE_PORT}" -i "${REMOTE_KEY}")

# ==================== 1. 数据库备份 ====================
log "========== 每日备份开始 =========="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "ERROR: 容器 ${CONTAINER_NAME} 未运行，跳过数据库备份"
    HAS_ERROR=1
else
    # 主库每日备份
    PROD_BACKUP="${BACKUP_DB_DIR}/${PROD_DB}_${DATE}.sql.gz"
    log "开始备份主库: ${PROD_DB}"
    if docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" "${PROD_DB}" | gzip > "${PROD_BACKUP}"; then
        log "主库备份成功: ${PROD_BACKUP} ($(du -h "${PROD_BACKUP}" | cut -f1))"
    else
        log "ERROR: 主库备份失败"
        rm -f "${PROD_BACKUP}"
        HAS_ERROR=1
    fi

    # 日志库每周日备份
    if [ "${WEEKDAY}" = "7" ]; then
        LOGS_BACKUP="${BACKUP_DB_DIR}/${LOGS_DB}_${DATE}.sql.gz"
        log "今日周日，开始备份日志库: ${LOGS_DB}"
        if docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" "${LOGS_DB}" | gzip > "${LOGS_BACKUP}"; then
            log "日志库备份成功: ${LOGS_BACKUP} ($(du -h "${LOGS_BACKUP}" | cut -f1))"
        else
            log "ERROR: 日志库备份失败"
            rm -f "${LOGS_BACKUP}"
            HAS_ERROR=1
        fi
    fi
fi

# ==================== 2. 当日新增附件备份 ====================
# 统计范围：前一天 00:00 至当天 00:00（脚本在凌晨 1:30 执行，覆盖完整的一天）
DAY_START="$(date -d 'yesterday' +'%Y-%m-%d 00:00:00')"
DAY_END="$(date +'%Y-%m-%d 00:00:00')"

FILE_LIST="${BACKUP_FILES_DIR}/.filelist_${DATE}.txt"
FILES_BACKUP="${BACKUP_FILES_DIR}/files_${DATE}.tar.gz"

if cd "${ATTACH_DIR}" 2>>"${LOG_FILE}"; then
    # shellcheck disable=SC2086
    find agent_storage agent_uploads -type f \
        -newermt "${DAY_START}" ! -newermt "${DAY_END}" \
        ! -path "${ATTACH_EXCLUDE%%/*}/tmp/*" \
        > "${FILE_LIST}" 2>/dev/null || true

    FILE_COUNT=$(wc -l < "${FILE_LIST}")

    if [ "${FILE_COUNT}" -gt 0 ]; then
        log "发现当日新增/修改文件 ${FILE_COUNT} 个，开始打包"
        if tar -czf "${FILES_BACKUP}" -T "${FILE_LIST}" 2>>"${LOG_FILE}"; then
            log "附件备份成功: ${FILES_BACKUP} ($(du -h "${FILES_BACKUP}" | cut -f1))"
        else
            log "ERROR: 附件打包失败"
            rm -f "${FILES_BACKUP}"
            HAS_ERROR=1
        fi
    else
        log "当日无新增附件，跳过打包"
    fi
    rm -f "${FILE_LIST}"
    cd - > /dev/null
else
    log "ERROR: 无法进入附件目录 ${ATTACH_DIR}，跳过附件备份"
    HAS_ERROR=1
fi

# ==================== 3. 配置备份 ====================
CONFIG_BACKUP="${BACKUP_CONFIG_DIR}/config_${DATE}.tar.gz"
log "开始打包关键配置"
if tar -czf "${CONFIG_BACKUP}" \
    -C / \
    var/www/agent/.env \
    var/www/agent/docker-compose.prod.yml \
    var/www/agent/deploy/gunicorn.conf.py \
    var/www/agent/deploy/agent_update.sh \
    etc/nginx/conf.d/agent.aidingyi.cn.conf \
    etc/nginx/conf.d/SSL \
    2>>"${LOG_FILE}"; then
    log "配置备份成功: ${CONFIG_BACKUP} ($(du -h "${CONFIG_BACKUP}" | cut -f1))"
else
    log "ERROR: 配置打包失败"
    rm -f "${CONFIG_BACKUP}"
    HAS_ERROR=1
fi

# ==================== 4. 传输到旧服务器 ====================
log "开始传输备份到 ${REMOTE_HOST}:${REMOTE_BASE}"
if ssh "${SSH_OPTS[@]}" "${REMOTE_HOST}" "mkdir -p ${REMOTE_BASE}/{db,files,config}" 2>>"${LOG_FILE}"; then
    RSYNC_SSH="ssh ${SSH_OPTS[*]}"

    if rsync -az --update --timeout=600 -e "${RSYNC_SSH}" \
        "${BACKUP_DB_DIR}/" \
        "${REMOTE_HOST}:${REMOTE_BASE}/db/" 2>>"${LOG_FILE}"; then
        log "数据库备份传输完成"
    else
        log "ERROR: 数据库备份传输失败"
        HAS_ERROR=1
    fi

    if rsync -az --update --timeout=600 -e "${RSYNC_SSH}" \
        "${BACKUP_FILES_DIR}/" "${REMOTE_HOST}:${REMOTE_BASE}/files/" 2>>"${LOG_FILE}"; then
        log "附件备份传输完成"
    else
        log "ERROR: 附件备份传输失败"
        HAS_ERROR=1
    fi

    if rsync -az --update --timeout=600 -e "${RSYNC_SSH}" \
        "${BACKUP_CONFIG_DIR}/" "${REMOTE_HOST}:${REMOTE_BASE}/config/" 2>>"${LOG_FILE}"; then
        log "配置备份传输完成"
    else
        log "ERROR: 配置备份传输失败"
        HAS_ERROR=1
    fi

    # 远端清理：数据库/配置保留 30 天，附件永久保留
    ssh "${SSH_OPTS[@]}" "${REMOTE_HOST}" \
        "find ${REMOTE_BASE}/db -name '*.sql.gz' -mtime +${RETENTION_DAYS} -delete; \
         find ${REMOTE_BASE}/config -name '*.tar.gz' -mtime +${RETENTION_DAYS} -delete" \
        2>>"${LOG_FILE}" || log "WARN: 远端清理失败（不影响备份）"
else
    log "ERROR: 无法连接旧服务器 ${REMOTE_HOST}，本次备份未异地传输"
    HAS_ERROR=1
fi

# ==================== 5. 本地清理 ====================
DELETED="$(find "${BACKUP_DB_DIR}" "${BACKUP_CONFIG_DIR}" -name '*.gz' -mtime +${RETENTION_DAYS} -delete -print | wc -l)"
log "本地清理 ${RETENTION_DAYS} 天前过期备份 ${DELETED} 个"
find "${BACKUP_LOG_DIR}" -name 'backup_*.log' -mtime +${RETENTION_DAYS} -delete

# ==================== 汇总 ====================
TOTAL_SIZE="$(du -sh "${BACKUP_ROOT}" | cut -f1)"
log "本地备份目录总大小: ${TOTAL_SIZE}"
if [ "${HAS_ERROR}" -eq 0 ]; then
    log "========== 每日备份完成（全部成功） =========="
else
    log "========== 每日备份完成（存在错误，请检查 ERROR 行） =========="
fi
exit "${HAS_ERROR}"
