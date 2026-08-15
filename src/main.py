"""
AID Work Agent Main Entry

V1.0 MVP version - LLM-driven agent architecture
"""

import asyncio
import json
import os
import uuid
import queue
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse, FileResponse, HTMLResponse
from loguru import logger
from pydantic import BaseModel

from src.config.settings import settings
from src.config.logging import setup_logging
from src.core.agent import master_agent
from src.core.agent_router import agent_router
from src.core.redis_client import redis_client
from src.core.session_queue import session_queue
from src.models.message import UnifiedMessage
from src.db.database import init_database, init_postgres_pool, close_postgres_pool
from src.api import auth, session as session_api, customer, scheduled_task, email_settings
from src.api import monitor as monitor_api
from src.api import social_media as social_media_api
from src.api import admin_subagent, subagent, subagent_extra, travel_quote
from src.api import admin_redis
from src.api import customer_followup, complaint_handling, after_sales
from src.knowledge.api import router as knowledge_router
from src.db.models import SessionDB, MessageDB
from src.channels import callback as channels_api
from src.services.session_record import SessionRecordManager


# ============== 默认子智能体路由 ==============


def _resolve_default_subagent(subagent_name, tenant_id, user):
    """
    当请求未指定子智能体时，检查租户是否只有 1 个可用智能体。
    如果是，自动路由到该智能体，跳过主智能体。

    Args:
        subagent_name: 当前请求的子智能体名称（None 表示未指定）
        tenant_id: 租户 ID（来自 X-Tenant-Id 解析，可能与 user["tenant_id"] 不同）
        user: 当前用户信息 dict

    Returns:
        str | None: 自动解析的子智能体名称，或 None（保持原样）
    """
    if subagent_name:
        return subagent_name

    if not tenant_id or not user:
        return None

    # demo 租户有多个内置智能体，不应自动路由
    if tenant_id == "demo":
        return None

    try:
        from src.saas.permissions.checker import get_allowed_agent_ids_for_user
        # 透传外层 tenant_id：platform_admin 通过 X-Tenant-Id 代管理时，
        # 应按目标租户订阅过滤，而非按 user["tenant_id"]=demo 返回全部
        allowed_ids = set(get_allowed_agent_ids_for_user(user, target_tenant_id=tenant_id))
        if not allowed_ids:
            return None

        # 获取所有已注册的子智能体
        registry = master_agent.subagent_registry
        if not registry:
            return None

        all_subagents = registry.get_all_subagents_with_type()
        # 过滤出用户有权限的
        available = [s for s in all_subagents if s["agent_id"] in allowed_ids]

        if len(available) == 1:
            agent_id = available[0]["agent_id"]
            logger.info(f"[AutoRoute] 租户 {tenant_id} 仅有 1 个可用智能体，自动路由到 {agent_id}")
            return agent_id
    except Exception as e:
        logger.warning(f"[AutoRoute] 解析默认子智能体失败: {e}", exc_info=True)

    return None


# ============== SSE Session Management ==============

# 全局 worker 标识，用于 Redis pub/sub 去重
_WORKER_ID = f"{os.getpid()}_{threading.current_thread().ident}"


class SSEConnectionManager:
    """管理SSE连接和会话

    多 worker 环境下通过 Redis Pub/Sub 实现跨 worker 消息广播，
    使前端 SSE 连接到 Worker A 时，Worker B 生成的消息也能送达。
    建议部署层同时配置 sticky session 以获得最佳体验。
    """

    def __init__(self):
        # session_id -> {"history": [], "sse_queues": []}
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.sse_connections: Dict[str, List[queue.Queue]] = {}
        # 取消标记已迁移到 Redis: key=cancelled_session:{session_id}, TTL=300s
        self.lock = threading.Lock()
        # Redis Pub/Sub
        self._redis_channel = "sse_broadcast"
        self._redis_pubsub = None
        self._redis_listener_thread: Optional[threading.Thread] = None
        self._start_redis_listener()

    def _start_redis_listener(self):
        """启动 Redis 订阅监听线程，接收其他 worker 的广播消息"""
        if not redis_client._connected:
            return
        try:
            pubsub = redis_client.subscribe(self._redis_channel)
            if pubsub:
                pubsub.subscribe(self._redis_channel)
                self._redis_pubsub = pubsub
                self._redis_listener_thread = threading.Thread(
                    target=self._redis_listen_loop, daemon=True
                )
                self._redis_listener_thread.start()
                logger.info("[SSE] Redis broadcast listener started")
        except Exception as e:
            logger.warning(f"[SSE] Failed to start Redis listener: {e}")

    def _redis_listen_loop(self):
        """Redis 订阅循环（daemon 线程）"""
        try:
            for message in self._redis_pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    # 忽略自己发出的消息，避免本地重复广播
                    if data.get("worker_id") == _WORKER_ID:
                        continue
                    session_id = data.get("session_id")
                    event = data.get("event")
                    if session_id and event:
                        self._local_broadcast(session_id, event)
        except Exception as e:
            logger.warning(f"[SSE] Redis listener error: {e}")

    def _local_broadcast(self, session_id: str, event: Dict[str, Any]):
        """向当前 worker 的本地 SSE 客户端推送消息"""
        with self.lock:
            queues = self.sse_connections.get(session_id, []).copy()
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def get_or_create_session(self, session_id: str = None) -> str:
        with self.lock:
            if session_id is None:
                session_id = str(uuid.uuid4())
            if session_id not in self.sessions:
                self.sessions[session_id] = {
                    "history": [],
                    "files": []
                }
                self.sse_connections[session_id] = []
                logger.info(f"Created SSE session: {session_id}")
            return session_id

    def add_sse_client(self, session_id: str) -> queue.Queue:
        client_queue = queue.Queue(maxsize=100)
        with self.lock:
            if session_id not in self.sse_connections:
                self.sse_connections[session_id] = []
            self.sse_connections[session_id].append(client_queue)
        return client_queue

    def remove_sse_client(self, session_id: str, client_queue: queue.Queue):
        with self.lock:
            if session_id in self.sse_connections:
                try:
                    self.sse_connections[session_id].remove(client_queue)
                except ValueError:
                    pass

    def _cancelled_key(self, session_id: str) -> str:
        return redis_client.make_key("cancelled_session", session_id)

    def cancel_session(self, session_id: str) -> None:
        """标记会话为已取消，停止后续生成"""
        key = self._cancelled_key(session_id)
        redis_client.sadd(key, session_id)
        redis_client.expire(key, 300)
        logger.info(f"Session cancelled by user: {session_id}")

    def is_cancelled(self, session_id: str) -> bool:
        """检查会话是否已被取消"""
        key = self._cancelled_key(session_id)
        return redis_client.sismember(key, session_id)

    def clear_cancelled(self, session_id: str) -> None:
        """清除取消标记（生成完成后）"""
        key = self._cancelled_key(session_id)
        redis_client.delete(key)

    def broadcast(self, session_id: str, event: Dict[str, Any]):
        # 本地广播
        self._local_broadcast(session_id, event)
        # 通过 Redis 广播给其他 worker
        try:
            redis_client.publish(
                self._redis_channel,
                json.dumps(
                    {"session_id": session_id, "event": event, "worker_id": _WORKER_ID},
                    default=str,
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            logger.warning(f"[SSE] Redis broadcast failed: {e}")

    def add_to_history(self, session_id: str, role: str, content: str):
        with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id]["history"].append({
                    "role": role,
                    "content": content
                })

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        with self.lock:
            if session_id in self.sessions:
                return self.sessions[session_id]["history"]
        return []


# 全局SSE连接管理器
sse_manager = SSEConnectionManager()


async def _continue_browser_agent(record, browser_result: dict) -> None:
    """后台恢复原 Agent；事件写 continuation stream，最终消息仍写 chat_messages。"""
    from src.models.user import User
    from src.tools.browser.resume_store import ResumeStore

    agent = agent_router.get_agent(
        record.agent_name, record.session_id, tenant_id=record.tenant_id
    )
    if record.tenant_id and not agent._init_tenant_id:
        agent._init_tenant_id = record.tenant_id
    user = User(user_id=record.user_id, name=record.user_id)
    store = ResumeStore()
    response_parts: list[str] = []
    tool_messages: list[dict] = []
    async for event in agent.continue_tool_call(
        session_id=record.session_id,
        tool_call_id=record.tool_call_id,
        result=browser_result,
        user=user,
    ):
        event_type = event.get("type")
        if event_type == "response":
            response_parts.append(str(event.get("data", "")))
        elif event_type == "tool_messages":
            tool_messages.extend(event.get("messages", []))
            continue
        elif event_type == "llm_call":
            # prompt/response trace 可能含敏感正文，不进入短期 continuation stream。
            continue
        if event_type in {
            "response", "progress", "tool_start", "tool_result", "thinking",
            "clarification", "images", "browser_human_required",
        }:
            await store.append_event(
                record.tenant_id, record.continuation_id, event
            )

    full_response = "".join(response_parts)
    batch_messages: list[dict] = []
    for message in tool_messages:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            batch_messages.append({
                "role": "assistant",
                "content": "",
                "metadata": {"tool_calls": message["tool_calls"]},
            })
        elif message.get("role") == "tool":
            content = message.get("content", "")
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, default=str)
            batch_messages.append({
                "role": "tool",
                "content": content,
                "metadata": {"tool_call_id": message.get("tool_call_id", "")},
            })
    if full_response:
        batch_messages.append({
            "role": "assistant",
            "content": full_response,
            "metadata": {"continued_from": record.continuation_id},
        })
    if batch_messages:
        created = await asyncio.to_thread(
            MessageDB.create_batch_transactional, record.session_id, batch_messages
        )
        if created is None:
            raise RuntimeError("CONTINUATION_PERSIST_FAILED")
    if full_response:
        sse_manager.add_to_history(record.session_id, "assistant", full_response)
    sse_manager.broadcast(record.session_id, {
        "type": "agent_continuation_available",
        "continuation_id": record.continuation_id,
    })


