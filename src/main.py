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
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from src.config.settings import settings
from src.config.logging import setup_logging
from src.core.agent import master_agent
from src.models.message import UnifiedMessage
from src.channels.wecom.adapter import WeComAdapter
from src.channels.manager import channel_manager
from src.db.database import init_database
from src.api import auth, session as session_api
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


class ChatResponse(BaseModel):
    session_id: str
    success: bool
    message: Optional[str] = None


# Initialize logging
setup_logging(
    log_level="DEBUG" if settings.app.debug else "INFO",
    log_dir="logs",
)

# WeCom adapter
wecom_adapter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # On startup
    logger.info(f"Starting {settings.app.name} v{settings.app.version}")
    logger.info(f"LLM Provider: {settings.llm.provider}")
    logger.info(f"Registered tools: {master_agent.tool_registry.list_tools()}")
    
    # Initialize database
    init_database()
    logger.info("Database initialized")
    
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
    
    yield
    
    # On shutdown
    logger.info("Application shutting down")


# ============== File Upload Configuration ==============
import shutil
from pathlib import Path

# 上传文件存储目录
UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Health Check ====================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": settings.app.version,
        "provider": settings.llm.provider,
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": settings.app.name,
        "version": settings.app.version,
        "status": "running",
    }


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
        
        if not user_input:
            return JSONResponse({
                "success": False,
                "error": "Message cannot be empty",
            })
        
        # Generate session ID if not provided
        if not session_id:
            session_id = f"web_{user_id}_{uuid.uuid4().hex[:8]}"
        
        # Process message using master agent
        response_text = await master_agent.process_message_sync(
            user_input=user_input,
            session_id=session_id,
        )
        
        return JSONResponse({
            "success": True,
            "response": response_text,
            "session_id": session_id,
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

        # 保存文件
        file_path = UPLOAD_DIR / f"{file_id}{suffix}"
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
    if file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="文件不存在")

    return JSONResponse({
        "success": True,
        **uploaded_files[file_id]
    })


@app.delete("/api/upload/{file_id}")
async def delete_uploaded_file(file_id: str):
    """
    删除已上传的文件
    """
    if file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        file_path = Path(uploaded_files[file_id]["path"])
        if file_path.exists():
            file_path.unlink()
        del uploaded_files[file_id]

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
                context_data={"user_info": {"user_id": user_id, "username": current_user.get("username")}}
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
            file_id = f.get("file_id")
            if file_id and file_id in uploaded_files:
                att["path"] = uploaded_files[file_id]["path"]
                att["file_id"] = file_id

            if "content" in f:
                att["content"] = f["content"]
            attachments.append(att)

    # 添加SSE客户端
    client_queue = sse_manager.add_sse_client(session_id)

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
    
    async def event_generator():
        """SSE事件生成器"""
        try:
            # 发送初始连接成功消息
            yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            
            # 使用线程方式运行agent，避免阻塞事件循环
            import threading
            from concurrent.futures import ThreadPoolExecutor
            
            results = {
                'chunks': [],
                'progress': [],
                'error': None
            }
            completed = threading.Event()
            
            def run_agent():
                """在线程中运行agent"""
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    # 进度回调：同步函数，支持多种事件类型
                    # event 格式: {"type": "progress"|"tool_start"|"tool_result"|"thinking", "data": str, ...}
                    def sync_progress_callback(event):
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
                    record_service = SessionRecordManager.start_record(
                        session_id=session_id,
                        user_id=user_id,
                        user_message=full_message
                    )
                    record_service.set_model(master_agent.llm.get_model_name())

                    # 包装成async回调
                    async def async_progress_callback(message: str):
                        sync_progress_callback({"type": "progress", "data": message})
                    
                    # 运行agent
                    async def consume_generator():
                        """创建协程来迭代async generator"""
                        async for chunk in master_agent.process_message(
                            user_input=full_message,
                            session_id=session_id,
                            attachments=attachments,
                            progress_callback=async_progress_callback
                        ):
                            results['chunks'].append(chunk)
                    
                    loop.run_until_complete(consume_generator())
                    loop.close()
                    
                    # 保存会话记录
                    full_response = "".join(results['chunks'])
                    record_service.complete(full_response)
                    if results.get('error'):
                        record_service.mark_error(results['error'])
                    SessionRecordManager.end_record()

                    # 同时保存消息到 chat_messages 表（用于前端显示历史消息）
                    # 保存用户消息
                    MessageDB.create(
                        session_id=session_id,
                        role="user",
                        content=full_message
                    )
                    # 保存AI回复
                    if full_response:
                        MessageDB.create(
                            session_id=session_id,
                            role="assistant",
                            content=full_response
                        )
                    
                except Exception as e:
                    results['error'] = str(e)
                    logger.error(f"Agent thread error: {e}")
                    # 记录错误
                    if SessionRecordManager.get_current_record():
                        SessionRecordManager.get_current_record().mark_error(str(e))
                        SessionRecordManager.end_record()
                finally:
                    completed.set()
            
            # 启动线程运行agent
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_agent)
                
                # 主循环：定期检查并yield结果
                last_progress_count = 0

                while not completed.is_set() or len(results['chunks']) > 0 or len(results['progress']) > last_progress_count:
                    # Yield 新的进度消息
                    while len(results['progress']) > last_progress_count:
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
                        else:
                            event["data"] = progress_event.get("data", progress_event.get("message", ""))

                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        last_progress_count += 1

                    # Yield 新的响应chunk
                    while len(results['chunks']) > 0:
                        chunk = results['chunks'].pop(0)
                        event = {
                            "type": "response",
                            "data": chunk,
                            "timestamp": int(datetime.now().timestamp() * 1000)
                        }
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                    if completed.is_set():
                        break

                    await asyncio.sleep(0.05)  # 50ms轮询间隔，不阻塞事件循环
                
                # 确保线程完成
                future.result()
            
            # 检查错误
            if results['error']:
                yield f"data: {json.dumps({'type': 'error', 'data': results['error'], 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
            
            # 保存完整响应到历史
            full_response = "".join(results['chunks'])
            sse_manager.add_to_history(session_id, "assistant", full_response)
            
            # 发送完成消息
            yield f"data: {json.dumps({'type': 'complete', 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"SSE chat error: {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'data': str(e), 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
        finally:
            sse_manager.remove_sse_client(session_id, client_queue)
    
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


# ==================== Auth & Session API ====================

app.include_router(auth.router)
app.include_router(session_api.router)
app.include_router(channels_api.router)


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
        )


if __name__ == "__main__":
    main()
