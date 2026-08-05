# AID Work Agent Dockerfile
# 多阶段构建，优化镜像大小并确保安全性

# ============== 阶段1：构建阶段 ==============
# 注意：不锁定 python:3.11-slim 的 digest，理由：
# 1) python:3.11-slim 自身不含 apt 源，digest 锁定不会带来 apt 安全更新
# 2) /tmp tmpfs 权限问题由 fix_tmp.sh（entrypoint）+ tmpfs mount（compose）双重兜底
# 3) 锁 digest 反而会错过 Python 自身的安全补丁
# 4) 如未来 digest 漂移再次导致问题，可临时锁定 digest 应急
FROM python:3.11-slim AS builder

# 设置工作目录
WORKDIR /app

# 安装构建依赖
# 使用腾讯云镜像加速 Debian 包下载（服务器在腾讯云内网，TTFB < 50ms）
RUN echo 'deb https://mirrors.cloud.tencent.com/debian/ trixie main non-free-firmware' > /etc/apt/sources.list && \
    echo 'deb https://mirrors.cloud.tencent.com/debian/ trixie-updates main non-free-firmware' >> /etc/apt/sources.list && \
    echo 'deb https://mirrors.cloud.tencent.com/debian-security/ trixie-security main non-free-firmware' >> /etc/apt/sources.list && \
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

