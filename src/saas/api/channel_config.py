"""
SaaS 渠道配置管理 API

路由：/api/saas/channels/*
"""

import asyncio
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

try:
    from psycopg2 import IntegrityError
except ImportError:  # psycopg2 未安装（开发/测试场景）
    IntegrityError = None  # type: ignore[assignment,misc]

from src.core import master_agent
from src.saas.api.tenant_auth import require_admin
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.enums import BehaviorAction, BehaviorResourceType
from src.saas.services.channel_factory import (
    ChannelFactory,
    MEDIA_SWITCH_CHANNEL_TYPES,
    extract_media_switches,
)
from src.services.behavior_log import audit_action
from src.db.database import get_db_connection
from src.wechat_mp import config_codec as wechat_mp_codec

router = APIRouter(prefix="/api/saas/channels", tags=["SaaS 渠道配置"])


class ChannelConfigCreateRequest(BaseModel):
    channel_type: str = Field(..., description="渠道类型：wecom/wecom_kf/dingtalk/feishu/wechat_mp 等")
    name: Optional[str] = Field(None, description="渠道名称（用户自定义，用于区分同一租户的多个同类渠道）")
    config: dict = Field(
        ...,
        description=(
            "渠道凭证配置。wechat_mp 类型支持可选 config.callback_token 自定义回调 Token"
            "（3~32 位字母数字，公众平台 Token 规则）；留空/缺省由服务端生成"
        ),
    )
    subagent_type: Optional[str] = Field(None, description="关联的数字员工类型（如 travel-consultant），不填则不绑定")


class ChannelConfigUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="渠道名称（用户自定义，用于区分同一租户的多个同类渠道）")
    config: dict = Field(
        ...,
        description=(
            "渠道凭证配置。wechat_mp 类型传入 config.callback_token 视为改密"
            "（3~32 位字母数字，旧 Token 失效）；留空/缺省保留旧值"
        ),
    )
    subagent_type: Optional[str] = Field(None, description="关联的数字员工类型（如 travel-consultant）")


# 各渠道类型必填字段
_REQUIRED_FIELDS = {
    "wecom": {
        "corp_id": "企业ID（在「我的企业」页面获取，格式 ww 开头）",
        "agent_id": "应用AgentId（在应用详情页获取）",
        "secret": "应用Secret（在应用详情页获取）",
        "token": "回调Token（设置API接收时配置）",
        "encoding_aes_key": "回调EncodingAESKey（设置API接收时配置，43字符Base64）",
    },
    "wecom_kf": {
        "corp_id": "企业ID（在「我的企业」页面获取，格式 ww 开头）",
        "secret": "应用Secret（自建应用的 Secret，微信客服无独立 Secret）",
        "token": "回调Token（设置API接收时配置）",
        "encoding_aes_key": "回调EncodingAESKey（设置API接收时配置，43字符Base64）",
    },
    "dingtalk": {
        "app_key": "应用AppKey",
        "app_secret": "应用AppSecret",
        "token": "回调Token",
        "encoding_aes_key": "回调EncodingAESKey",
    },
    "feishu": {
        "app_id": "应用App ID",
        "app_secret": "应用App Secret",
        "verification_token": "验证Token",
        # encrypt_key 可选（不填则非加密模式，仅用于开发/调试）
    },
    # wechat_mp 不走静态必填表：公众号「服务器配置」回调链路只需 callback_token
    # （明文模式）或 callback_token + encoding_aes_key + appid（安全模式）；
    # appid / original_id 均可选（空值在 create/update 的 wechat_mp 分支规整删除，
    # 安全模式下 appid 缺失由 _validate_wechat_mp_safe_mode 条件校验拦截）。
    # callback_token 也不在此表——默认服务端生成，WP11 起支持可选自定义
    # （config.callback_token，3~32 位字母数字，见 _validate_wechat_mp_custom_token）
    # 企业微信应用通知（wecom_app_notify，长任务企微应用消息）：corp_id/agentid/secret
    # 三项即用，无回调链路，secret 加密落库（channel_config_db 敏感字段机制）
    "wecom_app_notify": {
        "corp_id": "企业ID（在「我的企业」页面获取，格式 ww 开头）",
        "agentid": "通知应用AgentId（在应用详情页获取）",
        "secret": "通知应用Secret（在应用详情页获取）",
    },
    # web_c（官网客服）：无第三方凭证，channel_secret 由服务端自动生成，
    # 其余键（enabled/greeting/identity_mode）为可选展示/行为配置
    "web_c": {},
}

_WECHAT_MP_CHANNEL_TYPE = "wechat_mp"
# 官网客服（web_c）：C 端免登录聊天渠道，签名链接密钥由服务端自动生成
_WEB_C_CHANNEL_TYPE = "web_c"
# 企业微信应用通知（长任务企微应用消息，租户自备凭据；与消息渠道 wecom_app
# target/delivery 全链无关，仅是租户渠道配置类型名）
_WECOM_APP_NOTIFY_CHANNEL_TYPE = "wecom_app_notify"
_WECOM_CHANNEL_TYPE = "wecom"
# 飞书应用消息渠道（agent 对话接入；飞书通知接入 2026-11-15 起支持通知用途）
_FEISHU_CHANNEL_TYPE = "feishu"
# 钉钉应用消息渠道（agent 对话接入；钉钉通知接入 2026-11-16 起支持通知用途）
_DINGTALK_CHANNEL_TYPE = "dingtalk"

