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
from src.models.message import UnifiedMessage
from src.channels.wecom.adapter import WeComAdapter
from src.channels.manager import channel_manager
from src.db.database import init_database, init_postgres_pool, close_postgres_pool
from src.api import auth, session as session_api, credentials, customer, scheduled_task, email_settings
from src.api import admin_subagent, subagent, subagent_extra, travel_quote
from src.knowledge.api import router as knowledge_router
from src.db.models import SessionDB, MessageDB
from src.channels import callback as channels_api
from src.services.session_record import SessionRecordManager


# ============== SSE Session Management ==============

class SSEConnectionManager:
    """管理SSE连接和会话"""

    def __init__(self):
        # session_id -> {"history": [], "sse_queues": []}
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.sse_connections: Dict[str, List[queue.Queue]] = {}
        self.cancelled_sessions: set[str] = set()  # 被用户主动取消的会话
        self.lock = threading.Lock()

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

    def cancel_session(self, session_id: str) -> None:
        """标记会话为已取消，停止后续生成"""
        with self.lock:
            self.cancelled_sessions.add(session_id)
            logger.info(f"Session cancelled by user: {session_id}")

    def is_cancelled(self, session_id: str) -> bool:
        """检查会话是否已被取消"""
        with self.lock:
            return session_id in self.cancelled_sessions

    def clear_cancelled(self, session_id: str) -> None:
        """清除取消标记（生成完成后）"""
        with self.lock:
            self.cancelled_sessions.discard(session_id)

    def broadcast(self, session_id: str, event: Dict[str, Any]):
        with self.lock:
            queues = self.sse_connections.get(session_id, []).copy()
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

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


# ============== Pydantic Models ==============

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[str] = None  # 用于会话记录
    subagent: Optional[str] = None  # 子智能体名称（由前端从路由参数提取后传入）
    instance_id: Optional[str] = None  # 数字员工实例ID（用于并发控制锁）


class ChatResponse(BaseModel):
    session_id: str
    success: bool
    message: Optional[str] = None


# Initialize logging
setup_logging(
    log_level="DEBUG" if settings.app.debug else "INFO",
    log_dir="log/agent",
)

# WeCom adapter
wecom_adapter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # On startup
    logger.info(f"Starting {settings.app.name} v{settings.app.version}")
    logger.info(f"LLM Provider: {settings.llm.provider}, Model: {settings.llm.qwen.model}, Base URL: {settings.llm.qwen.base_url}")
    logger.info(f"Registered tools: {master_agent.tool_registry.list_tools()}")

    # Initialize PostgreSQL connection pool first (required by init_database)
    init_postgres_pool()

    # Initialize database (may use PostgreSQL)
    init_database()
    logger.info("Database initialized")

    # Initialize channel session manager (triggers lazy table creation)
    from src.channels.session import channel_session_manager
    channel_session_manager._ensure_tables()
    logger.info("Channel session manager initialized")

    # Initialize scheduled task scheduler
    try:
        from src.scheduler.manager import scheduled_task_manager
        scheduled_task_manager.start()
        logger.info("Scheduled task scheduler started")
    except Exception as e:
        logger.error(f"Failed to start scheduled task scheduler: {e}", exc_info=True)
    
    # Initialize WeCom adapter
    global wecom_adapter
    if settings.channels.wecom.enabled and settings.channels.wecom.corp_id:
        wecom_adapter = WeComAdapter()
        channel_manager.register(wecom_adapter)
        logger.info("WeCom adapter initialized")
    
    # Initialize Dingtalk adapter
    if settings.channels.dingtalk.enabled and settings.channels.dingtalk.app_key:
        from src.channels.dingtalk.adapter import DingtalkAdapter
        dingtalk_adapter = DingtalkAdapter()
        channel_manager.register(dingtalk_adapter)
        logger.info("Dingtalk adapter initialized")
    
    # Initialize Feishu adapter
    if settings.channels.feishu.enabled and settings.channels.feishu.app_id:
        from src.channels.feishu.adapter import FeishuAdapter
        feishu_adapter = FeishuAdapter()
        channel_manager.register(feishu_adapter)
        logger.info("Feishu adapter initialized")

    # Initialize SaaS instance manager
    if settings.saas.enabled:
        try:
            from src.saas.services.instance_manager import instance_manager
            restored = instance_manager.restore_running_instances()
            logger.info(f"SaaS instance manager initialized, restored {restored} instances")
        except Exception as e:
            logger.error(f"Failed to initialize SaaS instance manager: {e}", exc_info=True)

    # Start memory cleanup background task
    async def _memory_cleanup_loop():
        """后台定时清理过期会话记忆"""
        interval = settings.memory.cleanup_interval
        logger.info(f"Memory cleanup task started, interval={interval}s")
        while True:
            await asyncio.sleep(interval)
            try:
                cleaned = master_agent.memory.cleanup_expired()
                if cleaned > 0:
                    logger.debug(f"Memory cleanup: cleaned {cleaned} expired sessions")
            except Exception as e:
                logger.error(f"Memory cleanup error: {e}")
    asyncio.create_task(_memory_cleanup_loop())

    # Start instance lock cleanup background task
    if settings.saas.enabled:
        async def _instance_lock_cleanup_loop():
            """后台定时清理过期的智能体实例锁"""
            interval = 30  # 每30秒检查一次
            logger.info(f"Instance lock cleanup task started, interval={interval}s")
            from src.saas.services.instance_service import InstanceService
            while True:
                await asyncio.sleep(interval)
                try:
                    cleaned = InstanceService.cleanup_all_expired_locks()
                    if cleaned > 0:
                        logger.info(f"[InstanceLock] Cleaned up {cleaned} expired instance locks")
                except Exception as e:
                    logger.error(f"[InstanceLock] Cleanup error: {e}")
        asyncio.create_task(_instance_lock_cleanup_loop())

    yield

    # On shutdown
    logger.info("Application shutting down")

    # Close PostgreSQL connection pool
    close_postgres_pool()

    # Cleanup SaaS instances
    if settings.saas.enabled:
        try:
            from src.saas.services.instance_manager import instance_manager
            instance_manager.cleanup()
        except Exception:
            pass
    try:
        from src.scheduler.manager import scheduled_task_manager
        scheduled_task_manager.shutdown()
        logger.info("Scheduled task scheduler stopped")
    except Exception:
        pass

    # 关闭渠道适配器的 HTTP 连接池
    if wecom_adapter:
        try:
            await wecom_adapter.close()
        except Exception as e:
            logger.error(f"关闭 WeCom 适配器失败: {e}")


