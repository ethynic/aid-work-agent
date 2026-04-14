#!/bin/bash
# PostgreSQL 数据库自动备份脚本
# 备份生产库和测试库，保留最近 30 天备份
# 使用方式: ./backup_postgres.sh
# Crontab: 0 1 * * * /path/to/deploy/backup_postgres.sh >> /path/to/deploy/logs/backup.log 2>&1

set -euo pipefail

# ==================== 配置 ====================
CONTAINER_NAME="aid-postgres"

# 生产库
PROD_DB="aid_work_agent"
PROD_USER="aid_user"

# 测试库
TEST_DB="aid_work_agent2"
TEST_USER="aid_user"

# 备份目录（脚本所在目录下的 backups 文件夹）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="${SCRIPT_DIR}/backups"
LOG_DIR="${SCRIPT_DIR}/logs"

# 备份保留天数
RETENTION_DAYS=30

# 时间戳
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DATE_DIR="$(date +%Y%m)"

# ==================== 初始化 ====================
mkdir -p "${BACKUP_DIR}/${DATE_DIR}"
mkdir -p "${LOG_DIR}"

LOG_FILE="${LOG_DIR}/backup_${TIMESTAMP}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

# ==================== 检查容器状态 ====================
log "========== 开始数据库备份 =========="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "ERROR: 容器 ${CONTAINER_NAME} 未运行，备份终止"
    exit 1
fi

log "容器 ${CONTAINER_NAME} 运行正常"

# ==================== 备份生产库 ====================
PROD_BACKUP="${BACKUP_DIR}/${DATE_DIR}/${PROD_DB}_${TIMESTAMP}.sql.gz"
log "开始备份生产库: ${PROD_DB}"
if docker exec "${CONTAINER_NAME}" pg_dump -U "${PROD_USER}" "${PROD_DB}" | gzip > "${PROD_BACKUP}"; then
    PROD_SIZE="$(du -h "${PROD_BACKUP}" | cut -f1)"
    log "生产库备份成功: ${PROD_BACKUP} (${PROD_SIZE})"
else
    log "ERROR: 生产库备份失败"
    # 不退出，继续备份测试库
fi

# ==================== 备份测试库 ====================
TEST_BACKUP="${BACKUP_DIR}/${DATE_DIR}/${TEST_DB}_${TIMESTAMP}.sql.gz"
log "开始备份测试库: ${TEST_DB}"
if docker exec "${CONTAINER_NAME}" pg_dump -U "${TEST_USER}" "${TEST_DB}" | gzip > "${TEST_BACKUP}"; then
    TEST_SIZE="$(du -h "${TEST_BACKUP}" | cut -f1)"
    log "测试库备份成功: ${TEST_BACKUP} (${TEST_SIZE})"
else
    log "ERROR: 测试库备份失败"
fi

# ==================== 清理过期备份 ====================
log "清理 ${RETENTION_DAYS} 天前的过期备份..."
DELETED="$(find "${BACKUP_DIR}" -name "*.sql.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)"
log "已清理 ${DELETED} 个过期备份文件"

# ==================== 汇总 ====================
TOTAL_SIZE="$(du -sh "${BACKUP_DIR}" | cut -f1)"
TOTAL_FILES="$(find "${BACKUP_DIR}" -name "*.sql.gz" | wc -l)"
log "备份目录总大小: ${TOTAL_SIZE}，共 ${TOTAL_FILES} 个备份文件"
log "========== 备份完成 =========="
