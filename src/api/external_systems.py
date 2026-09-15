"""外部系统入口 API（SSO 打开第三方系统）

机制与实例分离：系统层只提供通用入口机制，外部系统细节由各子智能体租户文档
{subagent_name}-api.md 的 api-meta sso_* 键声明（解析见
src/services/tenant_api_doc.py），本模块代码不硬编码任何第三方系统。
system_id 即子智能体名。设计方案见 docs/system/external-system-entry-design.md。
"""

import asyncio
import re
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from src.api.auth import get_current_user
from src.services import tenant_api_doc

router = APIRouter(prefix="/api/external-systems", tags=["外部系统入口"])

_SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
]


def _sanitize_error_info(error_msg: str) -> str:
    for pattern in _SENSITIVE_PATTERNS:
        error_msg = re.sub(
            pattern, lambda m: m.group(0).split("=")[0] + "=***",
            error_msg, flags=re.IGNORECASE,
        )
    return error_msg


def _get_tenant_id(request: Request) -> Optional[str]:
    return getattr(request.state, "tenant_id", None)


def _fallback_response(fallback_url: str, error: str, debug: str) -> dict:
    """SSO 失败不阻断：返回 fallback_url，前端 window.open 打开登录页供手动登录"""
    return {
        "success": False,
        "error": error,
        "fallback_url": fallback_url,
        "debug": _sanitize_error_info(debug),
    }


def _build_ticket_url(base: str, param: str, ticket: str) -> str:
    from urllib.parse import quote

    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{param}={quote(ticket, safe='')}"


def _resolve_grant_url(cfg: dict, resp: dict) -> str:
    """从 sso_grant/sso_login 响应解析跳转地址：第三方返回 url 优先，否则自拼"""
    response = resp.get("Response") or {}
    url = response.get("url") or ""
    if url:
        return url
    ticket = response.get("sso_ticket") or ""
    if cfg["ticket_param"] and ticket:
        return _build_ticket_url(cfg["sso_url"], cfg["ticket_param"], ticket)
    raise ValueError("sso 响应缺少 url 与票据，无法生成跳转地址")


@router.get("")
async def list_external_systems(request: Request):
    tenant_id = _get_tenant_id(request)
    if not tenant_id:
        return JSONResponse(
            {"success": False, "error": "缺少租户上下文", "debug": "tenant_id is None"},
            status_code=400,
        )
    if get_current_user(request) is None:
        return JSONResponse(
            {"success": False, "error": "未登录或登录已过期", "debug": "get_current_user returned None"},
            status_code=401,
        )

    configs = tenant_api_doc.load_sso_configs(tenant_id)
    if not configs:
        return {"success": True, "data": {"items": []}}

    items = [
        {
            "system_id": cfg["system_id"],
            "name": cfg["system_name"],
            "mode": cfg["mode"],
            "sso_ready": cfg["sso_ready"],
            # direct_url 模式前端直接打开；其余模式需调 sso-url 接口换取跳转地址
            "entry_url": cfg["sso_url"] if cfg["mode"] == "direct_url" else None,
        }
        for cfg in configs
    ]
    return {"success": True, "data": {"items": items}}