# 应用渠道「通知用途」（config.notify_usage，企微渠道合并 2026-11-15，飞书接入
# 2026-11-15、钉钉接入 2026-11-16 同模式）：agent_only 仅智能体接入（缺省，存量
# 行为不变）；both 智能体接入+通知（通知借用此应用凭据发送）；notify_only 仅通知
# （不接 agent，回调链路不注册）
_NOTIFY_USAGE_KEY = "notify_usage"
_NOTIFY_USAGES = ("agent_only", "both", "notify_only")
# 支持「通知用途」单选的渠道类型
_NOTIFY_USAGE_CHANNEL_TYPES = (_WECOM_CHANNEL_TYPE, _FEISHU_CHANNEL_TYPE, _DINGTALK_CHANNEL_TYPE)

# notify_only 的必填字段：纯服务端 API 发消息无回调链路，回调字段免填
# （企微：token/encoding_aes_key；飞书：verification_token，encrypt_key 本就可选；
# 钉钉：token/encoding_aes_key——通知发送仅需 app_key/app_secret/agent_id 三项，
# agent_id 为工作通知应用 AgentId）
_WECOM_NOTIFY_ONLY_REQUIRED_FIELDS = {
    "wecom": {
        "corp_id": "企业ID（在「我的企业」页面获取，格式 ww 开头）",
        "agent_id": "应用AgentId（在应用详情页获取）",
        "secret": "应用Secret（在应用详情页获取）",
    }
}
_FEISHU_NOTIFY_ONLY_REQUIRED_FIELDS = {
    "feishu": {
        "app_id": "应用App ID",
        "app_secret": "应用App Secret",
    }
}
_DINGTALK_NOTIFY_ONLY_REQUIRED_FIELDS = {
    "dingtalk": {
        "app_key": "应用AppKey",
        "app_secret": "应用AppSecret",
        "agent_id": "应用AgentId（在应用详情页获取，工作通知发送必须）",
    }
}
# notify_only 保存时保留库内旧值的回调字段（wecom/feishu/dingtalk 同模式）：「仅通知」
# 表单隐藏这些字段、前端不提交；update 整体覆盖 config 若不回填，库内回调凭据会被
# 清除，改回 agent_only/both 时须重填。仅通知用途不注册回调链路，保留不影响运行。
_NOTIFY_ONLY_RETAINED_FIELDS = {
    _WECOM_CHANNEL_TYPE: ("token", "encoding_aes_key"),
    _FEISHU_CHANNEL_TYPE: ("verification_token", "encrypt_key"),
    _DINGTALK_CHANNEL_TYPE: ("token", "encoding_aes_key"),
}


def _generate_web_c_secret() -> str:
    """生成 web_c 签名密钥（32 字节 hex，与设计 §3.2 一致）。"""
    import secrets as _secrets

    return _secrets.token_hex(32)


_WEB_C_IDENTITY_MODES = ("ext", "anon", "uid")
# web_c config 缺省值（create 时补齐；identity_mode 非法值 400）
_WEB_C_DEFAULT_GREETING = "您好，我是本公司的智能助手，有什么可以帮您？"

# web_c 自定义 Logo 校验（2026-11-26）：仅 data:image base64（png/jpg/webp/svg），
# base64 串 ≤140_000 字符（约 100KB 原图）。Logo 为小图标直存 config JSON、
# 随 bootstrap 下发，不走文件存储/独立下载端点（C 端为免登录页面，避免新增匿名
# 文件通道）；非法值 400（表单来源，出现非法值即为异常调用，不静默纠正）
_WEB_C_LOGO_PATTERN = re.compile(
    r"^data:image/(png|jpe?g|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$")
_WEB_C_LOGO_MAX_BASE64_LEN = 140_000


def _validate_web_c_logo(value) -> str:
    """校验 web_c 自定义 Logo：合法返回原值，空串/None = 未配置（系统默认图标）。"""
    logo = str(value or "").strip()
    if not logo:
        return ""
    if len(logo) > _WEB_C_LOGO_MAX_BASE64_LEN:
        raise HTTPException(status_code=400, detail="Logo 图片过大，请上传 100KB 以内的图片")
    if not _WEB_C_LOGO_PATTERN.match(logo):
        raise HTTPException(status_code=400, detail="Logo 仅支持 png/jpg/webp/svg 格式的图片")
    return logo


