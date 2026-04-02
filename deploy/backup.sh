#!/bin/bash

# ============================================
# AID Work Agent 数据备份脚本
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 项目根目录
PROJECT_DIR="/var/www/agent"

# 备份目录
BACKUP_ROOT="/var/backups/agent"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_ROOT/$DATE"

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

echo "========================================"
echo "  AID Work Agent 数据备份"
echo "========================================"
echo ""

# 创建备份目录
log_info "创建备份目录: $BACKUP_DIR"
mkdir -p $BACKUP_DIR

# 1. 备份配置文件
log_info "备份配置文件..."
if [ -f "$PROJECT_DIR/.env" ]; then
    cp $PROJECT_DIR/.env $BACKUP_DIR/
    log_success ".env 文件已备份"
else
    log_error ".env 文件不存在"
fi

# 2. 备份数据目录
log_info "备份上传文件..."
if [ -d "/var/www/qb3_upload/agent_uploads" ]; then
    tar -czf $BACKUP_DIR/uploads.tar.gz -C /var/www/qb3_upload agent_uploads
    log_success "上传文件已备份"
else
    log_error "上传文件目录不存在"
fi

log_info "备份记忆文件..."
if [ -d "/var/www/qb3_upload/agent_memories" ]; then
    tar -czf $BACKUP_DIR/memories.tar.gz -C /var/www/qb3_upload agent_memories
    log_success "记忆文件已备份"
else
    log_error "记忆文件目录不存在"
fi

# 3. 备份日志文件
log_info "备份日志文件..."
if [ -d "$PROJECT_DIR/log" ]; then
    tar -czf $BACKUP_DIR/log.tar.gz -C $PROJECT_DIR log
    log_success "日志文件已备份"
else
    log_error "日志目录不存在"
fi

# 4. 备份数据库（如果使用）
# log_info "备份数据库..."
# docker exec aid-agent-api python -c "from src.config.database import backup_database; backup_database()" 2>/dev/null || true

# 5. 创建备份清单
log_info "创建备份清单..."
cat > $BACKUP_DIR/manifest.txt <<EOF
备份时间: $(date '+%Y-%m-%d %H:%M:%S')
备份目录: $BACKUP_DIR
备份内容:
  - .env (配置文件)
  - uploads.tar.gz (上传文件)
  - memories.tar.gz (记忆文件)
  - log.tar.gz (日志文件)

文件列表:
$(ls -lh $BACKUP_DIR)

磁盘使用:
$(df -h $BACKUP_ROOT)
EOF

log_success "备份清单已创建"

# 6. 压缩备份目录
log_info "压缩备份文件..."
cd $BACKUP_ROOT
tar -czf ${DATE}.tar.gz $DATE
rm -rf $DATE

# 7. 清理旧备份（保留最近7天的备份）
log_info "清理旧备份..."
find $BACKUP_ROOT -name "*.tar.gz" -mtime +7 -delete
log_success "旧备份已清理"

# 完成
echo ""
echo "========================================"
log_success "备份完成！"
echo "========================================"
echo ""
echo "备份文件: $BACKUP_ROOT/${DATE}.tar.gz"
echo "备份大小: $(du -h $BACKUP_ROOT/${DATE}.tar.gz | cut -f1)"
echo ""
echo "恢复命令:"
echo "  tar -xzf $BACKUP_ROOT/${DATE}.tar.gz -C /tmp"
echo "  cp /tmp/$DATE/.env $PROJECT_DIR/"
echo ""
