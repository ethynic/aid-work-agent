"""素材/载荷 resolver（R17，【计划 §6】payload resolver 行）

payload_ref 是受控租户存储引用，规范形态 da:<scenario_key>:<opaque>：
- 前缀 da + 场景键经 TrustedAdapterRegistry 定位受信适配器（仅进程内显式注册，
  无运行时脚本加载，不解析 opaque 语义）；
- 字节只能来自适配器 serve_payload，绝不接 URL/文件路径/重定向；
- hash 核对：expectation_hash 提供时必须等于 sha256(字节)，不符抛 PayloadHashMismatch
  （不返回字节），由调用方审计并按 500 处理（fail-closed）。

mime 由底座按 magic bytes 保守推断（适配器协议只交付字节）；文本要求 UTF-8 可
解码且无 NUL，未知类型一律 application/octet-stream。
"""

import hashlib
from typing import NamedTuple, Optional, Tuple

from src.desktop_automation.adapters import (
    AdapterContext,
    AdapterNotFoundError,
    TrustedAdapterRegistry,
)

PAYLOAD_REF_PREFIX = "da:"

# payload 端点允许下载的 invocation 状态（总工裁决收紧：须已被设备领取——Runtime 在
# started 后才取 payload，queued 窗口无消费方。claim token 校验未实现：GET 无 body，
# 设备归属已限定；此为对宪章 R17「claim 校验」的明示偏差，登记于 P1-D 报告）
PAYLOAD_ALLOWED_INVOCATION_STATES = ("claimed", "running", "cancel_requested")


class PayloadResolution(NamedTuple):
    """resolver 结果：(字节, mime, sha256 hex)——可按元组解包"""

    data: bytes
    mime: str
    payload_hash: str


class PayloadRefError(ValueError):
    """payload_ref 形态非法（非 da: 前缀或缺少场景/opaque 分量）"""


class PayloadHashMismatch(Exception):
    """适配器字节与期望 hash 不符（fail-closed：不返回字节）"""

    def __init__(self, expected_hash: Optional[str], actual_hash: str):
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"payload 字节与期望 hash 不符 expected={expected_hash} actual={actual_hash}"
        )


def parse_payload_ref(payload_ref: str) -> Tuple[str, str]:
    """'da:<scenario_key>:<opaque>' → (scenario_key, opaque)。

    opaque 允许包含冒号（split 上限 2）；场景键不允许为空。
    """
    if not isinstance(payload_ref, str) or not payload_ref.startswith(PAYLOAD_REF_PREFIX):
        raise PayloadRefError(f"payload_ref 必须以 {PAYLOAD_REF_PREFIX} 开头: {payload_ref!r}")
    parts = payload_ref.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise PayloadRefError(
            f"payload_ref 形态非法（应为 da:<scenario_key>:<opaque>）: {payload_ref!r}"
        )
    return parts[1], parts[2]


def sniff_mime(data: bytes) -> str:
    """保守内容类型推断：PNG/JPEG/GIF/PDF 按 magic bytes，UTF-8 文本，其余 octet-stream"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return "text/plain; charset=utf-8"
    return "application/octet-stream"


def resolve_payload(
    tenant_id: str,
    payload_ref: str,
    expectation_hash: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
    task_ref: str = "",
    revision_ref: str = "",
) -> PayloadResolution:
    """payload_ref → (受控字节, mime, sha256 hex)。

    场景/任务/版本上下文用于适配器 ACL（租户内素材可见范围）；适配器异常
    （含跨租户拒绝）原样传播，由调用方按服务端错误处理。expectation_hash 不符
    抛 PayloadHashMismatch，字节不返回。
    """
    scenario_key, _opaque = parse_payload_ref(payload_ref)
    adapter = TrustedAdapterRegistry.require(scenario_key)  # 未注册 → AdapterNotFoundError
    data = adapter.serve_payload(
        AdapterContext(
            tenant_id=tenant_id,
            user_id=user_id or "",
            scenario_key=scenario_key,
            task_ref=task_ref,
            revision_ref=revision_ref,
        ),
        payload_ref,
    )
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"适配器 {scenario_key} serve_payload 必须返回字节，实际 {type(data).__name__}"
        )
    data = bytes(data)
    payload_hash = hashlib.sha256(data).hexdigest()
    if expectation_hash is not None and expectation_hash != payload_hash:
        raise PayloadHashMismatch(expected_hash=expectation_hash, actual_hash=payload_hash)
    return PayloadResolution(data=data, mime=sniff_mime(data), payload_hash=payload_hash)
