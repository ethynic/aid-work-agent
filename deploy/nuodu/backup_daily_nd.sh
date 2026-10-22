#!/bin/bash
# PostgreSQL 数据库 + 附件 + 配置 每日备份脚本（济南诺度私有云 139.129.48.30 专用）
# 基于 deploy/backup_daily.sh（243 生产版）适配，差异：
#   1. 诺度无异地 NAS 目标，备份仅落本机 /var/backups/agent/；后续接入阿里云 OSS 等
#      异地目标时，在第 4 节预留位补传输逻辑
#   2. 备份对象按诺度目录适配（app/.env、docker-compose.main.yml、PG 独立 compose、
#      宝塔 nginx conf、自管证书 /var/www/agent/cert/）
#   3. 保留策略：db/配置/附件日增量本地保留 30 天；附件全量基线永久保留
#
# 首次部署需手动做一次附件全量基线（此后每日 tar 均为增量）：
#   sudo mkdir -p /var/backups/agent/files_baseline
#   sudo tar -czf /var/backups/agent/files_baseline/files_baseline_$(date +%Y%m%d).tar.gz \
#     -C /var/www/agent \
#     --exclude='agent_storage/tmp/*' --exclude='agent_uploads/tmp/*' \
#     agent_storage agent_uploads
#
# Crontab（root）：
#   30 1 * * * /var/www/agent/app/deploy/nuodu/backup_daily_nd.sh >> /var/backups/agent/logs/cron.log 2>&1

set -uo pipefail

# ==================== 配置 ====================
PROJECT_DIR="/var/www/agent"
CONTAINER_NAME="aid-postgres"

PROD_DB="aid_work_agent"
LOGS_DB="aid_work_logs"
DB_USER="aid_user"

# 本地备份根目录（诺度无异地目标，仅本机）
BACKUP_ROOT="/var/backups/agent"

# 附件目录（agent_storage / agent_uploads 的父目录）
ATTACH_DIR="${PROJECT_DIR}"

# 保留天数（附件基线 files_baseline 永久保留，不参与清理）
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

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

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
    find agent_storage agent_uploads -type f \
        -newermt "${DAY_START}" ! -newermt "${DAY_END}" \
        ! -path 'agent_storage/tmp/*' ! -path 'agent_uploads/tmp/*' \
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
    var/www/agent/app/.env \
    var/www/agent/app/docker-compose.main.yml \
    var/www/agent/app/configs/config.yaml \
    var/www/agent/postgres/docker-compose.postgres.yml \
    var/www/agent/postgres/.env \
    var/www/agent/cert \
    www/server/panel/vhost/nginx/agent.jzzysd.com.conf \
    agent_update_nd.sh \
    2>>"${LOG_FILE}"; then
    log "配置备份成功: ${CONFIG_BACKUP} ($(du -h "${CONFIG_BACKUP}" | cut -f1))"
else
    log "ERROR: 配置打包失败"
    rm -f "${CONFIG_BACKUP}"
    HAS_ERROR=1
fi

# ==================== 4. 异地备份（预留） ====================
# 诺度当前无异地 NAS/OSS 目标；接入后在此处补传输逻辑（参照 243 版 rsync 段）
log "无异地备份目标，跳过传输（诺度仅本机保留）"

# ==================== 5. 本地清理 ====================
DELETED="$(find "${BACKUP_DB_DIR}" "${BACKUP_CONFIG_DIR}" "${BACKUP_FILES_DIR}" \
    -name '*.gz' -mtime +${RETENTION_DAYS} -delete -print | wc -l)"
log "本地清理 ${RETENTION_DAYS} 天前过期备份 ${DELETED} 个（附件基线不清理）"
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
