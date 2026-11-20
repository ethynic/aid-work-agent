#!/bin/bash
# PostgreSQL 数据库 + 附件 + 配置 每日备份脚本（聊易私有云 47.104.107.85 专用）
# 适配自 243 生产 backup_daily.sh（参照 deploy/backup_daily.sh）；私有云无异地容灾，仅本机保留
#
# 功能：
#   1. 主库 aid_work_agent 每日完整备份；日志库 aid_work_logs 每周日备份
#   2. 当日（前一天 00:00 至当天 00:00）新增附件打包备份
#   3. 恢复所需关键配置打包（.env、compose×3、nginx conf + SSL 证书）
#   4. 本地保留 30 天（/var/backups/agent/）
#
# Crontab：/etc/cron.d/backup-agent，每日 02:17
# 线上部署：/usr/local/bin/backup_daily_ly.sh（root:root 750）。本仓库文件名沿用
#   li 后缀，与线上的 ly 不一致；内容以线上为准（2026-11-20 已回灌）。
# 恢复流程参照 aid-work-agent deploy/backup.md §4（库名/路径按本机调整）

set -uo pipefail

CONTAINER_NAME="aid-postgres"
PROD_DB="aid_work_agent"
LOGS_DB="aid_work_logs"
DB_USER="aid_user"

BACKUP_ROOT="/var/backups/agent"

# 附件宿主机目录（.env: AGENT_RUNNER_*_HOST_DIR）
ATTACH_BASE="/var/www/agent"

RETENTION_DAYS=30
DATE="$(date +%Y%m%d)"
WEEKDAY="$(date +%u)"   # 1=周一 ... 7=周日

BACKUP_DB_DIR="${BACKUP_ROOT}/db"
BACKUP_FILES_DIR="${BACKUP_ROOT}/files"
BACKUP_CONFIG_DIR="${BACKUP_ROOT}/config"
BACKUP_LOG_DIR="${BACKUP_ROOT}/logs"

LOG_FILE="${BACKUP_LOG_DIR}/backup_${DATE}.log"
HAS_ERROR=0

mkdir -p "${BACKUP_DB_DIR}" "${BACKUP_FILES_DIR}" "${BACKUP_CONFIG_DIR}" "${BACKUP_LOG_DIR}"
chmod 700 "${BACKUP_ROOT}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

# ==================== 1. 数据库备份 ====================
log "========== 每日备份开始 =========="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "ERROR: 容器 ${CONTAINER_NAME} 未运行，跳过数据库备份"
    HAS_ERROR=1
else
    PROD_BACKUP="${BACKUP_DB_DIR}/${PROD_DB}_${DATE}.sql.gz"
    log "开始备份主库: ${PROD_DB}"
    if docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" "${PROD_DB}" | gzip > "${PROD_BACKUP}"; then
        log "主库备份成功: ${PROD_BACKUP} ($(du -h "${PROD_BACKUP}" | cut -f1))"
    else
        log "ERROR: 主库备份失败"
        rm -f "${PROD_BACKUP}"
        HAS_ERROR=1
    fi

    # 日志库每周日备份（对齐 243 惯例）
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
DAY_START="$(date -d 'yesterday' +'%Y-%m-%d 00:00:00')"
DAY_END="$(date +'%Y-%m-%d 00:00:00')"

FILE_LIST="${BACKUP_FILES_DIR}/.filelist_${DATE}.txt"
FILES_BACKUP="${BACKUP_FILES_DIR}/files_${DATE}.tar.gz"

if cd "${ATTACH_BASE}" 2>>"${LOG_FILE}"; then
    find agent_storage agent_uploads -type f \
        -newermt "${DAY_START}" ! -newermt "${DAY_END}" \
        ! -path "*/tmp/*" \
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
    log "ERROR: 无法进入附件目录 ${ATTACH_BASE}，跳过附件备份"
    HAS_ERROR=1
fi

# ==================== 3. 配置备份 ====================
CONFIG_BACKUP="${BACKUP_CONFIG_DIR}/config_${DATE}.tar.gz"
log "开始打包关键配置"
if tar -czf "${CONFIG_BACKUP}" \
    -C / \
    var/www/agent/app/.env \
    var/www/agent/postgres/.env \
    var/www/agent/app/docker-compose.main.yml \
    var/www/agent/app/deploy/liaoyi/docker-compose.agent-runner.yml \
    var/www/agent/postgres/docker-compose.postgres.yml \
    etc/nginx/sites-available/agent.0635easy.com.conf \
    etc/nginx/nginx.conf \
    var/www/agent/cert/agent.0635easy.com \
    var/www/agent/www_static \
    2>>"${LOG_FILE}"; then
    chmod 600 "${CONFIG_BACKUP}"
    log "配置备份成功: ${CONFIG_BACKUP} ($(du -h "${CONFIG_BACKUP}" | cut -f1))"
else
    log "ERROR: 配置打包失败"
    rm -f "${CONFIG_BACKUP}"
    HAS_ERROR=1
fi

# ==================== 4. 本地清理 ====================
DELETED="$(find "${BACKUP_DB_DIR}" "${BACKUP_CONFIG_DIR}" -name '*.gz' -mtime +${RETENTION_DAYS} -delete -print | wc -l)"
log "本地清理 ${RETENTION_DAYS} 天前过期备份 ${DELETED} 个"
find "${BACKUP_FILES_DIR}" -name 'files_*.tar.gz' -mtime +${RETENTION_DAYS} -delete
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
