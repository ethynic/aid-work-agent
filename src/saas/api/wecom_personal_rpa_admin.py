"""企业微信个人账号 RPA 管理 / 绑定 / 审计 API

路由：/api/saas/wecom-personal-rpa/*

职责：
- 客户端注册 / 列表 / 密钥轮换
- 账号、会话、租户三层 pause / resume（幂等）
- 会话绑定复核（needs_review → active）
- 审计日志查询

安全约束（对齐 docs/system/wecom-personal-rpa-protocol.md §A.1 与 backend_dev.md）：
- 明文 client_secret 仅在「注册」「轮换」响应中出现一次，之后不可取回。
- 服务端只存储 encrypted_secret（secret_crypto.encrypt_secret 加密）。
- 错误响应的 debug 字段必须脱敏（剔除 secret/token/signature）。
- 严格遵守 SaaS 租户隔离：复用 require_admin + X-Tenant-Id 解析（参照 channel_config.py）。
"""

import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.channels.wecom_personal_rpa import db as rpa_db
from src.channels.wecom_personal_rpa import observability
from src.channels.wecom_personal_rpa.secret_crypto import encrypt_secret
from src.config.settings import settings
from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.saas.api.tenant_auth import require_admin, sanitize_error_info
from src.saas.db.channel_config_db import ChannelConfigDB

router = APIRouter(prefix="/api/saas/wecom-personal-rpa", tags=["企业微信个人账号RPA管理"])

# 客户端 ID 前缀（注册时生成，全局唯一）
_CLIENT_ID_PREFIX = "rpa_client_"


# ===========================================================================
# 请求模型
# ===========================================================================


class ClientRegisterRequest(BaseModel):
    name: str = Field(..., description="客户端显示名称，如「销售一组-RPA 客户端」")
    min_version: str = Field("1.0.0", description="允许继续托管的最小客户端版本")
    subagent_type: Optional[str] = Field(
        None, description="关联的数字员工类型（写入 tenant_channel_configs.subagent_type）"
    )


class PauseResumeRequest(BaseModel):
    scope: str = Field(..., description="暂停/恢复范围：tenant | account | conversation")
    account_id: Optional[str] = Field(None, description="scope=account 时必填")
    conversation_id: Optional[str] = Field(
        None, description="scope=conversation 时必填（对应 binding.id）"
    )
    reason: Optional[str] = Field(None, description="暂停原因（仅 pause 生效，脱敏后入审计）")


class UpdateBindingRequest(BaseModel):
    """更新绑定可编辑字段（首版仅监控白名单）。

    None 表示"不修改"；显式传空 list 表示"清除该字段"。
    """

    display_name: Optional[str] = Field(
        None, description="企微会话原始显示名，例如「陆伟@微信」；用于生成客户端搜索名"
    )
    monitor_user_names: Optional[List[str]] = Field(
        None, description="监控的发送人显示名数组（任一匹配即上报）；空数组 = 不按名字过滤"
    )
    monitor_user_ids: Optional[List[str]] = Field(
        None,
        description=(
            "监控的发送人稳定 ID 数组（external_userid/userid/room_id）；"
            "空数组 = 不按 ID 过滤。"
            "两个字段任一非空即按白名单过滤；都为空 = 监控所有（首版默认）。"
        ),
    )


class UpdateAccountIdentityRequest(BaseModel):
    wecom_user_id: str = Field(..., min_length=1, max_length=128)
    wecom_user_aliases: List[str] = Field(default_factory=list, max_length=20)


# ===========================================================================
# 工具函数
# ===========================================================================


