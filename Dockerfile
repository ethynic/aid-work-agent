# AID Work Agent Dockerfile
# 多阶段构建，优化镜像大小并确保安全性

# ============== 阶段1：构建阶段 ==============
FROM python:3.11-slim AS builder

# 设置工作目录
WORKDIR /app

# 安装构建依赖
# 使用清华镜像加速 Debian 包下载
RUN echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie main non-free-firmware' > /etc/apt/sources.list && \
    echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie-updates main non-free-firmware' >> /etc/apt/sources.list && \
    echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ trixie-security main non-free-firmware' >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 创建虚拟环境并安装依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 安装依赖，使用国内镜像加速（腾讯云）
# --ignore-installed 避免与系统已安装的包冲突
RUN pip install --no-cache-dir --ignore-installed -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple

# 确保关键依赖安装成功（部分镜像源可能同步延迟，pip install 失败可能被静默吞掉）
RUN python -c "import apscheduler; print('apscheduler loaded OK:', apscheduler.__version__)" || \
    (echo "ERROR: apscheduler not installed, reinstalling from pypi.org..." && \
     pip install --no-cache-dir 'APScheduler>=3.10.0' -i https://pypi.org/simple/ && \
     python -c "import apscheduler; print('apscheduler loaded OK:', apscheduler.__version__)")

# 确保 redis 安装成功
RUN python -c "import redis; print('redis loaded OK:', redis.__version__)" || \
    (echo "ERROR: redis not installed, reinstalling from pypi.org..." && \
     pip install --no-cache-dir 'redis>=5.0.0' -i https://pypi.org/simple/ && \
     python -c "import redis; print('redis loaded OK:', redis.__version__)")

# ============== 阶段2：运行阶段 ==============
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # 默认使用 FastAPI 服务器
    SERVER_MODE=fastapi \
    # 时区配置
    TZ=Asia/Shanghai \
    # Gunicorn worker 数量（5用户场景，3个足够，减少内存和磁盘IO）
    WORKERS=3 \
    # Gunicorn worker 超时（秒）
    WORKER_TIMEOUT=120

# 安装运行时依赖（字体、浏览器等）
# 使用清华镜像加速 Debian 包下载
RUN echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie main non-free-firmware' > /etc/apt/sources.list && \
    echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie-updates main non-free-firmware' >> /etc/apt/sources.list && \
    echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ trixie-security main non-free-firmware' >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    # 常用工具
    curl \
    wget \
    vim \
    less \
    procps \
    iputils-ping \
    net-tools \
    dnsutils \
    zip \
    unzip \
    tar \
    grep \
    htop \
    file \
    jq \
    lsof \
    tree \
    fonts-noto-cjk \
    pandoc \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户及 home 目录（Uvicorn control server 需要）
# 注意：uid=1000 与宿主机 SMB 挂载的 ubuntu 用户 uid 保持一致
RUN groupadd -r appgroup && useradd -r -g appgroup --uid 1000 -m appuser
ENV HOME=/home/appuser

# 设置工作目录
WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 修复虚拟环境所有权（builder 阶段是 root，运行阶段是 appuser）
RUN chown -R appuser:appgroup /opt/venv

# 复制应用代码
COPY --chown=appuser:appgroup . .

# 创建必要的目录（日志等）
RUN mkdir -p log/agent && chown -R appuser:appgroup log

# 安装 Playwright 浏览器（使用国内镜像）- 默认跳过，如需浏览器功能取消注释
# RUN playwright install chromium --with-deps -i https://playwright.aimir.cn/simple || \
#     playwright install chromium --with-deps

# 切换到非 root 用户
USER appuser

# 暴露端口
# 8000: FastAPI 服务
# 7860: Gradio UI
EXPOSE 8000 7860

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
# 生产模式：Gunicorn 管理多个 UvicornWorker 进程
# 运行参数统一在 deploy/gunicorn.conf.py 中管理
# 可通过环境变量覆盖：WORKERS、WORKER_TIMEOUT、SERVER_PORT、LOG_LEVEL
ENTRYPOINT ["sh", "-c", "gunicorn -c deploy/gunicorn.conf.py src.main:app"]
