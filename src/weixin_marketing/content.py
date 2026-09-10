"""内容块冻结与 payload_ref/payload_hash（R41）

- payload_ref 规范：da:weixin.fixed_content.v1:<revision_ref>:<block_position>；
  正文只在 content_blocks 背后，底座/deliveries/日志只持有 ref+hash（安全红线）。
- payload 字节：text → text_content UTF-8；link → url UTF-8（发送即网址文本）；
  image → 资产引用（P4 交付 operation，P2 仅冻结 hash 语义占位）。
- content_hash：revision 有序块的规范 JSON 摘要（发布快照指纹，重算不一致即拒绝）。
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from src.weixin_marketing.constants import (
    BLOCK_KIND_IMAGE,
    BLOCK_KIND_LINK,
    BLOCK_KIND_TEXT,
    PAYLOAD_REF_PREFIX,
    SCENARIO_KEY,
)


class ContentError(ValueError):
    """内容块非法（服务层 422 语义）"""


def build_payload_ref(revision_ref: str, position: int) -> str:
    """da:<scenario_key>:<revision_ref>:<block_position>（R41）"""
    return f"{PAYLOAD_REF_PREFIX}{SCENARIO_KEY}:{revision_ref}:{position}"


def parse_payload_ref(payload_ref: str) -> Tuple[str, int]:
    """解析本场景 payload_ref → (revision_ref, position)；形态非法抛 ContentError"""
    expected_prefix = f"{PAYLOAD_REF_PREFIX}{SCENARIO_KEY}:"
    if not isinstance(payload_ref, str) or not payload_ref.startswith(expected_prefix):
        raise ContentError(f"payload_ref 非本场景引用: {payload_ref!r}")
    remainder = payload_ref[len(expected_prefix):]
    parts = remainder.rsplit(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
        raise ContentError(f"payload_ref 形态非法: {payload_ref!r}")
    return parts[0], int(parts[1])


def block_payload_bytes(block: Dict[str, Any]) -> bytes:
    """单块 payload 字节（text→正文；link→url；image→受控资产引用 asset:<id>，
    P4-A：真实图片字节由 Runtime 素材下载端点按 invocation scope 获取）"""
    kind = block.get("kind")
    if kind == BLOCK_KIND_TEXT:
        text = block.get("text_content") or ""
        if not text:
            raise ContentError("text 块缺少 text_content")
        return text.encode("utf-8")
    if kind == BLOCK_KIND_LINK:
        url = block.get("url") or ""
        if not url:
            raise ContentError("link 块缺少 url")
        return url.encode("utf-8")
    if kind == BLOCK_KIND_IMAGE:
        # P4 图片 operation 未交付：资产 id 引用的规范字节（不做图片编译）
        asset_id = block.get("asset_id") or ""
        if not asset_id:
            raise ContentError("image 块缺少 asset_id")
        return f"asset:{asset_id}".encode("utf-8")
    raise ContentError(f"未知内容块 kind: {kind!r}")


def payload_hash_of(block: Dict[str, Any]) -> str:
    return hashlib.sha256(block_payload_bytes(block)).hexdigest()


def content_hash_of(blocks: List[Dict[str, Any]]) -> str:
    """revision 内容指纹：有序块 (position, kind, text/url/asset) 规范 JSON 摘要"""
    canonical = []
    for block in sorted(blocks, key=lambda b: b["position"]):
        canonical.append(
            {
                "position": block["position"],
                "kind": block["kind"],
                "text_content": block.get("text_content"),
                "url": block.get("url"),
                "asset_id": block.get("asset_id"),
            }
        )
    serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def freeze_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """写入 content_blocks 前的冻结视图：补 payload_ref/payload_hash（position 从 1 连续）"""
    frozen: List[Dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        frozen.append(
            {
                "position": index,
                "kind": block["kind"],
                "text_content": block.get("text_content"),
                "url": block.get("url"),
                "asset_id": block.get("asset_id"),
                "payload_hash": payload_hash_of(block),
            }
        )
    return frozen


def compile_blocks_for_revision(
    revision_ref: str,
    stored_blocks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """已存储块 → compile_operations 输入（payload_ref 指向冻结 revision）"""
    compiled = []
    for block in sorted(stored_blocks, key=lambda b: b["position"]):
        compiled.append(
            {
                "position": block["position"],
                "kind": block["kind"],
                "text_content": block.get("text_content"),
                "url": block.get("url"),
                "asset_id": block.get("asset_id"),
                "payload_hash": block.get("payload_hash") or payload_hash_of(block),
                "payload_ref": build_payload_ref(revision_ref, block["position"]),
            }
        )
    return compiled


def verify_stored_block(block: Dict[str, Any]) -> Optional[str]:
    """存储行一致性自检：payload_hash 与重算不一致时返回差异描述，一致返回 None"""
    stored = block.get("payload_hash")
    actual = payload_hash_of(block)
    if stored != actual:
        return f"position={block.get('position')} stored={stored} actual={actual}"
    return None


def verify_stored_blocks_list(blocks: List[Dict[str, Any]]) -> Optional[str]:
    """逐块自检：任一不一致返回首个差异描述（发布事务内复核冻结指纹）"""
    for block in blocks:
        mismatch = verify_stored_block(block)
        if mismatch:
            return mismatch
    return None