from src.tools.browser.agent_resume_coordinator import configure_continuation_callback
configure_continuation_callback(_continue_browser_agent)


# ============== Pydantic Models ==============

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = None  # 用于会话记录
    subagent: Optional[str] = None  # 子智能体名称（由前端从路由参数提取后传入）
    instance_id: Optional[str] = None  # 数字员工实例ID（用于并发控制锁）
    video_params: Optional[Dict[str, Any]] = None  # 视频创作参数（前端工具栏选择，仅 subagent=video-agent 时生效）


class ChatResponse(BaseModel):
    session_id: str
    success: bool
    message: Optional[str] = None


# Initialize logging
setup_logging(
    log_level="DEBUG" if settings.app.debug else "INFO",
    log_dir="log/agent",
)

_browser_shutdown_tasks: set[asyncio.Task] = set()


def _finish_browser_shutdown_task(task: asyncio.Task) -> None:
    """保留后台回收任务到终态，并消费异常避免未检索告警。"""
    _browser_shutdown_tasks.discard(task)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as exc:
        logger.warning("浏览器后台回收异常: type={}", type(exc).__name__)


async def _close_browser_runs_on_shutdown() -> None:
    """在 15 秒预算内关闭当前 worker 拥有的浏览器，不阻断其他收尾。"""
    try:
        from src.tools.browser.session import close_all_owned_browser_runs

        close_task = asyncio.create_task(
            close_all_owned_browser_runs(reason="shutdown")
        )
        _browser_shutdown_tasks.add(close_task)
        close_task.add_done_callback(_finish_browser_shutdown_task)
        report = await asyncio.wait_for(asyncio.shield(close_task), timeout=15.0)
        logger.info(
            "浏览器 shutdown 回收完成: requested={}, closed={}",
            report.get("requested", 0),
            report.get("closed", 0),
        )
    except asyncio.TimeoutError:
        logger.warning("浏览器 shutdown 回收超过 15 秒预算")
    except Exception as exc:
        logger.warning("浏览器 shutdown 回收异常: type={}", type(exc).__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    import os as _os
    _pid = _os.getpid()
    logger.info(f"[pid={_pid}] lifespan startup begin")

    # On startup
    logger.info(f"Starting {settings.app.name} v{settings.app.version}")
    logger.info(f"LLM Provider: {settings.llm.provider}, Model: {settings.llm.qwen.model}, Base URL: {settings.llm.qwen.base_url}")
    logger.info(f"Registered tools: {master_agent.tool_registry.list_tools()}")

    # 清理过期落盘文件（大内容检索闭环的临时文件，启动时清旧，防无限增长）
    # 非关键：失败不影响启动
    try:
        from src.tools._spill import cleanup_stale_spill_files
        cleaned = cleanup_stale_spill_files(max_age_hours=24)
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} stale spill files on startup")
    except Exception as e:
        logger.warning(f"清理过期落盘文件失败（不影响启动）: {e}")

    # Initialize PostgreSQL connection pool first (required by init_database)
    try:
        logger.info(f"[pid={_pid}] step1: init_postgres_pool ...")
        init_postgres_pool()
        logger.info(f"[pid={_pid}] step1: init_postgres_pool done")
    except Exception as e:
        logger.error(f"[pid={_pid}] step1 FAILED (critical): {e}", exc_info=True)
        raise

    # Initialize database (may use PostgreSQL)
    try:
        logger.info(f"[pid={_pid}] step2: init_database ...")
        init_database()
        logger.info(f"[pid={_pid}] step2: init_database done")
    except Exception as e:
        logger.error(f"[pid={_pid}] step2 FAILED (critical): {e}", exc_info=True)
        raise

    # Load subagent definitions from DB (Phase 2: 整体降级策略)
    try:
        from src.core.agent import master_agent as _master
        loaded = _master.subagent_registry.load_from_db()
        if loaded > 0:
            logger.info(f"Loaded {loaded} subagent definitions from database")
    except Exception as e:
        logger.warning(f"Failed to load subagent definitions from DB (non-critical): {e}")

    # Initialize logs database pool (observability, optional)
    try:
        logger.info(f"[pid={_pid}] step3: init_logs_pool ...")
        from src.db.database import init_logs_pool, init_logs_tables
        logs_ok = init_logs_pool()
        logger.info(f"[pid={_pid}] step3: init_logs_pool done, ok={logs_ok}")
        if logs_ok:
            logger.info(f"[pid={_pid}] step3b: init_logs_tables ...")
            init_logs_tables()
            logger.info(f"[pid={_pid}] step3b: init_logs_tables done")
        logger.info("Logs database pool initialized")
    except Exception as e:
        logger.warning(f"Logs database pool init failed (non-critical): {e}")

    # Initialize channel session manager (triggers lazy table creation)
    try:
        logger.info(f"[pid={_pid}] step4: channel_session_manager ...")
        from src.channels.session import channel_session_manager
        channel_session_manager._ensure_tables()
        logger.info(f"[pid={_pid}] step4: channel_session_manager done")
        logger.info("Channel session manager initialized")
    except Exception as e:
        logger.error(f"[pid={_pid}] step4 FAILED (critical): {e}", exc_info=True)
        raise

    # 注意：定时任务调度器 + 后台循环（memory_cleanup / dedup_cleanup / wecom_kf_timeout /
    # archive poller）已迁移至独立后台运行时（SERVER_MODE=background，见
    # src/background_runner.py）。HTTP worker 不再承载任何后台任务。

    _browser_reaper = None
    _browser_reaper_manager = None
    _browser_resume_worker = None
    try:
        from src.tools.browser.agent_resume_coordinator import BrowserResumeWorker
        from src.tools.browser.reaper import BrowserRunReaper
        from src.tools.browser.run_manager import BrowserRunManager

        _browser_reaper_manager = BrowserRunManager()
        _browser_reaper = BrowserRunReaper(_browser_reaper_manager)
        _browser_reaper.start()
        if _browser_reaper_manager.store.distributed:
            # Phase 3R：启用 resume worker 前一次性探测 Redis 原语
            # （GET/SET NX/EX/DEL/TTL/SCAN/publish/Lua CAS，不含已迁移的 Stream）。
            # 缺失时记录单条告警并跳过 resume worker，不形成高频异常循环。
            from src.core.redis_client import redis_client
            phase3_primitives_ok = redis_client.probe_phase3_primitives()
            if phase3_primitives_ok:
                _browser_resume_worker = BrowserResumeWorker()
                _browser_resume_worker.start()
            else:
                logger.warning(
                    "browser resume worker 未启用：Redis Phase 3 原语探针未通过，"
                    "人工接管将被禁用（server run 仍可用）"
                )
        logger.info(
            "browser run reaper 初始化完成: distributed={}",
            _browser_reaper_manager.store.distributed,
        )
    except Exception as e:
        logger.warning("browser run reaper 启动失败（不影响应用启动）: type={}", type(e).__name__)

    try:
        yield
    finally:
        # On shutdown
        logger.info("Application shutting down")

        # 浏览器优先回收；异常或超时不阻断数据库、调度器和渠道资源关闭。
        try:
            if _browser_resume_worker is not None:
                await _browser_resume_worker.stop()
            if _browser_reaper is not None:
                await _browser_reaper.stop()
            if _browser_reaper_manager is not None:
                await _browser_reaper_manager.close_all("shutdown")
            from src.tools.browser.human_control import stop_all_completion_monitors
            await stop_all_completion_monitors()
            from src.tools.browser.run_manager import close_all_active_browser_managers
            await close_all_active_browser_managers("shutdown")
        except Exception as e:
            logger.warning("browser run reaper 关闭异常: type={}", type(e).__name__)
        await _close_browser_runs_on_shutdown()

        # 关闭 archive poller 已迁移至 background runner，HTTP worker 不再启停 poller。

        # Close PostgreSQL connection pool
        close_postgres_pool()

        # Close logs database pool
        try:
            from src.db.database import close_logs_pool
            close_logs_pool()
        except Exception:
            pass

        # 定时任务调度器已迁移至 background runner，HTTP worker 不再负责 shutdown。

        # 关闭所有缓存的渠道 adapter（释放 httpx 连接池）
        try:
            from src.saas.services.channel_factory import ChannelFactory
            await ChannelFactory.close_all()
        except Exception as e:
            logger.warning(f"关闭渠道 adapter 失败: {e}")



