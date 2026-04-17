"""
SaaS 渠道配置管理 API

路由：/api/saas/channels/*
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.services.channel_factory import ChannelFactory
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/channels", tags=["SaaS 渠道配置"])


class ChannelConfigCreateRequest(BaseModel):
    channel_type: str = Field(..., description="渠道类型：wecom/dingtalk/feishu")
    config: dict = Field(..., description="渠道凭证配置")


class ChannelConfigUpdateRequest(BaseModel):
    config: dict = Field(..., description="渠道凭证配置")


# 各渠道类型必填字段
_REQUIRED_FIELDS = {
    "wecom": {
        "corp_id": "企业ID（在「我的企业」页面获取，格式 ww 开头）",
        "agent_id": "应用AgentId（在应用详情页获取）",
        "secret": "应用Secret（在应用详情页获取）",
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
        "encrypt_key": "加密密钥",
    },
}


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

    if body.channel_type not in ("wecom", "dingtalk", "feishu"):
        raise HTTPException(status_code=400, detail=f"不支持的渠道类型: {body.channel_type}")

    # 验证必填字段
    required = _REQUIRED_FIELDS.get(body.channel_type, {})
    missing = [f"{k}（{v}）" for k, v in required.items() if not body.config.get(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"缺少必填字段: {', '.join(missing)}",
        )

    config = ChannelConfigDB.create(
        tenant_id=admin["tenant_id"],
        channel_type=body.channel_type,
        config=body.config,
    )

    if not config:
        raise HTTPException(status_code=500, detail="创建渠道配置失败")

    logger.info(f"Channel config created: {config['config_id']} ({body.channel_type})")
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

    success = ChannelConfigDB.update(config_id, body.config)
    if success:
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
    return {"success": success}


@router.post("/{config_id}/verify")
async def verify_channel(config_id: str, request: Request):
    """验证渠道凭证有效性"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
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
