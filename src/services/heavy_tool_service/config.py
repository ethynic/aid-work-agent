"""重工具服务环境配置读取（设计 §4.1/§6.1）。

环境旋钮（一律经服务器 .env 注入，deploy 脚本不 export——既定裁决）：
- HEAVY_TOOLS_MODE          local（默认，全本地执行）/ remote（代理远程派发）
- HEAVY_TOOLS_URL           重工具服务地址，默认 http://heavy-tools:8093；
                            跨机部署改内网 IP（§6.1，沿用 AGENT_RUNNER_API_URL 注入先例）
- HEAVY_TOOLS_CONCURRENCY   重容器单副本信号量并发（默认 1）
- HEAVY_TOOLS_STORAGE_ROOT  重容器侧共享 storage 根（路径 allowlist 前缀，默认 /app/storage）
- HEAVY_TOOLS_UPLOADS_ROOT  重容器侧共享 uploads 根（历史附件路径只读，默认 /app/uploads）
- AGENT_RUNNER_WEB_SERVICE_TOKEN  服务间凭据（与 runner 服务间 token 同值，恒等比较）
"""

import os

from loguru import logger

DEFAULT_URL = "http://heavy-tools:8093"
DEFAULT_PORT = 8093
DEFAULT_TIMEOUT_SECONDS = 600
# paddleocr 全页识别大文档可超 10 分钟，防截断合法任务（设计 §4.1）
PADDLEOCR_TIMEOUT_SECONDS = 900
# 孤儿登记回收余量（设计 §4.3）：job 登记在其实际超时（timeout_seconds）之上再加
# 本余量后才允许清扫——固定 TTL 必然小于某个合法任务的超时（如 600 < paddleocr 900），
# 会把仍在运行中的 job 登记误当孤儿移除；余量覆盖清扫间隔（60s）与调度抖动
JOB_TTL_GRACE_SECONDS = 120

_MODE_REMOTE = "remote"
_MODE_LOCAL = "local"


def heavy_tools_mode() -> str:
    """重工具派发模式：remote=代理远程派发；local/未配置=全本地执行（零代码回滚开关）。"""
    value = (os.environ.get("HEAVY_TOOLS_MODE") or "").strip().lower()
    if value == _MODE_REMOTE:
        return _MODE_REMOTE
    return _MODE_LOCAL


def is_remote_mode() -> bool:
    return heavy_tools_mode() == _MODE_REMOTE


def heavy_tools_url() -> str:
    """重工具服务地址；单机 compose 走服务名 DNS，跨机部署改内网地址（§6.1）。"""
    return (os.environ.get("HEAVY_TOOLS_URL") or DEFAULT_URL).strip().rstrip("/")


def heavy_tools_concurrency() -> int:
    try:
        value = int(os.environ.get("HEAVY_TOOLS_CONCURRENCY", "1"))
    except ValueError:
        value = 1
    return max(1, value)


def service_token() -> str:
    """服务间凭据：与 runner 服务间 token 同一 .env 值（AGENT_RUNNER_WEB_SERVICE_TOKEN）。"""
    return (os.environ.get("AGENT_RUNNER_WEB_SERVICE_TOKEN") or "").strip()


def allowed_path_roots() -> "tuple[str, ...]":
    """重容器入参文件路径 allowlist 前缀（设计 §4.1 路径入参服务端校验）。"""
    storage_root = (os.environ.get("HEAVY_TOOLS_STORAGE_ROOT") or "/app/storage").strip()
    uploads_root = (os.environ.get("HEAVY_TOOLS_UPLOADS_ROOT") or "/app/uploads").strip()
    return (storage_root.rstrip("/"), uploads_root.rstrip("/"))


def require_remote_token() -> bool:
    """未配置服务 token 时拒绝全部 execute 请求（fail-closed），仅 /health 可访问。"""
    if service_token():
        return True
    logger.warning("AgentRunner 重工具服务未配置 AGENT_RUNNER_WEB_SERVICE_TOKEN，execute 请求将全部拒绝")
    return False