@router.post("/{system_id}/sso-url")
async def get_sso_url(system_id: str, request: Request):
    tenant_id = _get_tenant_id(request)
    if not tenant_id:
        return JSONResponse(
            {"success": False, "error": "缺少租户上下文", "debug": "tenant_id is None"},
            status_code=400,
        )
    user = get_current_user(request)
    if user is None:
        return JSONResponse(
            {"success": False, "error": "未登录或登录已过期", "debug": "get_current_user returned None"},
            status_code=401,
        )

    # system_id 即子智能体名，按名定位该智能体的租户文档
    cfg = tenant_api_doc.load_sso_config(tenant_id, system_id)
    if cfg is None or cfg["system_id"] != system_id:
        return JSONResponse(
            {"success": False, "error": "外部系统不存在或未开启入口", "debug": f"system_id={system_id}"},
            status_code=404,
        )
    if cfg["mode"] not in ("ticket_redirect", "token_param"):
        return JSONResponse(
            {"success": False, "error": "该系统不支持 SSO 换票，请直接打开入口地址",
             "fallback_url": cfg["fallback_url"], "debug": f"mode={cfg['mode']}"},
            status_code=400,
        )

    from src.services.recap.tasks.external_push import (
        _delegate_login,
        _get_agent_token,
        _post_json,
    )

    phone = (user or {}).get("phone") or ""
    if not phone:
        return _fallback_response(
            cfg["fallback_url"], "当前账号无手机号，无法单点登录，请手动登录", "user.phone is empty"
        )

    agent_token = _get_agent_token(tenant_id, system_id)
    if not agent_token:
        return _fallback_response(
            cfg["fallback_url"], "外部系统未完成对接配置，请联系管理员",
            "AGENT_TOKEN not configured for tenant",
        )

    login_url = cfg.get("login_url") or ""
    if not login_url:
        return _fallback_response(
            cfg["fallback_url"], "单点登录失败，已打开登录页，请手动登录",
            "api-meta 缺少 login_url，无法建立委托会话",
        )

    # name 供手机号不存在时自动建号使用，与委托登录/sso_login 同契约；空值不传
    user_name = (user or {}).get("nickname") or (user or {}).get("username") or None

    def _fail_fallback(debug: str) -> dict:
        logger.info(
            f"[external_systems] sso 换票失败 tenant={tenant_id} user={user.get('user_id')} "
            f"system={system_id} mode={cfg['mode']} debug={_sanitize_error_info(debug)}"
        )
        return _fallback_response(
            cfg["fallback_url"], "单点登录失败，已打开登录页，请手动登录", debug
        )

    async def _sso_login_fallback_ticket() -> Optional[str]:
        """委托会话不可用时的兜底签发：sso_login（agent_token + 手机号）直接换票据"""
        if not cfg["login_sso_url"]:
            return None
        sso_payload = {"mobile": phone}
        if user_name:
            sso_payload["name"] = user_name
        resp = await asyncio.to_thread(_post_json, cfg["login_sso_url"], sso_payload, agent_token)
        if isinstance(resp, dict) and resp.get("Code") == 0:
            return _resolve_grant_url(cfg, resp)
        return None

    try:
        if cfg["mode"] == "token_param":
            # 票据即委托会话 client_token 本身，由前端拼在 URL 打开（契约兜底模式）
            login = await asyncio.to_thread(
                _delegate_login, tenant_id, phone, agent_token, login_url, False, user_name, system_id
            )
            if login and login.get("client_token"):
                url = _build_ticket_url(cfg["sso_url"], cfg["ticket_param"], login["client_token"])
                logger.info(
                    f"[external_systems] sso-url 生成成功 tenant={tenant_id} "
                    f"user={user.get('user_id')} system={system_id} mode=token_param"
                )
                return {"success": True, "data": {"url": url}}
            url = await _sso_login_fallback_ticket()
            if url:
                return {"success": True, "data": {"url": url}}
            return _fail_fallback("delegate_login failed")

        # ticket_redirect：委托会话 client_token 调 sso_grant 换一次性票据
        login = await asyncio.to_thread(
            _delegate_login, tenant_id, phone, agent_token, login_url, False, user_name, system_id
        )
        if login and login.get("client_token"):
            grant = await asyncio.to_thread(
                _post_json, cfg["grant_url"], {}, agent_token,
                {"Client-Authorize-Token": login["client_token"]},
            )
            if isinstance(grant, dict) and grant.get("Code") == 0:
                url = _resolve_grant_url(cfg, grant)
                logger.info(
                    f"[external_systems] sso 换票成功 tenant={tenant_id} "
                    f"user={user.get('user_id')} system={system_id} mode=ticket_redirect"
                )
                return {"success": True, "data": {"url": url}}

        # 缓存 token 可能已被第三方判失效（如 Code=-99）：强刷委托会话后重试一次
        login = await asyncio.to_thread(
            _delegate_login, tenant_id, phone, agent_token, login_url, True, user_name, system_id
        )
        if login and login.get("client_token"):
            grant = await asyncio.to_thread(
                _post_json, cfg["grant_url"], {}, agent_token,
                {"Client-Authorize-Token": login["client_token"]},
            )
            if isinstance(grant, dict) and grant.get("Code") == 0:
                url = _resolve_grant_url(cfg, grant)
                logger.info(
                    f"[external_systems] sso 换票成功（强刷重试） tenant={tenant_id} "
                    f"user={user.get('user_id')} system={system_id} mode=ticket_redirect"
                )
                return {"success": True, "data": {"url": url}}

        # 委托会话仍不可用：文档声明了 sso_login 兜底签发路径则尝试
        url = await _sso_login_fallback_ticket()
        if url:
            logger.info(
                f"[external_systems] sso 换票成功（sso_login 兜底） tenant={tenant_id} "
                f"user={user.get('user_id')} system={system_id} mode=ticket_redirect"
            )
            return {"success": True, "data": {"url": url}}
        return _fail_fallback(
            "delegate_login failed 且 sso_login 兜底不可用或失败"
            if not cfg["login_sso_url"] else "sso_grant 与 sso_login 兜底均失败"
        )
    except Exception as e:
        logger.opt(exception=True).error(f"[external_systems] sso-url 生成异常 tenant={tenant_id}: {e}")
        return _fallback_response(
            cfg["fallback_url"], "单点登录失败，已打开登录页，请手动登录", str(e)
        )