# ============== File Upload Configuration ==============
import shutil
from pathlib import Path

# 上传文件存储目录（基于项目根目录，不受 cwd 影响）
# 新结构: storage/uploads/{tenant_id}/conversation/ (有租户)
#          storage/uploads/conversation/ (无租户)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = _PROJECT_ROOT / settings.storage.uploads_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _get_tenant_upload_dir() -> Path:
    """获取当前用户的文件上传目录

    有租户有用户: storage/uploads/{tenant_id}/{user_id}/
    有租户无用户: storage/uploads/{tenant_id}/
    无租户有用户: storage/uploads/{user_id}/
    无租户无用户: storage/uploads/conversation/
    """
    from src.saas.context import get_current_tenant_id, get_current_user_id
    tenant_id = get_current_tenant_id()
    user_id = get_current_user_id()

    if tenant_id and user_id:
        upload_dir = UPLOAD_DIR / tenant_id / user_id
    elif tenant_id:
        upload_dir = UPLOAD_DIR / tenant_id
    elif user_id:
        upload_dir = UPLOAD_DIR / user_id
    else:
        upload_dir = UPLOAD_DIR / "conversation"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir

# 已上传的文件存储 {file_id: file_info}
uploaded_files: Dict[str, Dict[str, Any]] = {}


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