def _ensure_saas_enabled() -> Optional[Dict[str, Any]]:
    """SaaS 未启用时返回错误响应 dict，启用时返回 None。"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式，无法访问"}
    return None


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """成功响应统一封装。"""
    resp: Dict[str, Any] = {"success": True}
    if data is not None:
        resp["data"] = data
    resp.update(extra)
    return resp


def _fail(message: str, debug: Optional[str] = None, status_code: int = 400) -> HTTPException:
    """构造失败响应（debug 脱敏后塞入 HTTPException detail）。"""
    safe_debug = sanitize_error_info(debug) if debug else None
    detail: Dict[str, Any] = {"success": False, "message": message}
    if safe_debug:
        detail["debug"] = safe_debug
    return HTTPException(status_code=status_code, detail=detail)


def _client_summary(client: Dict[str, Any]) -> Dict[str, Any]:
    """客户端对外摘要：剔除 encrypted_secret、user_id 等内部字段，附 accounts 概要与 last_seen。"""
    tenant_id = client.get("tenant_id")
    client_id = client.get("id")
    accounts: List[Dict[str, Any]] = []
    if tenant_id and client_id:
        try:
            accounts = rpa_db.list_accounts(tenant_id, client_id=client_id)
        except Exception as e:
            logger.warning(f"list accounts for client {client_id} failed: {type(e).__name__}")

    return {
        "client_id": client_id,
        "name": client.get("name"),
        "status": client.get("status"),
        "min_version": client.get("min_version"),
        "last_seen_at": client.get("last_seen_at"),
        "created_at": client.get("created_at"),
        "updated_at": client.get("updated_at"),
        "accounts_count": len(accounts),
        "accounts": [
            {
                "account_id": a.get("id"),
                "display_name": a.get("display_name"),
                "status": a.get("status"),
                "last_login_at": a.get("last_login_at"),
            }
            for a in accounts
        ],
    }


def _account_public(acc: Dict[str, Any]) -> Dict[str, Any]:
    """账号对外字段。"""
    wecom_user_id = (acc.get("wecom_user_id") or "").strip()
    masked_user_id = (
        f"***{wecom_user_id[-3:]}" if len(wecom_user_id) > 3
        else "***" if wecom_user_id else None
    )
    return {
        "account_id": acc.get("id"),
        "client_id": acc.get("client_id"),
        "display_name": acc.get("display_name"),
        "status": acc.get("status"),
        "paused_reason": acc.get("paused_reason"),
        "last_login_at": acc.get("last_login_at"),
        "created_at": acc.get("created_at"),
        "updated_at": acc.get("updated_at"),
        "wecom_user_id_configured": bool(wecom_user_id),
        "wecom_user_id_masked": masked_user_id,
        "identity_verified_at": acc.get("identity_verified_at"),
        "auto_reply_ready": bool(wecom_user_id) and acc.get("status") == "online",
    }


def _binding_public(b: Dict[str, Any]) -> Dict[str, Any]:
    """绑定对外字段（含客户端心跳与 agent_base_url，便于排查客户端连不上服务端类问题）。

    字段来源：
    - agent_base_url / last_heartbeat_at / client_id / client_name：来自关联的 wecom_rpa_clients 表。
      GET /all_bindings 经 LEFT JOIN 携带；list_bindings / accounts/{id}/bindings / confirm 等单 binding
      直查场景未 join clients 表，这几个字段为 None。

    注意：``GET /all_bindings`` 自 2026-06 改造后以 client 为基准返回，
    字段集与下方 ``_client_public`` 一致；此函数仅用于 ``/bindings`` 单 binding 视图。
    """
    return {
        "binding_id": b.get("id") or b.get("binding_id"),
        "tenant_id": b.get("tenant_id"),
        "account_id": b.get("account_id"),
        "conversation_type": b.get("conversation_type"),
        "display_name": b.get("display_name"),
        "search_key": b.get("search_key"),
        "stable_id": b.get("stable_id"),
        "status": b.get("status"),
        "last_verified_at": b.get("last_verified_at"),
        "created_at": b.get("created_at"),
        "updated_at": b.get("updated_at"),
        "client_id": b.get("client_id"),
        "client_name": b.get("client_name"),
        "agent_base_url": b.get("agent_base_url"),
        "last_heartbeat_at": b.get("last_heartbeat_at"),
        "monitor_user_names": list(b.get("monitor_user_names") or []),
        "monitor_user_ids": list(b.get("monitor_user_ids") or []),
    }


def _client_public(c: Dict[str, Any]) -> Dict[str, Any]:
    """客户端对外字段（``GET /all_bindings`` 列表专用，一行一 client 聚合视图）。

    字段含义：
    - client_id / tenant_id / client_name / client_status：来自 wecom_rpa_clients 主表。
    - agent_base_url：客户端回填的生产服务端 URL（运维排查用）。
    - last_heartbeat_at：来自 clients.last_seen_at；NULL 表示从未连上来过。
    - account_count / binding_count / last_account_name：聚合自 accounts / bindings 子表。
    """
    return {
        "client_id": c.get("client_id"),
        "tenant_id": c.get("tenant_id"),
        "client_name": c.get("client_name"),
        "client_status": c.get("client_status"),
        "agent_base_url": c.get("agent_base_url"),
        "last_heartbeat_at": c.get("last_heartbeat_at"),
        "min_version": c.get("min_version"),
        "created_at": c.get("created_at"),
        "updated_at": c.get("updated_at"),
        "account_count": c.get("account_count", 0),
        "binding_count": c.get("binding_count", 0),
        "last_account_name": c.get("last_account_name"),
    }


def _require_platform_admin(admin: Dict[str, Any]) -> None:
    """校验当前 admin 必须是 platform_admin，否则抛 403。"""
    if admin.get("role") != "platform_admin":
        raise _fail("仅平台管理员可访问", status_code=403)


def _audit_public(row: Dict[str, Any]) -> Dict[str, Any]:
    """审计日志对外字段（payload 已由 db 层解析为 dict）。"""
    return {
        "audit_id": row.get("id"),
        "client_id": row.get("client_id"),
        "account_id": row.get("account_id"),
        "action_id": row.get("action_id"),
        "category": row.get("category"),
        "payload": row.get("payload"),
        "created_at": row.get("created_at"),
    }


# ===========================================================================
# 1. 客户端注册 / 列表 / 密钥轮换
# ===========================================================================


@router.post("/clients")
async def register_client(request: Request, body: ClientRegisterRequest):
    """注册新 RPA 客户端。

    明文 client_secret 仅在此响应中出现一次，之后不可取回。
    同时在 tenant_channel_configs 写入一条 channel_type=wecom_personal_rpa 的配置，
    config 中保存 client_id 供渠道回调路由使用。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    client_id = f"{_CLIENT_ID_PREFIX}{secrets.token_hex(12)}"
    plaintext_secret = secrets.token_urlsafe(32)

    try:
        encrypted = encrypt_secret(plaintext_secret)
    except Exception as e:
        logger.error(f"register_client encrypt failed: {type(e).__name__}")
        raise _fail("客户端密钥加密失败", status_code=500)

    client = rpa_db.create_client(
        tenant_id=tenant_id,
        client_id=client_id,
        name=body.name,
        encrypted_secret=encrypted,
        min_version=body.min_version,
        user_id=user_id,
    )
    if not client:
        raise _fail("客户端注册失败", status_code=500)

    # 同步写入 tenant_channel_configs，供渠道回调路由定位 client_id
    try:
        ChannelConfigDB.create(
            tenant_id=tenant_id,
            channel_type="wecom_personal_rpa",
            config={"client_id": client_id, "name": body.name},
            subagent_type=body.subagent_type,
        )
    except Exception as e:
        # 渠道配置写入失败不回滚 client 注册（client 可单独使用），
        # 仅记录警告，由管理员后续补建。
        logger.warning(
            f"register_client: tenant_channel_configs 写入失败 "
            f"(client={client_id}, type={type(e).__name__})"
        )

    # 审计：脱敏后入 payload（不含 secret）
    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=client_id,
        account_id=None,
        category="client_register",
        payload_json=json.dumps(
            {"name": body.name, "min_version": body.min_version},
            ensure_ascii=False,
        ),
        user_id=user_id,
    )

    logger.info(f"RPA client registered: {client_id} (tenant={tenant_id})")

    return _ok(
        {
            "client_id": client_id,
            "client_secret": plaintext_secret,
            "min_version": body.min_version,
            "name": body.name,
            # 提醒调用方：明文 secret 仅此一次
            "secret_warning": "client_secret 仅本次响应返回一次，请立即妥善保存，之后无法再次获取。",
        }
    )


