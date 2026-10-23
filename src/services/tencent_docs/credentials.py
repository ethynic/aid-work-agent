"""腾讯文档凭据存取（tenant_id + user_id 维度，Fernet 加密落库）

安全约束：
- access_token / refresh_token 加密存储（src/core/secret_crypto），任何 API 响应
  不得返回明文或密文，仅返回脱敏视图（mask_value）。
- 开发期绑定个人 Token；任务 #2 OAuth 接入后 refresh_token 落 refresh_token_enc，
  本模块接口形态不变。
"""

from typing import Any, Dict, Optional

from loguru import logger

from src.core.secret_crypto import decrypt_secret, encrypt_secret, mask_value
from src.db.tdoc_import import TdocCredentialDB

from .models import TencentDocsCredentials


class CredentialsNotBoundError(Exception):
    """当前用户未绑定腾讯文档凭据。"""


def save_credentials(
    tenant_id: str, user_id: str, client_id: str, access_token: str, open_id: str,
    refresh_token: Optional[str] = None,
) -> None:
    """保存/更新凭据（UPSERT，恢复 status=active）。"""
    record = {
        "client_id": client_id,
        "access_token_enc": encrypt_secret(access_token),
        "refresh_token_enc": encrypt_secret(refresh_token) if refresh_token else None,
        "open_id": open_id,
    }
    TdocCredentialDB.upsert(tenant_id, user_id, record)
    logger.info(f"腾讯文档凭据已保存 tenant_id={tenant_id} user_id={user_id} open_id={open_id}")


def get_credentials(tenant_id: str, user_id: str) -> TencentDocsCredentials:
    """读取凭据（解密）。未绑定抛 CredentialsNotBoundError；绑定已失效同样抛出
    （调用方引导重新绑定/授权）。"""
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
        "client_id_masked": mask_value(row["client_id"]),
        "open_id_masked": mask_value(row["open_id"]),
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


def delete_credentials(tenant_id: str, user_id: str) -> bool:
    deleted = TdocCredentialDB.delete(tenant_id, user_id)
    if deleted:
        logger.info(f"腾讯文档凭据已解绑 tenant_id={tenant_id} user_id={user_id}")
    return deleted