# ============== File Upload Configuration ==============
import shutil
from pathlib import Path

# 上传文件存储目录（基于项目根目录，不受 cwd 影响）
# 新结构: storage/tenants/{tenant_id}/conversation/ (遵循租户附件存储规范)
# 新写入统一走 src.core.storage.ensure_tenant_storage_dir。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 新版租户附件根目录（写入路径，由 ensure_tenant_storage_dir 创建子目录）
TENANTS_STORAGE_DIR = _PROJECT_ROOT / "storage" / "tenants"
TENANTS_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _check_tenant_credit_blocked(tenant_id: Optional[str]) -> Optional[JSONResponse]:
    """租户积分余额硬阻断检查（#37 Phase 4）

    在 /api/chat 与 /api/chat/stream 入口处调用：
    - SaaS 模式未启用：放行（返回 None）
    - tenant_id 为空（演示/匿名）：放行
    - 租户余额 > 0：放行
    - 租户余额 ≤ 0：返回 403 JSONResponse，阻断对话

    返回 None 表示放行，返回 JSONResponse 表示阻断（直接 return 给前端）。
    """
    if not settings.saas.enabled:
        return None
    if not tenant_id:
        return None
    try:
        from src.saas.db.tenant_db import TenantDB
        tenant = TenantDB.get_by_id(tenant_id)
        if not tenant:
            # 租户不存在：交给后续流程处理（最终会 404），此处放行
            return None
        credit_balance = float(tenant.get("credit_balance") or 0)
        if credit_balance <= 0:
            logger.warning(f"租户 {tenant_id} 积分余额耗尽（balance={credit_balance}），阻断对话")
            return JSONResponse({
                "success": False,
                "error": "积分余额已耗尽",
                "details": "积分余额已耗尽，无法继续对话，请联系管理员充值",
                "code": "NO_CREDIT",
            }, status_code=403)
    except Exception as e:
        logger.error(f"租户积分余额检查失败 tenant_id={tenant_id}: {e}")
        # 检查异常时不阻断，避免误伤正常用户
        return None
    return None


def _get_tenant_upload_dir() -> Path:
    """获取当前会话的上传目录（遵循租户附件存储规范）

    路径: storage/tenants/{tenant_id}/conversation/
    无 tenant_id（演示/匿名/单租户模式）: storage/tenants/_anonymous/conversation/

    user_id 不进入路径，避免目录碎片化；user_id 仅作为元数据写入 Redis。
    """
    from src.core.storage import ensure_tenant_storage_dir
    from src.saas.context import get_current_tenant_id, get_current_user_id
    tenant_id = get_current_tenant_id() or "_anonymous"
    # user_id 仅作元数据，不进路径（保持兼容性，调用方仍可通过 ContextVar 取到）
    _ = get_current_user_id()
    return Path(ensure_tenant_storage_dir(tenant_id, "conversation"))

# 已上传的文件元数据已迁移到 Redis: uploaded_file:{file_id}, TTL=86400s


# Create FastAPI app
app = FastAPI(
    title=settings.app.name,
    version=settings.app.version,
    description="Enterprise Employee Intelligent Agent System",
    lifespan=lifespan,
)

