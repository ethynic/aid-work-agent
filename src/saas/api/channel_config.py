"""
SaaS 渠道配置管理 API

路由：/api/saas/channels/*
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

try:
    from psycopg2 import IntegrityError
except ImportError:  # psycopg2 未安装（开发/测试场景）
    IntegrityError = None  # type: ignore[assignment,misc]

from src.channels.wecom_personal_rpa.archive import credential_codec as rpa_credential_codec
from src.core import master_agent
from src.saas.api.tenant_auth import require_admin
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.enums import BehaviorAction, BehaviorResourceType
from src.saas.services.channel_factory import ChannelFactory
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
# wecom_personal_rpa 走动态校验（见 _validate_rpa_required_fields），不在此静态映射
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
}

_WECHAT_MP_CHANNEL_TYPE = "wechat_mp"


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


def _validate_rpa_required_fields(config: dict) -> None:
    """校验 wecom_personal_rpa 渠道的必填字段。

    根据 listen_mode 动态判断（第一期 MVP 强制 server，client 分支保留为未来开放做准备）。
    校验失败抛 HTTPException(400)。
    """
    missing = rpa_credential_codec.validate_required_fields(config)
    if missing:
        detail = "缺少必填字段: " + ", ".join(f"{k}（{v}）" for k, v in missing.items())
        raise HTTPException(status_code=400, detail=detail)


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

    if body.channel_type not in ("wecom", "wecom_kf", "wecom_personal_rpa", "dingtalk", "feishu", _WECHAT_MP_CHANNEL_TYPE):
        raise HTTPException(status_code=400, detail=f"不支持的渠道类型: {body.channel_type}")

    # wecom_personal_rpa 走动态校验（按 listen_mode），其他渠道走静态必填字段表
    if body.channel_type == "wecom_personal_rpa":
        _validate_rpa_required_fields(body.config)
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

    try:
        config = ChannelConfigDB.create(
            tenant_id=admin["tenant_id"],
            channel_type=body.channel_type,
            name=body.name,
            config=body.config,
            subagent_type=body.subagent_type,
        )
    except Exception as e:
        # IntegrityError 为 DB 部分唯一索引拦截（如 wecom_personal_rpa 并发创建同租户第二条）
        if IntegrityError is not None and isinstance(e, IntegrityError):
            if body.channel_type == "wecom_personal_rpa":
                raise HTTPException(
                    status_code=400,
                    detail="该租户已有 wecom_personal_rpa 渠道配置，请编辑现有配置切换模式（同租户仅允许一份该类型配置）",
                )
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

    # wecom_personal_rpa 走动态校验（按 listen_mode 判断必填字段）
    if existing["channel_type"] == "wecom_personal_rpa":
        _validate_rpa_required_fields(body.config)

    # wecom_kf：客服账号（kf_account）由 /wecom-kf/accounts 独立接口管理创建/编辑/删除，
    # 渠道保存只更新凭证、等待提示等渠道级配置。保存时以数据库现有 kf_account 为准，
    # 防止前端编辑页打开时的旧快照整体覆盖 config 导致新建账号被丢弃（客户扫码链接失效）。
    if existing["channel_type"] == "wecom_kf":
        body.config = dict(body.config)
        body.config["kf_account"] = (existing.get("config") or {}).get("kf_account") or []

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


@router.post("/{config_id}/generate-keypair")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG, id_arg="config_id")
async def generate_keypair(config_id: str, request: Request):
    """为 wecom_personal_rpa 渠道生成 RSA 2048bit 密钥对。

    业务场景：企微会话存档要求企业自己生成 RSA 密钥对，私钥自留解密消息，公钥上传到企微后台。
    本接口替用户在服务端生成密钥对，私钥 Fernet 加密后入库（不出 API 响应），公钥返回前端展示供用户复制上传企微。

    鉴权：require_admin + 校验租户归属（参照 update_channel 模式）。
    校验：config 必须存在 + 必须是 wecom_personal_rpa 渠道类型。
    覆盖语义：若 config.private_key 已有值，覆盖（用户主动点生成就是想换）。
    """
    admin = require_admin(request)

    existing = ChannelConfigDB.get_by_id(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if existing["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")
    if existing["channel_type"] != "wecom_personal_rpa":
        raise HTTPException(
            status_code=400,
            detail=f"仅 wecom_personal_rpa 渠道支持生成密钥对，当前渠道类型: {existing['channel_type']}",
        )

    try:
        # RSA 2048bit 生成约 50-200ms 阻塞，丢线程池避免阻塞事件循环（按 backend_dev.md 规范）
        private_pem, public_pem = await asyncio.to_thread(rpa_credential_codec.generate_rsa_keypair)
    except Exception as e:
        logger.error(f"generate_keypair 生成密钥失败 config_id={config_id}: {type(e).__name__}: {e}")
        return {
            "success": False,
            "message": "生成密钥对失败，请稍后重试",
            "debug": str(type(e).__name__),
        }

    # 私钥加密入库（覆盖旧值，用户主动点生成就是想换）
    try:
        # store_private_key 内部走同步 DB 读写（psycopg2），包 to_thread 避免阻塞事件循环
        ok = await asyncio.to_thread(rpa_credential_codec.store_private_key, config_id, private_pem)
    except Exception as e:
        logger.error(
            f"generate_keypair 私钥入库失败 config_id={config_id}: {type(e).__name__}: {e}"
        )
        return {
            "success": False,
            "message": "私钥保存失败，请稍后重试",
            "debug": str(type(e).__name__),
        }

    if not ok:
        # store_private_key 返回 False 通常意味着配置已被删（前面 get_by_id 已校验存在，理论不会发生）
        raise HTTPException(status_code=500, detail="私钥保存失败：配置可能已被删除")

    # 失效缓存的 adapter（私钥变了，下次回调/拉取需重建）
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

    logger.info(
        f"Keypair generated for config_id={config_id} tenant={admin['tenant_id']} "
        f"(private_key 已加密入库，公钥返回前端)"
    )

    # public_key_raw：原始 PEM 文本（含真实换行）
    # public_key：JSON 安全的转义版本（\n 替换真实换行，方便前端直接展示在 textarea 内 value 属性）
    # 两者内容一致，只是换行表示方式不同；前端按需取用
    return {
        "success": True,
        "public_key": public_pem.replace("\n", "\\n"),
        "public_key_raw": public_pem,
    }


@router.post("/{config_id}/verify")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG, id_arg="config_id")
async def verify_channel(config_id: str, request: Request):
    """验证渠道凭证有效性

    对 wecom_personal_rpa 类型走专属验证（按 listen_mode 分支）：
    - server 模式：拉一批密文 + RSA 解密一条 + 自测验签 AES 解密，5 步链路自测
    - client 模式：函数代码保留，路由层注释（第一期不开放）

    其他渠道走原有 ChannelFactory.create_adapter 路径。
    """
    admin = require_admin(request)

    # wecom_personal_rpa 需要明文凭证做实测，用 get_by_id_decrypted
    if _is_wecom_personal_rpa(config_id):
        from src.channels.wecom_personal_rpa.archive import verifier as rpa_verifier

        config = ChannelConfigDB.get_by_id_decrypted(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="渠道配置不存在")
        if config["tenant_id"] != admin["tenant_id"]:
            raise HTTPException(status_code=403, detail="无权操作此配置")

        config_data = config.get("config") or {}
        listen_mode = config_data.get("listen_mode", "server")

        if listen_mode == "server":
            result = await rpa_verifier.verify_archive_server_mode(
                config_data, admin["tenant_id"]
            )
        else:
            # 第一期永远不会进入此分支（codec 强制 server）
            # 代码保留为未来开放 client 模式做准备
            result = await rpa_verifier.verify_archive_client_mode(
                config_data, admin["tenant_id"]
            )

        # 根据验证结果更新 verified 字段
        ChannelConfigDB.set_verified(config_id, bool(result.get("verified")))
        return result

    # wechat_mp：回调通道无服务端可发起的凭据实测（接口通道验证属 P3），
    # 「验证连接」返回回调三态（config_verified_at / last_event_at / last_error）供前端展示
    cfg_masked = ChannelConfigDB.get_by_id(config_id)
    if cfg_masked and cfg_masked.get("channel_type") == _WECHAT_MP_CHANNEL_TYPE:
        if cfg_masked["tenant_id"] != admin["tenant_id"]:
            raise HTTPException(status_code=403, detail="无权操作此配置")
        cfg_data = cfg_masked.get("config") or {}
        config_verified_at = cfg_data.get("config_verified_at")
        return {
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


def _is_wecom_personal_rpa(config_id: str) -> bool:
    """判断 config_id 对应的配置是否是 wecom_personal_rpa 类型。

    用于 verify 路由分流。查不到配置返回 False（让后续 404 检查处理）。
    """
    cfg = ChannelConfigDB.get_by_id(config_id)
    return bool(cfg and cfg.get("channel_type") == "wecom_personal_rpa")


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


@router.get("/available-subagents")
async def get_available_subagents(request: Request):
    """获取当前租户可用的数字员工列表（用于渠道配置关联）"""
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    try:
        with get_db_connection() as conn:
            subagent_types = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)

        # 构建 agent_id -> 中文名 映射（主智能体 main 固定为 CEO智能体）
        name_map = {"main": "CEO智能体"}
        registry = master_agent.subagent_registry
        if registry:
            registry.load_from_db()
            for item in registry.get_all_subagents_with_type():
                name_map[item["agent_id"]] = item.get("name") or item["agent_id"]

        result = [
            {"agent_id": t, "name": name_map.get(t, t)}
            for t in subagent_types
        ]
        return {"success": True, "subagents": result}
    except Exception as e:
        logger.error(f"获取可用数字员工列表失败: {e}")
        return {"success": False, "subagents": [], "message": str(e)}
