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
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 创建虚拟环境并安装依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 安装依赖，使用国内镜像加速（腾讯云）
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple

# ============== 阶段2：运行阶段 ==============
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # 默认使用 FastAPI 服务器
    SERVER_MODE=fastapi \
    # 时区配置
    TZ=Asia/Shanghai

# 安装运行时依赖（字体、浏览器等）
# 使用清华镜像加速 Debian 包下载
RUN echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie main non-free-firmware' > /etc/apt/sources.list && \
    echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie-updates main non-free-firmware' >> /etc/apt/sources.list && \
    echo 'deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ trixie-security main non-free-firmware' >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    # Playwright 浏览器依赖
    wget \
    gnupg \
    # 字体支持（用于 PDF 等处理）
    fonts-noto-cjk \
    # 其他运行时依赖
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

# 创建非 root 用户
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# 设置工作目录
WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制应用代码
COPY --chown=appuser:appgroup . .

# 创建必要的目录
RUN mkdir -p logs && chown -R appuser:appgroup logs

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
# 默认启动 FastAPI 服务
# 可通过环境变量 SERVER_MODE=gradio 切换到 Gradio UI
ENTRYPOINT ["sh", "-c", "python -m uvicorn src.main:app --host 0.0.0.0 --port ${SERVER_PORT:-8000}"]
