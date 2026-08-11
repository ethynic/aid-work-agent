"""本地设备配对业务：配对码签发、code 换 device+token、设备生命周期

安全约束：
- 配对码明文只在创建响应中返回一次，库只存 hash
- 设备 token 明文只在 pair 响应中返回一次，库只存 hash
- Web access token 与 device token 完全隔离（device token 仅能调 /api/local-tools/runtime/*）
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from loguru import logger

from src.local_tools import repository
from src.local_tools.security import (
    generate_device_token,
    generate_pair_code,
    sha256_hex,
)

PAIR_CODE_TTL_SECONDS = 300  # 配对码 5 分钟有效


def create_pairing_ticket(tenant_id: str, user_id: str) -> Dict[str, Any]:
    """创建一次性配对码。同一用户未使用的旧码全部作废，防止多码并存。"""
    # 先作废旧码
    repository.invalidate_unused_tickets(tenant_id, user_id)

    code = generate_pair_code()
    expires_at = datetime.now() + timedelta(seconds=PAIR_CODE_TTL_SECONDS)
    repository.create_ticket(tenant_id, user_id, sha256_hex(code), expires_at)
    logger.info(f"后端日志：创建本地工具配对码 tenant={tenant_id} user={user_id} expires_at={expires_at}")
    return {"code": code, "expires_at": expires_at}


def pair(
    code: str,
    name: Optional[str] = None,
    platform: Optional[str] = None,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """配对码换设备 + 设备 token。code 无效/过期/已用返回 None。

    只有 consume_ticket 原子消费成功才创建设备，保证配对码单次使用。
    """
    normalized = (code or "").strip().upper()
    ticket = repository.consume_ticket(sha256_hex(normalized))
    if not ticket:
        return None

    device_token = generate_device_token()
    device = repository.create_device(
        tenant_id=ticket["tenant_id"],
        user_id=ticket["user_id"],
        token_hash=sha256_hex(device_token),
        name=name,
        platform=platform,
        runtime_version=runtime_version,
        capabilities=capabilities,
        machine_fingerprint_hash=sha256_hex(machine_fingerprint) if machine_fingerprint else None,
    )
    logger.info(
        f"后端日志：本地工具设备配对成功 device_id={device['id']} "
        f"tenant={ticket['tenant_id']} user={ticket['user_id']} platform={platform}"
    )
    return {"device_id": str(device["id"]), "device_token": device_token}
