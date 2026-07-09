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
from src.saas.api.tenant_auth import require_admin
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.services.channel_factory import ChannelFactory
from src.config.settings import settings
from src.db.database import get_db_connection

router = APIRouter(prefix="/api/saas/channels", tags=["SaaS 渠道配置"])


class ChannelConfigCreateRequest(BaseModel):
    channel_type: str = Field(..., description="渠道类型：wecom/wecom_kf/dingtalk/feishu")
    name: Optional[str] = Field(None, description="渠道名称（用户自定义，用于区分同一租户的多个同类渠道）")
    config: dict = Field(..., description="渠道凭证配置")
    subagent_type: Optional[str] = Field(None, description="关联的数字员工类型（如 travel-consultant），不填则不绑定")


class ChannelConfigUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="渠道名称（用户自定义，用于区分同一租户的多个同类渠道）")
    config: dict = Field(..., description="渠道凭证配置")
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
}


def _validate_rpa_required_fields(config: dict) -> None:
    """校验 wecom_personal_rpa 渠道的必填字段。

    根据 listen_mode 动态判断（第一期 MVP 强制 server，client 分支保留为未来开放做准备）。
    校验失败抛 HTTPException(400)。
    """
    missing = rpa_credential_codec.validate_required_fields(config)
    if missing:
        detail = "缺少必填字段: " + ", ".join(f"{k}（{v}）" for k, v in missing.items())
        raise HTTPException(status_code=400, detail=detail)


@router.get("")
async def list_channels(request: Request):
    """列出当前租户的渠道配置"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    configs = ChannelConfigDB.list_by_tenant(admin["tenant_id"])
    return {"success": True, "channels": configs}


@router.post("")
async def create_channel(request: Request, body: ChannelConfigCreateRequest):
    """新增渠道配置"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    if body.channel_type not in ("wecom", "wecom_kf", "wecom_personal_rpa", "dingtalk", "feishu"):
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
            raise HTTPException(
                status_code=400 if body.channel_type == "wecom_personal_rpa" else 409,
                detail=(
                    "该租户已有 wecom_personal_rpa 渠道配置，请编辑现有配置切换模式（同租户仅允许一份该类型配置）"
                    if body.channel_type == "wecom_personal_rpa"
                    else f"渠道配置唯一约束冲突（{body.channel_type}）"
                ),
            )
        logger.error(f"create channel exception: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="创建渠道配置失败")

    if not config:
        raise HTTPException(status_code=500, detail="创建渠道配置失败")

    logger.info(f"Channel config created: {config['config_id']} ({body.channel_type}, subagent={body.subagent_type})")
    return {"success": True, "channel": config}


@router.put("/{config_id}")
async def update_channel(config_id: str, request: Request, body: ChannelConfigUpdateRequest):
    """更新渠道配置"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    existing = ChannelConfigDB.get_by_id(config_id)

    if not existing:
        raise HTTPException(status_code=404, detail="渠道配置不存在")
    if existing["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此配置")

    # wecom_personal_rpa 走动态校验（按 listen_mode 判断必填字段）
    if existing["channel_type"] == "wecom_personal_rpa":
        _validate_rpa_required_fields(body.config)

    success = ChannelConfigDB.update(config_id, body.config, subagent_type=body.subagent_type, name=body.name)
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
        return {"success": True, "channel": updated}
    return {"success": False, "message": "更新失败"}


@router.delete("/{config_id}")
async def delete_channel(config_id: str, request: Request):
    """删除渠道配置"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

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
async def generate_keypair(config_id: str, request: Request):
    """为 wecom_personal_rpa 渠道生成 RSA 2048bit 密钥对。

    业务场景：企微会话存档要求企业自己生成 RSA 密钥对，私钥自留解密消息，公钥上传到企微后台。
    本接口替用户在服务端生成密钥对，私钥 Fernet 加密后入库（不出 API 响应），公钥返回前端展示供用户复制上传企微。

    鉴权：require_admin + 校验租户归属（参照 update_channel 模式）。
    校验：config 必须存在 + 必须是 wecom_personal_rpa 渠道类型。
    覆盖语义：若 config.private_key 已有值，覆盖（用户主动点生成就是想换）。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

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
async def verify_channel(config_id: str, request: Request):
    """验证渠道凭证有效性

    对 wecom_personal_rpa 类型走专属验证（按 listen_mode 分支）：
    - server 模式：拉一批密文 + RSA 解密一条 + 自测验签 AES 解密，5 步链路自测
    - client 模式：函数代码保留，路由层注释（第一期不开放）

    其他渠道走原有 ChannelFactory.create_adapter 路径。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

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

    # 其他渠道走原有 ChannelFactory 路径
    config = ChannelConfigDB.get_by_id(config_id)
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


@router.get("/available-subagents")
async def get_available_subagents(request: Request):
    """获取当前租户可用的数字员工列表（用于渠道配置关联）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    try:
        with get_db_connection() as conn:
            subagent_types = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)
        return {"success": True, "subagents": subagent_types}
    except Exception as e:
        logger.error(f"获取可用数字员工列表失败: {e}")
        return {"success": False, "subagents": [], "message": str(e)}
