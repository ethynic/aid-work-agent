"""腾讯文档 OAuth 2.0 授权码流（任务 #2，2026-11-20 第三方应用过审）

官方接口（ext/腾讯文档接口/01-overview-and-oauth.md §3）：
- authorize: GET https://docs.qq.com/oauth/v2/authorize（浏览器跳转，前端发起）
- token:     GET https://docs.qq.com/oauth/v2/token（仅服务端发起）
- userinfo:  GET https://docs.qq.com/oauth/v2/userinfo（可选，昵称 + unionID + token 校验）

关键约束（快照 §3 + 02-api-common.md）：
- redirect_uri 必须与 authorize 完全一致且仅 HTTPS；code 10 分钟一次性
- 刷新响应不含新 refresh_token，沿用旧值（1 年有效，刷新不换新）
- 37017 refresh_token 失效 -> 必须重新授权；37019/10301 token 过期 -> 可刷新
- client_secret 仅存服务端 env，不下发前端、不落库、不进日志

回调防 CSRF：一次性 state（Redis GETDEL），绑定 (tenant_id, user_id)。
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

import httpx
from loguru import logger

from src.config.settings import settings
from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client

AUTHORIZE_URL = "https://docs.qq.com/oauth/v2/authorize"
TOKEN_URL = "https://docs.qq.com/oauth/v2/token"
USERINFO_URL = "https://docs.qq.com/oauth/v2/userinfo"

# 刷新令牌失效码：必须重新授权，自动刷新无法恢复
REAUTH_REQUIRED_CODES = {37017}
# token 过期/鉴权失败码：可通过 refresh 恢复
REFRESHABLE_CODES = {37019, 10301}


class OAuthError(Exception):
    """OAuth 接口调用异常。reauthorization_required=True 时引导用户重新授权。"""

    def __init__(self, code: int, message: str, reauthorization_required: bool = False):
        self.code = code
        self.message = message
        self.reauthorization_required = reauthorization_required
        super().__init__(f"[tdoc oauth {code}] {message}")


@dataclass
class OAuthTokenResult:
    """token 接口统一结果（授权码换取 / 刷新共用）。"""

    access_token: str
    expires_in: int              # 秒；按响应实际值落库，不以文档口径硬编码
    open_id: str                 # 响应 user_id 字段即 Open ID
    refresh_token: str = ""      # 刷新响应不含新 refresh_token（沿用旧值）
    scope: str = ""


def is_oauth_configured() -> bool:
    """第三方应用凭据是否已配置（前端据此展示「授权绑定」入口）。"""
    cfg = settings.tencent_docs
    return bool(cfg.oauth_client_id and cfg.oauth_client_secret)


def redis_available() -> bool:
    """Redis 是否可用（oauth/start fail-fast 前置检查；state 禁内存降级充当跨 worker 会话）。"""
    return bool(redis_client.is_available())


def get_oauth_client_id() -> str:
    return settings.tencent_docs.oauth_client_id


def build_authorize_url(state: str, redirect_uri: Optional[str] = None) -> str:
    """构造授权跳转 URL（new_login=1 新版授权组件，scope 固定 all）。"""
    uri = redirect_uri or settings.tencent_docs.oauth_redirect_uri
    return (
        f"{AUTHORIZE_URL}?client_id={quote(get_oauth_client_id())}"
        f"&redirect_uri={quote(uri, safe='')}"
        f"&new_login=1&response_type=code&scope=all&state={quote(state, safe='')}"
    )


def frontend_base_url() -> str:
    """回调域名的前端基地址（与 redirect_uri 同 origin，回调 302 回租户前台页面）。"""
    parsed = urlparse(settings.tencent_docs.oauth_redirect_uri)
    return f"{parsed.scheme}://{parsed.netloc}"


# ------------------------------- 一次性 state（防 CSRF） -------------------------------

def _state_key(state: str) -> str:
    return f"{CacheKeys.TDOC_OAUTH_STATE}:{state}"


def create_oauth_state(tenant_id: str, user_id: str) -> str:
    """生成一次性 state 并绑定租户/用户身份，TTL 默认 600s（>= code 10 分钟有效期）。"""
    state = uuid.uuid4().hex
    ttl = max(300, int(settings.tencent_docs.oauth_state_ttl_seconds))
    redis_client.set(
        _state_key(state),
        json.dumps({"tenant_id": tenant_id, "user_id": user_id}),
        ex=ttl,
    )
    return state


def consume_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """消费一次性 state（GETDEL 原子取删，防重放）。

    Redis 不可用 / state 不存在 / 已过期均返回 None（fail-closed，回调失败引导重试）。
    """
    if not state:
        return None
    value = redis_client.getdel(_state_key(state))
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not payload.get("tenant_id") or not payload.get("user_id"):
        return None
    return payload


# ------------------------------- HTTP 调用（token / userinfo） -------------------------------

def _parse_token_response(data: Dict[str, Any]) -> OAuthTokenResult:
    """解析 token 响应；错误码映射 OAuthError（37017 须重授权）。"""
    code = data.get("code") or data.get("ret")
    if code not in (None, 0):
        code = int(code)
        message = data.get("message") or data.get("msg") or data.get("error_description") or "token 接口错误"
        raise OAuthError(
            code,
            message,
            reauthorization_required=code in REAUTH_REQUIRED_CODES,
        )
    access_token = data.get("access_token") or ""
    if not access_token:
        raise OAuthError(-1, "token 响应缺少 access_token")
    return OAuthTokenResult(
        access_token=access_token,
        expires_in=int(data.get("expires_in") or 0),
        open_id=data.get("user_id") or "",
        refresh_token=data.get("refresh_token") or "",
        scope=data.get("scope") or "",
    )


async def _get_json(
    url: str,
    params: Dict[str, str],
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Dict[str, Any]:
    """GET 请求并解析 JSON（官方 token/userinfo 均为 GET + query 参数）。"""
    async with httpx.AsyncClient(timeout=30.0, transport=transport) as http:
        resp = await http.get(url, params=params, headers={"Accept": "application/json"})
    # redirect_uri 不一致等错误以 HTTP 401 表达，body 形态未知，尽力解析
    try:
        return resp.json() or {}
    except ValueError:
        raise OAuthError(resp.status_code, f"OAuth 接口响应非 JSON（HTTP {resp.status_code}）")


async def exchange_code(
    code: str,
    redirect_uri: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> OAuthTokenResult:
    """授权码换取 token（grant_type=authorization_code）。"""
    cfg = settings.tencent_docs
    uri = redirect_uri or cfg.oauth_redirect_uri
    params = {
        "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "redirect_uri": uri,
        "grant_type": "authorization_code",
        "code": code,
    }
    data = await _get_json(TOKEN_URL, params, transport)
    result = _parse_token_response(data)
    logger.info(f"腾讯文档 OAuth code 换取成功 open_id={result.open_id} expires_in={result.expires_in}")
    return result


async def refresh_access_token(
    refresh_token: str,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> OAuthTokenResult:
    """刷新 access token（grant_type=refresh_token；响应不含新 refresh_token）。"""
    cfg = settings.tencent_docs
    params = {
        "client_id": cfg.oauth_client_id,
        "client_secret": cfg.oauth_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    data = await _get_json(TOKEN_URL, params, transport)
    return _parse_token_response(data)


async def get_userinfo(
    access_token: str,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> Dict[str, Any]:
    """获取用户信息（openID/nick/avatar/unionID），兼作 token 有效性校验。

    失败不抛异常返回空 dict —— 用户信息是可选增强，失败不应阻断授权落库
    （code 一次性，userinfo 失败迫使整轮重授权属体验回退）。
    """
    params = {"access_token": access_token}
    try:
        data = await _get_json(USERINFO_URL, params, transport)
    except (OAuthError, httpx.HTTPError) as e:
        logger.warning(f"腾讯文档 userinfo 获取失败（不影响授权）: {e}")
        return {}
    if data.get("ret") != 0:
        logger.warning(f"腾讯文档 userinfo 业务失败 ret={data.get('ret')}（不影响授权）")
        return {}
    return data.get("data") or {}
