"""客户留资记录工具

供 Agent 在微信客服售前咨询场景调用：将客户留下的联系方式（手机号/固定电话/
邮箱）记入留资线索表（bs_lead_capture_leads），并把会话状态机置为已留资。

客户索要顾问二维码不属于留资（客户可能并未添加顾问微信，线索无法跟进），
发码动作由独立工具 send_advisor_qr 承担。

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
# 固定电话：区号（3-4 位，0 开头）+ 号码（7-8 位），区号与号码间连字符可选
_LANDLINE_RE = re.compile(r"^0\d{2,3}-?\d{7,8}$")
# 邮箱：宽松校验（本地部分@域名，域名至少含一个点）
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# 主要联系方式类型派生优先级（同时提供多项时取最靠前的作为 contact_method）
_CONTACT_METHOD_PRIORITY = ("phone", "landline", "email")


class RecordLeadCaptureInput(BaseModel):
    phone: Optional[str] = Field(None, description="客户手机号（11 位数字）")
    landline: Optional[str] = Field(None, description="客户固定电话（含区号，如 0571-88888888）")
    email: Optional[str] = Field(None, description="客户电子邮箱")
    contact_name: Optional[str] = Field(
        None, description="客户姓名（对话中提取，可选）"
    )
    demand_summary: Optional[str] = Field(
        None, description="客户基本需求摘要（对话中提取，可选）"
    )


class RecordLeadCaptureTool(BaseTool):
    """客户留资记录工具

    收集到客户任一联系方式（手机号/固定电话/邮箱）时调用，记录线索。已留资
    客户再次明确要求留资视为新的跟进需求，正常登记（不防重）。
    """

    name = "record_lead_capture"
    description = (
        "为客户登记留资（记录线索），在售前咨询场景下收集到客户联系方式时调用。\n\n"
        "适用场景：\n"
        "- 客户主动留下手机号、固定电话或电子邮箱，或同意客服稍后电话/邮件联系\n"
        "- 已按判定规则识别为有意向客户并完成需求收集\n\n"
        "注意事项：\n"
        "- phone / landline / email 至少提供一项，按客户实际提供的情况填写\n"
        "- 客户已留资过但仍明确要求留资（再次留下联系方式）："
        "视为新的跟进需求，正常登记并通知员工（注明上次留资时间）\n"
        "- 客户未明确要求留资时，不要重复引导客户留资\n"
        "- 客户索要顾问二维码不属于留资，使用 send_advisor_qr 工具发送，"
        "不要为索要二维码的客户调用本工具\n"
        "- 调用成功后提示客户：客服会尽快联系。成功结果会返回 "
        "assignee_phone（归属员工手机号），供外部系统委托登录使用；"
        "未返回说明归属员工未配置手机号"
    )
    usage_guide = ""
    display_name = "客户留资"
    category = "lead_capture"
    InputModel = RecordLeadCaptureInput
    catalog = True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        phone = (kwargs.get("phone") or "").strip() or None
        landline = (kwargs.get("landline") or "").strip() or None
        email = (kwargs.get("email") or "").strip() or None
        contact_name = kwargs.get("contact_name")
        demand_summary = kwargs.get("demand_summary")

        from src.channels.wecom_kf.context import get_kf_context

        ctx = get_kf_context()
        if not ctx:
            logger.info("客户留资被拒绝（非微信客服渠道）")
            return {
                "success": False,
                "error": "当前渠道不支持客户留资，请直接用文字回复用户",
                "hint": "此工具仅在微信客服渠道下有效，请勿继续尝试留资",
            }

        if not (phone or landline or email):
            return {
                "success": False,
                "error": "请先向客户确认至少一项联系方式（手机号/固定电话/邮箱），再登记留资",
                "hint": "phone / landline / email 至少提供一项",
            }

        contact_method = self._derive_contact_method(phone, landline, email)
        error = self._validate_contact(contact_method, phone, landline, email)
        if error:
            return error

        kf_config = ctx.get("kf_config", {}) or {}
        session_id = ctx.get("session_id", "")
        tenant_id = ctx.get("tenant_id", "")
        external_userid = ctx.get("external_userid", "")
        open_kfid = ctx.get("open_kfid", "")

        if not session_id or not tenant_id:
            return {"success": False, "error": "缺少必要的会话上下文"}

        from src.channels.session import channel_session_manager

        # 已留资客户再次明确要求留资（再次留联系方式）视为新的跟进需求，正常登记；
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

        lead_id = f"lead_lc_{uuid.uuid4().hex[:12]}"
        assigned_to = kf_config.get("tenant_user_id")
        assignee_name = self._resolve_employee_name(assigned_to)
        assignee_phone = self._resolve_employee_phone(assigned_to)
        kf_account_name = kf_config.get("name", "")

        from src.saas.db.lead_capture_db import LeadCaptureDB
        from src.notifications.lead_producer import publish_lead_captured_in_tx

        kf_config_id = ctx.get("kf_config_id") or ""
        record = LeadCaptureDB.create(
            tenant_id=tenant_id,
            lead_id=lead_id,
            user_id=ctx.get("user_id"),
            customer_user_id=external_userid,
            channel_chat_id=open_kfid,
            kf_account_name=kf_account_name,
            contact_method=contact_method,
            phone=phone,
            landline=landline,
            email=email,
            contact_name=contact_name,
            demand_summary=demand_summary,
            assigned_to=assigned_to,
            assignee_name=assignee_name,
            session_id=session_id,
            # 留资同事务发布 lead.captured 事件（规则引擎按租户规则展开通知，
            # 收编旧邮件单发路径）；发布失败在 DB 层吞掉，不影响留资结果
            event_publisher=lambda cursor, rec: publish_lead_captured_in_tx(
                cursor, rec,
                kf_config_id=kf_config_id,
                previous_captured_at=previous_captured_at,
            ),
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

        logger.info(
            f"客户留资成功: lead_id={lead_id}, tenant={tenant_id}, "
            f"method={contact_method}, open_kfid={open_kfid}"
        )
        return {
            "success": True,
            "message": "已登记客户留资，请告知客户客服会尽快联系",
            "assignee_phone": assignee_phone,
        }

    @staticmethod
    def _derive_contact_method(
        phone: Optional[str],
        landline: Optional[str],
        email: Optional[str],
    ) -> str:
        """按优先级派生主要联系方式类型：phone > landline > email。"""
        provided = {
            "phone": bool(phone),
            "landline": bool(landline),
            "email": bool(email),
        }
        return next(m for m in _CONTACT_METHOD_PRIORITY if provided[m])

    @staticmethod
    def _validate_contact(
        contact_method: str,
        phone: Optional[str],
        landline: Optional[str],
        email: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """校验各联系方式格式，非法返回失败响应，合法返回 None。"""
        if phone and not _PHONE_RE.match(phone):
            return {
                "success": False,
                "error": "手机号格式不正确，请重新向客户确认",
                "hint": "手机号应为 11 位数字且以 1 开头",
            }
        if landline and not _LANDLINE_RE.match(landline):
            return {
                "success": False,
                "error": "固定电话格式不正确，请重新向客户确认",
                "hint": "固定电话应为区号（0 开头 3-4 位）+ 号码（7-8 位），如 0571-88888888",
            }
        if email and not _EMAIL_RE.match(email):
            return {
                "success": False,
                "error": "邮箱格式不正确，请重新向客户确认",
                "hint": "邮箱应包含 @ 和域名，如 name@example.com",
            }
        return None

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

    @staticmethod
    def _resolve_employee_phone(assigned_to: Optional[str]) -> Optional[str]:
        """解析归属员工手机号（users.phone），供外部系统委托登录使用。"""
        if not assigned_to:
            return None
        try:
            from src.db.models import UserDB

            user = UserDB.get_by_id(assigned_to)
            # 空串归一为 None，与「未配置手机号」降级语义一致
            return (user or {}).get("phone") or None
        except Exception as e:
            logger.warning(f"解析归属员工手机号失败 assigned_to={assigned_to}: {e}")
            return None
