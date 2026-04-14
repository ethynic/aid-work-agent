# Gunicorn 配置文件
# 生产环境使用 UvicornWorker 运行 FastAPI 应用
# 用法：gunicorn -c deploy/gunicorn.conf.py src.main:app

import os

# ── 应用 ──────────────────────────────────────────────
# 绑定地址与端口（可通过环境变量 SERVER_PORT 覆盖）
bind = f"0.0.0.0:{os.environ.get('SERVER_PORT', '8000')}"

# Worker 类型：必须使用 UvicornWorker 才能支持 ASGI/异步
worker_class = "uvicorn.workers.UvicornWorker"

# ── Worker 数量 ────────────────────────────────────────
# 推荐公式：2×CPU核数 + 1
# 当前服务器：4 核 → workers = 9
# 可通过环境变量 WORKERS 覆盖
workers = int(os.environ.get("WORKERS", 9))

# ── 超时 ───────────────────────────────────────────────
# LLM 调用耗时较长，timeout 不能设太小；建议 ≥ 120s
# 可通过环境变量 WORKER_TIMEOUT 覆盖
timeout = int(os.environ.get("WORKER_TIMEOUT", 120))

# 保持长连接，减少 TCP 握手开销（秒）
keepalive = 5

# ── 日志 ───────────────────────────────────────────────
# 输出到 stdout/stderr，方便 docker logs 查看
accesslog  = "-"
errorlog   = "-"
loglevel   = os.environ.get("LOG_LEVEL", "info").lower()

# ── 内存泄漏防护 ────────────────────────────────────────
# 每个 worker 处理 N 个请求后自动重启，防止长期运行内存膨胀
max_requests        = 1000
max_requests_jitter = 100   # 随机抖动，避免所有 worker 同时重启

# ── 预加载 ─────────────────────────────────────────────
# 禁用：应用使用 asyncio lifespan 初始化，preload 会在无事件循环的
# master 进程中执行，导致 "no running event loop" 错误
preload_app = False

# ── 自动重载（仅开发环境）─────────────────────────────
# 生产环境必须为 False；如需开发调试，在启动命令中加 --reload
reload = True

# ── 性能优化 ───────────────────────────────────────────
# 使用内存文件系统存储 worker 临时文件，提高进程间通信性能
# 容器中 /dev/shm 通常可用；若不存在可注释掉此行
worker_tmp_dir = "/dev/shm"
