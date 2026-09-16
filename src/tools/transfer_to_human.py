"""转人工客服工具

供 Agent 在微信客服场景下调用，将当前会话转接给人工客服人员。
渠道隔离完全由 execute 段的 get_kf_context() 判断：非微信客服渠道
返回友好失败提示；LLM 推理时拿不到渠道信息，故 description 不
约束渠道，usage_guide 留空。
"""
from typing import Any, Dict

from pydantic import BaseModel, Field
from loguru import logger

from src.tools.base import BaseTool


class TransferToHumanInput(BaseModel):
    reason: str = Field(
        ...,
        description=(
            "转人工的原因，必填。可选值参考："
            "「user_request」（用户明确要求人工）、"
            "「complaint」（用户投诉或情绪强烈不满）、"
            "「out_of_scope」（问题超出 AI 能力范围）、"
            "「repeated_failure」（连续多次无法解决用户问题）、"
            "「other」。也可直接填写简短中文描述。"
        ),
    )


class TransferToHumanTool(BaseTool):
    """转人工客服工具

    当 Agent 判断需要转人工（用户明确要求、投诉、问题无法解决等）时调用。
    渠道是否可用由 execute 段判断，不在 description/usage_guide 中约束 LLM。
    """

    name = "transfer_to_human"
    description = (
        "将会话转接给人工客服。\n\n"
        "适用场景：\n"
        "- 用户明确要求人工服务（\"转人工\"、\"找客服\"、\"人工\"等）\n"
        "- 用户表达强烈不满、投诉情绪\n"
        "- 用户的问题明确超出你的能力范围（如：涉及资金、法律判断、复杂业务办理）\n"
        "- 连续多次尝试仍无法解决用户问题\n\n"
        "不适用场景：\n"
        "- 用户的问题你能解决（即使解决起来稍慢）\n"
        "- 用户只是表达轻微的不耐烦\n\n"
        "调用此工具后，无需再向用户发送任何文字回复（转接动作本身就是对用户的反馈）。"
        "若工具返回失败，再根据失败原因回复用户。"
    )
    usage_guide = ""
    display_name = "转人工客服"
    category = "customer_service"
    InputModel = TransferToHumanInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        reason = kwargs.get("reason")
        if not reason:
            return {
                "success": False,
                "error": "缺少转人工原因（reason 字段必填）",
            }

        from src.channels.wecom_kf.context import get_kf_context

        ctx = get_kf_context()
        if not ctx:
            logger.info(
                f"转人工被拒绝（非微信客服渠道）: reason={reason}"
            )
            return {
                "success": False,
                "error": "当前渠道未提供人工客服，请直接用文字回复用户处理其问题",
                "hint": (
                    "此工具仅在微信客服渠道下有效，请勿继续尝试转人工。"
                    "请改为直接用文字回复用户，尝试解决其问题或说明情况。"
                ),
            }

        adapter = ctx.get("adapter")
        open_kfid = ctx.get("open_kfid", "")
        external_userid = ctx.get("external_userid", "")
        kf_config = ctx.get("kf_config", {}) or {}
        session_id = ctx.get("session_id", "")

        if not adapter or not open_kfid or not external_userid:
            return {"success": False, "error": "缺少必要的会话上下文"}

        allow_agent_transfer = kf_config.get("allow_agent_transfer", True)
        if not allow_agent_transfer:
            logger.info(
                f"转人工被拒绝（管理员禁用 Agent 主动转人工）: "
                f"open_kfid={open_kfid}, user={external_userid}, reason={reason}"
            )
            return {
                "success": False,
                "error": "管理员已禁用 Agent 主动转人工",
                "hint": "请改为直接用文字回复用户处理其问题",
            }

        servicer_list = kf_config.get("servicer_userid_list", [])
        if not servicer_list:
            return {
                "success": False,
                "error": "当前客服账号未配置人工客服人员，无法转接",
                "hint": "请联系管理员在客服账号配置中添加 servicer_userid_list",
            }

        servicer_userid = servicer_list[0]

        result = await adapter.transfer_to_human(
            open_kfid=open_kfid,
            external_userid=external_userid,
            servicer_userid=servicer_userid,
        )

        if result:
            from datetime import datetime
            from src.channels.session import channel_session_manager

            transferred_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                if session_id:
                    channel_session_manager.update_session(
                        session_id=session_id,
                        metadata={
                            "service_state": 3,
                            "transferred_to": servicer_userid,
                            "transfer_reason": reason,
                            "transfer_source": "agent",
                            "last_transferred_at": transferred_at,
                        },
                    )
            except Exception as e:
                logger.warning(f"转人工后更新会话元信息失败: {e}")

            # 写入一条 system 标记消息到 channel_messages，让 Agent 重建上下文时
            # 看到"此前的转人工请求已处理完成"，避免基于历史中的"请转人工"字样
            # 再次触发 transfer_to_human。不删除任何历史对话。
            try:
                tenant_id = ctx.get("tenant_id", "")
                if session_id:
                    channel_session_manager.add_message(
                        session_id=session_id,
                        role="system",
                        content=(
                            f"[已转人工] 用户此前已请求转人工并已转接给人工客服（{servicer_userid}），该次请求已处理完成。"
                        ),
                        message_type="text",
                        tenant_id=tenant_id,
                        metadata={
                            "kind": "transfer_to_human_marker",
                            "servicer_userid": servicer_userid,
                            "reason": reason,
                            "transferred_at": transferred_at,
                        },
                    )
            except Exception as e:
                logger.warning(f"转人工后写入 system 标记消息失败: {e}")

            # 人工服务归属回写线索（#64）：确定性数据事件驱动即时写，零 LLM 成本。
            # 仅当会话已留资（metadata.lead_capture）且线索未被删除时生效；
            # 多次转人工最后写赢（字段语义即「最近一次」）。失败仅留痕，不影响转接主流程
            try:
                import asyncio as _asyncio

                lead_id = ""
                if session_id:
                    session_row = await _asyncio.to_thread(
                        channel_session_manager.get_session_by_id, session_id
                    ) or {}
                    session_meta = session_row.get("metadata") or {}
                    lead_id = (session_meta.get("lead_capture") or {}).get("lead_id") or ""
                if lead_id:
                    from src.core.cache_utils import CacheKeys
                    from src.core.redis_client import redis_client
                    from src.saas.db.lead_capture_db import LeadCaptureDB

                    # 员工姓名尽力而为：复用渠道侧 userid->name Redis 缓存
                    # （人工期员工消息落库时已填充），映射不到为空（设计 §9.2）
                    servicer_name = ""
                    try:
                        cached = redis_client.get(
                            f"{CacheKeys.WECOM_KF_SERVICER_NAME}:{tenant_id}:{servicer_userid}"
                        )
                        servicer_name = str(cached) if cached else ""
                    except Exception:
                        pass

                    updated = await _asyncio.to_thread(
                        LeadCaptureDB.update_transfer_info,
                        lead_id,
                        tenant_id,
                        servicer_userid,
                        servicer_name or None,
                    )
                    logger.info(
                        f"转人工回写线索归属: lead_id={lead_id}, servicer={servicer_userid}, "
                        f"name={servicer_name or '空'}, updated={updated}"
                    )
            except Exception as e:
                logger.warning(f"转人工回写线索归属失败（不影响转接）: {e}")

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
