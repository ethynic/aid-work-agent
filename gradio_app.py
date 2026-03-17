#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AID Work Agent Gradio UI

一个现代化的对话界面，支持：
- 文字对话
- 文件上传（自动匹配技能处理）
- 流式输出
- 会话历史
"""

import asyncio
import base64
import os
import uuid
from pathlib import Path
from typing import List, Tuple, Dict, Any

import gradio as gr
from loguru import logger

# 添加项目根目录到 Python 路径
import sys
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.config.logging import setup_logging
from src.core.agent import master_agent


# 初始化日志
setup_logging(log_level="INFO", log_dir="logs")

# Gradio 会话存储
# session_id -> {"history": [ChatMessage, ...], "files": [file_info, ...]}
chat_sessions = {}


def get_or_create_session(session_id: str = None) -> str:
    """获取或创建会话ID"""
    if session_id is None or session_id not in chat_sessions:
        session_id = str(uuid.uuid4())
        chat_sessions[session_id] = {
            "history": [],
            "files": []
        }
        logger.info(f"Created new chat session: {session_id}")
    return session_id


def add_message_to_history(session_id: str, user_msg: str, assistant_msg: str):
    """添加消息到会话历史 - 使用 Gradio 6.0 格式"""
    if session_id in chat_sessions:
        # Gradio 6.0 格式: 字典列表
        chat_sessions[session_id]["history"].append(
            {"role": "user", "content": user_msg}
        )
        chat_sessions[session_id]["history"].append(
            {"role": "assistant", "content": assistant_msg}
        )


def format_chat_history(session_id: str) -> List[Dict[str, str]]:
    """格式化聊天历史为 Gradio Chatbot 6.0 格式"""
    if session_id in chat_sessions:
        return chat_sessions[session_id]["history"]
    return []


async def process_message_async(
    user_message: str,
    session_id: str,
    files: List[str] = None
) -> Tuple[str, str]:
    """
    处理用户消息

    Returns:
        (完整回复, session_id)
    """
    session_id = get_or_create_session(session_id)

    # 处理上传的文件
    attachments = []
    if files:
        for file_path in files:
            if file_path and os.path.exists(file_path):
                try:
                    with open(file_path, "rb") as f:
                        file_content = base64.b64encode(f.read()).decode("utf-8")
                    file_name = os.path.basename(file_path)
                    attachments.append({
                        "type": "file",
                        "name": file_name,
                        "content": file_content,
                        "mime_type": get_mime_type(file_name)
                    })
                    logger.info(f"Attached file: {file_name}")
                except Exception as e:
                    logger.error(f"Failed to attach file {file_path}: {e}")

    # 处理消息（异步生成器）
    response_chunks = []

    try:
        async for chunk in master_agent.process_message(
            user_input=user_message,
            session_id=session_id,
            attachments=attachments if attachments else None
        ):
            response_chunks.append(chunk)

        full_response = "".join(response_chunks)

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        full_response = f"抱歉，处理您的请求时发生错误：{str(e)}"

    # 保存到历史记录
    add_message_to_history(session_id, user_message, full_response)

    return full_response, session_id


def chat(
    user_message: str,
    session_id: str,
    files: List[str] = None,
    chatbot: List[Dict[str, str]] = None
) -> Tuple[List[Dict[str, str]], str, str]:
    """
    Gradio 聊天处理函数

    Args:
        user_message: 用户消息
        session_id: 会话ID
        files: 上传的文件列表
        chatbot: Chatbot 组件状态

    Returns:
        (chatbot更新, 新的session_id, "")
    """
    if not user_message and not files:
        return chatbot or [], session_id, ""

    # 确保会话存在
    session_id = get_or_create_session(session_id)

    # 获取当前历史
    history = format_chat_history(session_id)

    # 添加用户消息到显示（等待助手回复）
    if user_message:
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": "处理中..."})

    # 同步执行异步处理
    try:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # 如果已经在运行，创建一个新任务
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    process_message_async(user_message, session_id, files)
                )
                full_response, session_id = future.result()
        else:
            full_response, session_id = loop.run_until_complete(
                process_message_async(user_message, session_id, files)
            )
    except Exception as e:
        logger.error(f"Error in chat: {e}")
        full_response = f"处理出错: {str(e)}"

    # 更新最后一条助手消息
    if history:
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "assistant":
                history[i] = {"role": "assistant", "content": full_response}
                break

    # 同步到全局会话存储
    chat_sessions[session_id]["history"] = history

    return history, session_id, ""


def clear_chat(session_id: str) -> Tuple[str, List, str]:
    """清除聊天历史"""
    session_id = get_or_create_session(session_id)
    chat_sessions[session_id]["history"] = []
    chat_sessions[session_id]["files"] = []
    return "", [], session_id


def get_mime_type(filename: str) -> str:
    """根据文件扩展名获取 MIME 类型"""
    ext = os.path.splitext(filename)[1].lower()
    mime_types = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".csv": "text/csv",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".zip": "application/zip",
    }
    return mime_types.get(ext, "application/octet-stream")


# 自定义 CSS 样式
CUSTOM_CSS = """
.gradio-container {
    max-width: 1200px !important;
    margin: auto !important;
}