def _normalize_web_c_config(config: dict, existing: Optional[dict] = None) -> None:
    """原地规整 web_c config（create/update 共用）。

    - channel_secret：缺失/空串/掩码保留旧值（update 场景 existing 传入），
      create 时无旧值则自动生成；伪造防线密钥不由租户表单提供。
    - enabled：缺省 True；greeting：缺省通用文案；allow_attachment：缺省 False
      （纯文字聊天）；show_history：缺省 False（聊天页不显示历史会话入口；
      仅签名链接模式生效，uid/anon 在 bootstrap 强制 False）；
      identity_mode：缺省 anon（2026-11-19 起下线「签名 + 匿名」组合模式，
      仅保留匿名 / 签名 / userid 三种单一模式），
      非法值 400（表单单选来源，出现非法值即为异常调用，不静默纠正）。
    - title：聊天页 document.title（嵌入小程序 web-view 等容器时导航栏标题）；
      缺省空串 = 未配置，C 端保持系统默认标题（index.html 的「AID Work Agent」）。
    - subtitle：聊天页标题下方副标题小字；空串 = C 端兜底显示「智能助手」。
    - logo：聊天页自定义 Logo（data URL base64，校验见 _validate_web_c_logo）；
      空串 = 系统默认图标。
    """
    config["enabled"] = bool(config.get("enabled", True))
    config["greeting"] = str(config.get("greeting") or "").strip() or _WEB_C_DEFAULT_GREETING
    # 页面标题（2026-11-25）：空串 = 未配置，C 端不覆盖默认标题
    config["title"] = str(config.get("title") or "").strip()
    # 副标题（2026-11-26）：聊天页标题下方小字；空串 = C 端兜底显示「智能助手」
    config["subtitle"] = str(config.get("subtitle") or "").strip()
    # 自定义 Logo（2026-11-26）：data URL base64 直存 config；空串 = 系统默认图标
    config["logo"] = _validate_web_c_logo(config.get("logo"))
    # 附件上传开关：默认关闭（纯文字聊天）；开启后 C 端显示附件按钮并放行上传
    config["allow_attachment"] = bool(config.get("allow_attachment", False))
    # 历史会话入口开关：默认关闭（聊天页右上角不显示「历史会话」按钮）
    config["show_history"] = bool(config.get("show_history", False))
    mode = str(config.get("identity_mode") or "anon").strip()
    if mode not in _WEB_C_IDENTITY_MODES:
        raise HTTPException(status_code=400, detail="身份模式取值应为 ext / anon / uid")
    config["identity_mode"] = mode

    # 留资归属员工（2026-11-20 方案 2）：留资线索 assigned_to / 通知 kf_assignee
    # 接收人来源；空串 = 未配置（线索不归属员工，kf_assignee 模式零接收人）。
    # 归属校验（本租户 + active）在 create/update 处 _validate_web_c_lead_assignee 执行
    config["lead_assignee_user_id"] = str(config.get("lead_assignee_user_id") or "").strip()

    secret = str(config.get("channel_secret") or "").strip()
    if secret.startswith("***"):
        secret = ""
    if not secret:
        old_secret = str((existing or {}).get("channel_secret") or "").strip()
        config["channel_secret"] = old_secret or _generate_web_c_secret()


def _normalize_media_switch_config(channel_type: str, config: dict) -> None:
    """原地规整媒体识别开关字段（create/update 共用，2026-11-19）。

    支持渠道（wecom/wecom_kf/dingtalk/feishu）将 asr_enabled（缺省 True）/
    image_recognition_enabled（缺省 False）以规整后的布尔值写回 config，
    保证存量/前端漏传时落库值确定；非支持渠道不动（不注入无关键）。
    """
    switches = extract_media_switches(channel_type, config)
    config.update(switches)


def _notify_usage(channel_type: str, config: dict) -> str:
    """规整并校验支持「通知用途」渠道（wecom/feishu/dingtalk）config 中的通知用途。

    缺省/空值 = agent_only（存量配置行为不变）；非法值 400（表单单选来源，
    出现非法值即为异常调用，不静默纠正）。
    """
    if channel_type not in _NOTIFY_USAGE_CHANNEL_TYPES:
        return "agent_only"
    raw = str((config or {}).get(_NOTIFY_USAGE_KEY) or "").strip()
    if not raw:
        return "agent_only"
    if raw not in _NOTIFY_USAGES:
        raise HTTPException(
            status_code=400,
            detail="通知用途取值应为 agent_only / both / notify_only",
        )
    return raw


def _required_fields_missing(channel_type: str, config: dict) -> list:
    """支持「通知用途」的渠道（wecom/feishu/dingtalk）按用途分支的必填校验
    （create/update 共用）。

    notify_only 仅发消息凭据必填（无回调链路，回调字段免填）；agent_only/both
    保持原必填。其他渠道返回空列表。
    """
    if channel_type == _WECOM_CHANNEL_TYPE:
        required_map = (_WECOM_NOTIFY_ONLY_REQUIRED_FIELDS[_WECOM_CHANNEL_TYPE]
                        if _notify_usage(channel_type, config) == "notify_only"
                        else _REQUIRED_FIELDS[_WECOM_CHANNEL_TYPE])
    elif channel_type == _FEISHU_CHANNEL_TYPE:
        required_map = (_FEISHU_NOTIFY_ONLY_REQUIRED_FIELDS[_FEISHU_CHANNEL_TYPE]
                        if _notify_usage(channel_type, config) == "notify_only"
                        else _REQUIRED_FIELDS[_FEISHU_CHANNEL_TYPE])
    elif channel_type == _DINGTALK_CHANNEL_TYPE:
        required_map = (_DINGTALK_NOTIFY_ONLY_REQUIRED_FIELDS[_DINGTALK_CHANNEL_TYPE]
                        if _notify_usage(channel_type, config) == "notify_only"
                        else _REQUIRED_FIELDS[_DINGTALK_CHANNEL_TYPE])
    else:
        return []
    return [f"{k}（{v}）" for k, v in required_map.items() if not (config or {}).get(k)]


