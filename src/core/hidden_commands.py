"""
隐藏命令模块

提供渠道端隐藏命令的检测和处理。
渠道端只能发纯文本消息，无法提供 UI 按钮，
通过特定文本触发命令（如清空会话）。
仅用于内部测试，无 UI 提示。

扩展方式：在 HIDDEN_COMMANDS 字典中添加新条目即可。
"""

from typing import Optional, Dict, Any, Callable, Awaitable


# 隐藏命令处理器类型：async def handler(session_id, tenant_id) -> None
Handler = Callable[[str, str], Awaitable[None]]


async def _handle_new_session(session_id: str, tenant_id: str) -> None:
    """清空当前会话的 channel_messages"""
    from src.channels.session import channel_session_manager
    channel_session_manager.delete_messages(session_id)


HIDDEN_COMMANDS: Dict[str, Handler] = {
    "新会话": _handle_new_session,
}


def is_hidden_command(text: str) -> bool:
    """检查文本是否为隐藏命令"""
    return text.strip() in HIDDEN_COMMANDS


async def execute_hidden_command(text: str, session_id: str, tenant_id: str = "") -> bool:
    """
    执行隐藏命令。

    Returns:
        True 如果匹配并执行了隐藏命令，False 如果不是命令
    """
    cmd = text.strip()
    handler = HIDDEN_COMMANDS.get(cmd)
    if handler:
        await handler(session_id, tenant_id)
        return True
    return False