@router.get("/clients")
async def list_clients(request: Request):
    """列出租户下所有客户端，附 accounts 概要与 last_seen，不含 encrypted_secret。"""
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    clients = rpa_db.list_clients(admin["tenant_id"])
    return _ok([_client_summary(c) for c in clients])


@router.get("/clients/{client_id}/accounts")
async def list_client_accounts(client_id: str, request: Request):
    """列出某客户端下的所有账号。"""
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    client = rpa_db.get_client(client_id)
    if not client:
        raise _fail("客户端不存在", status_code=404)
    if client.get("tenant_id") != tenant_id:
        raise _fail("无权操作此客户端", status_code=403)

    accounts = rpa_db.list_accounts(tenant_id, client_id=client_id)
    return _ok([_account_public(a) for a in accounts])


@router.patch("/accounts/{account_id}/identity")
async def update_account_identity(
    account_id: str, body: UpdateAccountIdentityRequest, request: Request
):
    """配置账号权威企微成员身份；响应不返回 userid 明文。"""
    if err := _ensure_saas_enabled():
        return err
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = body.wecom_user_id.strip()
    aliases = sorted({value.strip() for value in body.wecom_user_aliases if value.strip()})
    if not user_id:
        raise _fail("企微成员身份不能为空", status_code=422)
    if any(len(value) > 128 for value in aliases):
        raise _fail("企微成员历史身份长度不能超过 128", status_code=422)
    if user_id in aliases:
        aliases.remove(user_id)
    account = rpa_db.get_account_for_tenant(tenant_id, account_id)
    if not account:
        raise _fail("账号不存在", status_code=404)
    try:
        updated = rpa_db.update_account_identity(
            tenant_id, account_id, user_id, aliases, admin.get("user_id")
        )
    except Exception as exc:
        if (
            isinstance(exc, ValueError) and str(exc) == "account_identity_conflict"
        ) or getattr(exc, "sqlstate", None) == "23505" or getattr(exc, "pgcode", None) == "23505":
            raise _fail("该企微成员身份已绑定其他账号", status_code=409) from exc
        # 数据库异常可能包含唯一键明文值，身份接口禁止把 debug 返回给调用方。
        logger.warning(f"账号身份更新失败 account_id={account_id}: {type(exc).__name__}")
        raise _fail("账号身份更新失败", status_code=500) from exc
    if not updated:
        raise _fail("账号身份更新失败", status_code=500)
    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=account.get("client_id"),
        account_id=account_id,
        category="account_identity_updated",
        payload_json=json.dumps(
            {"alias_count": len(aliases), "operator_id": admin.get("user_id")},
            ensure_ascii=False,
        ),
        user_id=admin.get("user_id"),
    )
    return _ok(_account_public(rpa_db.get_account_for_tenant(tenant_id, account_id) or {}))


