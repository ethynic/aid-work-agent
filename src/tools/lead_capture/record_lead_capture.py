"""客户留资记录工具

供 Agent 在微信客服售前咨询场景调用：将客户手机号或"选择添加员工微信"
记入留资线索表（bs_lead_capture_leads），并把会话状态机置为已留资。

渠道隔离完全由 execute 段的 get_kf_context() 判断：非微信客服渠道返回友好
失败提示。catalog=True 进入工具目录，靠工具内 get_kf_context 渠道隔离兜底
（与 transfer_to_human 的模式一致）。
"""
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from loguru import logger

from src.tools.base import BaseTool

# 中国大陆手机号：1 开头 11 位数字
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class RecordLeadCaptureInput(BaseModel):
    contact_method: str = Field(
        ...,
        description="留资方式：phone（客户提供了手机号）| qr（客户选择添加员工微信）",
    )
    phone: Optional[str] = Field(
        None, description="客户手机号，contact_method=phone 时必填"
    )
    contact_name: Optional[str] = Field(
        None, description="客户姓名（对话中提取，可选）"
    )
    demand_summary: Optional[str] = Field(
        None, description="客户基本需求摘要（对话中提取，可选）"
    )


class RecordLeadCaptureTool(BaseTool):
    """客户留资记录工具

    收集到客户手机号或客户选择添加员工微信时调用，记录线索并防止重复留资。
    """

    name = "record_lead_capture"
    description = (
        "为客户登记留资（记录线索），在售前咨询场景下收集到客户手机号、"
        "或客户选择添加员工微信时调用。\n\n"
        "适用场景：\n"
        "- 客户主动留下手机号，或同意客服稍后电话联系\n"
        "- 客户主动要求添加员工微信/企微，或选择扫码添加\n"
        "- 已按判定规则识别为有意向客户并完成需求收集\n\n"
        "注意事项：\n"
        "- 客户已留资过但仍明确要求留资（再次留下手机号或要求加微信）："
        "视为新的跟进需求，正常登记并通知员工（注明上次留资时间）\n"
        "- 客户未明确要求留资时，不要重复引导客户留资\n"
        "- 工具返回失败（如未配置员工二维码）时，降级仅引导客户留下手机号\n"
        "- 调用成功后提示客户：客服会尽快联系 / 可添加下方微信"
    )
    usage_guide = ""
    display_name = "客户留资"
    category = "lead_capture"
    InputModel = RecordLeadCaptureInput
    catalog = True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        contact_method = kwargs.get("contact_method")
        phone = kwargs.get("phone")
        contact_name = kwargs.get("contact_name")
        demand_summary = kwargs.get("demand_summary")

        if contact_method not in ("phone", "qr"):
            return {
                "success": False,
                "error": "留资方式 contact_method 必须是 phone 或 qr",
            }

        from src.channels.wecom_kf.context import get_kf_context

        ctx = get_kf_context()
        if not ctx:
            logger.info("客户留资被拒绝（非微信客服渠道）")
            return {
                "success": False,
                "error": "当前渠道不支持客户留资，请直接用文字回复用户",
                "hint": "此工具仅在微信客服渠道下有效，请勿继续尝试留资",
            }

        kf_config = ctx.get("kf_config", {}) or {}
        session_id = ctx.get("session_id", "")
        tenant_id = ctx.get("tenant_id", "")
        external_userid = ctx.get("external_userid", "")
        open_kfid = ctx.get("open_kfid", "")

        if not session_id or not tenant_id:
            return {"success": False, "error": "缺少必要的会话上下文"}

        # 手机号校验（contact_method=phone 必填）
        if contact_method == "phone":
            phone = (phone or "").strip()
            if not phone:
                return {
                    "success": False,
                    "error": "请先向客户确认手机号，再登记留资",
                    "hint": "contact_method=phone 时必须提供客户手机号",
                }
            if not _PHONE_RE.match(phone):
                return {
                    "success": False,
                    "error": "手机号格式不正确，请重新向客户确认",
                    "hint": "手机号应为 11 位数字且以 1 开头",
                }

        from src.channels.session import channel_session_manager

        # 已留资客户再次明确要求留资（加微信/留手机号）视为新的跟进需求，正常登记；
        # 仅记录上次留资时间用于通知正文，让员工结合历史需求跟进（不拒绝、不防重）。
        # 客户未明确要求时，由 Agent 引导策略避免重复引导留资，工具不在本层判断。
        try:
            session = channel_session_manager.get_session_by_id(session_id)
        except Exception as e:
            logger.warning(f"客户留资读会话状态失败 session={session_id}: {e}")
            session = None
        session_metadata = (session or {}).get("metadata") or {}
        lead_state = session_metadata.get("lead_capture") or {}
        previous_captured_at = lead_state.get("captured_at")

        # contact_method=qr 时先校验员工二维码可用性，再落库：
        # 未配置或读取失败必须「无副作用降级」，避免写入一条无二维码可发的 qr 线索。
        qr_ref = None
        if contact_method == "qr":
            employee_qr_file_id = kf_config.get("employee_qr_file_id")
            if not employee_qr_file_id:
                logger.info(
                    f"留资(qr)降级为手机号：未配置员工二维码 "
                    f"tenant={tenant_id}, open_kfid={open_kfid}"
                )
                return {
                    "success": False,
                    "error": "当前客服账号未配置员工二维码，请仅引导客户留下手机号",
                    "hint": "请改为引导客户留下手机号，并说明客服会尽快联系",
                }
            try:
                from src.core.image_asset import get_image_registry

                qr_ref = await get_image_registry().get_ref_by_file_id(
                    employee_qr_file_id
                )
            except Exception as e:
                logger.warning(f"读取员工二维码失败 open_kfid={open_kfid}: {e}")
                qr_ref = None
            if not qr_ref:
                logger.info(
                    f"留资(qr)降级为手机号：员工二维码资产缺失 "
                    f"tenant={tenant_id}, open_kfid={open_kfid}"
                )
                return {
                    "success": False,
                    "error": "员工二维码读取失败，请仅引导客户留下手机号",
                    "hint": "请改为引导客户留下手机号，并说明客服会尽快联系",
                }

        lead_id = f"lead_lc_{uuid.uuid4().hex[:12]}"
        assigned_to = kf_config.get("tenant_user_id")
        assignee_name = self._resolve_employee_name(assigned_to)
        kf_account_name = kf_config.get("name", "")

        from src.saas.db.lead_capture_db import LeadCaptureDB

        record = LeadCaptureDB.create(
            tenant_id=tenant_id,
            lead_id=lead_id,
            user_id=ctx.get("user_id"),
            customer_user_id=external_userid,
            channel_chat_id=open_kfid,
            kf_account_name=kf_account_name,
            contact_method=contact_method,
            phone=phone if contact_method == "phone" else None,
            contact_name=contact_name,
            demand_summary=demand_summary,
            assigned_to=assigned_to,
            assignee_name=assignee_name,
            session_id=session_id,
        )
        if not record:
            return {"success": False, "error": "留资记录保存失败，请稍后重试"}

        # 更新会话状态机：已留资
        # 注意：update_session 是「整体替换」metadata 而非合并，必须把既有键
        # （如 wecom_kf 的 service_state）合并进来再写，否则会误清其它会话状态。
        captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            merged_metadata = dict(session_metadata)
            merged_metadata["lead_capture"] = {
                "stage": "captured",
                "lead_id": lead_id,
                "contact_method": contact_method,
                "captured_at": captured_at,
            }
            channel_session_manager.update_session(
                session_id=session_id,
                metadata=merged_metadata,
            )
        except Exception as e:
            logger.warning(f"留资后更新会话状态失败 session={session_id}: {e}")

        # 通知归属员工（注明上次留资时间，回头客场景）；qr 时随回复下发员工二维码
        await self._notify_lead_capture(
            lead_id, tenant_id, assigned_to, assignee_name, contact_method,
            previous_captured_at=previous_captured_at,
        )
        if contact_method == "qr":
            logger.info(
                f"客户留资成功(qr): lead_id={lead_id}, tenant={tenant_id}, "
                f"open_kfid={open_kfid}"
            )
            return {
                "success": True,
                "message": "已为客户登记留资，请引导客户添加下方员工微信",
                "images": [qr_ref.model_dump()],
            }
        logger.info(
            f"客户留资成功: lead_id={lead_id}, tenant={tenant_id}, "
            f"method=phone, open_kfid={open_kfid}"
        )
        return {
            "success": True,
            "message": "已登记客户留资，请告知客户客服会尽快联系",
        }

    @staticmethod
    async def _notify_lead_capture(
        lead_id: str,
        tenant_id: str,
        assigned_to: Optional[str],
        assignee_name: Optional[str],
        contact_method: str,
        previous_captured_at: Optional[str] = None,
    ) -> None:
        """留资成功后即时通知归属员工（复用 notification_service 邮件通知）。

        设计 §4.3：通知侧按服务器时间判断工作时间，非工作时间留资的邮件正文
        提示次日跟进。员工邮箱取 user_email_settings.email_address，未配置则
        跳过。通知失败不影响留资结果，仅记录日志。

        previous_captured_at：该客户此前留资时间，非空时在通知正文注明，
        提示员工结合历史需求跟进（回头客场景）。
        """
        try:
            if not assigned_to:
                return
            from src.db.email_credential import EmailCredentialDB

            cred = EmailCredentialDB.get_by_user(assigned_to)
            email = (cred or {}).get("email_address")
            if not email:
                logger.info(f"留资通知跳过（员工未配置邮箱）: assigned_to={assigned_to}")
                return

            from src.services.notification_service import (
                NotificationChannel,
                NotificationMessage,
                notification_service,
            )

            now = datetime.now()
            is_work_time = now.weekday() < 5 and 9 <= now.hour < 18
            method_label = "手机号" if contact_method == "phone" else "员工微信"
            work_note = "" if is_work_time else "（当前为非工作时间，请于下一个工作日跟进）"
            previous_note = ""
            if previous_captured_at:
                previous_note = (
                    f"该客户此前已于 {previous_captured_at} 留资，"
                    f"请结合历史需求跟进。"
                )
            msg = NotificationMessage(
                title=f"[客户留资] 新线索 {lead_id[:12]}",
                content=(
                    f"归属员工「{assignee_name or '-'}」：客户已通过{method_label}"
                    f"方式留资，线索号 {lead_id}，请及时跟进。{previous_note}{work_note}"
                ),
                urgency="medium",
                recipient=email,
                channel=NotificationChannel.EMAIL,
                metadata={"lead_id": lead_id, "tenant_id": tenant_id},
            )
            ok = await notification_service.send(msg)
            if not ok:
                logger.warning(f"留资通知发送失败 lead_id={lead_id}, recipient={email}")
        except Exception as e:
            logger.warning(f"留资通知异常 lead_id={lead_id}: {e}")

    @staticmethod
    def _resolve_employee_name(assigned_to: Optional[str]) -> Optional[str]:
        """解析归属员工姓名（users.nickname），用于线索 assignee_name 快照。"""
        if not assigned_to:
            return None
        try:
            from src.db.models import UserDB

            user = UserDB.get_by_id(assigned_to)
            return (user or {}).get("nickname") or (user or {}).get("username")
        except Exception as e:
            logger.warning(f"解析归属员工姓名失败 assigned_to={assigned_to}: {e}")
            return None