def _build_wechat_mp_callback_url(request: Request, config_id: str) -> str:
    """拼接 wechat_mp 回调完整 URL。

    公网 base 优先取 settings.app.public_base_url（部署约定，文件下载链接同源配置），
    未配置时回退请求自身的 base（与前端 window.location.origin 约定一致）。
    """
    from src.config.settings import settings

    base = (getattr(settings.app, "public_base_url", "") or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}/api/wechat-mp/callback/{config_id}"


def _attach_wechat_mp_callback_url(request: Request, channel: Optional[dict]) -> Optional[dict]:
    """对 wechat_mp 渠道的响应补充完整 callback_url 字段。"""
    if channel and channel.get("channel_type") == _WECHAT_MP_CHANNEL_TYPE:
        channel = dict(channel)
        channel["callback_url"] = _build_wechat_mp_callback_url(request, channel["config_id"])
    return channel


def _validate_wechat_mp_custom_token(config: dict) -> str:
    """规整并校验 wechat_mp 的可选自定义回调 Token（WP11）。

    返回去空白后的自定义 Token（空串/掩码 *** 开头表示未提供——create 走服务端
    生成、update 保留旧值，与 DB 层掩码保留语义一致）；其余格式不合法
    （非 3~32 位字母数字）抛 HTTPException(400)。
    """
    custom_token = str((config or {}).get("callback_token") or "").strip()
    if (
        custom_token
        and not custom_token.startswith("***")
        and not wechat_mp_codec.is_valid_callback_token(custom_token)
    ):
        raise HTTPException(
            status_code=400,
            detail="回调 Token 需为 3~32 位字母或数字（公众平台服务器配置 Token 规则）",
        )
    return "" if custom_token.startswith("***") else custom_token


# wechat_mp 可选键：值为空串/纯空白时从 config 中删除（appid / original_id / 敏感字段）
_WECHAT_MP_TRIM_KEYS = ("appid", "original_id", "encoding_aes_key", "secret")


def _normalize_wechat_mp_config(config: dict) -> None:
    """原地删除 wechat_mp config 中值为空串/纯空白 的可选键。

    空串 appid 若照写入库，同租户第二条空 appid 会撞部分唯一索引
    uq_tenant_channel_configs_wechat_mp_appid（config->>'appid'）返回误导性的
    409「该公众号已配置过」，故统一 pop（键缺失 → JSONB ->> 返回 NULL → 不冲突）。
    敏感字段 pop 后由 DB 层「缺失/空值保留旧值」语义兜底（update 场景），行为不变。
    """
    for key in _WECHAT_MP_TRIM_KEYS:
        val = config.get(key)
        if isinstance(val, str) and not val.strip():
            config.pop(key, None)


def _validate_wechat_mp_safe_mode(config: dict, existing_appid: str = "") -> None:
    """安全模式条件校验：提供了 EncodingAESKey（非空且非 *** 掩码）但 appid 仍为空 → 400。

    安全模式回调密文携带接收方 appid，AES 解密依赖它做接收方校验，缺 appid 无法解密。
    update 场景传 existing_appid（get_by_id 返回的 appid 非敏感字段、为明文），
    本次提交值与 DB 现有值合并后仍为空才拦截；掩码（*** 开头）= 未改动旧值，跳过校验。
    """
    aes_key = str(config.get("encoding_aes_key") or "")
    if not aes_key or aes_key.startswith("***"):
        return
    appid = str(config.get("appid") or "").strip() or str(existing_appid or "").strip()
    if not appid:
        raise HTTPException(
            status_code=400,
            detail="安全模式需同时提供公众号 AppID（AES 解密接收方校验用）",
        )


@router.get("")
async def list_channels(request: Request):
    """列出当前租户的渠道配置"""
    admin = require_admin(request)
    configs = ChannelConfigDB.list_by_tenant(admin["tenant_id"])
    configs = [_attach_wechat_mp_callback_url(request, c) for c in configs]
    return {"success": True, "channels": configs}