# Add CORS middleware
cors_origins = settings.cors.allowed_origins
# 支持环境变量覆盖：CORS_ORIGINS=http://a.com,http://b.com
_env_origins = os.getenv("CORS_ORIGINS")
if _env_origins:
    cors_origins = [o.strip() for o in _env_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add SaaS tenant context middleware (conditionally enabled)
if settings.saas.enabled:
    from src.saas.middleware import TenantContextMiddleware
    app.add_middleware(TenantContextMiddleware)
    logger.info("SaaS tenant context middleware enabled")


# ==================== Health Check ====================

@app.get("/health")
async def health_check():
    """Health check endpoint（含数据库连接池状态）"""
    from src.db.database import get_postgres_pool_status, health_check_pool

    pool_status = get_postgres_pool_status()
    db_healthy = pool_status.get("initialized", False) and pool_status.get("pool_available", 0) >= 0

    return {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.app.version,
        "provider": settings.llm.provider,
        "db_pool": pool_status,
    }


@app.get("/health/db")
async def health_check_db():
    """数据库连接池详细健康检查（检测并清理坏连接）"""
    from src.db.database import get_postgres_pool_status, health_check_pool

    pool_status = get_postgres_pool_status()
    pool_health = health_check_pool()

    return {
        "pool_status": pool_status,
        "pool_health": pool_health,
    }


@app.get("/api/clear_cache")
async def clear_all_cache(request: Request):
    """清空所有 Redis 缓存（需要平台管理员登录）

    按 REDIS_KEY_PREFIX 隔离清理，用于解决缓存与数据库不一致问题。
    不使用 flushdb，避免误删其他系统共享同一 Redis 实例的键。
    """
    from src.saas.api.tenant_auth import require_admin
    from src.core.redis_client import redis_client

    # 验证平台管理员登录
    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "message": "仅限平台管理员访问"}

    result = redis_client.clear_all()

    logger.info(
        f"后端日志：清空所有 Redis 缓存，"
        f"已删除 {result['deleted']} 个 key，"
        f"内存降级清理 {result['fallback_cleared']} 个 key"
    )
    return {
        "success": True,
        "deleted": result["deleted"],
        "keys_found": result["keys_found"],
        "fallback_cleared": result["fallback_cleared"],
    }


@app.get("/")
async def root():
    """Root endpoint - 演示模式开启时返回JSON，关闭时返回租户入口页面"""
    return {
        "name": settings.app.name,
        "version": settings.app.version,
        "status": "running",
    }


@app.post("/api/tenant/enter")
async def tenant_enter(request: Request):
    """
    验证租户代码并返回重定向URL
    """
    from src.saas.db.tenant_db import TenantDB
    from src.saas.models.enums import TenantStatus

    try:
        data = await request.json()
        tenant_code = data.get("tenant_code", "").strip().upper()

        if not tenant_code:
            return JSONResponse({
                "success": False,
                "error": "租户代码不能为空"
            }, status_code=400)

        # 格式验证：4-8位字母数字
        import re
        if not re.match(r'^[A-Z0-9]{4,8}$', tenant_code):
            return JSONResponse({
                "success": False,
                "error": "租户代码格式无效（需4-8位字母数字）"
            }, status_code=400)

        # 查询租户
        tenant = TenantDB.get_by_code(tenant_code)
        if not tenant:
            return JSONResponse({
                "success": False,
                "error": "租户代码不存在"
            }, status_code=404)

        # 检查租户状态
        status = tenant.get("status")
        if status != TenantStatus.ACTIVE.value:
            if status == TenantStatus.SUSPENDED.value:
                return JSONResponse({
                    "success": False,
                    "error": "租户已被暂停，请联系管理员"
                }, status_code=403)
            elif status == TenantStatus.EXPIRED.value:
                return JSONResponse({
                    "success": False,
                    "error": "租户已过期，请联系管理员续期"
                }, status_code=403)
            else:
                return JSONResponse({
                    "success": False,
                    "error": f"租户状态异常：{status}"
                }, status_code=403)

        # 检查租户是否已过期（expire_at字段）
        expire_at = tenant.get("expire_at")
        if expire_at:
            from datetime import datetime
            try:
                expire_date = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                if expire_date < datetime.now():
                    return JSONResponse({
                        "success": False,
                        "error": "租户已过期，请联系管理员续期"
                    }, status_code=403)
            except Exception:
                # 日期解析失败，忽略过期检查
                pass

        # 返回重定向URL
        tenant_id = tenant["tenant_id"]
        return JSONResponse({
            "success": True,
            "redirect_url": f"/t/{tenant_id}",
            "tenant_id": tenant_id,
            "company_name": tenant.get("company_name", "")
        })

    except Exception as e:
        logger.error(f"Tenant enter error: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误"
        }, status_code=500)


# ==================== Web Chat API ====================

