#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AID Work Agent Gradio UI

一个现代化的对话界面，支持：
- 文字对话
- 文件上传（自动匹配技能处理）
- 流式输出
- 会话历史
- 执行过程实时显示
"""

import asyncio
import base64
import os
import uuid
import threading
import queue
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import gradio as gr
from loguru import logger

# 添加项目根目录到 Python 路径
import sys
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.config.logging import setup_logging
from src.core.agent import master_agent


# 初始化日志
setup_logging(log_level="INFO", log_dir="log/agent")

# Gradio 会话存储
# session_id -> {"history": [ChatMessage, ...], "files": [file_info, ...]}
chat_sessions = {}

# 会话级别的进度消息队列
# session_id -> queue.Queue
session_progress_queues: Dict[str, queue.Queue] = {}
session_progress_lock = threading.Lock()


def get_or_create_session(session_id: str = None) -> str:
    """获取或创建会话ID"""
    if session_id is None or session_id not in chat_sessions:
        session_id = str(uuid.uuid4())
        chat_sessions[session_id] = {
            "history": [],
            "files": [],
            "processing": False  # 标记当前是否有任务在后台执行
        }
        # 初始化进度队列
        with session_progress_lock:
            session_progress_queues[session_id] = queue.Queue()
        logger.info(f"Created new chat session: {session_id}")
    return session_id


def get_progress_queue(session_id: str) -> Optional[queue.Queue]:
    """获取会话的进度队列"""
    with session_progress_lock:
        return session_progress_queues.get(session_id)


def add_progress_message(session_id: str, message: str):
    """添加进度消息到队列"""
    with session_progress_lock:
        if session_id in session_progress_queues:
            session_progress_queues[session_id].put(message)
            logger.info(f"[PROGRESS] {message}")


def get_and_clear_progress(session_id: str) -> List[str]:
    """获取并清除所有进度消息"""
    messages = []
    with session_progress_lock:
        if session_id in session_progress_queues:
            q = session_progress_queues[session_id]
            while not q.empty():
                try:
                    messages.append(q.get_nowait())
                except queue.Empty:
                    break
    return messages


def clear_progress(session_id: str):
    """清除进度队列"""
    with session_progress_lock:
        if session_id in session_progress_queues:
            q = session_progress_queues[session_id]
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break


def add_message_to_history(session_id: str, user_msg: str, assistant_msg: str):
    """添加消息到会话历史 - 使用 Gradio 6.0 格式"""
    if session_id in chat_sessions:
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


async def progress_callback(session_id: str, message: str):
    """
    进度回调函数 - 由 agent 调用，接收实时进度消息
    """
    add_progress_message(session_id, message)
    # 让出控制权，允许其他协程执行
    await asyncio.sleep(0)


async def process_message_async(
    user_message: str,
    session_id: str,
    files: List[str] = None
) -> Tuple[str, str]:
    """
    处理用户消息（支持进度回调）

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
        async for event in master_agent.process_message(
            user_input=user_message,
            session_id=session_id,
            attachments=attachments if attachments else None,
        ):
            event_type = event.get("type")
            if event_type == "response":
                response_chunks.append(event.get("data", ""))
            elif event_type == "tool_start":
                add_progress_message(session_id, f"🔧 执行工具: {event.get('toolName')}")
            elif event_type == "tool_result":
                add_progress_message(session_id, f"📤 工具完成: {event.get('toolName')}")
            elif event_type == "progress":
                add_progress_message(session_id, event.get("data", ""))

        full_response = "".join(response_chunks)

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        full_response = f"抱歉，处理您的请求时发生错误：{str(e)}"

    # 保存到历史记录
    add_message_to_history(session_id, user_message, full_response)

    # 处理完成，清除processing标志
    if session_id in chat_sessions:
        chat_sessions[session_id]["processing"] = False
    # 添加完成标记到进度队列
    add_progress_message(session_id, "✅ 任务完成")

    return full_response, session_id


def process_in_thread(
    user_message: str,
    session_id: str,
    files: List[str] = None
):
    """
    在后台线程中处理消息，避免阻塞主线程
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process_message_async(user_message, session_id, files))
    except Exception as e:
        logger.error(f"Error in background processing: {e}")
    finally:
        try:
            loop.close()
        except:
            pass


def chat(
    user_message: str,
    session_id: str,
    files: List[str] = None,
    chatbot: List[Dict[str, str]] = None
) -> Tuple[List[Dict[str, str]], str, str, str]:
    """
    Gradio 聊天处理函数

    Args:
        user_message: 用户消息
        session_id: 会话ID
        files: 上传的文件列表
        chatbot: Chatbot 组件状态

    Returns:
        (chatbot更新, 新的session_id, "", 进度HTML)
    """
    if not user_message and not files:
        return chatbot or [], session_id, "", ""

    # 确保会话存在
    session_id = get_or_create_session(session_id)

    # 清除之前的进度
    clear_progress(session_id)

    # 获取当前历史
    history = format_chat_history(session_id)

    # 添加用户消息到显示（等待助手回复）
    if user_message:
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": "🚀 正在思考，请稍候..."})

    # 立即返回当前状态，启动后台线程处理
    chat_sessions[session_id]["history"] = history
    chat_sessions[session_id]["processing"] = True  # 标记开始处理

    # 在后台线程中处理消息
    thread = threading.Thread(
        target=process_in_thread,
        args=(user_message, session_id, files),
        daemon=True
    )
    thread.start()
    logger.info(f"Started background processing for session {session_id}")

    # 返回初始状态
    return history, session_id, "", ""


def poll_progress(
    session_id: str,
) -> str:
    """
    轮询获取最新进度消息

    Returns:
        进度HTML
    """
    if not session_id:
        return '<div class="progress-container"><div class="progress-item">等待任务开始...</div></div>'

    messages = get_and_clear_progress(session_id)

    if not messages:
        return ""

    html_parts = ['<div class="progress-container">']
    for msg in messages:
        html_parts.append(f'<div class="progress-item">{msg}</div>')
    html_parts.append('</div>')

    return '\n'.join(html_parts)


def clear_chat(session_id: str) -> Tuple[str, List, str]:
    """清除聊天历史"""
    session_id = get_or_create_session(session_id)
    chat_sessions[session_id]["history"] = []
    chat_sessions[session_id]["files"] = []
    chat_sessions[session_id]["processing"] = False
    clear_progress(session_id)
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
    max-width: 1400px !important;
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

/* 进度显示区域样式 */
.progress-container {
    background: #fafafa;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 10px;
    max-height: 400px;
    overflow-y: auto;
    font-size: 13px;
    line-height: 1.8;
}

.progress-item {
    padding: 6px 10px;
    margin: 4px 0;
    border-radius: 4px;
    background: #fff;
    border-left: 3px solid #2196F3;
    color: #333;
}

.progress-item:empty {
    display: none;
}
"""