@router.post("")
@audit_action(BehaviorAction.CREATE, BehaviorResourceType.CONFIG, name_arg="name")
async def create_channel(request: Request, body: ChannelConfigCreateRequest):
    """新增渠道配置"""
    admin = require_admin(request)

    if body.channel_type not in ("wecom", "wecom_kf", "dingtalk", "feishu",
                                 _WECHAT_MP_CHANNEL_TYPE, _WECOM_APP_NOTIFY_CHANNEL_TYPE,
                                 _WEB_C_CHANNEL_TYPE):
        raise HTTPException(status_code=400, detail=f"不支持的渠道类型: {body.channel_type}")

    # wecom/feishu/dingtalk 按通知用途分支必填（notify_usage 非法值在此处 400）；其余渠道静态必填表
    if body.channel_type in _NOTIFY_USAGE_CHANNEL_TYPES:
        missing = _required_fields_missing(body.channel_type, body.config)
    else:
        required = _REQUIRED_FIELDS.get(body.channel_type, {})
        missing = [f"{k}（{v}）" for k, v in required.items() if not body.config.get(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"缺少必填字段: {', '.join(missing)}",
        )

    # wechat_mp：appid/original_id 可选（空值规整删除）；original_id 有值才校验 gh_
    # 前缀（运行时 callback.py 同为可选旁路校验，留空即跳过）；callback_token 可选
    # 自定义（3~32 位字母数字），留空/缺省由服务端生成（现状行为不变）
    if body.channel_type == _WECHAT_MP_CHANNEL_TYPE:
        body.config = dict(body.config)
        _normalize_wechat_mp_config(body.config)
        original_id = str(body.config.get("original_id") or "").strip()
        if original_id and not original_id.startswith("gh_"):
            raise HTTPException(status_code=400, detail="original_id 格式应为 gh_ 开头的公众号原始 ID")
        custom_token = _validate_wechat_mp_custom_token(body.config)
        if custom_token:
            body.config["callback_token"] = custom_token
        else:
            body.config.pop("callback_token", None)
        _validate_wechat_mp_safe_mode(body.config)

    if body.channel_type == _WEB_C_CHANNEL_TYPE:
        body.config = dict(body.config)
        _normalize_web_c_config(body.config)
        _validate_web_c_lead_assignee(admin["tenant_id"], body.config)

    # 媒体识别开关（2026-11-19）：支持渠道规整布尔值落库（缺省 ASR 开、图片识别关）
    if body.channel_type in MEDIA_SWITCH_CHANNEL_TYPES:
        body.config = dict(body.config)
        _normalize_media_switch_config(body.channel_type, body.config)

    _validate_channel_subagent(admin["tenant_id"], body.subagent_type)

    try:
        config = ChannelConfigDB.create(
            tenant_id=admin["tenant_id"],
            channel_type=body.channel_type,
            name=body.name,
            config=body.config,
            subagent_type=body.subagent_type,
        )
    except Exception as e:
        # IntegrityError 为 DB 部分唯一索引拦截（如 wechat_mp 同租户同 appid 并发创建）
        if IntegrityError is not None and isinstance(e, IntegrityError):
            if body.channel_type == _WECHAT_MP_CHANNEL_TYPE:
                raise HTTPException(
                    status_code=409,
                    detail="该公众号（appid）已在本租户配置过，请编辑现有配置",
                )
            raise HTTPException(
                status_code=409,
                detail=f"渠道配置唯一约束冲突（{body.channel_type}）",
            )
        logger.error(f"create channel exception: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="创建渠道配置失败")

    if not config:
        # wechat_mp 同租户同 appid 应用层拦截（DB 唯一索引并发兜底走上面 409）
        if body.channel_type == _WECHAT_MP_CHANNEL_TYPE:
            raise HTTPException(status_code=409, detail="该公众号（appid）已在本租户配置过，请编辑现有配置")
        raise HTTPException(status_code=500, detail="创建渠道配置失败")

    logger.info(f"Channel config created: {config['config_id']} ({body.channel_type}, subagent={body.subagent_type})")
    config = _attach_wechat_mp_callback_url(request, config)
    response = {"success": True, "channel": config}
    if body.channel_type == _WECHAT_MP_CHANNEL_TYPE:
        # callback_token 仅创建时返回一次明文（此后一律掩码），供用户粘贴到公众平台后台
        decrypted = ChannelConfigDB.get_by_id_decrypted(config["config_id"])
        token_plain = ((decrypted or {}).get("config") or {}).get("callback_token") or ""
        if token_plain:
            response["callback_token_plaintext"] = token_plain
    return response


@router.put("/{config_id}")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG, id_arg="config_id", name_arg="name")
async def update_channel(config_id: str, request: Request, body: ChannelConfigUpdateRequest):
    """更新渠道配置"""
    admin = require_admin(request)
    existing = ChannelConfigDB.get_by_id(config_id)

    if not existing:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if existing["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")

    # wecom_kf：客服账号（kf_account）由 /wecom-kf/accounts 独立接口管理创建/编辑/删除，
    # 渠道保存只更新凭证、等待提示等渠道级配置。保存时以数据库现有 kf_account 为准，
    # 防止前端编辑页打开时的旧快照整体覆盖 config 导致新建账号被丢弃（客户扫码链接失效）。
    if existing["channel_type"] == "wecom_kf":
        body.config = dict(body.config)
        body.config["kf_account"] = (existing.get("config") or {}).get("kf_account") or []

    # wecom/feishu/dingtalk：update 同样按通知用途分支必填（notify_only 免填回调字段；
    # agent_only/both 保持原必填，按本次提交的 config 校验，notify_usage 非法值同样
    # 在此 400）
    missing = _required_fields_missing(existing["channel_type"], body.config)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"缺少必填字段: {', '.join(missing)}",
        )

    # 切到 notify_only：未提交的回调字段保留库内旧值（改回 agent_only/both 免重填）；
    # 显式提交的值以提交值为准，库内无旧值则不补键
    if _notify_usage(existing["channel_type"], body.config) == "notify_only":
        body.config = dict(body.config)
        for key in _NOTIFY_ONLY_RETAINED_FIELDS[existing["channel_type"]]:
            if not body.config.get(key) and (existing.get("config") or {}).get(key):
                body.config[key] = existing["config"][key]

    # wechat_mp：appid/original_id 可选（空值规整删除，DB 层缺失回填旧值语义不变）；
    # original_id 有值才校验 gh_ 前缀；callback_token 传入合法明文视为改密
    # （旧 Token 失效语义与轮换一致），留空/缺省/掩码保留旧值
    mp_custom_token = ""
    if existing["channel_type"] == _WECHAT_MP_CHANNEL_TYPE:
        body.config = dict(body.config)
        _normalize_wechat_mp_config(body.config)
        mp_custom_token = _validate_wechat_mp_custom_token(body.config)
        if mp_custom_token:
            body.config["callback_token"] = mp_custom_token
        else:
            body.config.pop("callback_token", None)
        original_id = str(body.config.get("original_id") or "").strip()
        if original_id and not original_id.startswith("gh_"):
            raise HTTPException(status_code=400, detail="original_id 格式应为 gh_ 开头的公众号原始 ID")
        _validate_wechat_mp_safe_mode(
            body.config,
            existing_appid=str((existing.get("config") or {}).get("appid") or ""),
        )

    if existing["channel_type"] == _WEB_C_CHANNEL_TYPE:
        body.config = dict(body.config)
        # web_c get_by_id 返回明文 config（非敏感字段机制渠道），旧 secret 可直接沿用
        _normalize_web_c_config(body.config, existing.get("config") or {})
        _validate_web_c_lead_assignee(admin["tenant_id"], body.config)

    # 媒体识别开关（2026-11-19）：支持渠道规整布尔值落库（缺省 ASR 开、图片识别关；
    # 前端漏传/存量旧表单整体覆盖时开关值确定，不会静默丢失）
    if existing["channel_type"] in MEDIA_SWITCH_CHANNEL_TYPES:
        body.config = dict(body.config)
        _normalize_media_switch_config(existing["channel_type"], body.config)

    _validate_channel_subagent(admin["tenant_id"], body.subagent_type)

    try:
        success = ChannelConfigDB.update(config_id, body.config, subagent_type=body.subagent_type, name=body.name)
    except Exception as e:
        # wechat_mp appid 变更撞同租户唯一索引 → 友好 409（与 create 对称）
        if (
            IntegrityError is not None
            and isinstance(e, IntegrityError)
            and existing["channel_type"] == _WECHAT_MP_CHANNEL_TYPE
        ):
            raise HTTPException(status_code=409, detail="该公众号（appid）已在本租户配置过，请编辑现有配置")
        logger.error(f"update channel exception: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="更新渠道配置失败")
    if success:
        # 配置变更后失效缓存的 adapter，下次回调重建
        # 按 config_id 精确失效（避免清掉同租户同渠道其他 config 的缓存）
        try:
            await ChannelFactory.invalidate_adapter(
                existing["tenant_id"],
                existing["channel_type"],
                config_id=existing["config_id"],
                close=True,
            )
        except Exception as e:
            logger.warning(f"失效 adapter 缓存失败: {e}")
        updated = ChannelConfigDB.get_by_id(config_id)
        updated = _attach_wechat_mp_callback_url(request, updated)
        response = {"success": True, "channel": updated}
        if mp_custom_token:
            # 传入自定义回调 Token 视为改密：新 Token 明文仅此一次返回（与创建/轮换语义一致）
            decrypted = ChannelConfigDB.get_by_id_decrypted(config_id)
            token_plain = ((decrypted or {}).get("config") or {}).get("callback_token") or ""
            if token_plain:
                response["callback_token_plaintext"] = token_plain
        return response
    return {"success": False, "message": "更新失败"}


@router.delete("/{config_id}")
@audit_action(BehaviorAction.DELETE, BehaviorResourceType.CONFIG, id_arg="config_id")
async def delete_channel(config_id: str, request: Request):
    """删除渠道配置"""
    admin = require_admin(request)
    existing = ChannelConfigDB.get_by_id(config_id)

    if not existing:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if existing["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")

    success = ChannelConfigDB.delete(config_id)
    if success:
        # 删除配置后关闭并失效缓存的 adapter
        # 按 config_id 精确失效（避免清掉同租户同渠道其他 config 的缓存）
        try:
            await ChannelFactory.invalidate_adapter(
                existing["tenant_id"],
                existing["channel_type"],
                config_id=existing["config_id"],
                close=True,
            )
        except Exception as e:
            logger.warning(f"失效 adapter 缓存失败: {e}")
    return {"success": success}


def _run_wechat_mp_api_check(tenant_id: str, config_id: str, appid: str, secret: str) -> dict:
    """接口通道三步实测（WP9，同步实现，调用方放 asyncio.to_thread）。

    stable_token → batchget(offset 0, count 1, no_content=1) → 有消息时对首条
    getarticle。返回 {ok, steps, errcode, message}；任何失败不带凭据/响应正文/
    异常原文出栈（客户端已保证 message 脱敏可直接展示）。batchget 空集合视为
    通过（新公众号可能还没有「发布」文章，getarticle 无从实测）。
    """
    from src.wechat_mp.client import WeChatMPAPIClient, friendly_api_error

    steps: list = []
    client = WeChatMPAPIClient(
        tenant_id=tenant_id, config_id=config_id, appid=appid, secret=secret
    )
    try:
        return _wechat_mp_api_check_steps(client, steps)
    finally:
        # 客户端自持 httpx 连接池：一次性探针即用即弃（所有返回路径均释放）
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _wechat_mp_api_check_steps(client, steps: list) -> dict:
    """三步实测主体（client 生命周期由调用方管理）。"""
    try:
        client.get_access_token()
        steps.append({"step": "stable_token", "ok": True})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "steps": steps, "errcode": getattr(e, "errcode", None),
                "message": friendly_api_error(e)}

    try:
        page = client.batchget_page(0, 1, no_content=1)
        total_count = page.get("total_count")
        items = page.get("item") if isinstance(page.get("item"), list) else []
        steps.append({"step": "batchget", "ok": True, "total_count": total_count})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "steps": steps, "errcode": getattr(e, "errcode", None),
                "message": friendly_api_error(e)}

    first_id = items[0].get("article_id") if items and isinstance(items[0], dict) else None
    if not first_id:
        return {
            "ok": True, "steps": steps, "errcode": None,
            "message": "接口通道验证通过（当前公众号暂无已发布文章，正文拉取待有文章后实测）",
        }
    try:
        client.getarticle(first_id)
        steps.append({"step": "getarticle", "ok": True})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "steps": steps, "errcode": getattr(e, "errcode", None),
                "message": friendly_api_error(e)}
    return {"ok": True, "steps": steps, "errcode": None, "message": "接口通道验证通过"}