.main-title {
    text-align: center;
    font-size: 28px;
    font-weight: 600;
    color: #1a1a2e;
    margin-bottom: 10px;
}

.sub-title {
    text-align: center;
    color: #666;
    font-size: 14px;
    margin-bottom: 20px;
}

.status-bar {
    background: #f0f0f0;
    padding: 8px 15px;
    border-radius: 20px;
    font-size: 12px;
    color: #666;
}
"""


def create_gradio_app():
    """创建 Gradio 应用"""

    with gr.Blocks(
        title="AID Work Agent",
        css=CUSTOM_CSS
    ) as demo:

        # 状态存储
        session_state = gr.State("")

        # 标题
        gr.HTML("""
        <div class="main-title">
            🤖 AID Work Agent
        </div>
        <div class="sub-title">
            智能工作助手 - 支持对话和文件处理
        </div>
        """)

        # 状态栏
        with gr.Row():
            status_text = gr.HTML(
                '<div class="status-bar">🟢 在线 | 已加载工具: 邮件、浏览器、文件、OCR、搜索等</div>',
                visible=True
            )

        # 主聊天区域
        with gr.Row():
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    label="对话历史",
                    height=500,
                )
            with gr.Column(scale=1):
                # 文件上传
                file_output = gr.File(
                    label="📎 上传文件",
                    file_count="multiple",
                    file_types=[
                        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
                        ".txt", ".md", ".json", ".csv",
                        ".png", ".jpg", ".jpeg", ".gif"
                    ]
                )

        # 输入区域
        with gr.Row():
            with gr.Column(scale=4):
                # 文本输入
                msg_input = gr.Textbox(
                    label="发送消息",
                    placeholder="输入您的问题或任务...",
                    lines=3,
                    show_label=False,
                    submit_btn="发送",
                )
            with gr.Column(scale=1):
                # 按钮行
                submit_btn = gr.Button("🚀 发送", variant="primary")
                clear_btn = gr.Button("🗑️ 清除", variant="secondary")

        # 事件处理
        # 发送消息
        submit_btn.click(
            fn=chat,
            inputs=[msg_input, session_state, file_output, chatbot],
            outputs=[chatbot, session_state, msg_input],
            api_name="chat"
        )

        # 回车发送
        msg_input.submit(
            fn=chat,
            inputs=[msg_input, session_state, file_output, chatbot],
            outputs=[chatbot, session_state, msg_input],
            api_name="chat"
        )

        # 清除聊天
        clear_btn.click(
            fn=clear_chat,
            inputs=[session_state],
            outputs=[msg_input, chatbot, session_state],
            api_name="clear"
        )

        # 初始化会话
        demo.load(
            fn=get_or_create_session,
            inputs=None,
            outputs=[session_state]
        )

    return demo


def main():
    """启动 Gradio 应用"""
    import argparse

    parser = argparse.ArgumentParser(description="AID Work Agent Gradio UI")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址")
    parser.add_argument("--port", type=int, default=7860, help="端口号")
    parser.add_argument("--share", action="store_true", help="创建公开链接")
    parser.add_argument("--debug", action="store_true", help="调试模式")

    args = parser.parse_args()

    logger.info(f"Starting Gradio UI on {args.host}:{args.port}")

    demo = create_gradio_app()

    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        debug=args.debug,
        show_error=True
    )


if __name__ == "__main__":
    main()