@router.post("/clients/{client_id}/rotate-secret")
async def rotate_client_secret(client_id: str, request: Request):
    """轮换客户端密钥。

    生成新 secret，加密入库（db.rotate_secret），返回新明文一次。
    旧密钥立即失效。客户端需用新 secret 重新计算 HMAC 签名。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    client = rpa_db.get_client(client_id)
    if not client:
        raise _fail("客户端不存在", status_code=404)
    if client.get("tenant_id") != tenant_id:
        raise _fail("无权操作此客户端", status_code=403)

    new_plaintext = secrets.token_urlsafe(32)
    try:
        new_encrypted = encrypt_secret(new_plaintext)
    except Exception as e:
        logger.error(f"rotate_secret encrypt failed: client={client_id}, type={type(e).__name__}")
        raise _fail("密钥加密失败", status_code=500)

    ok = rpa_db.rotate_secret(tenant_id, client_id, new_encrypted)
    if not ok:
        raise _fail("密钥轮换失败", status_code=500)

    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=client_id,
        account_id=None,
        category="secret_rotate",
        payload_json=json.dumps({"client_id": client_id}, ensure_ascii=False),
        user_id=user_id,
    )

    logger.info(f"RPA client secret rotated: {client_id}")

    return _ok(
        {
            "client_id": client_id,
            "client_secret": new_plaintext,
            "secret_warning": "新 client_secret 仅本次响应返回一次，请立即妥善保存，之后无法再次获取。",
        }
    )


@router.post("/clients/{client_id}/pause")
async def pause_client(client_id: str, request: Request):
    """暂停客户端（status: active → disabled）。

    暂停后该 client 的所有 account / binding 不再被自动处理（具体由 inbound 路由判定）。
    平台管理员代管理时必须用 X-Tenant-Id 指定目标租户（对齐 rotate_secret 的隔离模型）。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    client = rpa_db.get_client(client_id)
    if not client:
        raise _fail("客户端不存在", status_code=404)
    if client.get("tenant_id") != tenant_id:
        raise _fail("无权操作此客户端", status_code=403)

    if client.get("status") == "disabled":
        # 幂等
        return _ok({"client_id": client_id, "client_status": "disabled"})

    ok = rpa_db.update_client_status(tenant_id, client_id, "disabled")
    if not ok:
        raise _fail("客户端暂停失败", status_code=500)

    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=client_id,
        account_id=None,
        category="client_pause_resume",
        payload_json=json.dumps(
            {"action": "pause", "client_id": client_id}, ensure_ascii=False,
        ),
        user_id=user_id,
    )
    logger.info(f"RPA client paused: {client_id}")
    return _ok({"client_id": client_id, "client_status": "disabled"})