@app.get("/")
async def root():
    """Root endpoint - 演示模式开启时返回JSON，关闭时返回租户入口页面"""
    if settings.demo_enabled:
        return {
            "name": settings.app.name,
            "version": settings.app.version,
            "status": "running",
        }
    else:
        # 返回租户入口页面
        html_content = """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>租户登录 - AID Work Agent</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin: 0;
                    padding: 20px;
                }
                .container {
                    background: white;
                    border-radius: 12px;
                    padding: 40px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    max-width: 400px;
                    width: 100%;
                    text-align: center;
                }
                h1 {
                    color: #333;
                    margin-bottom: 10px;
                    font-size: 24px;
                }
                .subtitle {
                    color: #666;
                    margin-bottom: 30px;
                    font-size: 14px;
                }
                .form-group {
                    margin-bottom: 20px;
                    text-align: left;
                }
                label {
                    display: block;
                    margin-bottom: 6px;
                    color: #555;
                    font-weight: 500;
                }
                input {
                    width: 100%;
                    padding: 12px 16px;
                    border: 2px solid #ddd;
                    border-radius: 8px;
                    font-size: 16px;
                    transition: border-color 0.3s;
                    box-sizing: border-box;
                }
                input:focus {
                    outline: none;
                    border-color: #667eea;
                }
                .error {
                    color: #e74c3c;
                    font-size: 14px;
                    margin-top: 5px;
                    display: none;
                }
                button {
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 14px;
                    font-size: 16px;
                    font-weight: 600;
                    cursor: pointer;
                    width: 100%;
                    transition: transform 0.2s, box-shadow 0.2s;
                }
                button:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
                }
                button:disabled {
                    opacity: 0.6;
                    cursor: not-allowed;
                }
                .result {
                    margin-top: 20px;
                    padding: 12px;
                    border-radius: 8px;
                    display: none;
                }
                .success {
                    background: #d4edda;
                    color: #155724;
                }
                .error-result {
                    background: #f8d7da;
                    color: #721c24;
                }
                .redirect-link {
                    display: inline-block;
                    margin-top: 10px;
                    color: #667eea;
                    text-decoration: none;
                    font-weight: 500;
                }
                .redirect-link:hover {
                    text-decoration: underline;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>租户登录</h1>
                <p class="subtitle">请输入您的租户代码以继续</p>

                <form id="tenantForm">
                    <div class="form-group">
                        <label for="tenantCode">租户代码</label>
                        <input type="text" id="tenantCode" placeholder="例如：ALIBB" maxlength="8" autocomplete="off" autofocus>
                        <div id="formatError" class="error">请输入4-8位字母数字组合</div>
                    </div>
                    <button type="submit" id="submitBtn">进入租户</button>
                </form>

                <div id="result" class="result"></div>
            </div>

            <script>
                const tenantCodeInput = document.getElementById('tenantCode');
                const formatError = document.getElementById('formatError');
                const submitBtn = document.getElementById('submitBtn');
                const resultDiv = document.getElementById('result');
                const form = document.getElementById('tenantForm');

                // 格式验证：4-8位字母数字
                function validateFormat(code) {
                    return /^[A-Za-z0-9]{4,8}$/.test(code);
                }

                // 实时格式验证
                tenantCodeInput.addEventListener('input', function() {
                    const code = this.value.trim();
                    if (code === '') {
                        formatError.style.display = 'none';
                        submitBtn.disabled = false;
                        return;
                    }
                    if (!validateFormat(code)) {
                        formatError.style.display = 'block';
                        submitBtn.disabled = true;
                    } else {
                        formatError.style.display = 'none';
                        submitBtn.disabled = false;
                    }
                });

                // 表单提交
                form.addEventListener('submit', async function(e) {
                    e.preventDefault();
                    const code = tenantCodeInput.value.trim().toUpperCase();

                    if (!validateFormat(code)) {
                        showError('请输入有效的租户代码（4-8位字母数字）');
                        return;
                    }

                    submitBtn.disabled = true;
                    submitBtn.textContent = '验证中...';

                    try {
                        const response = await fetch('/api/tenant/enter', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({ tenant_code: code }),
                        });

                        const data = await response.json();

                        if (data.success) {
                            showSuccess(`验证成功，正在跳转到租户...`);
                            // 2秒后重定向
                            setTimeout(() => {
                                window.location.href = data.redirect_url;
                            }, 2000);
                        } else {
                            showError(data.error || '租户代码无效或租户不可用');
                        }
                    } catch (err) {
                        showError('网络错误，请稍后重试');
                    } finally {
                        submitBtn.disabled = false;
                        submitBtn.textContent = '进入租户';
                    }
                });

                function showError(message) {
                    resultDiv.className = 'result error-result';
                    resultDiv.innerHTML = `<strong>错误：</strong> ${message}`;
                    resultDiv.style.display = 'block';
                }

                function showSuccess(message) {
                    resultDiv.className = 'result success';
                    resultDiv.innerHTML = `<strong>成功：</strong> ${message}<br>
                    <a href="#" id="redirectLink" class="redirect-link">如果未自动跳转，请点击此处</a>`;
                    resultDiv.style.display = 'block';
                    document.getElementById('redirectLink').addEventListener('click', function(e) {
                        e.preventDefault();
                        window.location.href = data.redirect_url;
                    });
                }
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)


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

        agent_user = None
        if current_user:
            from src.models.user import User
            agent_user = User(
                user_id=current_user["user_id"],
                name=current_user.get("username", current_user.get("phone", "unknown")),
            )

        # Generate session ID if not provided
        if not session_id:
            session_id = f"web_{user_id}_{uuid.uuid4().hex[:8]}"

        # 通过租户实例管理器或默认路由获取 Agent
        agent = None
        _tenant_id = getattr(request.state, 'tenant_id', None)
        instance_id = getattr(request.state, 'instance_id', None)
        if instance_id and settings.saas.enabled:
            from src.saas.services.instance_manager import instance_manager
            agent = instance_manager.get_agent(instance_id, subagent_name, session_id)
        if not agent:
            agent = agent_router.get_agent(subagent_name, session_id, tenant_id=_tenant_id)

        # 注入 tenant_id（供租户 skills 按需加载使用）
        if _tenant_id and not agent._init_tenant_id:
            agent._init_tenant_id = _tenant_id

        # Process message — run in executor to avoid blocking the event loop
        # under high concurrency (LLM calls can take 2-30s).
        # This mirrors the SSE endpoint's pattern of offloading agent work to
        # a separate thread with its own event loop.
        loop = asyncio.get_running_loop()
        response_text = await loop.run_in_executor(
            None,
            lambda: asyncio.run(
                agent.process_message_sync(
                    user_input=user_input,
                    session_id=session_id,
                    user=agent_user,
                )
            ),
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
            '.mp3': 'audio/mpeg',
            '.mp4': 'video/mp4',
        }
        mime_type = mime_type_map.get(suffix, 'application/octet-stream')

        # 保存文件信息
        uploaded_files[file_id] = {
            "file_id": file_id,
            "name": file.filename or "unknown",
            "path": str(file_path.absolute()),
            "size": file_size,
            "mime_type": mime_type,
            "type": "image" if mime_type.startswith("image/") else "file"
        }

        logger.info(f"文件上传成功: {file.filename}, file_id: {file_id}, size: {file_size}")

        return JSONResponse({
            "success": True,
            "file_id": file_id,
            "name": file.filename,
            "size": file_size,
            "mime_type": mime_type,
            "type": uploaded_files[file_id]["type"]
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
                if parent_dir != UPLOAD_DIR and parent_dir.is_dir() and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
                    logger.info(f"已清理空目录: {parent_dir}")
            except OSError:
                pass
        # Also remove from in-memory dict if present
        uploaded_files.pop(file_id, None)

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
    获取文件信息：优先从内存字典获取，若不存在则尝试从磁盘恢复。
    服务重启后 uploaded_files 会清空，但文件仍在磁盘上。
    """
    if file_id in uploaded_files:
        return uploaded_files[file_id]

    # 尝试从磁盘目录扫描恢复（包括租户/用户子目录，最多3层）
    skip_dirs = {"knowledge", "wecom"}
    search_dirs = [UPLOAD_DIR]
    if UPLOAD_DIR.exists():
        for d1 in UPLOAD_DIR.iterdir():
            if d1.is_dir() and d1.name not in skip_dirs:
                search_dirs.append(d1)
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
                # 缓存回内存，避免重复磁盘扫描
                uploaded_files[file_id] = file_info
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

    # 调试日志：记录请求关键信息
    logger.info(f"[并发控制调试] chat_stream 请求: subagent={request.subagent}, instance_id={request.instance_id}, session_id={request.session_id}, user_id={user_id}")

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

    # 并发控制：验证并自动锁定实例（如果提供了 instance_id）
    instance_id = request.instance_id
    logger.info(f"[并发控制调试] 前置条件检查: instance_id={instance_id}, saas.enabled={settings.saas.enabled}, current_user={current_user is not None}")
    # TODO: 临时修改 - 屏蔽实例并发控制
    # 当 instance_id 为空时，跳过实例并发控制检查
    # 未来需要恢复实例并发控制逻辑
    if instance_id and settings.saas.enabled and current_user:
        from src.saas.services.instance_service import InstanceService
        from src.saas.db.agent_instance_db import AgentInstanceDB
        from src.db.models import UserDB

        # session_id 不能为空（需要用它来锁定）
        if not request.session_id:
            from fastapi.responses import JSONResponse
            return JSONResponse({
                "success": False,
                "error": "需要先创建会话",
                "details": "session_id 不能为空",
            }, status_code=400)

        instance = AgentInstanceDB.get_by_id(instance_id)
        if not instance:
            from fastapi.responses import JSONResponse
            return JSONResponse({
                "success": False,
                "error": "实例不存在",
            }, status_code=404)

        logger.info(f"[并发控制调试] 实例状态: instance_id={instance_id}, status={instance.get('status')}, current_session_id={instance.get('current_session_id')}, locked_at={instance.get('locked_at')}")

        # 检查实例当前是否被其他会话占用
        if instance["current_session_id"] is not None and instance["current_session_id"] != request.session_id:
            logger.warning(f"[并发控制调试] 实例被占用, instance_id={instance_id}, current_session={instance.get('current_session_id')}, current_user={instance.get('current_user_id')}")

            # 获取当前使用者的用户名
            holder_username = "其他用户"
            is_same_user = False
            if instance.get("current_user_id"):
                holder_user = UserDB.get_by_id(instance["current_user_id"])
                if holder_user and holder_user.get("username"):
                    holder_username = holder_user["username"]
                # 判断是否是同一个用户在不同设备上访问
                is_same_user = (instance.get("current_user_id") == user_id)

            instance_name = instance.get("instance_name") or instance.get("display_name") or "数字员工"
            if is_same_user:
                busy_message = f"[{instance_name}] 正在为用户【{holder_username}】（您的另一台设备）提供服务，请稍后再试或选择其他数字员工"
            else:
                busy_message = f"[{instance_name}] 正在为用户【{holder_username}】提供服务，请稍后再试或选择其他数字员工"

            # 返回 200 并通过 SSE 流式输出提示（带 busy 标志供前端识别）
            def busy_event_generator():
                busy_data = {
                    'type': 'busy',
                    'flag': 'busy',
                    'message': busy_message,
                    'instance_id': instance_id,
                    'is_same_user': is_same_user,
                    'current_user_name': holder_username,
                }
                yield f"data: {json.dumps({'type': 'connected', 'session_id': request.session_id, 'agent_type': 'default'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps(busy_data, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"

            return StreamingResponse(busy_event_generator(), media_type="text/event-stream")

        # 自动锁定空闲实例
        if instance["current_session_id"] is None:
            logger.info(f"[并发控制调试] 实例空闲，自动锁定: instance_id={instance_id}, session_id={request.session_id}, user_id={user_id}")
            lock_result = InstanceService.try_lock_instance(
                instance_id=instance_id,
                session_id=request.session_id,
                user_id=user_id,
            )
            if not lock_result.get("success") and not lock_result.get("was_idle"):
                logger.warning(f"[并发控制调试] 自动锁定失败: instance_id={instance_id}, result={lock_result}")
                # 锁定失败（竞态情况），通过 SSE 返回友好提示（带 busy 标志）
                instance_name = instance.get("instance_name") or instance.get("display_name") or "数字员工"
                busy_message = f"[{instance_name}] 正在被其他用户占用，请稍后再试或选择其他数字员工"

                def busy_event_generator():
                    busy_data = {
                        'type': 'busy',
                        'flag': 'busy',
                        'message': busy_message,
                        'instance_id': instance_id,
                        'is_same_user': False,
                        'current_user_name': '其他用户',
                    }
                    yield f"data: {json.dumps({'type': 'connected', 'session_id': request.session_id, 'agent_type': 'default'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps(busy_data, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"

                return StreamingResponse(busy_event_generator(), media_type="text/event-stream")
            logger.info(f"[并发控制调试] 自动锁定成功: instance_id={instance_id}, was_idle={lock_result.get('was_idle')}")

    # 获取当前租户ID（所有分支共享）
    from src.saas.context import get_current_tenant_id
    chat_tenant_id = get_current_tenant_id()

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

            # 如果提供了 file_id，使用 uploaded_files 中的实际路径
            # 兜底：多 worker 内存隔离时 uploaded_files 可能无此 file_id，
            # 此时根据 file_id 直接在 UPLOAD_DIR 中查找文件
            file_id = f.get("file_id")
            if file_id:
                if file_id in uploaded_files:
                    att["path"] = uploaded_files[file_id]["path"]
                else:
                    # 多 worker 兜底：递归扫描 UPLOAD_DIR 中以该 file_id 开头的文件
                    matched = list(UPLOAD_DIR.glob(f"**/{file_id}.*"))
                    if matched:
                        att["path"] = str(matched[0].absolute())
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
    agent = None
    _tenant_id = getattr(http_request.state, 'tenant_id', None)
    _instance_id = getattr(http_request.state, 'instance_id', None) if settings.saas.enabled else None
    if _instance_id:
        from src.saas.services.instance_manager import instance_manager
        agent = instance_manager.get_agent(_instance_id, request.subagent, session_id)
    if not agent:
        agent = agent_router.get_agent(request.subagent, session_id, tenant_id=_tenant_id)

    # 注入 tenant_id（供租户 skills 按需加载使用）
    if _tenant_id and not agent._init_tenant_id:
        agent._init_tenant_id = _tenant_id

    async def event_generator():
        """SSE事件生成器"""
        sse_start_time = datetime.now()
        logger.info(f"[SSE] event_generator started, session_id={session_id}")
        
        try:
            # 发送初始连接成功消息
            try:
                init_msg = f"data: {json.dumps({'type': 'connected', 'session_id': session_id, 'agent_type': agent.mode.value}, ensure_ascii=False)}\n\n"
                yield init_msg
                logger.info(f"[SSE] Initial connected message sent, session_id={session_id}")
            except Exception as e:
                logger.error(f"[SSE] Failed to send initial message: {e}", exc_info=True)
                return
            
            # 使用线程方式运行agent，避免阻塞事件循环
            import threading
            from concurrent.futures import ThreadPoolExecutor
            
            results = {
                'chunks': [],
                'progress': [],
                'error': None,
                'full_response': ""
            }
            completed = threading.Event()
            logger.info(f"[SSE] ThreadPoolExecutor initialized, session_id={session_id}")
            
            def run_agent():
                """在线程中运行agent"""
                thread_start_time = datetime.now()
                logger.info(f"[SSE-Thread] run_agent started, session_id={session_id}")

                try:
                    # 提前检查是否已被取消
                    if sse_manager.is_cancelled(session_id):
                        logger.info(f"[SSE-Thread] Session already cancelled before start, exiting: session_id={session_id}")
                        results['error'] = "Cancelled by user"
                        completed.set()
                        return

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    logger.info(f"[SSE-Thread] Event loop created, session_id={session_id}")

                    # 进度回调：同步函数，支持多种事件类型
                    # event 格式: {"type": "progress"|"tool_start"|"tool_result"|"thinking", "data": str, ...}
                    def sync_progress_callback(event):
                        # 检查是否已被取消
                        if sse_manager.is_cancelled(session_id):
                            logger.info(f"[SSE-Thread] Generation cancelled by user during progress: session_id={session_id}")
                            raise asyncio.CancelledError("Cancelled by user")
                        results['progress'].append(event)
                        # 同时将事件传递给会话记录服务（同步版本）
                        record_service.handle_progress_event(event)

                    # 从请求头解析用户身份（优先使用真实用户）
                    # 如果请求头中没有有效的认证信息，才使用请求体中的user_id
                    current_user = auth.get_current_user(http_request)
                    if current_user:
                        user_id = current_user["user_id"]
                        logger.info(f"后端日志：从请求头解析用户身份 user_id={user_id}")
                    else:
                        user_id = request.user_id or "anonymous"
                        logger.info(f"后端日志：使用匿名用户或请求体user_id user_id={user_id}")

                    # 构建 User 对象传入 agent
                    from src.models.user import User
                    agent_user = None
                    if current_user:
                        agent_user = User(
                            user_id=current_user["user_id"],
                            name=current_user.get("username", current_user.get("phone", "unknown")),
                        )
                    record_service = SessionRecordManager.start_record(
                        session_id=session_id,
                        user_id=user_id,
                        user_message=full_message,
                        tenant_id=chat_tenant_id
                    )
                    record_service.set_model(agent.llm.get_model_name())
                    record_service.set_provider(agent.llm.get_provider_name())

                    # 包装成async回调
                    # 支持直接接收 dict 事件（子智能体的 tool_start/tool_result 等完整事件）
                    # 或字符串消息（主智能体的 progress 消息）
                    async def async_progress_callback(message):
                        # 检查是否已被取消
                        if sse_manager.is_cancelled(session_id):
                            logger.info(f"[SSE-Thread] Generation cancelled by user during async progress: session_id={session_id}")
                            raise asyncio.CancelledError("Cancelled by user")
                        if isinstance(message, dict):
                            # 已经是完整的事件格式，直接传递
                            sync_progress_callback(message)
                        else:
                            # 字符串消息，包装为 progress 事件
                            sync_progress_callback({"type": "progress", "data": message})

                    # 运行agent
                    async def consume_generator():
                        """创建协程来迭代async generator"""
                        try:
                            async for chunk in agent.process_message(
                                user_input=full_message,
                                session_id=session_id,
                                user=agent_user,
                                attachments=attachments,
                                progress_callback=async_progress_callback
                            ):
                                # 每接收一个chunk就检查一次是否被取消
                                if sse_manager.is_cancelled(session_id):
                                    logger.info(f"[SSE-Thread] Generation cancelled by user during chunk generation: session_id={session_id}")
                                    raise asyncio.CancelledError("Cancelled by user")
                                results['chunks'].append(chunk)
                        except asyncio.CancelledError:
                            logger.info(f"[SSE-Thread] Consumption cancelled: session_id={session_id}")
                            raise

                    loop.run_until_complete(consume_generator())
                    loop.close()

                    # 如果未被取消，保存会话记录
                    full_response = "".join(results['chunks'])
                    results['full_response'] = full_response
                    if not sse_manager.is_cancelled(session_id) and full_response:
                        record_service.complete(full_response)
                        if results.get('error'):
                            record_service.mark_error(results['error'])
                        SessionRecordManager.end_record()

                        # 同时保存消息到 chat_messages 表（用于前端显示历史消息）
                        # 保存用户消息
                        user_metadata = {"progressMessages": []}
                        # 将附件信息保存到 metadata，以便前端历史消息能显示附件
                        if request.files:
                            user_metadata["attachments"] = request.files
                        MessageDB.create(
                            session_id=session_id,
                            role="user",
                            content=full_message,
                            metadata=user_metadata
                        )
                        # 保存AI回复（包含执行详情，但不作为模型上下文）
                        if full_response:
                            MessageDB.create(
                                session_id=session_id,
                                role="assistant",
                                content=full_response,
                                metadata={"progressMessages": results.get('progress', [])}  # 执行详情仅用于显示
                            )

                except asyncio.CancelledError:
                    logger.info(f"[SSE-Thread] Agent generation was cancelled by user: session_id={session_id}")
                    results['error'] = "Cancelled by user"
                    if SessionRecordManager.get_current_record():
                        SessionRecordManager.get_current_record().mark_error("Cancelled by user")
                        SessionRecordManager.end_record()
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    results['error'] = str(e)
                    logger.error(f"[SSE-Thread] Agent thread error, session_id={session_id}, error: {e}")
                    logger.error(f"[SSE-Thread] Traceback:\n{error_trace}")
                    # 记录错误
                    if SessionRecordManager.get_current_record():
                        SessionRecordManager.get_current_record().mark_error(str(e))
                        SessionRecordManager.end_record()
                finally:
                    # 清除取消标记
                    sse_manager.clear_cancelled(session_id)
                    completed.set()
                    thread_duration = (datetime.now() - thread_start_time).total_seconds()
                    logger.info(f"[SSE-Thread] run_agent finished, session_id={session_id}, duration={thread_duration:.2f}s, chunks={len(results['chunks'])}, progress={len(results['progress'])}")
            
            # 启动线程运行agent
            logger.info(f"[SSE] Starting ThreadPoolExecutor, session_id={session_id}")
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_agent)
                logger.info(f"[SSE] Agent thread submitted, session_id={session_id}")
                
                # 主循环：定期检查并yield结果
                last_progress_count = 0
                iteration_count = 0

                while not completed.is_set() or len(results['chunks']) > 0 or len(results['progress']) > last_progress_count:
                    iteration_count += 1
                    if iteration_count == 1 or iteration_count % 10 == 0:
                        logger.info(f"[SSE-MAIN] Loop iteration {iteration_count}, completed={completed.is_set()}, chunks={len(results['chunks'])}, progress={len(results['progress'])}, last_progress={last_progress_count}")
                    
                    # Yield 新的进度消息
                    while len(results['progress']) > last_progress_count:
                        try:
                            progress_event = results['progress'][last_progress_count]
                            # progress_event 格式: {"type": "progress"|"tool_start"|"tool_result"|"thinking", "data": str, ...}
                            event = {
                                "type": progress_event.get("type", "progress"),
                                "timestamp": int(datetime.now().timestamp() * 1000)
                            }
                            # 根据事件类型添加相应字段
                            if event["type"] == "tool_start":
                                event["toolName"] = progress_event.get("toolName", "")
                                event["toolArgs"] = progress_event.get("toolArgs", {})
                            elif event["type"] == "tool_result":
                                event["toolName"] = progress_event.get("toolName", "")
                                event["result"] = progress_event.get("result", {})
                                event["success"] = progress_event.get("success", True)
                            elif event["type"] == "clarification":
                                event["subagentName"] = progress_event.get("subagent_name", "")
                                event["question"] = progress_event.get("question", "")
                            else:
                                event["data"] = progress_event.get("data", progress_event.get("message", ""))

                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            last_progress_count += 1
                            
                        except (BrokenPipeError, ConnectionResetError, OSError) as e:
                            # 客户端已断开，优雅退出
                            logger.warning(f"[SSE] Client disconnected during progress yield, session_id={session_id}, error: {e}")
                            completed.set()
                            break
                        except Exception as e:
                            logger.error(f"[SSE] Error yielding progress event, session_id={session_id}, error: {e}", exc_info=True)
                            last_progress_count += 1
                            continue
                    
                    # Yield 新的响应chunk
                    while len(results['chunks']) > 0:
                        try:
                            chunk = results['chunks'].pop(0)
                            event = {
                                "type": "response",
                                "data": chunk,
                                "timestamp": int(datetime.now().timestamp() * 1000)
                            }
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            
                        except (BrokenPipeError, ConnectionResetError, OSError) as e:
                            # 客户端已断开，优雅退出
                            logger.warning(f"[SSE] Client disconnected during chunk yield, session_id={session_id}, error: {e}")
                            completed.set()
                            break
                        except Exception as e:
                            logger.error(f"[SSE] Error yielding chunk, session_id={session_id}, error: {e}", exc_info=True)
                            continue

                    await asyncio.sleep(0.05)  # 50ms轮询间隔，不阻塞事件循环
                
                # 确保线程完成
                try:
                    future.result()
                    logger.info(f"[SSE] Agent thread completed successfully, session_id={session_id}")
                except Exception as e:
                    logger.error(f"[SSE] Agent thread raised exception, session_id={session_id}, error: {e}", exc_info=True)
            
            # 检查错误
            if results['error']:
                logger.error(f"[SSE] Agent returned error, session_id={session_id}, error: {results['error']}")
                try:
                    yield f"data: {json.dumps({'type': 'error', 'data': results['error'], 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    logger.warning(f"[SSE] Client disconnected before error message sent, session_id={session_id}, error: {e}")
            
            # 保存完整响应到历史
            full_response = results.get('full_response', "".join(results['chunks']))
            sse_manager.add_to_history(session_id, "assistant", full_response)

            # 并发控制：刷新实例锁（3分钟思考窗口）
            if instance_id and settings.saas.enabled and not results.get('error'):
                from src.saas.services.instance_service import InstanceService
                try:
                    InstanceService.refresh_lock(instance_id, session_id, extend_minutes=3)
                    logger.info(f"[Concurrency] Lock refreshed: instance={instance_id}, session={session_id}")
                except Exception as e:
                    logger.warning(f"[Concurrency] Failed to refresh lock: {e}")

            # 发送完成消息
            try:
                yield f"data: {json.dumps({'type': 'complete', 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
                sse_duration = (datetime.now() - sse_start_time).total_seconds()
                logger.info(f"[SSE] Stream completed successfully, session_id={session_id}, duration={sse_duration:.2f}s, chunks={len(results['chunks'])}, progress={len(results['progress'])}")
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"[SSE] Client disconnected before complete message sent, session_id={session_id}, error: {e}")
            
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
app.include_router(credentials.router)
app.include_router(customer.router)
app.include_router(scheduled_task.router)
app.include_router(email_settings.router)
app.include_router(knowledge_router)
# 聊天实例并发控制API
from src.api import chat_instances
app.include_router(chat_instances.router)
app.include_router(admin_subagent.router)
app.include_router(subagent_extra.router)
app.include_router(travel_quote.router)
app.include_router(subagent.router)

# Word 文档处理 API
from src.api import word as word_api
app.include_router(word_api.router)

# 平台管理报表 API
from src.api import admin_reports
app.include_router(admin_reports.router)

# SaaS 多租户 API（始终注册，未启用时返回友好提示）
from src.saas.api import tenant_auth, tenant_mgmt, subscriptions, agent_instances
from src.saas.api import channel_config, tenant_skills, channel_routes
from src.saas.api import tenant_users, usage_reports, permissions
app.include_router(tenant_auth.router)
app.include_router(tenant_mgmt.router)
app.include_router(subscriptions.router)
app.include_router(agent_instances.router)
app.include_router(channel_config.router)
app.include_router(tenant_skills.router)
app.include_router(channel_routes.router)
app.include_router(tenant_users.router)
app.include_router(usage_reports.router)
app.include_router(usage_reports.public_router)
app.include_router(permissions.router)



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
            async for chunk in master_agent.process_message(user_input, session_id):
                print(chunk, end="", flush=True)
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
