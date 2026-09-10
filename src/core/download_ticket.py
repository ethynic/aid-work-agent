"""下载票据（HMAC 短期签名，自包含 payload）

解决浏览器原生下载（window.open / <a> 直链导航）无法携带 Authorization /
X-Tenant-Id header 的问题：前端先经认证接口换取短期票据，再用票据直链下载。
票据无状态不落库，格式 ``base64url(payload).{hmac_hex}``（mini-JWT）。

payload 字段：
- r: 绑定的下载请求路径（票据只能用于该路径，防止挪用）
- t: tenant_id
- u: user_id
- role: 用户角色（require_admin 类端点的票据兜底用）
- e: 过期时间戳（秒）

使用方：
- src/saas/middleware.py：对带 ticket 的下载请求还原租户上下文
- src/knowledge/api.py、src/saas/api/tenant_config_file.py、src/api/travel_quote.py：签发票据
"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

TICKET_TTL_SECONDS = 300  # 5 分钟内有效，足够浏览器发起导航下载

_ADMIN_ROLES = ("platform_admin", "tenant_admin")


def _secret() -> bytes:
    """签名主密钥：优先环境变量，回退由 DATABASE_URL 派生（各环境必然存在且稳定，
    避免新增必配环境变量；数据库密码更换只会导致极少量未过期票据失效，无害）。"""
    s = os.environ.get("DOWNLOAD_TICKET_SECRET")
    if not s:
        s = "db:" + os.environ.get("DATABASE_URL", "aid_work_agent")
    return hashlib.sha256(s.encode("utf-8")).digest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_download_ticket(
    resource_path: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
) -> str:
    """签发下载票据，绑定目标下载路径 + 租户 + 用户 + 角色。

    resource_path 必须与后续下载请求的 path 完全一致（不含 query）。
    """
    payload = json.dumps(
        {
            "r": resource_path,
            "t": tenant_id or "",
            "u": user_id or "",
            "role": role or "",
            "e": int(time.time()) + TICKET_TTL_SECONDS,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    body = _b64url(payload)
    sig = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def validate_download_ticket(ticket: str, request_path: str) -> Optional[dict]:
    """校验票据签名、有效期与路径绑定。

    返回 {"tenant_id": ..., "user_id": ..., "role": ...}；无效、过期或路径
    不匹配返回 None。
    """
    if not ticket or "." not in ticket:
        return None
    body, sig = ticket.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("r") != request_path:
        return None
    if int(time.time()) > payload.get("e", 0):
        return None
    return {
        "tenant_id": payload.get("t") or None,
        "user_id": payload.get("u") or None,
        "role": payload.get("role") or None,
    }


def payload_is_admin(payload: Optional[dict]) -> bool:
    """票据 payload 中的角色是否为管理员（require_admin 票据兜底用）。"""
    return bool(payload) and payload.get("role") in _ADMIN_ROLES