@router.post("/clients/{client_id}/resume")
async def resume_client(client_id: str, request: Request):
    """恢复客户端（status: disabled → active）。

    平台管理员代管理时必须用 X-Tenant-Id 指定目标租户（对齐 rotate_secret 的隔离模型）。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    client = rpa_db.get_client(client_id)
    if not client:
        raise _fail("客户端不存在", status_code=404)
    if client.get("tenant_id") != tenant_id:
        raise _fail("无权操作此客户端", status_code=403)

    if client.get("status") == "active":
        # 幂等
        return _ok({"client_id": client_id, "client_status": "active"})

    ok = rpa_db.update_client_status(tenant_id, client_id, "active")
    if not ok:
        raise _fail("客户端恢复失败", status_code=500)

    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=client_id,
        account_id=None,
        category="client_pause_resume",
        payload_json=json.dumps(
            {"action": "resume", "client_id": client_id}, ensure_ascii=False,
        ),
        user_id=user_id,
    )
    logger.info(f"RPA client resumed: {client_id}")
    return _ok({"client_id": client_id, "client_status": "active"})


# ===========================================================================
# 2. 账号 / 绑定查询
# ===========================================================================


@router.get("/accounts/{account_id}/bindings")
async def list_account_bindings(account_id: str, request: Request):
    """列出某账号下的所有会话绑定。"""
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    acc = rpa_db.get_account(account_id)
    if not acc:
        raise _fail("账号不存在", status_code=404)
    if acc.get("tenant_id") != tenant_id:
        raise _fail("无权操作此账号", status_code=403)

    bindings = rpa_db.list_bindings(tenant_id, account_id=account_id)
    return _ok([_binding_public(b) for b in bindings])


@router.get("/bindings")
async def list_bindings(
    request: Request,
    status: Optional[str] = Query(None, description="按状态过滤：pending/active/paused/invalid/needs_review"),
    account_id: Optional[str] = Query(None, description="按账号过滤"),
):
    """列出绑定（可按 status / account_id 过滤）。

    status=needs_review 用于列待复核绑定。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    bindings = rpa_db.list_bindings(tenant_id, account_id=account_id)
    if status:
        bindings = [b for b in bindings if b.get("status") == status]
    return _ok([_binding_public(b) for b in bindings])


@router.get("/all_bindings")
async def list_all_bindings(
    request: Request,
    status: Optional[str] = Query(
        None,
        description=(
            "状态过滤：active / disabled / needs_review_only（默认全部，显示所有 client）。"
            "注意：自 2026-06 改造后 status 字段语义为 client.status，不再是 binding.status。"
        ),
    ),
    tenant_id: Optional[str] = Query(None, description="按租户过滤（平台管理员专用）"),
):
    """平台管理员视角：跨租户列出所有 RPA 客户端（一行一 client，聚合 account/binding）。

    仅 platform_admin 可访问。数据源以 ``wecom_rpa_clients`` 为基准，确保「创建 client 后即可看到」。

    返回字段（``_client_public``）：client_id / tenant_id / client_name / client_status /
    agent_base_url / last_heartbeat_at / min_version / created_at / updated_at /
    account_count / binding_count / last_account_name。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    _require_platform_admin(admin)

    rows = rpa_db.list_all_bindings_rich(
        tenant_id_filter=tenant_id,
        status_filter=status,
    )
    return _ok([_client_public(c) for c in rows])


@router.patch("/clients/{client_id}/agent_base_url")
async def update_client_agent_base_url(
    client_id: str,
    request: Request,
    body: Dict[str, Any] = None,
):
    """更新客户端回填的 agent_base_url（运维排查用）。

    平台管理员可更新任意租户的客户端；租户管理员仅能更新自己租户的客户端（tenant 隔离由 require_admin 保证）。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    user_id = admin.get("user_id")

    payload = body or {}
    raw_url = payload.get("agent_base_url")
    if raw_url is not None and not isinstance(raw_url, str):
        raise _fail("agent_base_url 必须为字符串")
    agent_base_url = (raw_url or "").strip() or None

    client = rpa_db.get_client(client_id)
    if not client:
        raise _fail("客户端不存在", status_code=404)
    # 租户管理员只能改自己的；平台管理员任意（require_admin 已切换 tenant_id）
    if admin.get("role") != "platform_admin" and client.get("tenant_id") != admin.get("tenant_id"):
        raise _fail("无权操作此客户端", status_code=403)

    ok = rpa_db.update_client_agent_base_url(
        tenant_id=client["tenant_id"],
        client_id=client_id,
        agent_base_url=agent_base_url,
    )
    if not ok:
        raise _fail("更新失败", status_code=500)

    rpa_db.write_audit(
        tenant_id=client["tenant_id"],
        client_id=client_id,
        account_id=None,
        category="agent_base_url_update",
        payload_json=json.dumps(
            {"client_id": client_id, "agent_base_url": agent_base_url},
            ensure_ascii=False,
        ),
        user_id=user_id,
    )
    logger.info(f"RPA client agent_base_url updated: {client_id} -> {agent_base_url}")

    return _ok({"client_id": client_id, "agent_base_url": agent_base_url})