@app.post("/api/chat")
async def chat(request: Request):
    """
    Web chat API

    For direct web client calls
    """
    try:
        data = await request.json()
        user_input = data.get("message", "")
        user_id = data.get("user_id", "web_user")
        session_id = data.get("session_id")
        subagent_name = data.get("subagent")

        if not user_input:
            return JSONResponse({
                "success": False,
                "error": "Message cannot be empty",
            })

        # 从请求头解析用户身份
        current_user = auth.get_current_user(request)

        # 权限检查：数字员工访问授权（演示用户tenant_id='demo'豁免）
        if subagent_name and current_user and current_user.get("tenant_id") != "demo":
            from src.saas.permissions.checker import check_agent_access
            if not check_agent_access(subagent_name, current_user):
                return JSONResponse({
                    "success": False,
                    "error": "未授权使用数字员工",
                    "details": "您没有权限访问此数字员工，请联系管理员申请授权",
                }, status_code=403)

        # 积分余额硬阻断：SaaS 模式下余额 ≤ 0 拒绝对话
        _tenant_id_for_credit = getattr(request.state, 'tenant_id', None) or (current_user.get("tenant_id") if current_user else None)
        credit_block_response = _check_tenant_credit_blocked(_tenant_id_for_credit)
        if credit_block_response is not None:
            return credit_block_response

        agent_user = None
        if current_user:
            from src.models.user import User
            agent_user = User(
                user_id=current_user["user_id"],
                name=current_user.get("username", current_user.get("phone", "unknown")),
                phone=current_user.get("phone"),
            )

        # Generate session ID if not provided
        if not session_id:
            session_id = f"web_{user_id}_{uuid.uuid4().hex[:8]}"

        # 通过默认路由获取 Agent
        _tenant_id = getattr(request.state, 'tenant_id', None)
        # 未指定子智能体时，检查租户是否只有 1 个可用智能体，自动路由
        subagent_name = _resolve_default_subagent(subagent_name, _tenant_id, current_user)
        agent = agent_router.get_agent(subagent_name, session_id, tenant_id=_tenant_id)

        # 注入 tenant_id（供租户 skills 按需加载使用）
        if _tenant_id and not agent._init_tenant_id:
            agent._init_tenant_id = _tenant_id

        # Process message — process_message_sync is now pure async, no thread needed
        response_text = await agent.process_message_sync(
            user_input=user_input,
            session_id=session_id,
            user=agent_user,
        )

        return JSONResponse({
            "success": True,
            "response": response_text,
            "session_id": session_id,
            "agent_type": agent.mode.value,
        })
    
    except Exception as e:
        logger.error(f"Failed to process chat request: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.get("/api/tools")
async def list_tools():
    """
    Get available tools list
    """
    tools = master_agent.tool_registry.list_tools()
    return JSONResponse({
        "tools": tools,
        "count": len(tools),
    })


# ==================== File Upload API ====================

from fastapi import UploadFile, File
from src.config.settings import settings


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件接口

    上传文件并返回文件ID，供后续聊天使用

    返回:
    {
        "success": true,
        "file_id": "file_xxx",
        "name": "文件名",
        "size": 12345,
        "mime_type": "application/pdf"
    }
    """
    try:
        # 生成唯一文件ID
        file_id = f"file_{uuid.uuid4().hex[:12]}"

        # 获取文件扩展名
        suffix = Path(file.filename or "unknown").suffix.lower()

        # 验证文件大小
        max_size = settings.storage.max_general_file_size
        if file.size and file.size > max_size:
            max_size_mb = max_size / 1024 / 1024
            return JSONResponse(
                status_code=200,
                content={
                    "success": False,
                    "error": f"文件过大，最大支持 {max_size_mb:.0f}MB"
                }
            )

        # 保存文件（租户模式下按 tenant_id 隔离）
        upload_dir = _get_tenant_upload_dir()
        file_path = upload_dir / f"{file_id}{suffix}"
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 获取文件信息
        file_size = file_path.stat().st_size

        # 根据扩展名判断文件类型
        mime_type_map = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.txt': 'text/plain',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.html': 'text/html',
            '.htm': 'text/html',
            '.mp3': 'audio/mpeg',
            '.mp4': 'video/mp4',
        }
        mime_type = mime_type_map.get(suffix, 'application/octet-stream')

        # 保存文件信息到 Redis
        file_info = {
            "file_id": file_id,
            "name": file.filename or "unknown",
            "path": str(file_path.absolute()),
            "size": file_size,
            "mime_type": mime_type,
            "type": "image" if mime_type.startswith("image/") else "file"
        }
        key = redis_client.make_key("uploaded_file", file_id)
        for field, value in file_info.items():
            redis_client.hset(key, field, value)
        redis_client.expire(key, 86400)

        logger.info(f"文件上传成功: {file.filename}, file_id: {file_id}, size: {file_size}")

        return JSONResponse({
            "success": True,
            "file_id": file_id,
            "name": file.filename,
            "size": file_size,
            "mime_type": mime_type,
            "type": file_info["type"]
        })

    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        return JSONResponse({
            "success": False,
            "error": "文件上传失败",
            "debug": str(e)
        }, status_code=500)


@app.get("/api/upload/{file_id}")
async def get_uploaded_file(file_id: str):
    """
    获取已上传文件的信息

    返回文件的元信息（不返回文件内容）
    """
    file_info = _get_file_info(file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="文件不存在")
    return JSONResponse({
        "success": True,
        **file_info
    })


@app.delete("/api/upload/{file_id}")
async def delete_uploaded_file(file_id: str):
    """
    删除已上传的文件
    """
    file_info = _get_file_info(file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        file_path = Path(file_info["path"])
        if file_path.exists():
            file_path.unlink()
            # 清理空的租户目录
            try:
                parent_dir = file_path.parent
                if parent_dir != TENANTS_STORAGE_DIR and parent_dir.is_dir() and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
                    logger.info(f"已清理空目录: {parent_dir}")
            except OSError:
                pass
        # Also remove from Redis if present
        key = redis_client.make_key("uploaded_file", file_id)
        redis_client.delete(key)

        return JSONResponse({
            "success": True,
            "message": "文件已删除"
        })
    except Exception as e:
        logger.error(f"删除文件失败: {e}")
        return JSONResponse({
            "success": False,
            "error": "删除文件失败",
            "debug": str(e)
        }, status_code=500)


def _get_file_info(file_id: str) -> dict | None:
    """
    获取文件信息：优先从 Redis 获取，若不存在则尝试从磁盘恢复。
    服务重启后 Redis 中仍有数据（TTL=86400s），但文件也可能仍在磁盘上。
    """
    # 优先从 Redis 获取
    key = redis_client.make_key("uploaded_file", file_id)
    cached = redis_client.hgetall(key)
    if cached:
        # 转换数值类型
        if "size" in cached:
            try:
                cached["size"] = int(cached["size"])
            except (ValueError, TypeError):
                pass
        return cached

    # 尝试从磁盘目录扫描恢复（包括租户/用户子目录，最多3层）
    # 扫描新目录 storage/tenants/{tenant}/conversation/ 等
    search_dirs: list[Path] = []
    if TENANTS_STORAGE_DIR.exists():
        for d1 in TENANTS_STORAGE_DIR.iterdir():
            if d1.is_dir():
                for d2 in d1.iterdir():
                    if d2.is_dir():
                        search_dirs.append(d2)
                        for d3 in d2.iterdir():
                            if d3.is_dir():
                                search_dirs.append(d3)

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for f in search_dir.iterdir():
            if f.is_file() and f.stem == file_id:
                suffix = f.suffix.lower()
                mime_type_map = {
                    '.pdf': 'application/pdf',
                    '.doc': 'application/msword',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.xls': 'application/vnd.ms-excel',
                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.txt': 'text/plain',
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.gif': 'image/gif',
                    '.html': 'text/html',
                    '.htm': 'text/html',
                    '.mp3': 'audio/mpeg',
                    '.mp4': 'video/mp4',
                }
                mime_type = mime_type_map.get(suffix, 'application/octet-stream')
                file_info = {
                    "file_id": file_id,
                    "name": f.name,
                    "path": str(f.absolute()),
                    "size": f.stat().st_size,
                    "mime_type": mime_type,
                    "type": "image" if mime_type.startswith("image/") else "file"
                }
                # 缓存回 Redis，避免重复磁盘扫描
                for field, value in file_info.items():
                    redis_client.hset(key, field, value)
                redis_client.expire(key, 86400)
                return file_info

    return None


@app.get("/api/files/{file_id}")
async def serve_file(file_id: str):
    """
    内联方式提供文件（用于浏览器预览图片/PDF等）
    """
    file_info = _get_file_info(file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")

    file_path = Path(file_info["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")

    return FileResponse(
        path=str(file_path),
        media_type=file_info["mime_type"],
        filename=file_info["name"],
        content_disposition_type="inline"
    )


@app.get("/api/files/{file_id}/download")
async def download_file(file_id: str):
    """
    以附件方式下载文件
    """
    file_info = _get_file_info(file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")

    file_path = Path(file_info["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")

    return FileResponse(
        path=str(file_path),
        media_type=file_info["mime_type"],
        filename=file_info["name"],
        content_disposition_type="attachment"
    )


# ==================== SSE Chat API ====================

@app.post("/api/chat/stream")
async def chat_stream(http_request: Request, request: ChatRequest):
    """
    SSE流式聊天接口

    前端通过EventSource连接此接口，接收实时的进度和响应消息

    请求体:
    {
        "message": "用户消息",
        "session_id": "会话ID（可选）",
        "files": [] // 可选的文件列表
    }

    响应: Server-Sent Events 流
    """
    # 从请求头解析用户身份
    current_user = auth.get_current_user(http_request)
    user_id = current_user["user_id"] if current_user else "anonymous"

    # 记录用户消息接收时间，作为 user 消息的 created_at（避免与助手回复落入同一事务导致时间戳相同）
    user_message_time = datetime.now()

    # 权限检查：数字员工访问授权（演示用户tenant_id='demo'豁免）
    if request.subagent and current_user and current_user.get("tenant_id") != "demo":
        from src.saas.permissions.checker import check_agent_access
        if not check_agent_access(request.subagent, current_user):
            from fastapi.responses import JSONResponse
            return JSONResponse({
                "success": False,
                "error": "未授权使用数字员工",
                "details": "您没有权限访问此数字员工，请联系管理员申请授权",
            }, status_code=403)

    instance_id = request.instance_id

    # 获取当前租户ID（所有分支共享）
    from src.saas.context import get_current_tenant_id
    chat_tenant_id = get_current_tenant_id()

    # 积分余额硬阻断：SaaS 模式下余额 ≤ 0 拒绝对话
    credit_block_response = _check_tenant_credit_blocked(chat_tenant_id)
    if credit_block_response is not None:
        return credit_block_response

    # 如果没有传入 session_id，创建一个新的会话记录到数据库
    if not request.session_id:
        # 从用户第一条消息提取前20个字作为会话标题
        first_message = request.message.strip()
        title = first_message[:20] + ("..." if len(first_message) > 20 else "")

        # 从请求头获取用户身份后创建会话
        if current_user:
            session = SessionDB.create(
                user_id=user_id,
                title=title,
                context_data={"user_info": {"user_id": user_id, "username": current_user.get("username")}},
                tenant_id=chat_tenant_id,
                instance_id=instance_id
            )
            if session:
                session_id = session["session_id"]
                logger.info(f"后端日志：创建新会话 session_id={session_id} user_id={user_id} title={title}")
            else:
                session_id = sse_manager.get_or_create_session(None)
        else:
            # 没有登录的用户，使用内存中的 session
            session_id = sse_manager.get_or_create_session(None)
            logger.info(f"后端日志：匿名用户使用内存会话 session_id={session_id}")
    else:
        session_id = sse_manager.get_or_create_session(request.session_id)

    # 活动工具挂起期间禁止同一会话启动第二个 Agent loop。
    if chat_tenant_id and session_id:
        from src.tools.browser.resume_store import get_active_session_suspension
        active_suspension = await get_active_session_suspension(chat_tenant_id, session_id)
        if active_suspension:
            return JSONResponse({
                "success": False, "error_code": "TOOL_WAITING_HUMAN",
                "error": "当前浏览器任务正在等待你的操作",
                "assistance_id": active_suspension["assistance_id"],
            }, status_code=409)

    # 处理附件
    attachments = None
    if request.files:
        attachments = []
        for f in request.files:
            att = {
                "type": f.get("type", "file"),
                "name": f.get("name", "unknown"),
                "mime_type": f.get("mime_type", ""),
            }

            # 如果提供了 file_id，使用 Redis 中存储的实际路径
            file_id = f.get("file_id")
            if file_id:
                file_info = _get_file_info(file_id)
                if file_info:
                    att["path"] = file_info["path"]
                att["file_id"] = file_id

            if "content" in f:
                att["content"] = f["content"]
            attachments.append(att)

    # 添加SSE客户端
    logger.info(f"[SSE-SETUP] Adding SSE client for session_id={session_id}")
    client_queue = sse_manager.add_sse_client(session_id)
    logger.info(f"[SSE-SETUP] SSE client added for session_id={session_id}")

    # 构建带文件路径的上下文消息
    file_context = ""
    if attachments:
        file_paths = []
        for att in attachments:
            path = att.get("path", "")
            name = att.get("name", "")
            if path:
                file_paths.append(f"  - {name}: {path}")
            else:
                file_paths.append(f"  - {name}")
        if file_paths:
            file_context = f"\n\n【已上传文件路径】\n" + "\n".join(file_paths) + "\n请使用上述路径读取文件内容。"

    # 添加用户消息到历史（包含文件路径上下文）
    full_message = request.message + file_context
    sse_manager.add_to_history(session_id, "user", full_message)

    # 通过租户实例管理器或默认路由获取 Agent
    _tenant_id = getattr(http_request.state, 'tenant_id', None)
    # 未指定子智能体时，检查租户是否只有 1 个可用智能体，自动路由
    resolved_subagent = _resolve_default_subagent(request.subagent, _tenant_id, current_user)
    agent = agent_router.get_agent(resolved_subagent, session_id, tenant_id=_tenant_id)

    # 注入 tenant_id（供租户 skills 按需加载使用）
    if _tenant_id and not agent._init_tenant_id:
        agent._init_tenant_id = _tenant_id

    async def event_generator():
        """SSE事件生成器 — 直接 async for 迭代，无需线程"""
        sse_start_time = datetime.now()
        logger.info(f"[SSE] event_generator started, session_id={session_id}")

        sse_manager.clear_cancelled(session_id)

        # 准备 record_service 和 agent_user
        current_user_for_record = auth.get_current_user(http_request)
        record_user_id = current_user_for_record["user_id"] if current_user_for_record else (request.user_id or "anonymous")

        from src.models.user import User
        agent_user = None
        if current_user_for_record:
            agent_user = User(
                user_id=current_user_for_record["user_id"],
                name=current_user_for_record.get("username", current_user_for_record.get("phone", "unknown")),
                phone=current_user_for_record.get("phone"),
            )

        record_service = SessionRecordManager.start_record(
            session_id=session_id,
            user_id=record_user_id,
            user_message=full_message,
            tenant_id=chat_tenant_id
        )
        record_service.set_model(agent.llm.get_model_name())
        record_service.set_provider(agent.llm.get_provider_name())

        # TraceCollector 已下沉到 Agent.process_message 内部（见 observability-channel-sessions-design.md 方案 C）
        # record_service 仍然是 trace 上下文的来源，必须保留。

        response_parts = []
        progress_events = []
        tool_messages_collected = []  # 收集本轮 tool 消息序列，供事务持久化
        error_occurred = None
        suspended_for_browser = False
        suspension_messages_persisted = False

        try:
            # 发送初始连接成功消息
            try:
                init_msg = f"data: {json.dumps({'type': 'connected', 'session_id': session_id, 'agent_type': agent.mode.value}, ensure_ascii=False)}\n\n"
                yield init_msg
                logger.info(f"[SSE] Initial connected message sent, session_id={session_id}")
            except Exception as e:
                logger.error(f"[SSE] Failed to send initial message: {e}", exc_info=True)
                return

            # 直接在 FastAPI event loop 中迭代 agent
            session_queue.mark_responding(session_id)
            try:
                async for event in agent.process_message(
                    user_input=full_message,
                    session_id=session_id,
                    user=agent_user,
                    attachments=attachments,
                    cancel_check=lambda: sse_manager.is_cancelled(session_id) or session_queue.check_cancel(session_id),
                    video_params=request.video_params,
                ):
                    event_type = event.get("type")
                    if event_type == "tool_messages":
                        tool_messages_collected.extend(event.get("messages", []))
                        if event.get("suspended"):
                            # assistance 已在 Redis 建立；必须先持久化原 user +
                            # assistant(tool_calls) 上下文，再把人工卡片交给客户端。
                            # 否则此处断线会让后台 continuation 无法找到原 tool_call_id。
                            suspended_batch = [{
                                "role": "user",
                                "content": full_message,
                                "metadata": {
                                    "progressMessages": [],
                                    **({"attachments": request.files} if request.files else {}),
                                },
                            }]
                            for tm in tool_messages_collected:
                                if tm.get("role") == "assistant" and tm.get("tool_calls"):
                                    metadata = {"tool_calls": tm["tool_calls"]}
                                    if tm.get("reasoning_content"):
                                        metadata["reasoning_content"] = tm["reasoning_content"]
                                    suspended_batch.append({
                                        "role": "assistant", "content": "",
                                        "metadata": metadata,
                                    })
                                elif tm.get("role") == "tool":
                                    content = tm.get("content", "")
                                    if isinstance(content, (dict, list)):
                                        content = json.dumps(
                                            content, ensure_ascii=False, default=str
                                        )
                                    suspended_batch.append({
                                        "role": "tool", "content": content,
                                        "metadata": {
                                            "tool_call_id": tm.get("tool_call_id", "")
                                        },
                                    })
                            created = await asyncio.to_thread(
                                MessageDB.create_batch_transactional,
                                session_id,
                                suspended_batch,
                            )
                            if created is None:
                                raise RuntimeError("SUSPENSION_PERSIST_FAILED")
                            suspension_messages_persisted = True
                    # 每个 event 直接序列化为 SSE 帧
                    try:
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except (BrokenPipeError, ConnectionResetError, OSError) as e:
                        logger.warning(f"[SSE] Client disconnected during event yield, session_id={session_id}, error: {e}")
                        sse_manager.cancel_session(session_id)
                        return

                    # 收集 response 和 progress 数据
                    if event_type == "response":
                        response_parts.append(event.get("data", ""))
                    elif event_type == "tool_result":
                        record_service.handle_progress_event(event)
                        progress_events.append(event)
                    elif event_type in ("tool_start", "progress", "thinking", "clarification"):
                        record_service.handle_progress_event(event)
                        progress_events.append(event)
                    elif event_type == "browser_human_required":
                        suspended_for_browser = True

            except asyncio.CancelledError:
                logger.info(f"[SSE] Agent cancelled by user, session_id={session_id}")
                error_occurred = "Cancelled by user"
                try:
                    yield f"data: {json.dumps({'type': 'cancelled', 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                if SessionRecordManager.get_current_record():
                    SessionRecordManager.get_current_record().mark_error("Cancelled by user")
                    SessionRecordManager.end_record()
            except Exception as e:
                logger.error(f"[SSE] Agent error, session_id={session_id}, error: {type(e).__name__}: {e}", exc_info=True)
                error_occurred = str(e)
                try:
                    yield f"data: {json.dumps({'type': 'error', 'data': str(e), 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                if SessionRecordManager.get_current_record():
                    SessionRecordManager.get_current_record().mark_error(str(e))
                    SessionRecordManager.end_record()

            # 完成记录
            full_response = "".join(response_parts)
            if not error_occurred and full_response:
                record_service.complete(full_response)
                SessionRecordManager.end_record()
            elif suspended_for_browser and not error_occurred:
                # 本次 HTTP/SSE 已正常结束，但原工具仍挂起；关闭请求级记录，
                # 后台 continuation 会以同一 session 独立完成后续持久化。
                SessionRecordManager.end_record()

            # TraceCollector.on_complete 已在 Agent.process_message 的 finally 中调用。

            # 发送完成消息
            try:
                yield f"data: {json.dumps({'type': 'complete', 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
                sse_duration = (datetime.now() - sse_start_time).total_seconds()
                logger.info(f"[SSE] Stream completed, session_id={session_id}, duration={sse_duration:.2f}s")
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"[SSE] Client disconnected before complete, session_id={session_id}, error: {e}")
                return

            # 保存完整响应到内存历史
            sse_manager.add_to_history(session_id, "assistant", full_response)

            # 保存消息到 DB（事务：user + tool 消息序列 + assistant 最终回复，要么全成功要么全失败）
            if (
                (full_response or suspended_for_browser)
                and not error_occurred
                and not suspension_messages_persisted
            ):
                try:
                    user_metadata = {"progressMessages": []}
                    if request.files:
                        user_metadata["attachments"] = request.files

                    # 提取可下载文件
                    downloadable_files = []
                    download_tool_names = {"write", "cp"}
                    for evt in progress_events:
                        if (evt.get("type") == "tool_result"
                            and evt.get("toolName") in download_tool_names
                            and evt.get("success") is True):
                            result = evt.get("result", {})
                            if result.get("file_id"):
                                downloadable_files.append({
                                    "file_id": result["file_id"],
                                    "file_name": result.get("download_file_name") or result.get("file_name", "未命名文件"),
                                    "file_size": result.get("file_size", 0),
                                    "download_url": result.get("download_url", ""),
                                    "mime_type": result.get("mime_type", ""),
                                })

                    assistant_metadata = {"progressMessages": progress_events}
                    if downloadable_files:
                        assistant_metadata["downloadableFiles"] = downloadable_files

                    # 构造事务消息列表：user + 本轮 tool 消息序列 + assistant 最终回复
                    # 助手回复完成时间，作为 assistant / tool 消息的 created_at
                    assistant_message_time = datetime.now()
                    batch_messages = [
                        {"role": "user", "content": full_message, "metadata": user_metadata,
                         "created_at": user_message_time},
                    ]
                    for tm in tool_messages_collected:
                        if tm.get("role") == "assistant" and tm.get("tool_calls"):
                            # assistant(tool_calls): content 清空（丢弃中间思考），reasoning_content 放 metadata
                            tm_metadata = {"tool_calls": tm["tool_calls"]}
                            if tm.get("reasoning_content"):
                                tm_metadata["reasoning_content"] = tm["reasoning_content"]
                            batch_messages.append({
                                "role": "assistant",
                                "content": "",
                                "metadata": tm_metadata,
                                "created_at": assistant_message_time,
                            })
                        elif tm.get("role") == "tool":
                            # tool result content 可能是 dict（工具返回的 JSON），持久化前转字符串
                            tc = tm.get("content", "")
                            if isinstance(tc, (dict, list)):
                                tc = json.dumps(tc, ensure_ascii=False, default=str)
                            batch_messages.append({
                                "role": "tool",
                                "content": tc,
                                "metadata": {"tool_call_id": tm.get("tool_call_id", "")},
                                "created_at": assistant_message_time,
                            })
                    if full_response:
                        batch_messages.append({
                            "role": "assistant",
                            "content": full_response,
                            "metadata": assistant_metadata,
                            "created_at": assistant_message_time,
                        })

                    created = MessageDB.create_batch_transactional(session_id, batch_messages)
                    if created is None:
                        raise RuntimeError("create_batch_transactional returned None (transaction rolled back)")
                    logger.info(
                        f"[SSE] Messages saved to DB (transactional, {len(created)} rows), session_id={session_id}"
                    )
                except Exception as e:
                    logger.error(f"[SSE] Failed to save messages: {e}", exc_info=True)

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            logger.error(f"[SSE] SSE chat error, session_id={session_id}, error: {e}")
            logger.error(f"[SSE] Traceback:\n{error_trace}")
            try:
                yield f"data: {json.dumps({'type': 'error', 'data': str(e), 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.warning(f"[SSE] Cannot send error message to client, already disconnected, session_id={session_id}")
        finally:
            session_queue.mark_idle(session_id)
            sse_manager.remove_sse_client(session_id, client_queue)
            logger.info(f"[SSE] SSE cleanup completed, session_id={session_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """获取聊天历史"""
    history = sse_manager.get_history(session_id)
    if not history:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse({
        "session_id": session_id,
        "history": history
    })


@app.delete("/api/chat/session/{session_id}")
async def delete_chat_session(session_id: str):
    """删除会话"""
    with sse_manager.lock:
        if session_id in sse_manager.sessions:
            del sse_manager.sessions[session_id]
        if session_id in sse_manager.sse_connections:
            del sse_manager.sse_connections[session_id]
    return JSONResponse({"status": "deleted", "session_id": session_id})


@app.post("/api/chat/{session_id}/cancel")
async def cancel_chat_generation(session_id: str):
    """用户主动取消当前正在生成的会话"""
    sse_manager.cancel_session(session_id)
    logger.info(f"[Cancel] User requested cancel generation: session_id={session_id}")
    return JSONResponse({
        "success": True,
        "message": "Cancel request accepted",
        "session_id": session_id
    })


# ==================== Auth & Session API ====================

app.include_router(auth.router)
app.include_router(session_api.router)
app.include_router(channels_api.router)
app.include_router(customer.router)
app.include_router(customer_followup.router)
app.include_router(complaint_handling.router)
app.include_router(after_sales.router)
app.include_router(scheduled_task.router)
app.include_router(email_settings.router)
app.include_router(knowledge_router)
app.include_router(admin_subagent.router)
app.include_router(admin_redis.router)
app.include_router(subagent_extra.router)

# Prompt 版本管理 API
from src.api import prompt_management
app.include_router(prompt_management.admin_router)
app.include_router(prompt_management.tenant_router)

# 子智能体定义管理 API
from src.api import agent_definitions
app.include_router(agent_definitions.router)
from src.api import agent_definition_sections
app.include_router(agent_definition_sections.router)
app.include_router(travel_quote.router)
app.include_router(subagent.router)

# Word 文档处理 API
from src.api import word as word_api
app.include_router(word_api.router)

# 子智能体环境变量管理 API
from src.api import subagent_env_var
app.include_router(subagent_env_var.router)

# 子智能体知识库关联 API
from src.api import subagent_knowledge_source
app.include_router(subagent_knowledge_source.router)

# 子智能体模板文件 API（定制提示词页模板上传）
from src.api import subagent_template_file
app.include_router(subagent_template_file.router)

from src.api import tenant_config_file
app.include_router(tenant_config_file.router)

# 平台管理报表 API
from src.api import admin_reports
app.include_router(admin_reports.router)

# 平台错误日志管理 API
from src.api import admin_error_logs
app.include_router(admin_error_logs.router)
app.include_router(monitor_api.router)
app.include_router(social_media_api.router)

# 数据分析 API
from src.api import data_analysis
app.include_router(data_analysis.router)

# 长期记忆 API
from src.api import memory as memory_api
app.include_router(memory_api.router)
from src.api import browser_runs as browser_runs_api
app.include_router(browser_runs_api.router)
app.include_router(browser_runs_api.agent_router)

# 工作日报 API（个人日报 / 团队日报 / 推送配置）
from src.api import work_reports
app.include_router(work_reports.router)

# 工作成果 API（文件交付 / 复盘成果的列表 / 统计 / 详情 / 删除 / 手动复盘）
from src.api import work_outcomes
app.include_router(work_outcomes.router)

# SaaS 多租户 API（始终注册，未启用时返回友好提示）
from src.saas.api import tenant_auth, tenant_mgmt, subscriptions
from src.saas.api import channel_config, tenant_skills, channel_routes
from src.saas.api import tenant_users, usage_reports, permissions, reply_styles, external_customers, tenant_migration
from src.saas.api import context_compression_routes
from src.saas.api import billing_recharges, billing_balance
from src.saas.api.wecom_personal_rpa_routes import router as wecom_personal_rpa_router
from src.saas.api.wecom_personal_rpa_admin import router as wecom_personal_rpa_admin_router
app.include_router(tenant_auth.router)
app.include_router(tenant_mgmt.router)
app.include_router(subscriptions.router)
app.include_router(channel_config.router)
app.include_router(tenant_skills.router)
app.include_router(channel_routes.router)
app.include_router(wecom_personal_rpa_router)
app.include_router(wecom_personal_rpa_admin_router)
app.include_router(tenant_users.router)
app.include_router(usage_reports.router)
app.include_router(usage_reports.public_router)
app.include_router(permissions.router)
app.include_router(reply_styles.router)
app.include_router(external_customers.router)
app.include_router(tenant_migration.router)
app.include_router(context_compression_routes.router)
app.include_router(billing_recharges.router)
app.include_router(billing_balance.router)
# 协会客户端（docs/tools/association-client-design.md）
from src.api import client_routes  # noqa: E402
from src.saas.api import client_activation_mgmt  # noqa: E402
from src.saas.api import client_usage_mgmt  # noqa: E402
app.include_router(client_routes.router)
app.include_router(client_activation_mgmt.activation_router)
app.include_router(client_activation_mgmt.binding_router)
app.include_router(client_usage_mgmt.router)
# 视频生成工具（MVP 抽卡式，见 docs/system/content-production/mvp-design.md）
from src.api import video_gen as video_gen_api  # noqa: E402
app.include_router(video_gen_api.router)
# 视频创作智能体（会话化，见 docs/plans/plan-video-agent-phase1.md）
from src.api import video_agent as video_agent_api  # noqa: E402
app.include_router(video_agent_api.router)
# 本地工具基础设施 M0.3（见 docs/plans/recruiting/m03-implementation-spec.md）
from src.local_tools import api as local_tools_api  # noqa: E402
app.include_router(local_tools_api.router)

# Desktop Agent D1 is opt-in. Default production startup neither imports its
# module nor registers routes; changing the setting requires a process restart.
if settings.desktop_agent.enabled:
    from src.desktop_agent import api as desktop_agent_api  # noqa: E402
    app.include_router(desktop_agent_api.router)
# app.include_router(context_compression_routes.router)



# ==================== CLI Interface ====================

async def cli_chat():
    """Command line chat mode"""
    print("=" * 50)
    print(f" {settings.app.name} v{settings.app.version}")
    print(" CLI Chat Mode (type 'quit' to exit)")
    print("=" * 50)
    print(f" LLM Provider: {settings.llm.provider}")
    print(f" Tools: {', '.join(master_agent.tool_registry.list_tools())}")
    print()
    
    session_id = f"cli_{uuid.uuid4().hex[:8]}"
    
    while True:
        try:
            user_input = input("\nYou: ").strip()
            
            if user_input.lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break
            
            if not user_input:
                continue
            
            # Process message
            print("\nAssistant: ", end="")
            async for event in master_agent.process_message(user_input, session_id):
                if event.get("type") == "response":
                    print(event.get("data", ""), end="", flush=True)
                elif event.get("type") == "tool_start":
                    print(f"\n  [tool] {event.get('toolName')}")
                elif event.get("type") == "progress":
                    print(f"\n  [progress] {event.get('data', '')}")
            print()
        
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


def main():
    """Main entry point"""
    import uvicorn
    
    # Check if CLI mode
    if os.getenv("CLI_MODE") == "true":
        asyncio.run(cli_chat())
    else:
        # Start web server
        uvicorn.run(
            "src.main:app",
            host=settings.app.host,
            port=settings.app.port,
            reload=settings.app.debug,
            access_log=False,
        )


if __name__ == "__main__":
    main()
