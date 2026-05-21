"""转人工客服工具

供 Agent 在微信客服场景下调用，将当前会话转接给人工客服人员。
通过 contextvars 获取当前回调的 adapter/open_kfid 等上下文信息。
"""
from typing import Any, Dict

from pydantic import BaseModel
from loguru import logger

from src.tools.base import BaseTool


class TransferToHumanInput(BaseModel):
    reason: str = "用户要求人工服务"


class TransferToHumanTool(BaseTool):
    """转人工客服工具

    当用户明确要求人工服务、表达强烈不满或问题无法解决时调用。
    """

    name = "transfer_to_human"
    description = "将会话转接给人工客服。当用户明确要求人工服务、投诉或问题无法解决时使用。"
    usage_guide = (
        "当用户说'人工服务'、'转人工'、'人工客服'或表达投诉意图时调用此工具。"
        "调用后需回复客户：'正在为您转接人工客服，请稍候...'"
    )
    display_name = "转人工客服"
    category = "customer_service"
    InputModel = TransferToHumanInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        reason = kwargs.get("reason", "用户要求人工服务")

        # 从 contextvars 获取当前回调上下文
        try:
            from src.channels.wecom_kf.context import get_kf_context
        except ImportError:
            return {"success": False, "error": "转人工工具未正确初始化"}

        ctx = get_kf_context()
        if not ctx:
            return {"success": False, "error": "不在微信客服会话上下文中"}

        adapter = ctx.get("adapter")
        open_kfid = ctx.get("open_kfid", "")
        external_userid = ctx.get("external_userid", "")
        kf_config = ctx.get("kf_config", {})
        session_id = ctx.get("session_id", "")

        if not adapter or not open_kfid or not external_userid:
            return {"success": False, "error": "缺少必要的会话上下文"}

        servicer_list = kf_config.get("servicer_userid_list", [])
        if not servicer_list:
            return {"success": False, "error": "未配置人工客服人员"}

        servicer_userid = servicer_list[0]

        result = await adapter.transfer_to_human(
            open_kfid=open_kfid,
            external_userid=external_userid,
            servicer_userid=servicer_userid,
        )

        if result:
            # 更新会话元信息
            try:
                from src.channels.session import channel_session_manager
                if session_id:
                    channel_session_manager.update_session(
                        session_id=session_id,
                        metadata={
                            "service_state": 3,
                            "transferred_to": servicer_userid,
                            "transfer_reason": reason,
                        },
                    )
            except Exception as e:
                logger.warning(f"转人工后更新会话元信息失败: {e}")

            logger.info(
                f"微信客服转人工成功: servicer={servicer_userid}, "
                f"user={external_userid}, reason={reason}"
            )
            return {
                "success": True,
                "message": f"已转接人工客服（{servicer_userid}）",
            }
        else:
            logger.error(
                f"微信客服转人工失败: open_kfid={open_kfid}, user={external_userid}"
            )
            return {"success": False, "error": "转接失败，请稍后重试"}
