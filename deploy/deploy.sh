#!/bin/bash
#
# AID Work Agent 部署脚本
# 支持 Docker 和直接部署两种方式
#

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 默认配置
DEPLOY_MODE="docker"  # docker | native
APP_NAME="aid-agent"
API_PORT=8000
GRADIO_PORT=7860

# 打印函数
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 帮助信息
show_help() {
    cat << EOF
AID Work Agent 部署脚本

用法: $0 [选项]

选项:
    -m, --mode MODE        部署模式: docker (默认) | native
    -p, --port PORT        API 端口 (默认: 8000)
    -g, --gradio-port PORT Gradio 端口 (默认: 7860)
    -o, --only-api         仅启动 API 服务
    -u, --with-ui          同时启动 Gradio UI
    -d, --down             停止并移除容器
    -r, --restart          重启服务
    -l, --logs             查看日志
    -s, --status           查看服务状态
    -b, --build            构建 Docker 镜像
    -h, --help             显示帮助信息

示例:
    $0 -m docker -p 8000           # Docker 模式，API 端口 8000
    $0 --with-ui                   # 启动 API + Gradio UI
    $0 -d                          # 停止服务
    $0 -l                          # 查看日志
    $0 -m native                   # 直接部署模式

EOF
}

# 检查依赖
check_dependencies() {
    print_info "检查依赖..."

    if [ "$DEPLOY_MODE" = "docker" ]; then
        if ! command -v docker &> /dev/null; then
            print_error "Docker 未安装，请先安装 Docker"
            exit 1
        fi
        print_success "Docker 已安装: $(docker --version)"

        if ! command -v docker compose &> /dev/null; then
            if docker compose version &> /dev/null; then
                DOCKER_COMPOSE="docker compose"
            else
                print_error "Docker Compose 未安装"
                exit 1
            fi
        else
            DOCKER_COMPOSE="docker compose"
        fi
        print_success "Docker Compose 已安装: $($DOCKER_COMPOSE --version)"
    else
        # 检查 Python
        if ! command -v python3 &> /dev/null; then
            print_error "Python3 未安装"
            exit 1
        fi
        print_success "Python 已安装: $(python3 --version)"

        # 检查 pip
        if ! command -v pip3 &> /dev/null && ! python3 -m pip --version &> /dev/null; then
            print_warning "pip3 未安装，尝试安装依赖..."
            python3 -m ensurepip --default-pip || true
        fi
    fi
}

# 加载环境变量
load_env() {
    if [ -f "deploy/.env" ]; then
        print_info "加载环境变量: deploy/.env"
        export $(grep -v '^#' deploy/.env | xargs)
    fi
}

# 构建 Docker 镜像
build_image() {
    print_info "构建 Docker 镜像..."
    docker build -t ${APP_NAME}:latest .
    print_success "镜像构建完成"
}

# 启动服务 (Docker 模式)
start_docker() {
    print_info "启动 Docker 服务..."

    if [ "$WITH_UI" = true ]; then
        print_info "启动 API + Gradio UI..."
        $DOCKER_COMPOSE --profile ui up -d
    else
        print_info "启动 API 服务..."
        $DOCKER_COMPOSE up -d aid-agent-api
    fi

    print_success "服务启动完成"
    echo ""
    echo "========================================"
    echo " 服务地址:"
    echo "   API:      http://localhost:${API_PORT}"
    if [ "$WITH_UI" = true ]; then
        echo "   Gradio UI: http://localhost:${GRADIO_PORT}"
    fi
    echo "========================================"
    echo ""
}

# 停止服务 (Docker 模式)
stop_docker() {
    print_info "停止 Docker 服务..."
    $DOCKER_COMPOSE down
    print_success "服务已停止"
}

# 重启服务
restart_docker() {
    print_info "重启 Docker 服务..."
    $DOCKER_COMPOSE restart
    print_success "服务已重启"
}

# 查看状态
status_docker() {
    $DOCKER_COMPOSE ps
}

# 查看日志
logs_docker() {
    if [ "$WITH_UI" = true ]; then
        $DOCKER_COMPOSE logs -f aid-agent-api aid-agent-ui
    else
        $DOCKER_COMPOSE logs -f aid-agent-api
    fi
}

# 启动服务 (原生模式)
start_native() {
    print_info "检查 Python 依赖..."
    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    fi

    print_info "启动服务..."

    if [ "$WITH_UI" = true ]; then
        print_warning "原生模式暂不支持同时启动 Gradio UI"
        print_info "启动 API 服务..."
    fi

    export SERVER_PORT=$API_PORT
    python3 -m uvicorn src.main:app --host 0.0.0.0 --port $API_PORT &
    PID=$!

    print_success "服务启动完成 (PID: $PID)"
    echo ""
    echo "========================================"
    echo " 服务地址:"
    echo "   API:      http://localhost:${API_PORT}"
    echo "   Gradio UI: 使用 Docker 部署以支持 Gradio UI"
    echo "========================================"
    echo ""
    echo "按 Ctrl+C 停止服务"

    # 等待信号
    trap "print_info '正在停止服务...'; kill $PID 2>/dev/null; exit 0" SIGINT SIGTERM
    wait $PID
}

# 主函数
main() {
    # 解析参数
    WITH_UI=false
    ACTION="start"

    while [[ $# -gt 0 ]]; do
        case $1 in
            -m|--mode)
                DEPLOY_MODE="$2"
                shift 2
                ;;
            -p|--port)
                API_PORT="$2"
                shift 2
                ;;
            -g|--gradio-port)
                GRADIO_PORT="$2"
                shift 2
                ;;
            -o|--only-api)
                WITH_UI=false
                shift
                ;;
            -u|--with-ui)
                WITH_UI=true
                shift
                ;;
            -d|--down)
                ACTION="down"
                shift
                ;;
            -r|--restart)
                ACTION="restart"
                shift
                ;;
            -l|--logs)
                ACTION="logs"
                shift
                ;;
            -s|--status)
                ACTION="status"
                shift
                ;;
            -b|--build)
                ACTION="build"
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                print_error "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done

    # 执行操作
    case $ACTION in
        build)
            check_dependencies
            load_env
            build_image
            ;;
        start)
            check_dependencies
            load_env
            if [ "$DEPLOY_MODE" = "docker" ]; then
                start_docker
            else
                start_native
            fi
            ;;
        down)
            check_dependencies
            stop_docker
            ;;
        restart)
            check_dependencies
            load_env
            restart_docker
            ;;
        logs)
            check_dependencies
            logs_docker
            ;;
        status)
            check_dependencies
            status_docker
            ;;
    esac
}

# 运行主函数
main "$@"