@router.post("/{config_id}/verify")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG, id_arg="config_id")
async def verify_channel(config_id: str, request: Request):
    """验证渠道凭证有效性。

    wechat_mp 走回调三态 + 接口通道三步实测；其他渠道走 ChannelFactory.create_adapter 路径。
    """
    admin = require_admin(request)

    # wechat_mp：回调三态（config_verified_at / last_event_at / last_error）+（WP9）
    # config 含 appid+secret 时的接口通道三步实测（stable_token → batchget → getarticle）
    cfg_masked = ChannelConfigDB.get_by_id(config_id)
    if cfg_masked and cfg_masked.get("channel_type") == _WECHAT_MP_CHANNEL_TYPE:
        if cfg_masked["tenant_id"] != admin["tenant_id"]:
            raise HTTPException(status_code=403, detail="无权操作此配置")
        cfg_data = cfg_masked.get("config") or {}
        config_verified_at = cfg_data.get("config_verified_at")
        result = {
            "success": True,
            "verified": bool(config_verified_at),
            "config_verified_at": config_verified_at,
            "last_event_at": cfg_data.get("last_event_at"),
            "last_error": cfg_data.get("last_error"),
            "callback_url": _build_wechat_mp_callback_url(request, config_id),
            "message": (
                "回调 URL 已验证通过"
                if config_verified_at
                else "尚未完成回调 URL 验证：请在公众平台后台「设置与开发→服务器配置」填入回调地址与 Token 并保存"
            ),
        }

        # WP9 接口通道实测：AppID+AppSecret 均已配置才发起（留空=仅回调+手动粘贴通道）
        decrypted = ChannelConfigDB.get_by_id_decrypted(config_id)
        mp_cfg = (decrypted or {}).get("config") or {}
        appid = str(mp_cfg.get("appid") or "").strip()
        secret = str(mp_cfg.get("secret") or "").strip()
        if not (appid and secret):
            return result

        api_check = await asyncio.to_thread(
            _run_wechat_mp_api_check, admin["tenant_id"], config_id, appid, secret
        )
        result["api_check"] = api_check
        if api_check.get("ok"):
            # verified 列语义=凭据实测可用（与其他渠道一致）；回调三态 config_verified_at 不动
            ChannelConfigDB.set_verified(config_id, True)
            result["verified"] = True
            result["message"] = f"回调三态如上；接口通道：{api_check.get('message')}"
            return result

        # 实测失败：仅当回调验证态不存在时才回落 verified=0（与其他渠道失败语义一致）。
        # config_verified_at 已存在（回调曾实测通过）时不撤销 verified——一次 IP 白名单
        # 未加/接口权限等接口通道问题，不应覆盖回调链路的实测结论（误报会让前端把可用
        # 配置当无效配置）。两通道结论在 message/api_check 节点分别呈现。
        if not config_verified_at:
            ChannelConfigDB.set_verified(config_id, False)
            result["verified"] = False
        result["message"] = f"回调三态如上；接口通道验证失败：{api_check.get('message')}"
        return result

    # 其他渠道走原有 ChannelFactory 路径
    config = cfg_masked
    if not config:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if config["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")

    try:
        adapter = ChannelFactory.create_adapter(config["channel_type"], config["config"])
        ChannelConfigDB.set_verified(config_id, True)
        return {"success": True, "message": "渠道凭证验证通过", "verified": True}
    except Exception as e:
        ChannelConfigDB.set_verified(config_id, False)
        return {"success": False, "message": f"验证失败: {str(e)}", "verified": False}


