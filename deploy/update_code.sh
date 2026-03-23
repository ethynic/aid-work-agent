#!/bin/bash

# ============================================
# AID Work Agent 代码更新脚本
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

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

echo "========================================"
echo "  AID Work Agent 代码更新"
echo "========================================"
echo ""

cd $PROJECT_DIR

# 1. 备份当前版本
log_info "步骤 1/5: 备份当前配置..."
BACKUP_DIR="/tmp/agent_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p $BACKUP_DIR
cp .env $BACKUP_DIR/ 2>/dev/null || true
cp -r logs $BACKUP_DIR/ 2>/dev/null || true
log_success "配置已备份到: $BACKUP_DIR"

# 2. 前端更新
log_info "步骤 2/5: 更新前端..."
if [ -d "frontend" ]; then
    cd frontend
    
    # 检查是否需要安装依赖
    if [ "package.json" -nt "node_modules" ] 2>/dev/null; then
        log_info "检测到依赖更新，重新安装..."
        npm install
    fi
    
    # 构建前端
    npm run build
    
    if [ -d "dist" ]; then
        log_success "前端构建完成"
    else
        log_error "前端构建失败"
        exit 1
    fi
    cd ..
else
    log_warning "frontend 目录不存在，跳过前端更新"
fi

# 3. 后端更新（通过 volume 挂载自动生效）
log_info "步骤 3/5: 更新后端..."
log_success "后端代码已通过 volume 挂载，无需手动更新"

# 4. 重启服务
log_info "步骤 4/5: 重启服务..."
read -p "是否重启 Docker 容器？(y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker-compose -f docker-compose.prod.yml restart aid-agent-api
    log_success "容器已重启"
    
    # 等待容器启动
    log_info "等待容器启动..."
    sleep 10
    
    # 检查容器状态
    if docker ps | grep -q "aid-agent-api"; then
        log_success "容器运行正常"
    else
        log_error "容器启动失败"
        docker-compose -f docker-compose.prod.yml logs --tail 50
        exit 1
    fi
else
    log_warning "跳过容器重启，后端代码更新将在下次重启时生效"
fi

# 5. 健康检查
log_info "步骤 5/5: 健康检查..."
RETRY_COUNT=0
MAX_RETRY=3

while [ $RETRY_COUNT -lt $MAX_RETRY ]; do
    API_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null)
    
    if [ "$API_RESPONSE" = "200" ]; then
        log_success "健康检查通过 (HTTP $API_RESPONSE)"
        break
    else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -lt $MAX_RETRY ]; then
            log_warning "健康检查失败，重试中... ($RETRY_COUNT/$MAX_RETRY)"
            sleep 5
        else
            log_error "健康检查失败 (HTTP $API_RESPONSE)"
            docker-compose -f docker-compose.prod.yml logs --tail 100
            exit 1
        fi
    fi
done

# 完成
echo ""
echo "========================================"
log_success "代码更新完成！"
echo "========================================"
echo ""
echo "备份位置: $BACKUP_DIR"
echo ""
echo "如需回滚，请执行："
echo "  cp $BACKUP_DIR/.env $PROJECT_DIR/.env"
echo "  docker-compose -f docker-compose.prod.yml restart"
echo ""