# ===========================================================================
# 3. 绑定复核
# ===========================================================================


@router.post("/bindings/{binding_id}/confirm")
async def confirm_binding(binding_id: str, request: Request):
    """复核通过：needs_review → active。

    只有 needs_review / pending 状态的绑定可被确认。已 active 视为幂等成功。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    binding = rpa_db.get_binding(binding_id)
    if not binding:
        raise _fail("绑定不存在", status_code=404)
    if binding.get("tenant_id") != tenant_id:
        raise _fail("无权操作此绑定", status_code=403)

    cur_status = binding.get("status")
    if cur_status == "active":
        # 幂等：已 active 直接返回成功
        return _ok({"binding_id": binding_id, "status": "active"})

    if cur_status not in ("needs_review", "pending"):
        raise _fail(
            f"当前状态不允许确认（{cur_status}），仅 needs_review / pending 可确认",
            status_code=409,
        )

    ok = rpa_db.set_binding_status(tenant_id, binding_id, "active")
    if not ok:
        raise _fail("绑定确认失败", status_code=500)

    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=None,
        account_id=binding.get("account_id"),
        category="binding_confirm",
        payload_json=json.dumps(
            {"binding_id": binding_id, "from_status": cur_status, "to_status": "active"},
            ensure_ascii=False,
        ),
        user_id=user_id,
    )

    logger.info(f"RPA binding confirmed: {binding_id}")

    return _ok({"binding_id": binding_id, "status": "active"})


@router.patch("/bindings/{binding_id}")
async def update_binding(binding_id: str, request: Request, body: UpdateBindingRequest):
    """更新绑定可编辑字段（首版仅监控白名单）。

    None 表示"不修改"；显式传空 list 表示"清除该字段"。
    平台管理员代管理时通过 X-Tenant-Id 指定目标租户。
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    binding = rpa_db.get_binding(binding_id)
    if not binding:
        raise _fail("绑定不存在", status_code=404)
    if binding.get("tenant_id") != tenant_id:
        raise _fail("无权操作此绑定", status_code=403)

    update_kwargs = dict(
        tenant_id=tenant_id,
        binding_id=binding_id,
        monitor_user_names=body.monitor_user_names,
        monitor_user_ids=body.monitor_user_ids,
    )
    if body.display_name is not None:
        update_kwargs["display_name"] = body.display_name
    ok = rpa_db.update_binding(**update_kwargs)
    if not ok:
        raise _fail("绑定更新失败", status_code=500)

    try:
        rpa_db.write_audit(
            tenant_id=tenant_id,
            client_id=None,
            account_id=binding.get("account_id"),
            category="binding_update",
            payload_json=json.dumps(
                {
                    "binding_id": binding_id,
                    "display_name": body.display_name,
                    "monitor_user_names": body.monitor_user_names,
                    "monitor_user_ids": body.monitor_user_ids,
                },
                ensure_ascii=False,
            ),
            user_id=user_id,
        )
    except Exception as audit_err:
        # 审计非强一致：失败不影响主流程，但要记录告警便于事后排查
        logger.warning(
            f"update_binding 审计写入失败 binding={binding_id} error={audit_err}"
        )

    logger.info(f"RPA binding updated: {binding_id}")

    updated = rpa_db.get_binding(binding_id)
    return _ok(_binding_public(updated))


# ===========================================================================
# 4. pause / resume（account / conversation / tenant 三级，幂等）
# ===========================================================================


def _do_pause_account(tenant_id: str, account_id: str, reason: Optional[str]) -> bool:
    cur = rpa_db.get_account_status(tenant_id, account_id)
    if cur == "paused":
        return True  # 幂等
    return rpa_db.set_account_status(tenant_id, account_id, "paused", paused_reason=reason)


