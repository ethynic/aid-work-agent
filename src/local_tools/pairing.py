"""本地设备配对业务：配对码签发、code 换 device+token、设备生命周期

安全约束：
- 配对码明文只在创建响应中返回一次，库只存 hash
- 协议1/2 token明文仅初次响应；协议3响应回传客户端受保护token，库只存hash
- Web access token 与 device token 完全隔离（device token 仅能调 /api/local-tools/runtime/*）
"""

from datetime import datetime, timedelta
import json
import re
import uuid
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
    code: Optional[str],
    name: Optional[str] = None,
    platform: Optional[str] = None,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint: Optional[str] = None,
    pairing_protocol: Optional[int] = None,
    registration_id: Optional[str] = None,
    registration_token: Optional[str] = None,
    previous_device_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """配对码换设备 + 设备 token。code 无效/过期/已用返回 None。

    票据消费与设备创建同一事务；协议3同时完成授权替换及选定。
    """
    normalized = (code or "").strip().upper()
    if pairing_protocol == 3:
        try:
            registration_id = str(uuid.UUID(registration_id or ""))
        except (ValueError, TypeError, AttributeError):
            raise repository.DeviceReplacementError("REGISTRATION_INVALID") from None
        if not isinstance(registration_token, str) or not re.fullmatch(r"[0-9a-f]{64}", registration_token):
            raise repository.DeviceReplacementError("REGISTRATION_INVALID")
        device_token = registration_token
        previous_hash = sha256_hex(previous_device_token) if previous_device_token else None
        intent = {"name": name, "platform": platform, "runtime_version": runtime_version,
                  "capabilities": capabilities, "machine_fingerprint": machine_fingerprint,
                  "previous_device_token_hash": previous_hash, "pairing_protocol": 3}
        intent_hash = sha256_hex(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
        device = repository.register_device_from_ticket(
            code_hash=sha256_hex(normalized) if normalized else None,
            token_hash=sha256_hex(device_token), registration_id=registration_id,
            request_hash=intent_hash, previous_token_hash=previous_hash,
            name=name, platform=platform, runtime_version=runtime_version,
            capabilities=capabilities,
            machine_fingerprint_hash=sha256_hex(machine_fingerprint) if machine_fingerprint else None,
        )
    else:
        if registration_id is not None or registration_token is not None or previous_device_token is not None:
            raise repository.DeviceReplacementError("PAIRING_PROTOCOL_REQUIRED")
        if not normalized:
            raise repository.DeviceReplacementError("PAIRING_CODE_REQUIRED")
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
            **({"pairing_protocol": 3, "registration_completed": True,
                "replacement_completed": bool(device.get("replacement_device_id"))}
               if pairing_protocol == 3 else
               ({"replacement_pending": True} if device.get("replacement_device_id") else {}))}
