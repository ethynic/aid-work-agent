"""发送顾问二维码工具

供 Agent 在微信客服售前咨询场景调用：客户索要顾问微信二维码时，把顾问二维码
图片随回复下发给客户。发送二维码不属于留资（客户可能并未添加顾问微信，无法
跟进），不写线索表、不发通知、不改会话留资状态机。

渠道隔离完全由 execute 段的 get_kf_context() 判断：非微信客服渠道返回友好
失败提示。catalog=True 进入工具目录，靠工具内 get_kf_context 渠道隔离兜底
（与 record_lead_capture 的模式一致）。
"""
from typing import Any, Dict

from pydantic import BaseModel
from loguru import logger

from src.tools.base import BaseTool


class SendAdvisorQrInput(BaseModel):
    """无参数工具，保留 InputModel 以满足 schema 生成约定。"""


class SendAdvisorQrTool(BaseTool):
    """发送顾问二维码工具"""

    name = "send_advisor_qr"
    description = (
        "把顾问微信二维码图片发送给客户，在售前咨询场景下客户索要顾问二维码时调用。\n\n"
        "适用场景：\n"
        "- 客户主动要求添加顾问微信/企微，或要求发送顾问二维码\n\n"
        "注意事项：\n"
        "- 发送二维码不属于留资：不要因此调用 record_lead_capture，"
        "也不要向客户承诺登记或回电\n"
        "- 工具返回失败（如未配置顾问二维码）时，降级引导客户留下"
        "手机号/固定电话/邮箱等联系方式，并说明客服会尽快联系\n"
        "- 调用成功后二维码图片已随本次回复发送给客户，引导客户扫码添加即可，"
        "无需再复制/下载该图片\n"
        "- 未调用本工具（或工具未返回成功）之前，禁止向客户输出任何暗示"
        "二维码已发送或即将发送的表述"
    )
    usage_guide = ""
    display_name = "发送顾问二维码"
    category = "lead_capture"
    InputModel = SendAdvisorQrInput
    catalog = True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.channels.wecom_kf.context import get_kf_context

        ctx = get_kf_context()
        if not ctx:
            logger.info("顾问二维码发送被拒绝（非微信客服渠道）")
            return {
                "success": False,
                "error": "当前渠道不支持发送顾问二维码，请直接用文字回复用户",
                "hint": "此工具仅在微信客服渠道下有效，请勿继续尝试",
            }

        kf_config = ctx.get("kf_config", {}) or {}
        tenant_id = ctx.get("tenant_id", "")
        open_kfid = ctx.get("open_kfid", "")

        employee_qr_file_id = kf_config.get("employee_qr_file_id")
        if not employee_qr_file_id:
            logger.info(
                f"顾问二维码发送降级：未配置顾问二维码 "
                f"tenant={tenant_id}, open_kfid={open_kfid}"
            )
            return {
                "success": False,
                "error": "当前客服账号未配置顾问二维码，请引导客户留下联系方式",
                "hint": "请改为引导客户留下手机号/固定电话/邮箱，并说明客服会尽快联系",
            }

        try:
            from src.core.image_asset import get_image_registry

            qr_ref = await get_image_registry().get_ref_by_file_id(
                employee_qr_file_id
            )
        except Exception as e:
            logger.warning(f"读取顾问二维码失败 open_kfid={open_kfid}: {e}")
            qr_ref = None
        if not qr_ref:
            logger.info(
                f"顾问二维码发送降级：顾问二维码资产缺失 "
                f"tenant={tenant_id}, open_kfid={open_kfid}"
            )
            return {
                "success": False,
                "error": "顾问二维码读取失败，请引导客户留下联系方式",
                "hint": "请改为引导客户留下手机号/固定电话/邮箱，并说明客服会尽快联系",
            }

        logger.info(
            f"顾问二维码发送成功: tenant={tenant_id}, open_kfid={open_kfid}"
        )
        return {
            "success": True,
            "message": "顾问二维码图片已随本次回复发送给客户，"
            "请引导客户扫码添加，请勿再次复制或下载该二维码文件",
            "images": [qr_ref.model_dump()],
        }
