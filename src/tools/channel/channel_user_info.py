"""渠道用户信息工具

透出当前渠道侧客户信息（昵称/头像/性别/unionid/external_userid 等）给 Agent，
供外部系统推送时的字段映射使用。channel 无关：微信客服渠道读 kf context 的
channel_user_info（消息处理时已取好，零额外 API 调用）；其他渠道当前从 users
表回退读取已落库的昵称/头像/unionid。后续新渠道接入时往各自渠道 context 塞
同结构 channel_user_info 即可复用。

渠道隔离与 record_lead_capture 模式一致：execute 段判断上下文，非渠道会话
返回友好失败提示。catalog=True 进入自动发现目录。
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel
from loguru import logger

from src.tools.base import BaseTool

# 微信性别字段取值映射（官方枚举）
_GENDER_LABELS = {0: "未知", 1: "男", 2: "女"}


class _EmptyInput(BaseModel):
    """无入参"""

    pass


class GetChannelUserInfoTool(BaseTool):
    """获取当前渠道侧客户信息"""

    name = "get_channel_user_info"
    description = (
        "获取当前渠道侧客户的信息（昵称、头像、性别、微信 unionid、"
        "external_userid、归属员工等），无入参。\n\n"
        "适用场景：\n"
        "- 外部系统推送前获取客户基础信息，用于字段映射（以外部系统接口文档为准）\n"
        "- 需要客户微信昵称、头像、性别等渠道侧资料时\n\n"
        "注意事项：\n"
        "- 仅渠道会话（如微信客服）中可用；非渠道会话调用返回失败，请勿重试\n"
        "- 返回的 assignee_phone（归属员工手机号）仅用于外部系统委托登录，"
        "严禁向客户展示或用于其他用途"
    )
    usage_guide = ""
    display_name = "渠道客户信息"
    category = "channel"
    InputModel = _EmptyInput
    catalog = True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.channels.wecom_kf.context import get_kf_context

        kf_ctx = get_kf_context()
        if kf_ctx:
            return self._from_wecom_kf(kf_ctx)

        # 非微信客服渠道：回退到工具执行上下文 + users 表已落库信息
        return self._from_users_db()

    def _from_wecom_kf(self, kf_ctx: Dict[str, Any]) -> Dict[str, Any]:
        """微信客服渠道：读 kf context（实时，消息处理时已取好）"""
        info = kf_ctx.get("channel_user_info") or {}
        external_userid = kf_ctx.get("external_userid", "")
        result: Dict[str, Any] = {
            "success": True,
            "source": "wecom_kf",
            "channel": "wecom_kf",
            "user_id": kf_ctx.get("user_id"),
            "session_id": kf_ctx.get("session_id", ""),
            "external_userid": external_userid,
            "nickname": info.get("nickname", ""),
            "avatar": info.get("avatar", ""),
            "gender": info.get("gender", 0),
            "gender_label": _GENDER_LABELS.get(info.get("gender", 0), "未知"),
            "wx_unionid": info.get("wx_unionid", ""),
        }
        result.update(self._resolve_assignee(kf_ctx.get("kf_config") or {}))
        return result

    def _from_users_db(self) -> Dict[str, Any]:
        """非微信客服渠道：从工具执行上下文 + users 表回退读取"""
        from src.tools.context import current_tool_execution_context

        ctx = current_tool_execution_context()
        if not ctx or not ctx.channel or not ctx.user_id:
            logger.info("渠道客户信息被拒绝（非渠道会话）")
            return {
                "success": False,
                "error": "当前会话不是渠道会话，无法获取渠道客户信息",
                "hint": "此工具仅在渠道接入（微信客服等）场景下有效，请勿继续调用",
            }
        user = self._get_user(ctx.user_id)
        return {
            "success": True,
            "source": "users_db",
            "channel": ctx.channel,
            "user_id": ctx.user_id,
            "session_id": ctx.session_id or "",
            "external_userid": "",
            "nickname": (user or {}).get("nickname") or "",
            "avatar": (user or {}).get("avatar_url") or "",
            "gender": 0,
            "gender_label": "未知",
            "wx_unionid": (user or {}).get("wx_unionid") or "",
        }

    @staticmethod
    def _resolve_assignee(kf_config: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """解析归属员工姓名与手机号（供外部系统委托登录使用）"""
        tenant_user_id = kf_config.get("tenant_user_id")
        if not tenant_user_id:
            return {"assignee_name": None, "assignee_phone": None}
        user = GetChannelUserInfoTool._get_user(tenant_user_id)
        return {
            "assignee_name": (user or {}).get("nickname") or (user or {}).get("username"),
            "assignee_phone": (user or {}).get("phone") or None,
        }

    @staticmethod
    def _get_user(user_id: str) -> Optional[Dict[str, Any]]:
        try:
            from src.db.models import UserDB

            return UserDB.get_by_id(user_id)
        except Exception as e:
            logger.warning(f"渠道客户信息读用户记录失败 user_id={user_id}: {e}")
            return None
