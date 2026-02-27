"""
AID Work Agent Main Entry

V1.0 MVP version - LLM-driven agent architecture
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

from src.config.settings import settings
from src.config.logging import setup_logging
from src.core.agent import master_agent
from src.models.message import UnifiedMessage
from src.channels.wecom.adapter import WeComAdapter


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
