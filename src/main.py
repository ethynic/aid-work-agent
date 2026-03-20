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
    
    # Initialize WeCom adapter
    global wecom_adapter
    if settings.channels.wecom.corp_id:
        wecom_adapter = WeComAdapter()
        logger.info("WeCom adapter initialized")
    
    yield
    
    # On shutdown
    logger.info("Application shutting down")


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


# ==================== WeCom Callback ====================

@app.get("/wecom/callback")
async def wecom_callback_get(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
):
    """
    WeCom callback verification endpoint
    
    WeCom sends a GET request to verify URL validity when configuring callback
    """
    if not wecom_adapter:
        return PlainTextResponse("WeCom adapter not configured", status_code=500)
    
    # Verify signature
    if not await wecom_adapter.verify_signature(msg_signature, timestamp, nonce, echostr):
        logger.warning("WeCom signature verification failed")
        return PlainTextResponse("Invalid signature", status_code=403)
    
    # Decrypt echostr and return
    return PlainTextResponse(echostr)


@app.post("/wecom/callback")
async def wecom_callback_post(
    request: Request,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
):
    """
    WeCom message callback endpoint
    
    Receives messages pushed by WeCom
    """
    if not wecom_adapter:
        return PlainTextResponse("WeCom adapter not configured", status_code=500)
    
    # Get request body
    body = await request.body()
    
    # Verify signature
    if not await wecom_adapter.verify_signature(msg_signature, timestamp, nonce, body.decode()):
        logger.warning("WeCom signature verification failed")
        return PlainTextResponse("Invalid signature", status_code=403)
    
    try:
        # Parse message
        raw_message = {
            "body": body.decode(),
        }
        message = await wecom_adapter.parse_message(raw_message)
        
        logger.info(f"Received WeCom message: {message.user_id}, content: {message.text}")
        
        # Process message using master agent
        session_id = f"wecom_{message.user_id}"
        response_text = await master_agent.process_message_sync(
            user_input=message.text,
            session_id=session_id,
        )
        
        # Send response
        if wecom_adapter:
            await wecom_adapter.send_message({
                "user_id": message.user_id,
                "message": response_text,
            })
        
        return PlainTextResponse("success")
    
    except Exception as e:
        logger.error(f"Failed to process WeCom message: {e}")
        return PlainTextResponse("error", status_code=500)


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


# ==================== SSE Chat API ====================

@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
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
            if "content" in f:
                att["content"] = f["content"]
            attachments.append(att)
    
    # 添加SSE客户端
    client_queue = sse_manager.add_sse_client(session_id)
    
    # 添加用户消息到历史
    sse_manager.add_to_history(session_id, "user", request.message)
    
    async def event_generator():
        """SSE事件生成器"""
        try:
            # 发送初始连接成功消息
            yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            
            # 创建一个进度回调，在agent运行时发送进度
            async def progress_callback(session_id: str, message: str):
                event = {
                    "type": "progress",
                    "data": message,
                    "timestamp": int(datetime.now().timestamp() * 1000)
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            
            # 在新线程中运行async agent（因为FastAPI不支持在event_generator中直接await太久）
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            response_chunks = []
            
            async def run_agent():
                nonlocal response_chunks
                async for chunk in master_agent.process_message(
                    user_input=request.message,
                    session_id=session_id,
                    attachments=attachments,
                    progress_callback=lambda msg: sse_manager.broadcast(
                        session_id, 
                        {"type": "progress", "data": msg, "timestamp": int(datetime.now().timestamp() * 1000)}
                    )
                ):
                    response_chunks.append(chunk)
                    # 发送响应chunk
                    event = {
                        "type": "response",
                        "data": chunk,
                        "timestamp": int(datetime.now().timestamp() * 1000)
                    }
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            
            # 执行agent并yield事件
            try:
                async for event in run_agent():
                    yield event
            finally:
                loop.close()
            
            # 保存完整响应到历史
            full_response = "".join(response_chunks)
            sse_manager.add_to_history(session_id, "assistant", full_response)
            
            # 发送完成消息
            yield f"data: {json.dumps({'type': 'complete', 'timestamp': int(datetime.now().timestamp() * 1000)}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"SSE chat error: {e}")
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
