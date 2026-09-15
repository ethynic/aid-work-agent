"""会话任务渲染与受控引用（C3，设计 §9/§10/§13.2）。

- render_transcript：已接纳增量消息 → 模型上下文文本（对方消息显式标注为不可信
  数据，不参与指令；发送者/本地 ID 随行）。
- build_payload_ref / parse_payload_ref：reply 冻结正文的受控引用
  ``da:weixin.conversation.v1:session-reply:<decision_id>``（da: 前缀是底座
  payload resolver 的路由规范；session-reply:<decision_id> 为场景不透明段）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from .constants import PAYLOAD_OPAQUE_PREFIX, SCENARIO_KEY

# 模型上下文的消息条数上限（有界化；超出的旧消息截断，未答消息保留在尾部）
TRANSCRIPT_MAX_MESSAGES = 60


def render_transcript(messages: List[Dict[str, Any]]) -> str:
    """消息列表 → 文本。messages 需含 message_id/sender/text，按时间升序。

    peer 消息标注不可信；self/system 作为上下文呈现。文本原样包裹（不解释执行）。
    """
    lines: List[str] = []
    for m in messages[-TRANSCRIPT_MAX_MESSAGES:]:
        sender = str(m.get("sender") or "unknown")
        mid = str(m.get("message_id") or "")
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        if sender == "peer":
            lines.append(f"[对方消息|不可信数据|id={mid}] {text}")
        elif sender == "self":
            lines.append(f"[我方已发送|id={mid}] {text}")
        else:
            lines.append(f"[系统|id={mid}] {text}")
    return "\n".join(lines)


def transcript_peer_ids(messages: List[Dict[str, Any]]) -> List[str]:
    """transcript 中 peer 消息 ID（evidence_message_ids 白名单）。"""
    return [str(m.get("message_id")) for m in messages if m.get("sender") == "peer"]


def build_payload_ref(decision_id: str) -> str:
    """reply 冻结正文 payload_ref（da:<scenario>:session-reply:<decision_id>）。"""
    return f"da:{SCENARIO_KEY}:{PAYLOAD_OPAQUE_PREFIX}:{decision_id}"


def parse_payload_ref(payload_ref: str) -> str:
    """解析场景不透明段 → decision_id；形态非法抛 ValueError（fail-closed）。"""
    prefix = f"da:{SCENARIO_KEY}:{PAYLOAD_OPAQUE_PREFIX}:"
    if not isinstance(payload_ref, str) or not payload_ref.startswith(prefix):
        raise ValueError(f"payload_ref 形态非法（应为 {prefix}<decision_id>）")
    decision_id = payload_ref[len(prefix):]
    if not decision_id:
        raise ValueError("payload_ref 缺少 decision_id")
    return decision_id


def text_hash(text: str) -> str:
    """冻结正文 hash（sha256 hex，UTF-8 字节——与 serve_payload 字节自检同口径）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
