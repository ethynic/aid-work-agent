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


def create_pairing_ticket(tenant_id: str, user_id: str, replacement_device_id: Optional[str] = None) -> Dict[str, Any]:
    """创建一次性配对码。同一用户未使用的旧码全部作废，防止多码并存。"""
    code = generate_pair_code()
    expires_at = datetime.now() + timedelta(seconds=PAIR_CODE_TTL_SECONDS)
    repository.issue_pairing_ticket(tenant_id, user_id, sha256_hex(code), expires_at, replacement_device_id)
    logger.info(f"后端日志：创建本地工具配对码 tenant={tenant_id} user={user_id} expires_at={expires_at}")
    return {"code": code, "expires_at": expires_at, **({"replacement_device_id": replacement_device_id} if replacement_device_id else {})}


def pair(
    code: str,
    name: Optional[str] = None,
    platform: Optional[str] = None,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint: Optional[str] = None,
    pairing_protocol: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """配对码换设备 + 设备 token。code 无效/过期/已用返回 None。

    票据消费与设备创建同一事务；替换配对不会提前撤销旧设备。
    """
    normalized = (code or "").strip().upper()
    device_token = generate_device_token()
    device = repository.pair_device_from_ticket(
        code_hash=sha256_hex(normalized),
        token_hash=sha256_hex(device_token),
        name=name,
        platform=platform,
        runtime_version=runtime_version,
        capabilities=capabilities,
        machine_fingerprint_hash=sha256_hex(machine_fingerprint) if machine_fingerprint else None,
        pairing_protocol=pairing_protocol,
    )
    if not device:
        return None
    logger.info(
        f"后端日志：本地工具设备配对成功 device_id={device['id']} "
        f"tenant={device['tenant_id']} user={device['user_id']}"
    )
    return {"device_id": str(device["id"]), "device_token": device_token,
            **({"replacement_pending": True} if device.get("replacement_device_id") else {})}
