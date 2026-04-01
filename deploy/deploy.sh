#!/bin/bash

# ============================================
# AID Work Agent 一键部署脚本
# ============================================

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 项目根目录
PROJECT_DIR="/var/www/agent"
DEPLOY_DIR="$PROJECT_DIR/deploy"

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

# 检查命令是否存在
check_command() {
    if ! command -v $1 &> /dev/null; then
        log_error "$1 未安装，请先安装 $1"
        exit 1
    fi
}

# 主部署流程
main() {
    echo "========================================"
    echo "  AID Work Agent 生产环境部署"
    echo "========================================"
    echo ""

    # 1. 环境检查
    log_info "步骤 1/7: 检查系统环境..."
    check_command docker
    check_command docker-compose
    check_command nginx
    log_success "环境检查通过"

    # 2. 检查项目目录
    log_info "步骤 2/7: 检查项目目录..."
    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "项目目录不存在: $PROJECT_DIR"
        exit 1
    fi
    cd $PROJECT_DIR
    log_success "项目目录检查通过"

    # 3. 检查环境变量配置
    log_info "步骤 3/7: 检查环境变量配置..."
    if [ ! -f ".env" ]; then
        if [ -f "$DEPLOY_DIR/.env.production.example" ]; then
            log_warning ".env 文件不存在，正在从模板创建..."
            cp $DEPLOY_DIR/.env.production.example .env
            log_error "请编辑 .env 文件配置必要的环境变量后再运行部署脚本"
            log_info "编辑命令: vim $PROJECT_DIR/.env"
            exit 1
        else
            log_error ".env 文件和模板文件都不存在"
            exit 1
        fi
    fi
    log_success "环境变量配置文件存在"

    # 4. 创建必要的目录
    log_info "步骤 4/7: 创建必要的目录..."
    mkdir -p log
    mkdir -p /var/www/qb3_upload/agent_uploads
    mkdir -p /var/www/qb3_upload/agent_memories
    
    # 设置正确的权限（允许容器内的 appuser 写入，同时在宿主机可查看）
    # 获取当前用户（通常是部署用户，如 gaofang）
    CURRENT_USER=$(whoami)
    CURRENT_GROUP=$(id -gn)
    
    # 设置日志目录权限
    chown -R ${CURRENT_USER}:${CURRENT_GROUP} log
    chmod -R 775 log
    
    # 设置数据库文件权限（如果已存在）
    if [ -f "aid_work_agent.db" ]; then
        chmod 777 aid_work_agent.db
        log_info "数据库文件权限已设置为 777"
    else
        log_warning "数据库文件不存在，将在首次启动时自动创建"
        log_warning "首次启动后请执行: chmod 777 aid_work_agent.db"
    fi
    
    # 设置上传目录权限
    sudo chown -R www-data:www-data /var/www/qb3_upload
    
    log_success "目录创建完成"
    log_info "数据库文件位置: $PROJECT_DIR/aid_work_agent.db"
    log_info "日志文件位置: $PROJECT_DIR/log/"

    # 把 gaofang 用户加入 docker 组
    sudo usermod -aG docker gaofang

    # 修复 .docker 目录权限
    sudo chown -R gaofang:gaofang /home/gaofang/.docker

    # 重新登录使组成员资格生效（或执行以下命令）
    newgrp docker

    # 验证（不用 sudo 也能用 docker compose 了）
    docker compose version

    # 6. 启动 Docker 容器
    log_info "步骤 6/7: 启动 Docker 容器..."
    if [ -f "docker-compose.prod.yml" ]; then
        # 停止旧容器
        docker compose -f docker-compose.prod.yml down 2>/dev/null || true
        
        # 构建并启动新容器
        docker compose -f docker-compose.prod.yml up -d --build
        
        # 等待容器启动
        log_info "等待容器启动..."
        sleep 10
        
        # 检查容器状态
        if docker compose -f docker-compose.prod.yml ps | grep -q "Up"; then
            log_success "容器启动成功"
        else
            log_error "容器启动失败"
            docker compose -f docker-compose.prod.yml logs
            exit 1
        fi
    else
        log_error "docker-compose.prod.yml 文件不存在"
        exit 1
    fi

    
    # 部署完成
    echo ""
    echo "========================================"
    log_success "部署完成！"
    echo "========================================"
    echo ""
    echo "访问地址:"
    echo "  - 前端: https://agent.aidingyi.cn"
    echo "  - 后端API: https://agent.aidingyi.cn/api"
    echo ""
    echo "常用命令:"
    echo "  - 查看容器状态: docker compose -f docker-compose.prod.yml ps"
    echo "  - 查看日志: docker compose -f docker-compose.prod.yml logs -f"
    echo "  - 重启服务: docker compose -f docker-compose.prod.yml restart"
    echo "  - 检查部署: $DEPLOY_DIR/check_deployment.sh"
    echo ""
}

# 运行主函数
main "$@"
