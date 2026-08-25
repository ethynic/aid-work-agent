"""
隐藏命令模块

提供渠道端隐藏命令的检测和处理。
渠道端只能发纯文本消息，无法提供 UI 按钮，
通过特定文本触发命令（如清空会话）。
仅用于内部测试，无 UI 提示。

扩展方式：在 HIDDEN_COMMANDS 字典中添加新条目即可。
"""

from typing import Optional, Dict, Any, Callable, Awaitable


# 隐藏命令处理器类型：async def handler(session_id, tenant_id) -> str
Handler = Callable[[str, str], Awaitable[str]]


async def _handle_clear_session(session_id: str, tenant_id: str) -> str:
    """清空会话：物理删除 channel_messages，历史记录不保留"""
    from src.channels.session import channel_session_manager
    channel_session_manager.delete_messages(session_id, tenant_id)
    return "会话消息已清空，开始新会话。"


async def _handle_new_session(session_id: str, tenant_id: str) -> str:
    """新会话：软删除 channel_messages（status=invalid），历史记录保留、不进入新上下文"""
    from src.channels.session import channel_session_manager
    channel_session_manager.soft_delete_messages(session_id, tenant_id)
    return "已开启新会话，历史聊天记录已保留。"


HIDDEN_COMMANDS: Dict[str, Handler] = {
    "清空会话": _handle_clear_session,
    "新会话": _handle_new_session,
}


def is_hidden_command(text: str) -> bool:
    """检查文本是否为隐藏命令"""
    return text.strip() in HIDDEN_COMMANDS


async def execute_hidden_command(text: str, session_id: str, tenant_id: str = "") -> Optional[str]:
    """
    执行隐藏命令。

    Returns:
        命令执行后的回复文本，如果不是命令则返回 None
    """
    cmd = text.strip()
    handler = HIDDEN_COMMANDS.get(cmd)
    if handler:
        return await handler(session_id, tenant_id)
    return None
