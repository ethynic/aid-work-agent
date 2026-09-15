"""弹层自愈 LLM 分析（2026-08-31，设计 docs/design/recruiting/boss-overlay-heal-design.md）

本地工具失败（UI_CHANGED/BUSY）后，云端自愈编排从设备导出的覆盖层候选清单中挑选
「关闭控件」：先启发式（白名单文本 + 弹层类名特征，零 LLM 费用），未命中再升级
chat_no_thinking 关思考结构化选择。

安全铁律（与 CLI 端 DISMISS_TEXT_WHITELIST 双重校验）：
- LLM 只能在白名单集合里挑，输出非白名单文本一律拒绝（防幻觉误点「立即领取」类按钮）
- 候选清单里不存在的文本一律拒绝（防凭空捏造坐标/文本）
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.llm.gateway import llm_gateway
from src.services.session_record import record_background_llm_usage

logger = logging.getLogger(__name__)

BILLING_SOURCE = "boss_overlay_heal"

# 关闭语义白名单（与 boss-cli OverlayInspector.DISMISS_TEXT_WHITELIST 保持一致，改动须两侧同步）
DISMISS_WHITELIST = frozenset({
    "关闭", "关闭弹窗", "关闭广告",
    "知道了", "我知道了", "我知道啦",
    "以后再说", "下次再说", "稍后再说",
    "暂不", "暂不需要", "暂不使用",
    "不再提醒", "不再提示", "残忍拒绝",
    "取消", "跳过", "忽略",
    "×", "✕", "✖", "X",
    "No thanks", "Close",
})

# class 名弹层特征（启发式直选的必要条件：白名单文本 + 弹层类名，避免误点正常页面的「取消」）
_OVERLAY_CLS_HINTS = ("dialog", "mask", "modal", "popup", "pop-", "pop_", "layer", "guide", "banner", "toast")

# icon 关闭控件 class 识别（与 boss-cli OverlayInspector.ICON_CLOSE_HINT 保持一致：
# 2026-08-31 真机实证广告弹窗关闭 × 无文字，class 惯例 boss-popup__close / icon-close / ad-banner-close）
ICON_CLOSE_HINT = re.compile(r"close|guanbi", re.IGNORECASE)

_SYSTEM_PROMPT = (
    "你是网页弹层识别助手。给你 BOSS 直聘页面视口内的两类候选："
    "text_nodes（文本节点 text/x/y/w/h/class）与 icon_candidates（无文字图标控件 icon_cls/x/y/w/h，"
    "class 已预筛含关闭语义 close/guanbi）。页面疑似被弹层/弹窗遮挡。"
    "任务：从候选中找出「关闭这个弹层」的控件。"
    "硬性约束：只能选择关闭/放弃语义的控件；绝不能选择领取、打开、开通、下载、查看、立即等正向动作控件；"
    "text_nodes 选出的控件输出 text=其原文，icon_candidates 选出的控件输出 text=\"icon:\"+其完整 icon_cls；"
    "必须逐字取自清单，禁止改写或编造。"
    '输出严格 JSON（不要多余文字）：{"found": true, "text": "控件精确文本或 icon:类名", "why": "一句话理由"}；'
    '找不到关闭控件时输出 {"found": false, "why": "一句话理由"}。'
)


def _is_icon_ref(text: str) -> bool:
    """icon:<cls> 引用形态，且 class 命中关闭语义（icon 版白名单）"""
    if not text.startswith("icon:"):
        return False
    return bool(ICON_CLOSE_HINT.search(text[5:]))


def pick_heuristic(candidates: List[Dict[str, Any]],
                   icon_candidates: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """启发式直选（零 LLM 费用）：
    ① icon 候选：class 本身已预筛含 close/guanbi（最强信号），直接取视口上层（y 小者，弹窗多在上部）——
       2026-08-31 真机实证广告弹窗关闭 × 无文字，纯文本路线抓不到
    ② 文本候选：白名单文本 + 弹层类名特征同时命中；无类名特征交给 LLM 结合上下文判断
       （正常页面也可能有「取消/关闭」字样，盲点会误关用户正在操作的对话框）
    """
    for c in icon_candidates or []:
        icon_cls = str(c.get("icon_cls") or "").strip()
        if icon_cls and ICON_CLOSE_HINT.search(icon_cls):
            return f"icon:{icon_cls}"
    for c in candidates or []:
        text = str(c.get("text") or "").strip()
        cls = str(c.get("cls") or "").lower()
        if text in DISMISS_WHITELIST and any(k in cls for k in _OVERLAY_CLS_HINTS):
            return text
    return None


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 输出 JSON（容忍 ```json 围栏）"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def pick_dismiss_text_with_llm(
    tenant_id: str,
    user_id: str,
    candidates: List[Dict[str, Any]],
    icon_candidates: Optional[List[Dict[str, Any]]] = None,
    viewport: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """LLM 从候选（文本节点 + icon 关闭控件）挑关闭控件（chat_no_thinking 关思考，token 记账）。

    返回白名单文本或 `icon:<cls>`（class 命中关闭语义且在候选清单内）；
    两轮尝试仍失败/不合法返回 None（自愈放弃）。
    """
    if not candidates and not icon_candidates:
        return None
    known_texts = {str(c.get("text") or "").strip() for c in candidates}
    known_icons = {str(c.get("icon_cls") or "").strip() for c in icon_candidates or []}
    user_payload = json.dumps(
        {"viewport": viewport or {}, "text_nodes": candidates, "icon_candidates": icon_candidates or []},
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_payload},
    ]
    model_name = llm_gateway.get_model_name()
    last_error = "未知"
    for attempt in (1, 2):
        try:
            response = await llm_gateway.chat_no_thinking(messages=messages, temperature=0, max_tokens=300)
        except Exception as e:  # noqa: BLE001  LLM 异常不外抛，自愈放弃返回 None
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"弹层自愈 LLM 调用失败（第 {attempt} 次）: {last_error}")
            continue

        # token 记账（含解析失败的那次——真实 token 已消耗）
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            tenant_id=tenant_id,
            user_id=user_id,
            source=BILLING_SOURCE,
            user_message="弹层自愈关闭控件识别",
            model=model_name or None,
        )

        content = (response.get("content") or "") if isinstance(response, dict) else ""
        parsed = _parse_llm_json(content)
        if not parsed:
            last_error = "输出 JSON 不合法"
            logger.warning(f"弹层自愈 LLM 输出解析失败（第 {attempt} 次）: {content[:200]}")
            continue
        if not parsed.get("found"):
            return None  # LLM 明确说没有关闭控件：放弃自愈（不是错误）
        text = str(parsed.get("text") or "").strip()
        # 双重校验：文本候选必须命中白名单；icon 引用必须命中关闭语义且在候选清单内（防幻觉）
        if text in DISMISS_WHITELIST and text in known_texts:
            return text
        if _is_icon_ref(text) and text[5:].strip() in known_icons:
            return text
        last_error = f"LLM 选择「{text}」不在白名单/候选清单内（防幻觉拦截）"
        logger.warning(f"弹层自愈 LLM 选择被拒（第 {attempt} 次）: {last_error}")

    logger.warning(f"弹层自愈 LLM 识别失败（重试后仍败）: {last_error}")
    return None
