#!/bin/bash

# ============================================
# AID Work Agent 部署检查脚本
# ============================================

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
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

# 检查结果统计
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0

# 检查函数
check() {
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ $? -eq 0 ]; then
        log_success "$1"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        log_error "$1"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
        return 1
    fi
}

echo "========================================"
echo "  AID Work Agent 部署检查"
echo "========================================"
echo ""

# 1. 检查项目目录
log_info "检查项目目录..."
if [ -d "$PROJECT_DIR" ]; then
    log_success "项目目录存在: $PROJECT_DIR"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "项目目录不存在: $PROJECT_DIR"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 2. 检查配置文件
log_info "检查配置文件..."
if [ -f "$PROJECT_DIR/.env" ]; then
    log_success ".env 配置文件存在"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error ".env 配置文件不存在"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 3. 检查前端构建
log_info "检查前端构建..."
if [ -d "$PROJECT_DIR/frontend/dist" ]; then
    if [ "$(ls -A $PROJECT_DIR/frontend/dist 2>/dev/null)" ]; then
        log_success "前端构建文件存在"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        log_error "前端 dist 目录为空"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
else
    log_error "前端 dist 目录不存在"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 4. 检查 Docker 容器
log_info "检查 Docker 容器..."
cd $PROJECT_DIR

if docker ps | grep -q "aid-agent-api"; then
    log_success "后端容器正在运行"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "后端容器未运行"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 5. 检查容器健康状态
log_info "检查容器健康状态..."
HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' aid-agent-api 2>/dev/null)
if [ "$HEALTH_STATUS" = "healthy" ]; then
    log_success "容器健康状态: $HEALTH_STATUS"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
elif [ "$HEALTH_STATUS" = "starting" ]; then
    log_warning "容器健康状态: $HEALTH_STATUS (正在启动)"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "容器健康状态: $HEALTH_STATUS"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 6. 检查后端API健康
log_info "检查后端API健康..."
API_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null)
if [ "$API_RESPONSE" = "200" ]; then
    log_success "后端API健康检查通过 (HTTP $API_RESPONSE)"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "后端API健康检查失败 (HTTP $API_RESPONSE)"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 7. 检查端口监听
log_info "检查端口监听..."
if sudo netstat -tlnp 2>/dev/null | grep -q ":8000"; then
    log_success "端口 8000 正在监听"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "端口 8000 未监听"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 8. 检查 Nginx 配置
log_info "检查 Nginx 配置..."
if sudo nginx -t 2>&1 | grep -q "successful"; then
    log_success "Nginx 配置正确"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "Nginx 配置错误"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 9. 检查 Nginx 运行状态
log_info "检查 Nginx 运行状态..."
if sudo systemctl is-active nginx | grep -q "active"; then
    log_success "Nginx 正在运行"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "Nginx 未运行"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 10. 检查上传目录
log_info "检查上传目录..."
if [ -d "/var/www/qb3_upload/agent_uploads" ]; then
    if [ -w "/var/www/qb3_upload/agent_uploads" ]; then
        log_success "上传目录存在且可写"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        log_error "上传目录存在但不可写"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
else
    log_error "上传目录不存在"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 11. 检查记忆目录
log_info "检查记忆目录..."
if [ -d "/var/www/qb3_upload/agent_memories" ]; then
    if [ -w "/var/www/qb3_upload/agent_memories" ]; then
        log_success "记忆目录存在且可写"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        log_error "记忆目录存在但不可写"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
else
    log_error "记忆目录不存在"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 12. 检查 HTTPS 访问
log_info "检查 HTTPS 访问..."
HTTPS_RESPONSE=$(curl -s -k -o /dev/null -w "%{http_code}" https://agent.aidingyi.cn 2>/dev/null)
if [ "$HTTPS_RESPONSE" = "200" ] || [ "$HTTPS_RESPONSE" = "301" ]; then
    log_success "HTTPS 访问正常 (HTTP $HTTPS_RESPONSE)"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_warning "HTTPS 访问异常 (HTTP $HTTPS_RESPONSE) - 可能是DNS未解析或证书问题"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 13. 检查日志目录
log_info "检查日志目录..."
if [ -d "$PROJECT_DIR/logs" ]; then
    if [ -w "$PROJECT_DIR/logs" ]; then
        log_success "日志目录存在且可写"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        log_error "日志目录存在但不可写"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
else
    log_warning "日志目录不存在"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 14. 检查磁盘空间
log_info "检查磁盘空间..."
DISK_USAGE=$(df -h $PROJECT_DIR | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 90 ]; then
    log_success "磁盘空间充足 (使用率: ${DISK_USAGE}%)"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
elif [ "$DISK_USAGE" -lt 95 ]; then
    log_warning "磁盘空间紧张 (使用率: ${DISK_USAGE}%)"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_error "磁盘空间不足 (使用率: ${DISK_USAGE}%)"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 15. 检查容器资源使用
log_info "检查容器资源使用..."
CONTAINER_STATS=$(docker stats --no-stream aid-agent-api 2>/dev/null)
if [ -n "$CONTAINER_STATS" ]; then
    CPU_USAGE=$(echo "$CONTAINER_STATS" | tail -1 | awk '{print $3}' | sed 's/%//')
    MEM_USAGE=$(echo "$CONTAINER_STATS" | tail -1 | awk '{print $4}' | sed 's/%//')
    log_success "容器资源使用 - CPU: ${CPU_USAGE}%, 内存: ${MEM_USAGE}%"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    log_warning "无法获取容器资源使用情况"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# 输出汇总
echo ""
echo "========================================"
echo "  检查结果汇总"
echo "========================================"
echo -e "总检查项: ${BLUE}$TOTAL_CHECKS${NC}"
echo -e "通过: ${GREEN}$PASSED_CHECKS${NC}"
echo -e "失败: ${RED}$FAILED_CHECKS${NC}"
echo ""

if [ $FAILED_CHECKS -eq 0 ]; then
    echo -e "${GREEN}所有检查项均通过！部署成功！${NC}"
    exit 0
elif [ $FAILED_CHECKS -le 2 ]; then
    echo -e "${YELLOW}部分检查项失败，请检查上述错误项。${NC}"
    exit 1
else
    echo -e "${RED}多个检查项失败，请检查部署配置。${NC}"
    exit 2
fi