# MCP 单独安装（依赖较多，独立一层便于排查与缓存；zhipuai 已移除，不再存在 pyjwt 冲突）
RUN pip install --no-cache-dir --no-deps 'mcp>=1.27.0' -i https://mirrors.cloud.tencent.com/pypi/simple && \
    pip install --no-cache-dir \
      'pyjwt>=2.10.1' \
      'httpx-sse>=0.4' \
      'jsonschema>=4.20' \
      'pydantic-settings>=2.5' \
      'sse-starlette>=1.6' \
      -i https://mirrors.cloud.tencent.com/pypi/simple && \
    python -c "import mcp; print('mcp loaded OK, version:', mcp.__version__ if hasattr(mcp, '__version__') else 'unknown')"

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
    WORKER_TIMEOUT=120 \
    # 临时目录（/tmp 权限可能被外部工具重置为 755，使用应用自有目录）
    TMPDIR=/home/appuser/tmp \
    # Playwright 浏览器安装路径（与 Dockerfile 中 PLAYWRIGHT_BROWSERS_PATH 一致）
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# 安装运行时依赖（字体、浏览器等）
# 使用腾讯云镜像加速 Debian 包下载（服务器在腾讯云内网，TTFB < 50ms）
# 注意：apt-get install 阶段只跑一次，构建缓存依赖 BuildKit 层缓存
# （Docker 镜像层复用），不依赖 --mount=type=cache（在你的 BuildKit 版本下
# 会有 /var/lib/apt/lists 锁冲突问题）
RUN echo 'deb https://mirrors.cloud.tencent.com/debian/ trixie main non-free-firmware' > /etc/apt/sources.list && \
    echo 'deb https://mirrors.cloud.tencent.com/debian/ trixie-updates main non-free-firmware' >> /etc/apt/sources.list && \
    echo 'deb https://mirrors.cloud.tencent.com/debian-security/ trixie-security main non-free-firmware' >> /etc/apt/sources.list && \
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
    ripgrep \
    fonts-noto-cjk \
    libreoffice-writer \
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
    # FFmpeg：用于视频生成模块烧录「AI 生成内容」标识（2025.9.1 法规）
    # 中文字形由 fonts-noto-cjk 提供，drawtext 已验证可用
    ffmpeg \
    # gosu 用于 entrypoint 中以非 root 用户身份启动 gunicorn（保持 PID 1 信号处理）
    gosu \
    && rm -rf /var/lib/apt/lists/*

# 构建时校验文档转换工具，避免依赖缺失延迟到运行时才暴露
RUN soffice --headless --version && pandoc --version && ffmpeg -hide_banner -version | head -1

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

# 安装 Playwright Chromium 浏览器（表格渲染为图片）
# 重要：此步骤必须在 `COPY . .` 之前执行，否则任何项目代码改动都会让本层缓存失效，
# 导致每次 build 重新下载 Chrome（约 150MB）。本层只依赖：
#   1) apt 系统库（前面已安装）
#   2) venv 里的 playwright Python 包（已 COPY 进来）
#   3) PLAYWRIGHT_BROWSERS_PATH 环境变量
# 这三者稳定时，本层缓存长期命中，浏览器随镜像层保存。
#
# 2026-06-26: 华为云镜像偶发返回损坏 zip，增加多镜像 fallback
# 说明：
# 1) 所有系统依赖库（libnss3/libgbm1/libasound2/libpango 等）已在阶段 2 的
#    apt-get install 中手动安装完成，playwright install 不再需要 --with-deps
# 2) PLAYWRIGHT_DOWNLOAD_HOST 使用国内镜像加速下载，并配置多个 fallback
# 3) 浏览器文件留在镜像层中（/opt/ms-playwright），后续构建如果 RUN 不变
#    就会被 BuildKit 层缓存命中
# 4) TMPDIR=/tmp 强制覆盖：BuildKit build 阶段 USER=root，但 /home/appuser/tmp
#    可能在某些 union FS 实现下不可见（ENOENT on mkdtemp），改用 /tmp 最稳
# 5) 先用 mkdir -p 兜底创建临时目录（针对 root 用户）
# 6) 浏览器下载到 PLAYWRIGHT_BROWSERS_PATH（/opt/ms-playwright），随镜像保存
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
# 注意：Dockerfile 不支持 shell heredoc（cat <<'EOF' ... EOF），经典解析器会把
# EOF 之后的行当成独立指令（报 "unknown instruction: chmod"）。改用 printf 生成
# 脚本文件，所有内容在同一个 RUN 指令内完成。单引号包裹保证 $host 等变量原样写入。
RUN printf '%s\n' \
      '#!/bin/bash' \
      'set -e' \
      'for host in https://playwright-akamai.azureedge.net https://playwright-verizon.azureedge.net https://mirrors.huaweicloud.com/playwright; do' \
      '    echo ">>> Trying Playwright mirror: $host"' \
      '    rm -rf /opt/ms-playwright/* /tmp/playwright-download-* /tmp/pw-*' \
      '    if PLAYWRIGHT_DOWNLOAD_HOST=$host playwright install chromium; then' \
      '        echo ">>> Playwright installed successfully from $host"' \
      '        exit 0' \
      '    fi' \
      '    echo ">>> Failed to install from $host, trying next mirror..."' \
      'done' \
      'echo ">>> ERROR: All Playwright mirrors failed"' \
      'exit 1' \
      > /tmp/install_pw.sh && \
    chmod +x /tmp/install_pw.sh && \
    mkdir -p /opt/ms-playwright /tmp && \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    TMPDIR=/tmp \
    bash /tmp/install_pw.sh && \
    chown -R appuser:appgroup /opt/ms-playwright && \
    rm -f /tmp/install_pw.sh

# 创建必要的目录（日志等）
RUN mkdir -p log/agent && chown -R appuser:appgroup log

# 复制应用代码
# 注意：从这里开始的层会因项目代码改动而失效，所以 Playwright 安装必须放在此行之前。
COPY --chown=appuser:appgroup . .

# 创建应用临时目录（替代 /tmp，避免外部工具重置权限导致 appuser 无法写入）
# 注意：此目录不在 volume 挂载范围内，确保每次容器启动时干净
RUN mkdir -p /home/appuser/tmp && chown appuser:appgroup /home/appuser/tmp

# 安装 fix_tmp.sh 脚本
# python:3.11-slim 的 /tmp 是 tmpfs，Dockerfile 里的 RUN chmod 1777 /tmp 不会作用到
# 运行时挂载的 tmpfs。必须在 entrypoint 启动时（容器内运行时）处理。
# 注意：fix_tmp.sh 需要以 root 身份运行才能 chmod /tmp，
# 所以脚本必须在 USER appuser 之前/之后通过 entrypoint 切换身份来执行
COPY --chown=root:root deploy/fix_tmp.sh /usr/local/bin/fix_tmp.sh
RUN chmod +x /usr/local/bin/fix_tmp.sh

# 注意：此处不设置 USER appuser。
# ENTRYPOINT 需要以 root 身份启动：先执行 fix_tmp.sh（root 才能 chmod /tmp），
# 再用 gosu 降权到 appuser 启动 gunicorn。若设置 USER appuser，gosu 会因
# 缺少 CAP_SETUID 失败（"failed switching to appuser: operation not permitted"）。

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
# entrypoint 在 root 下执行 fix_tmp.sh 修复 /tmp 权限（python:3.11-slim 的 /tmp 是 tmpfs，
# Dockerfile 的 chmod 不会生效），然后用 gosu 切换到 appuser 启动 gunicorn（保持 PID 1）
ENTRYPOINT ["sh", "-c", "/usr/local/bin/fix_tmp.sh && exec gosu appuser gunicorn -c deploy/gunicorn.conf.py src.main:app"]
