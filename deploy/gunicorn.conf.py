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
# 推荐公式：2×CPU核数 + 1（同步 worker 参考值；本项目为异步 UvicornWorker，
# worker 数主要影响进程隔离与 IO 并发，不必按 2n+1 拉满）
# 实际生产值由 compose 的 WORKERS 环境变量决定：
#   - 新生产服务器（129.211.65.243，4核16G）：6（docker-compose.prod.yml）
#   - 旧生产 124.222.3.254（4核8G，多实例共存）：3-4
# 此处默认值仅兜底无 compose 的裸启动场景
# 可通过环境变量 WORKERS 覆盖
workers = int(os.environ.get("WORKERS", 3))

# ── 超时 ───────────────────────────────────────────────
# LLM 调用耗时较长，timeout 不能设太小；建议 ≥ 120s
# 可通过环境变量 WORKER_TIMEOUT 覆盖
timeout = int(os.environ.get("WORKER_TIMEOUT", 120))

# 保持长连接，减少 TCP 握手开销（秒）
keepalive = 5

# ── 日志 ───────────────────────────────────────────────
# 关闭 access log，避免 /health 健康检查刷屏（排错可借助 Nginx access log）
accesslog  = None
errorlog   = "-"
loglevel   = os.environ.get("LOG_LEVEL", "info").lower()

# 关闭 Uvicorn 自身的 access log（UvicornWorker 会独立初始化 uvicorn.access logger）
try:
    from uvicorn.workers import UvicornWorker
    UvicornWorker.CONFIG_KWARGS["access_log"] = False
except ImportError:
    pass


def when_ready(server):
    """启动前保护检查：多 worker 模式下 Redis 为必选项"""
    if server.cfg.workers > 1:
        redis_enabled = os.environ.get("REDIS_ENABLED", "").lower()
        if redis_enabled not in ("true", "1", "yes"):
            import sys
            sys.stderr.write(
                "\n[CRITICAL] 启动被拒绝：多 worker 模式下 Redis 是强依赖组件。\n"
                "当前配置 workers={}，但 REDIS_ENABLED 未设为 true。\n"
                "请将 REDIS_ENABLED 设为 true，或将 WORKERS 调整为 1。\n\n"
                .format(server.cfg.workers)
            )
            sys.exit(1)


def post_worker_init(worker):
    """Worker 初始化完成后执行：强制静默 uvicorn.access logger"""
    import logging
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers = []
    uvicorn_access.propagate = False
    uvicorn_access.setLevel(logging.WARNING)
    uvicorn_access.disabled = True

# ── 内存泄漏防护 ────────────────────────────────────────
# 每个 worker 处理 N 个请求后自动重启，防止长期运行内存膨胀
# 2026-09-08：1000 -> 20000。当前流量约 1 万请求/天（local-tools heartbeat/claim
# 占大头），1000 会让 worker 每 40-60 分钟自重启一次，杀死进行中的 recap 等
# 进程内后台任务（真实事故：2026-09-08 10:52 external_push 推送被重启静默丢掉）
max_requests        = 20000
max_requests_jitter = 2000  # 随机抖动，避免所有 worker 同时重启

# ── 预加载 ─────────────────────────────────────────────
# 禁用：应用使用 asyncio lifespan 初始化，preload 会在无事件循环的
# master 进程中执行，导致 "no running event loop" 错误
preload_app = False

# ── 自动重载 ───────────────────────────────────────────
# 始终关闭。gunicorn 仅用于服务端环境（生产/测试/在线开发），多 worker 下
# 任何 .py 变更（如部署脚本 git reset 改写源码）都会触发全部 worker 同时
# 重新初始化，在多环境共享的低配服务器上会造成内存/Swap 风暴、服务长时间无响应。
# 本地开发用 uvicorn --reload（docker-compose.local.yml），不走本文件，天然支持热重载。
reload = False

# ── 性能优化 ───────────────────────────────────────────
# 使用内存文件系统存储 worker 临时文件，提高进程间通信性能
# 容器中 /dev/shm 通常可用；若不存在可注释掉此行
worker_tmp_dir = "/dev/shm"
