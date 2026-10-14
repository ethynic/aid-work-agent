"""结构化提问的发送事实记录（设计 §7.2 投递记录收敛口径：只存发送事实）。

按 wait_id 记录：本次表单的分段全文、已确认发送的段、最近失败原因、重投次数
与在途提交（reply control id）。存放于 ``channel_sessions.metadata`` 的
``ask_send_facts`` 键——渠道入站与发送路径可能落在不同进程，DB 是
runner_agent 与 session 发送封装共同可达的最小持久位置。

用途边界（评审固化）：
- 仅用于「重发」续传去重、受理未应用期间暂停补发、应用成功后清理；
- 不承担答案准入，无状态机、无自动补发；
- 记录失败一律吞掉并记 warning（辅助事实不得阻断回复主链路）。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

_METADATA_KEY = 'ask_send_facts'


def _manager():
    from src.channels.session import channel_session_manager
    return channel_session_manager


def _load_metadata(session_id: str) -> Dict[str, Any]:
    session = _manager().get_session_by_id(session_id) or {}
    metadata = session.get('metadata')
    return dict(metadata) if isinstance(metadata, dict) else {}


def _save_metadata(session_id: str, metadata: Dict[str, Any]) -> None:
    # update_session 的 metadata 是整体替换，必须带全量既有键回写
    _manager().update_session(session_id=session_id, metadata=metadata)


def _facts(metadata: Dict[str, Any]) -> Dict[str, Any]:
    value = metadata.get(_METADATA_KEY)
    return value if isinstance(value, dict) else {}


def load_facts(session_id: str, wait_id: str) -> Optional[Dict[str, Any]]:
    """读取某 wait 的发送事实；无记录返回 None。

    形状：{"segments": [str], "sent": [int], "last_error": str|None,
          "retries": int, "submitted": {"control_id": str}|None}
    """
    if not session_id or not wait_id:
        return None
    try:
        facts = _facts(_load_metadata(session_id)).get(wait_id)
        return dict(facts) if isinstance(facts, dict) else None
    except Exception as e:  # noqa: BLE001 - 事实读取失败不阻断主链路
        logger.warning(f"[ask_delivery] 读取发送事实失败 session={session_id} wait={wait_id}: {e}")
        return None


def init_facts(session_id: str, wait_id: str, segments: List[str]) -> None:
    """首次分段投递前登记分段全文（已确认发送为空，失败可整单续传）。"""
    if not session_id or not wait_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        if wait_id in facts:
            return  # 重跑/重试不覆盖既有事实
        facts[wait_id] = {'segments': list(segments), 'sent': [], 'last_error': None,
                          'retries': 0, 'submitted': None}
        metadata[_METADATA_KEY] = facts
        _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 登记发送事实失败 session={session_id} wait={wait_id}: {e}")


def record_sent(session_id: str, wait_id: str, index: int) -> None:
    """逐段发送结果确认处记录（成功一段记一段）。"""
    if not session_id or not wait_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        entry = facts.get(wait_id)
        if not isinstance(entry, dict):
            return
        if index not in set(entry.get('sent') or []):
            entry.setdefault('sent', []).append(index)
        entry['last_error'] = None
        metadata[_METADATA_KEY] = facts
        _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 记录已发送段失败 session={session_id} wait={wait_id} index={index}: {e}")


def record_failure(session_id: str, wait_id: str, index: int, reason: str) -> None:
    """分段发送失败：记录最近失败原因（续传提示事实），不承担答案准入。"""
    if not session_id or not wait_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        entry = facts.get(wait_id)
        if not isinstance(entry, dict):
            return
        entry['last_error'] = f'segment[{index}]: {reason}'[:300]
        metadata[_METADATA_KEY] = facts
        _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 记录发送失败事实失败 session={session_id} wait={wait_id}: {e}")


def record_submission(session_id: str, wait_id: str, control_id: str) -> None:
    """答案/跳过/自然语言 reply 受理后登记在途 control（受理未应用边界判断）。"""
    if not session_id or not wait_id or not control_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        entry = facts.get(wait_id)
        if not isinstance(entry, dict):
            return
        entry['submitted'] = {'control_id': control_id}
        metadata[_METADATA_KEY] = facts
        _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 登记在途提交失败 session={session_id} wait={wait_id}: {e}")


def clear_submission(session_id: str, wait_id: str) -> None:
    """控制被拒或应用判定失败：清除在途标记，允许修正/重发。"""
    if not session_id or not wait_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        entry = facts.get(wait_id)
        if isinstance(entry, dict) and entry.get('submitted'):
            entry['submitted'] = None
            metadata[_METADATA_KEY] = facts
            _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 清除在途提交失败 session={session_id} wait={wait_id}: {e}")


def record_resend(session_id: str, wait_id: str) -> None:
    """用户明确请求重发：重投次数 +1（可观测事实，不构成自动补发）。"""
    if not session_id or not wait_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        entry = facts.get(wait_id)
        if isinstance(entry, dict):
            entry['retries'] = int(entry.get('retries') or 0) + 1
            metadata[_METADATA_KEY] = facts
            _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 记录重发次数失败 session={session_id} wait={wait_id}: {e}")


def missing_segments(facts: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按发送事实计算缺段（只发缺的，不重复已发）。"""
    if not isinstance(facts, dict) or not isinstance(facts.get('segments'), list):
        return []
    sent = set(facts.get('sent') or [])
    return [{'index': index, 'text': text}
            for index, text in enumerate(facts['segments']) if index not in sent]


def clear_facts(session_id: str, wait_id: str) -> None:
    """答案应用成功（wait 消失/consumed）后清理该 form 的剩余发送记录；
    此后「重发」不再针对旧表单。"""
    if not session_id or not wait_id:
        return
    try:
        metadata = _load_metadata(session_id)
        facts = _facts(metadata)
        if wait_id in facts:
            facts.pop(wait_id, None)
            metadata[_METADATA_KEY] = facts
            _save_metadata(session_id, metadata)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ask_delivery] 清理发送事实失败 session={session_id} wait={wait_id}: {e}")