@router.post("/{config_id}/rotate-wechat-mp-token")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG, id_arg="config_id")
async def rotate_wechat_mp_token(config_id: str, request: Request):
    """重置 wechat_mp 回调 Token（密钥轮换，设计 §4）。

    生成新 token 加密入库、递增凭据版本并撤销回调验证态（旧 token 事件随之拒收）。
    新 token 明文仅此一次返回，供用户更新公众平台后台配置。
    """
    admin = require_admin(request)
    existing = ChannelConfigDB.get_by_id(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if existing["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")
    if existing["channel_type"] != _WECHAT_MP_CHANNEL_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"仅 wechat_mp 渠道支持轮换回调 Token，当前渠道类型: {existing['channel_type']}",
        )

    new_token = await asyncio.to_thread(ChannelConfigDB.rotate_wechat_mp_token, config_id)
    if not new_token:
        raise HTTPException(status_code=500, detail="Token 轮换失败")

    logger.info(f"wechat_mp 回调 Token 轮换成功: config_id={config_id} tenant={admin['tenant_id']}")
    return {
        "success": True,
        "callback_token_plaintext": new_token,
        "callback_url": _build_wechat_mp_callback_url(request, config_id),
        "message": "回调 Token 已重置，请同步更新公众平台后台服务器配置中的 Token 并重新保存",
    }


