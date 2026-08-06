"""
协会客户端鉴权中间件。

设计文档：docs/tools/association-client-design.md §2.3
verify_client_token(access_token) → ClientBinding | None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.db.client_binding_db import ClientBindingDB
from src.saas.db.tenant_db import TenantDB


@dataclass
class ClientBinding:
    """鉴权后的客户端绑定上下文。"""

    binding_id: str
    tenant_id: str
    client_name: Optional[str]
    status: str
    access_token: str
    tenant: dict  # TenantDB.get_by_id 的完整 dict，含 credit_balance / status


def verify_client_token(access_token: str) -> Optional[ClientBinding]:
    """校验客户端 access_token，返回绑定上下文。

    Returns:
        ClientBinding 对象；token 无效 / 绑定已禁用 / 租户非 active 时返回 None。
    """
    if not access_token or not access_token.strip():
        return None

    binding_row = ClientBindingDB.get_by_token(access_token.strip())
    if not binding_row:
        return None

    if binding_row.get("status") != "active":
        logger.warning(f"客户端绑定非 active: binding_id={binding_row.get('binding_id')}")
        return None

    # 检查绑定过期
    expires_at = binding_row.get("expires_at")
    if expires_at:
        from datetime import datetime

        if datetime.now() >= expires_at:
            logger.warning(f"客户端绑定已过期: binding_id={binding_row.get('binding_id')}")
            return None

    tenant_id = binding_row["tenant_id"]
    tenant = TenantDB.get_by_id(tenant_id)
    if not tenant:
        logger.warning(f"客户端绑定的租户不存在: tenant_id={tenant_id}")
        return None
    if tenant.get("status") != "active":
        logger.warning(f"客户端绑定的租户非 active: tenant_id={tenant_id} status={tenant.get('status')}")
        return None

    # 异步更新 last_seen（不阻塞请求；此处同步写，开销可接受）
    try:
        ClientBindingDB.update_last_seen(binding_row["binding_id"])
    except Exception:
        pass

    return ClientBinding(
        binding_id=binding_row["binding_id"],
        tenant_id=tenant_id,
        client_name=binding_row.get("client_name"),
        status=binding_row["status"],
        access_token=binding_row["access_token"],
        tenant=tenant,
    )


def get_client_token_from_header(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization: Bearer {token} 头提取 token。"""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None
