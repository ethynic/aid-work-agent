"""
隐藏命令模块

提供渠道端隐藏命令的检测和处理。
渠道端只能发纯文本消息，无法提供 UI 按钮，
通过特定文本触发命令（如清空会话）。
仅用于内部测试，无 UI 提示。

扩展方式：在 HIDDEN_COMMANDS 字典中添加新条目即可。
"""

import asyncio
from typing import Optional, Dict, Any, Callable, Awaitable

from loguru import logger


# 隐藏命令处理器类型：async def handler(session_id, tenant_id) -> str
Handler = Callable[[str, str], Awaitable[str]]


async def _clear_session_lead_info(session_id: str, tenant_id: str) -> None:
    """清空/新会话时清除会话附带的留资信息

    ① 删 metadata.lead_capture：阻断 recap 售前推送链路继续取到旧留资手机号
    ② 物理删除 bs_lead_capture_leads 线索记录
    失败不抛（清留资失败不阻断命令本身），记 error 日志。
    """
    try:
        from src.channels.session import channel_session_manager

        session = await asyncio.to_thread(
            channel_session_manager.get_session_by_id, session_id
        ) or {}
        metadata = session.get("metadata") or {}
        lead_capture = metadata.get("lead_capture") or {}
        if not lead_capture:
            return

        # update_session 的 metadata 是整体替换而非合并，必须带全量既有键回写，
        # 避免误清 service_state / transferred_to 等其他键
        merged = dict(metadata)
        merged.pop("lead_capture", None)
        await asyncio.to_thread(
            channel_session_manager.update_session,
            session_id=session_id, metadata=merged,
        )

        lead_id = lead_capture.get("lead_id")
        if lead_id and tenant_id:
            from src.saas.db.lead_capture_db import LeadCaptureDB

            # 已转化（converted）的真实客户线索不删，仅清 metadata 断开推送引用
            lead = await asyncio.to_thread(LeadCaptureDB.get_by_id, lead_id, tenant_id) or {}
            if lead.get("stage") == "converted":
                logger.info(
                    f"[hidden_commands] 会话 {session_id} 线索 {lead_id} 已转化，保留记录仅清 metadata"
                )
                return

            deleted = await asyncio.to_thread(LeadCaptureDB.delete, lead_id, tenant_id)
            logger.info(
                f"[hidden_commands] 会话 {session_id} 清除留资：lead={lead_id}, "
                f"线索删除={'成功' if deleted else '未找到'}"
            )
    except Exception as e:
        logger.opt(exception=True).error(
            f"[hidden_commands] 清除会话留资失败 session={session_id}: {e}"
        )


async def _handle_clear_session(session_id: str, tenant_id: str) -> str:
    """清空会话：物理删除 channel_messages，历史记录不保留"""
    from src.channels.session import channel_session_manager
    channel_session_manager.delete_messages(session_id, tenant_id)
    await _clear_session_lead_info(session_id, tenant_id)
    return "会话消息已清空，开始新会话。"


async def _handle_new_session(session_id: str, tenant_id: str) -> str:
    """新会话：软删除 channel_messages（status=invalid），历史记录保留、不进入新上下文"""
    from src.channels.session import channel_session_manager
    channel_session_manager.soft_delete_messages(session_id, tenant_id)
    await _clear_session_lead_info(session_id, tenant_id)
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