@router.post("/{config_id}/rotate-web-c-secret")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG, id_arg="config_id")
async def rotate_web_c_secret(config_id: str, request: Request):
    """重置 web_c 签名密钥（渠道密钥轮换，设计 §3.2）。

    旧链接全部失效，前端需提示租户更新官网签名服务。
    """
    admin = require_admin(request)
    existing = ChannelConfigDB.get_by_id(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if existing["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")
    if existing["channel_type"] != _WEB_C_CHANNEL_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"仅 web_c 渠道支持轮换签名密钥，当前渠道类型: {existing['channel_type']}",
        )

    new_secret = _generate_web_c_secret()
    cfg = dict(existing.get("config") or {})
    cfg["channel_secret"] = new_secret
    success = ChannelConfigDB.update(config_id, cfg, subagent_type=existing.get("subagent_type"))
    if not success:
        raise HTTPException(status_code=500, detail="签名密钥轮换失败")
    try:
        await ChannelFactory.invalidate_adapter(
            existing["tenant_id"], existing["channel_type"], config_id=config_id, close=True,
        )
    except Exception as e:
        logger.warning(f"失效 adapter 缓存失败: {e}")
    logger.info(f"web_c 签名密钥轮换成功: config_id={config_id} tenant={admin['tenant_id']}")
    return {
        "success": True,
        "channel_secret": new_secret,
        "message": "签名密钥已重置，旧签名链接全部失效，请更新官网的签名服务配置",
    }


def _validate_web_c_lead_assignee(tenant_id: str, config: dict) -> None:
    """校验 web_c 留资归属员工：本租户 + active（防跨租户/已删除用户落库）。

    空值 = 未配置，直接放行（清空归属是合法操作）。
    """
    user_id = str(config.get("lead_assignee_user_id") or "").strip()
    if not user_id:
        return
    from src.db.models import UserDB

    user = UserDB.get_by_id(user_id) or {}
    if user.get("tenant_id") != tenant_id or user.get("status") != "active":
        raise HTTPException(status_code=400, detail="留资归属员工不存在或非本租户在职用户，请重新选择")


def _validate_channel_subagent(tenant_id: str, subagent_type: Optional[str]) -> None:
    """校验渠道绑定的 subagent_type：本租户实例（active）或已订阅基础员工。

    防跨租户绑定：实例须 tenant_id 匹配；基础 id 须在租户有效订阅内。
    """
    if not subagent_type:
        return  # 不绑定数字员工
    from src.db.tenant_agent_instance_db import TenantAgentInstanceDB

    row = TenantAgentInstanceDB.get(tenant_id, subagent_type)
    if row and row.get("status") == "active":
        return
    if row:
        raise HTTPException(status_code=400, detail="该数字员工副本已删除，请重新选择")
    with get_db_connection() as conn:
        allowed = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)
    if subagent_type not in allowed:
        raise HTTPException(status_code=400, detail="未订阅该数字员工，无法绑定")


@router.get("/available-subagents")
async def get_available_subagents(request: Request):
    """获取当前租户可用的数字员工列表（用于渠道配置关联，含本租户副本实例）"""
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    try:
        with get_db_connection() as conn:
            subagent_types = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)

        # 附加本租户数字员工副本实例（base 在订阅内才可见，订阅继承）
        copy_rows = []
        try:
            from src.db.tenant_agent_instance_db import TenantAgentInstanceDB
            allowed_set = set(subagent_types)
            for row in TenantAgentInstanceDB.list_by_tenant(tenant_id):
                if row.get("agent_id") == row.get("base_agent_id"):
                    continue
                if row.get("base_agent_id") not in allowed_set:
                    continue
                copy_rows.append(row)
        except Exception as e:
            logger.warning(f"附加租户数字员工副本失败: {e}")

        # 名称解析：租户实例 display_name（含基础员工定制改名）优先，其次注册表/DB 定义
        registry = master_agent.subagent_registry
        if registry:
            registry.load_from_db()
        from src.saas.services.agent_name_resolver import resolve_agent_names
        name_map = resolve_agent_names(
            tenant_id,
            [*subagent_types, *[row["agent_id"] for row in copy_rows]],
        )
        if name_map.get("main", "main") == "main":
            name_map["main"] = "CEO智能体"

        result = [
            {"agent_id": t, "name": name_map.get(t, t)}
            for t in subagent_types
        ]
        for row in copy_rows:
            name = (
                name_map.get(row["agent_id"])
                or row.get("display_name")
                or name_map.get(row["base_agent_id"], row["base_agent_id"])
            )
            result.append({"agent_id": row["agent_id"], "name": f"{name}（副本）"})

        return {"success": True, "subagents": result}
    except Exception as e:
        logger.error(f"获取可用数字员工列表失败: {e}")
        return {"success": False, "subagents": [], "message": str(e)}