def _do_resume_account(tenant_id: str, account_id: str) -> bool:
    cur = rpa_db.get_account_status(tenant_id, account_id)
    if cur in ("online", None):
        return True  # 幂等：已 online 视为成功
    # resume 不强制设为 online（实际是否在线由客户端心跳决定），这里解除 paused 标记
    # 先清危险窗口再解除暂停；Redis 异常时保持 paused，避免恢复后被旧窗口立即重触发。
    if not redis_client.is_available():
        raise RuntimeError("self echo 熔断窗口不可用，拒绝恢复账号")
    redis_client.delete(f"{CacheKeys.WECOM_RPA_SELF_ECHO_ESCAPE}:{tenant_id}:{account_id}")
    return rpa_db.set_account_status(tenant_id, account_id, "online", paused_reason=None)


def _do_pause_conversation(tenant_id: str, binding_id: str) -> bool:
    binding = rpa_db.get_binding(binding_id)
    if not binding or binding.get("tenant_id") != tenant_id:
        return False
    if binding.get("status") == "paused":
        return True  # 幂等
    return rpa_db.set_binding_status(tenant_id, binding_id, "paused")


def _do_resume_conversation(tenant_id: str, binding_id: str) -> bool:
    binding = rpa_db.get_binding(binding_id)
    if not binding or binding.get("tenant_id") != tenant_id:
        return False
    if binding.get("status") == "active":
        return True  # 幂等
    return rpa_db.set_binding_status(tenant_id, binding_id, "active")


@router.post("/pause")
async def pause(request: Request, body: PauseResumeRequest):
    """暂停（tenant / account / conversation 三选一，幂等）。

    - account：set_account_status(paused, paused_reason)
    - conversation：set_binding_status(paused)
    - tenant：遍历 list_accounts，全部置 paused
    """
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")
    reason = body.reason

    scope = body.scope
    affected: List[str] = []

    if scope == "account":
        if not body.account_id:
            raise _fail("scope=account 时 account_id 必填")
        acc = rpa_db.get_account(body.account_id)
        if not acc:
            raise _fail("账号不存在", status_code=404)
        if acc.get("tenant_id") != tenant_id:
            raise _fail("无权操作此账号", status_code=403)
        if not _do_pause_account(tenant_id, body.account_id, reason):
            raise _fail("账号暂停失败", status_code=500)
        affected.append(body.account_id)

    elif scope == "conversation":
        if not body.conversation_id:
            raise _fail("scope=conversation 时 conversation_id 必填（binding.id）")
        binding = rpa_db.get_binding(body.conversation_id)
        if not binding:
            raise _fail("绑定不存在", status_code=404)
        if binding.get("tenant_id") != tenant_id:
            raise _fail("无权操作此绑定", status_code=403)
        if not _do_pause_conversation(tenant_id, body.conversation_id):
            raise _fail("会话暂停失败", status_code=500)
        affected.append(body.conversation_id)

    elif scope == "tenant":
        accounts = rpa_db.list_accounts(tenant_id)
        for acc in accounts:
            aid = acc.get("id")
            if aid and _do_pause_account(tenant_id, aid, reason):
                affected.append(aid)

    else:
        raise _fail("scope 必须为 tenant | account | conversation")

    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=None,
        account_id=body.account_id if scope == "account" else None,
        category="pause_resume",
        payload_json=json.dumps(
            {
                "action": "pause",
                "scope": scope,
                "account_id": body.account_id,
                "conversation_id": body.conversation_id,
                "reason": reason,
                "affected": affected,
            },
            ensure_ascii=False,
        ),
        user_id=user_id,
    )

    logger.info(f"RPA pause: scope={scope}, affected={len(affected)} (tenant={tenant_id})")

    return _ok({"scope": scope, "action": "pause", "affected": affected})


