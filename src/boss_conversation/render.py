"""会话任务渲染与受控引用（B2，设计 §5.3/§5.4）。

- render_transcript：已接纳增量消息 → 模型上下文文本（对方消息显式标注为不可信
  数据）；照微信 render 语义独立实现（场景包不反向依赖微信实现）。
- build_payload_ref / parse_payload_ref：reply 冻结正文的受控引用
  ``da:boss.chat_reply.v1:boss-reply:<decision_id>``。
- render_script（确定性渲染器，§5.4 冻结）：逐字替换 {slot_name} 占位符；
  **绝不调用模型、不消耗模型修复次数**；失败三级分流：
  ①必需 slot/resume 字段/证据缺失 → handoff(missing_evidence)（正常业务转人工）；
  ②渲染超长 → handoff(policy_conflict)；
  ③未知占位符/content_hash 不一致等渲染不变量破坏 → RenderInvariantViolation
  （不可修复终局，human_required(render_invariant_violation)+脱敏审计）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    PAYLOAD_REF_PREFIX,
    PLACEHOLDER_RE,
    REPLY_TEXT_MAX_CHARS,
    SCENARIO_KEY,
)

# 模型上下文的消息条数上限（有界化；超出的旧消息截断）
TRANSCRIPT_MAX_MESSAGES = 60


class RenderInvariantViolation(Exception):
    """渲染不变量破坏（设计 §5.4 冻结第三级）：不可修复终局。

    decision_terminal_reason 为受控码（^ [a-z][a-z0-9_]{0,63}$），通用层据此跳过
    模型修复重试直接 failed + human_required（渲染器绝不调模型、不消耗修复次数）。"""

    decision_terminal_reason = "render_invariant_violation"

    def __init__(self, detail: str) -> None:
        super().__init__(f"render_invariant_violation: {detail}")


def render_transcript(messages: List[Dict[str, Any]]) -> str:
    """消息列表 → 文本。messages 需含 message_id/sender/text，按时间升序。

    peer 消息标注不可信；self/system 作为上下文呈现。文本原样包裹（不解释执行）。"""
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


def build_payload_ref(decision_id: str) -> str:
    """reply 冻结正文 payload_ref（da:<scenario>:boss-reply:<decision_id>）。"""
    return f"da:{SCENARIO_KEY}:{PAYLOAD_REF_PREFIX}:{decision_id}"


def parse_payload_ref(payload_ref: str) -> str:
    """解析场景不透明段 → decision_id；形态非法抛 ValueError（fail-closed）。"""
    prefix = f"da:{SCENARIO_KEY}:{PAYLOAD_REF_PREFIX}:"
    if not isinstance(payload_ref, str) or not payload_ref.startswith(prefix):
        raise ValueError(f"payload_ref 形态非法（应为 {prefix}<decision_id>）")
    decision_id = payload_ref[len(prefix):]
    if not decision_id:
        raise ValueError("payload_ref 缺少 decision_id")
    return decision_id


def text_hash(text: str) -> str:
    """冻结正文 hash（sha256 hex，UTF-8 字节——与 serve_payload 字节自检同口径）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_unknown_placeholders(template: str, slot_schema: Dict[str, Any]) -> List[str]:
    """模板占位符 ⊆ slot_schema 校验（渲染不变量）；返回未知占位符列表。"""
    return [name for name in re.findall(PLACEHOLDER_RE, template) if name not in slot_schema]


def render_script(
    template: str,
    content_hash: str,
    slot_schema: Dict[str, Any],
    slot_values: Dict[str, str],
    *,
    max_chars: int = REPLY_TEXT_MAX_CHARS,
) -> Tuple[Optional[str], Optional[str]]:
    """确定性渲染（§5.4 三级分流；可选槽位缺失语义六审非阻断 b 冻结）。

    返回 (text, handoff_reason)：
    - (正文, None)                    → 渲染成功；
    - (None, "missing_evidence")      → **必需** slot 值缺失（第一级，正常转人工）；
    - (None, "policy_conflict")       → 渲染超长（第二级）；
    - RenderInvariantViolation        → 未知占位符/content_hash 不一致（第三级，
      系统异常，调用方不得伪装成 missing_evidence）。

    **可选槽位（required=False）缺值冻结语义**：按空串逐字替换占位符后照常发送
    （required=False 的产品语义即"缺值可发"；若按 missing_evidence 则该标记无
    效果，与 required=True 无法区分）。必需槽位缺失仍走 missing_evidence 转人工。
    slot_values 为**已就绪**的槽位全集（peer 槽位来自模型输出+证据校验、resume
    槽位来自服务端简历库取值）；渲染器绝不输出裸占位符。
    """
    from .models import template_content_hash

    unknown = find_unknown_placeholders(template, slot_schema)
    if unknown:
        raise RenderInvariantViolation(f"未知占位符 {unknown[:3]}")
    if template_content_hash(template) != content_hash:
        raise RenderInvariantViolation("frozen_template 与 content_hash 不一致")
    placeholders = re.findall(PLACEHOLDER_RE, template)
    # 必需槽位（required 缺省 true）取值缺失不得渲染 → 第一级分流
    missing_required = [
        k for k in placeholders
        if (slot_schema.get(k) or {}).get("required", True)
        and (k not in slot_values or str(slot_values[k]) == "")
    ]
    if missing_required:
        return None, "missing_evidence"

    # 单遍逐字替换（CR 补强设计 §5.3 "逐字替换"冻结）：re.sub 一次扫描模板本体，
    # 替换值内容不再参与扫描——槽位值（peer 证据提取，含对方消息原文）中的
    # "{xxx}" 字面量原样输出，绝不递归展开为其他槽位值（尤其 resume 服务端值）。
    def _substitute(m: "re.Match[str]") -> str:  # noqa: ANN401
        value = slot_values.get(m.group(1))
        # 可选槽位缺值/空值 → 空串替换（冻结语义，见 docstring）；不输出裸占位符
        return str(value) if value not in (None, "") else ""

    text = re.sub(PLACEHOLDER_RE, _substitute, template)
    # 注：替换后文本即使含形如 {x} 的字面量（对方消息原文带入）也不是占位符，
    # 不做二次扫描（避免把槽位值误判为不变量破坏）。
    if len(text) > max_chars:
        return None, "policy_conflict"
    return text, None