def create_gradio_app():
    """创建 Gradio 应用"""

    with gr.Blocks(
        title="AID Work Agent",
    ) as demo:

        # 状态存储
        session_state = gr.State("")
        progress_state = gr.State("")

        # 标题
        gr.HTML("""
        <div class="main-title">
            🤖 AID Work Agent
        </div>
        <div class="sub-title">
            智能工作助手 - 支持对话、文件处理和实时进度显示
        </div>
        """)

        # 状态栏
        with gr.Row():
            status_text = gr.HTML(
                '<div class="status-bar">🟢 在线 | 已加载工具: 邮件、浏览器、文件、OCR、搜索、内容生成等</div>',
                visible=True
            )

        # 进度显示区域
        with gr.Row():
            progress_html = gr.HTML(
                value='<div class="progress-container"><div class="progress-item">发送消息开始任务...</div></div>',
                label="执行进度",
                elem_id="progress-display",
            )

        # 主聊天区域
        with gr.Row():
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    label="对话历史",
                    height=400,
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
                refresh_btn = gr.Button("🔄 刷新进度", variant="secondary")

        # 事件处理
        # 发送消息（立即返回，后台处理）
        submit_btn.click(
            fn=chat,
            inputs=[msg_input, session_state, file_output, chatbot],
            outputs=[chatbot, session_state, msg_input, progress_html],
        )

        # 回车发送
        msg_input.submit(
            fn=chat,
            inputs=[msg_input, session_state, file_output, chatbot],
            outputs=[chatbot, session_state, msg_input, progress_html],
        )

        # 刷新进度按钮
        refresh_btn.click(
            fn=poll_progress,
            inputs=[session_state],
            outputs=[progress_html],
        )

        # 清除聊天
        clear_btn.click(
            fn=clear_chat,
            inputs=[session_state],
            outputs=[msg_input, chatbot, session_state],
        ).then(
            fn=lambda: '<div class="progress-container"><div class="progress-item">发送消息开始任务...</div></div>',
            inputs=None,
            outputs=[progress_html],
        )

        # 定时刷新进度（使用 Timer 组件）
        timer = gr.Timer(value=1)  # 每秒刷新

        def timer_update(session_id):
            """
            定时更新进度，同时更新 Chatbot 和进度 HTML
            将进度信息追加到 Chatbot 最后一条 assistant 消息中
            """
            if not session_id:
                empty_progress = '<div class="progress-container"><div class="progress-item">等待任务开始...</div></div>'
                return ([], empty_progress)

            # 检查是否在处理中
            is_processing = chat_sessions.get(session_id, {}).get("processing", False)

            # 获取并清除进度消息
            messages = get_and_clear_progress(session_id)

            # 获取当前历史
            history = format_chat_history(session_id)

            # 构建进度 HTML
            progress_html = ""

            if messages:
                # 构建新进度文本
                new_progress = "\n".join([f"• {msg}" for msg in messages])

                # 更新最后一条消息（如果是 assistant 消息）
                if history and history[-1].get("role") == "assistant":
                    current_content = history[-1].get("content", "")
                    # 检查是否是初始占位消息
                    if "正在思考，请稍候" in current_content:
                        history[-1]["content"] = new_progress
                    else:
                        history[-1]["content"] = current_content + "\n\n" + new_progress
                else:
                    history.append({"role": "assistant", "content": new_progress})

                # 构建进度 HTML
                html_parts = ['<div class="progress-container">']
                for msg in messages:
                    html_parts.append(f'<div class="progress-item">{msg}</div>')
                html_parts.append('</div>')
                progress_html = '\n'.join(html_parts)
            elif not is_processing:
                # 任务已完成且没有新消息，确保显示完成状态
                if history and history[-1].get("role") == "assistant":
                    content = history[-1].get("content", "")
                    if "正在思考，请稍候" in content:
                        history[-1]["content"] = "✅ 任务完成"

            return (history, progress_html)

        timer.tick(
            fn=timer_update,
            inputs=[session_state],
            outputs=[chatbot, progress_html],
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

    # Gradio 6.0: css 参数移到 launch() 方法
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        debug=args.debug,
        show_error=True,
        css=CUSTOM_CSS,
    )


if __name__ == "__main__":
    main()