@router.post("/resume")
async def resume(request: Request, body: PauseResumeRequest):
    """恢复（tenant / account / conversation 三选一，幂等）。"""
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    user_id = admin.get("user_id")

    scope = body.scope
    affected: List[str] = []

    if scope == "account":
        if not body.account_id:
            raise _fail("scope=account 时 account_id 必填")
        acc = rpa_db.get_account(body.account_id)
        if not acc:
            raise _fail("账号不存在", status_code=404)
        if acc.get("tenant_id") != tenant_id:
            raise _fail("无权操作此账号", status_code=403)
        if not _do_resume_account(tenant_id, body.account_id):
            raise _fail("账号恢复失败", status_code=500)
        affected.append(body.account_id)

    elif scope == "conversation":
        if not body.conversation_id:
            raise _fail("scope=conversation 时 conversation_id 必填（binding.id）")
        binding = rpa_db.get_binding(body.conversation_id)
        if not binding:
            raise _fail("绑定不存在", status_code=404)
        if binding.get("tenant_id") != tenant_id:
            raise _fail("无权操作此绑定", status_code=403)
        if not _do_resume_conversation(tenant_id, body.conversation_id):
            raise _fail("会话恢复失败", status_code=500)
        affected.append(body.conversation_id)

    elif scope == "tenant":
        accounts = rpa_db.list_accounts(tenant_id)
        for acc in accounts:
            aid = acc.get("id")
            if aid and acc.get("status") == "paused" and _do_resume_account(tenant_id, aid):
                affected.append(aid)

    else:
        raise _fail("scope 必须为 tenant | account | conversation")

    rpa_db.write_audit(
        tenant_id=tenant_id,
        client_id=None,
        account_id=body.account_id if scope == "account" else None,
        category="pause_resume",
        payload_json=json.dumps(
            {
                "action": "resume",
                "scope": scope,
                "account_id": body.account_id,
                "conversation_id": body.conversation_id,
                "affected": affected,
            },
            ensure_ascii=False,
        ),
        user_id=user_id,
    )

    logger.info(f"RPA resume: scope={scope}, affected={len(affected)} (tenant={tenant_id})")

    return _ok({"scope": scope, "action": "resume", "affected": affected})


# ===========================================================================
# 5. 审计日志
# ===========================================================================


@router.get("/audit")
async def list_audit(
    request: Request,
    category: Optional[str] = Query(None, description="按 category 过滤"),
    client_id: Optional[str] = Query(None, description="按 client_id 过滤"),
    account_id: Optional[str] = Query(None, description="按 account_id 过滤"),
    action_id: Optional[str] = Query(None, description="按 action_id 过滤"),
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
):
    """查询审计日志（支持 category / client_id / account_id / action_id 过滤）。"""
    if err := _ensure_saas_enabled():
        return err

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    filters: Dict[str, Any] = {}
    for k, v in (
        ("category", category),
        ("client_id", client_id),
        ("account_id", account_id),
        ("action_id", action_id),
    ):
        if v:
            filters[k] = v

    rows = rpa_db.list_audit(tenant_id, filters=filters, limit=limit)
    return _ok([_audit_public(r) for r in rows])


# ===========================================================================
# 6. 指标 / 告警（只读，不写审计）
# ===========================================================================


@router.get("/metrics")
async def get_metrics(
    request: Request,
    window_hours: int = Query(24, ge=1, le=720, description="聚合时间窗（小时），默认 24h"),
):
    """RPA 渠道指标聚合：客户端/账号在线率、action 成功率、审计计数、绑定状态。

    只读端点，不写审计。时间窗内的审计/outbox 聚合 + 当前态的客户端/账号/绑定。
    """
    if err := _ensure_saas_enabled():
        return err
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    since_dt = datetime.now() - timedelta(hours=window_hours)
    try:
        metrics = observability.build_metrics(
            client_liveness=rpa_db.get_client_liveness(tenant_id),
            account_states=rpa_db.get_account_states(tenant_id),
            audit_counts=rpa_db.get_audit_counts(tenant_id, since_dt),
            outcome_counts=rpa_db.get_outcome_status_counts(tenant_id, since_dt),
            binding_counts=rpa_db.get_binding_status_counts(tenant_id),
            window_hours=window_hours,
        )
    except Exception as e:
        logger.error(f"RPA metrics failed (tenant={tenant_id}): {type(e).__name__}: {e}")
        raise _fail("指标聚合失败", status_code=500)
    return _ok(data=metrics)


@router.get("/alerts")
async def get_alerts(request: Request):
    """按需评估活跃告警：客户端离线 / 账号未登录 / 连续动作失败 / 待复核积压。

    只读端点，不写审计。基于当前态 + 最近 50 条 outbox 评估，无后台调度。
    版本过低 / 延迟类告警暂未实现（前者需协议增加 client_version，后者无数据源）。
    """
    if err := _ensure_saas_enabled():
        return err
    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    try:
        active_alerts = observability.evaluate_alerts(
            client_liveness=rpa_db.get_client_liveness(tenant_id),
            account_states=rpa_db.get_account_states(tenant_id),
            recent_outcomes=rpa_db.get_recent_outcomes(tenant_id),
            binding_counts=rpa_db.get_binding_status_counts(tenant_id),
        )
    except Exception as e:
        logger.error(f"RPA alerts failed (tenant={tenant_id}): {type(e).__name__}: {e}")
        raise _fail("告警评估失败", status_code=500)
    return _ok(data=active_alerts)
