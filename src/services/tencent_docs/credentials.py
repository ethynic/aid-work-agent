"""腾讯文档凭据存取（tenant_id + user_id 维度，Fernet 加密落库）

安全约束：
- access_token / refresh_token 加密存储（src/core/secret_crypto），任何 API 响应
  不得返回明文或密文，仅返回脱敏视图（mask_value）。
- 开发期绑定个人 Token（auth_type=personal）；OAuth 授权落 OAuthTokenResult
  （auth_type=oauth），使用时提前 24h 懒刷新，37017/37019 失效引导重新授权。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from src.core.secret_crypto import decrypt_secret, encrypt_secret, mask_value
from src.db.tdoc_import import TdocCredentialDB

from .models import TencentDocsCredentials
from .oauth import OAuthError, OAuthTokenResult, refresh_access_token

# access token 到期前多久触发懒刷新（官方建议提前 1 天）
REFRESH_AHEAD = timedelta(hours=24)
# refresh token 有效期 1 年（官方刷新不换新，从授权时刻起算）
REFRESH_TOKEN_LIFETIME = timedelta(days=365)


class CredentialsNotBoundError(Exception):
    """当前用户未绑定腾讯文档凭据。"""


def save_credentials(
    tenant_id: str, user_id: str, client_id: str, access_token: str, open_id: str,
    refresh_token: Optional[str] = None,
) -> None:
    """保存/更新凭据（UPSERT，恢复 status=active；手工绑定覆盖 OAuth 绑定属预期）。"""
    record = {
        "client_id": client_id,
        "access_token_enc": encrypt_secret(access_token),
        "refresh_token_enc": encrypt_secret(refresh_token) if refresh_token else None,
        "open_id": open_id,
        "auth_type": "personal",
    }
    TdocCredentialDB.upsert(tenant_id, user_id, record)
    logger.info(f"腾讯文档凭据已保存 tenant_id={tenant_id} user_id={user_id} open_id={open_id}")


async def save_oauth_credentials(
    tenant_id: str,
    user_id: str,
    token: OAuthTokenResult,
    userinfo: Optional[Dict[str, Any]] = None,
) -> None:
    """OAuth 授权成功后落库（auth_type=oauth，含过期时间与 refresh token）。"""
    now = datetime.now()
    record = {
        "client_id": _oauth_client_id(),
        "access_token_enc": encrypt_secret(token.access_token),
        "refresh_token_enc": encrypt_secret(token.refresh_token) if token.refresh_token else None,
        "open_id": token.open_id,
        "auth_type": "oauth",
        "expires_at": now + timedelta(seconds=token.expires_in or 0),
        "refresh_expires_at": now + REFRESH_TOKEN_LIFETIME,
        "union_id": (userinfo or {}).get("unionID"),
        "nick": (userinfo or {}).get("nick"),
        "scope": token.scope,
    }
    TdocCredentialDB.upsert(tenant_id, user_id, record)
    logger.info(
        f"腾讯文档 OAuth 凭据已保存 tenant_id={tenant_id} user_id={user_id} "
        f"open_id={token.open_id} expires_in={token.expires_in}"
    )


def _oauth_client_id() -> str:
    from src.config.settings import settings
    return settings.tencent_docs.oauth_client_id


def get_credentials(tenant_id: str, user_id: str) -> TencentDocsCredentials:
    """读取凭据（解密，同步版，不刷新）。未绑定抛 CredentialsNotBoundError；
    绑定已失效同样抛出（调用方引导重新绑定/授权）。"""
    row = TdocCredentialDB.get(tenant_id, user_id)
    if not row:
        raise CredentialsNotBoundError("尚未绑定腾讯文档账号")
    if row.get("status") != "active":
        raise CredentialsNotBoundError("腾讯文档授权已失效，请重新绑定")
    try:
        access_token = decrypt_secret(row["access_token_enc"]).decode("utf-8")
    except ValueError as e:
        logger.error(f"腾讯文档凭据解密失败 tenant_id={tenant_id} user_id={user_id}: {e}")
        raise CredentialsNotBoundError("凭据解密失败，请重新绑定")
    return TencentDocsCredentials(
        client_id=row["client_id"],
        access_token=access_token,
        open_id=row["open_id"],
    )


async def get_credentials_with_refresh(tenant_id: str, user_id: str) -> TencentDocsCredentials:
    """读取凭据并在需要时懒刷新（异步版，业务调用统一走此函数）。

    刷新时机：auth_type=oauth 且 access token 到期前 24h 内（或已过期）。
    - 刷新成功：新 access_token 落库（refresh_token 沿用旧值），返回新凭据
    - 刷新报 37017（refresh_token 失效）/ refresh 已过期：标记失效，引导重新授权
    - 其他瞬时失败：旧 token 仍未过期则沿用旧 token，否则抛错引导重试
    """
    row = TdocCredentialDB.get(tenant_id, user_id)
    if not row:
        raise CredentialsNotBoundError("尚未绑定腾讯文档账号")
    if row.get("status") != "active":
        raise CredentialsNotBoundError("腾讯文档授权已失效，请重新授权")

    if row.get("auth_type") != "oauth" or not row.get("refresh_token_enc"):
        return _credentials_from_row(row)

    expires_at = row.get("expires_at")
    refresh_expires_at = row.get("refresh_expires_at")
    now = datetime.now()
    token_still_valid = bool(expires_at and expires_at > now + REFRESH_AHEAD)

    if not token_still_valid:
        # refresh token 自身过期（1 年未使用）——刷新无法恢复，重新授权
        if refresh_expires_at and refresh_expires_at <= now:
            TdocCredentialDB.mark_expired(tenant_id, user_id)
            raise CredentialsNotBoundError("腾讯文档授权已过期（超过 1 年），请重新授权")
        try:
            refresh_token = decrypt_secret(row["refresh_token_enc"]).decode("utf-8")
            result = await refresh_access_token(refresh_token)
        except ValueError as e:
            logger.error(f"腾讯文档 refresh token 解密失败 tenant_id={tenant_id} user_id={user_id}: {e}")
            raise CredentialsNotBoundError("凭据解密失败，请重新授权")
        except OAuthError as e:
            if e.reauthorization_required:
                TdocCredentialDB.mark_expired(tenant_id, user_id)
                raise CredentialsNotBoundError("腾讯文档授权已失效，请重新授权")
            # 瞬时失败（网络/频控）：旧 token 仍有效则沿用，否则报错
            if expires_at and expires_at > now:
                logger.warning(f"腾讯文档 token 刷新失败（沿用旧 token）: {e}")
                return _credentials_from_row(row)
            raise CredentialsNotBoundError("腾讯文档 token 刷新失败，请稍后重试")
        except httpx.HTTPError as e:
            # 网络异常（连接失败/超时）与 OAuth 瞬时失败同语义：旧 token 仍有效则沿用
            if expires_at and expires_at > now:
                logger.warning(f"腾讯文档 token 刷新网络异常（沿用旧 token）: {e}")
                return _credentials_from_row(row)
            raise CredentialsNotBoundError("腾讯文档 token 刷新失败（网络异常），请稍后重试")
        else:
            new_expires_at = datetime.now() + timedelta(seconds=result.expires_in or 0)
            TdocCredentialDB.update_access_token(
                tenant_id, user_id, encrypt_secret(result.access_token), new_expires_at
            )
            return TencentDocsCredentials(
                client_id=row["client_id"],
                access_token=result.access_token,
                open_id=result.open_id or row["open_id"],
            )

    return _credentials_from_row(row)


def _credentials_from_row(row: Dict[str, Any]) -> TencentDocsCredentials:
    try:
        access_token = decrypt_secret(row["access_token_enc"]).decode("utf-8")
    except ValueError as e:
        logger.error(f"腾讯文档凭据解密失败: {e}")
        raise CredentialsNotBoundError("凭据解密失败，请重新绑定")
    return TencentDocsCredentials(
        client_id=row["client_id"],
        access_token=access_token,
        open_id=row["open_id"],
    )


def try_get_credentials(tenant_id: str, user_id: str) -> Optional[TencentDocsCredentials]:
    """不抛版读取：未绑定/失效返回 None。"""
    try:
        return get_credentials(tenant_id, user_id)
    except CredentialsNotBoundError:
        return None


def get_status(tenant_id: str, user_id: str) -> Dict[str, Any]:
    """脱敏状态视图（前端授权卡片）。"""
    row = TdocCredentialDB.get(tenant_id, user_id)
    if not row:
        return {"bound": False, "status": "none"}
    return {
        "bound": True,
        "status": row.get("status") or "active",
        "auth_type": row.get("auth_type") or "personal",
        "nick": row.get("nick"),
        "client_id_masked": mask_value(row["client_id"]),
        "open_id_masked": mask_value(row["open_id"]),
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


def delete_credentials(tenant_id: str, user_id: str) -> bool:
    deleted = TdocCredentialDB.delete(tenant_id, user_id)
    if deleted:
        logger.info(f"腾讯文档凭据已解绑 tenant_id={tenant_id} user_id={user_id}")
    return deleted
