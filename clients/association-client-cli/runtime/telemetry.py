"""遥测上报 —— 关键事件批量 POST 到服务端 /api/client/v1/logs。

只上报 start / log / error / complete（不上报高频 progress，控量）。
设计：所有操作吞异常——遥测绝不能影响采集。失败就丢，不重试不抛错。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

# 只对这些事件入缓冲（控量；progress 太高频）
_TELEMETRY_EVENTS = {"start", "log", "error", "complete"}
_FLUSH_THRESHOLD = 20  # 缓冲达此数量触发一次非阻塞中途 flush

_session_id: Optional[str] = None
_buffer: list[dict[str, Any]] = []
_flushing = False


def set_session_id(sid: str) -> None:
    global _session_id
    _session_id = sid


def record_event(event: dict) -> None:
    """仅关键事件入缓冲；缓冲达阈值触发非阻塞 flush。吞异常。"""
    try:
        if event.get("event") not in _TELEMETRY_EVENTS:
            return
        entry = _map_to_log_entry(event)
        if entry is None:
            return
        _buffer.append(entry)
        if len(_buffer) >= _FLUSH_THRESHOLD:
            try:
                asyncio.create_task(_flush())
            except RuntimeError:
                # 无运行中的事件循环（非 async 上下文）——留给 finally 兜底 flush
                pass
    except Exception:
        pass


def _map_to_log_entry(event: dict) -> Optional[dict[str, Any]]:
    """把内部事件信封映射成服务端 LogEntry（client_routes.py LogEntry schema）。"""
    etype = event.get("event")
    ts = event.get("timestamp")
    if etype == "start":
        assocs = event.get("associations") or []
        return {
            "timestamp": ts,
            "level": "INFO",
            "stage": "run_start",
            "association_name": "",
            "message": f"开始收集 {len(assocs)} 个协会",
            "detail": {"server_url": event.get("server_url", ""), "associations_count": len(assocs)},
        }
    if etype == "complete":
        return {
            "timestamp": ts,
            "level": "INFO",
            "stage": "run_complete",
            "association_name": "",
            "message": "收集完成",
            "detail": {
                "total_consumed": event.get("total_consumed"),
                "output": event.get("output", ""),
                "summary": event.get("summary") or {},
            },
        }
    if etype == "log":
        known = {"event", "level", "association", "message", "timestamp"}
        detail = {k: v for k, v in event.items() if k not in known}
        stage = "log"
        if "stage" in detail:
            sv = detail.pop("stage")
            if isinstance(sv, str) and sv:
                stage = sv
        return {
            "timestamp": ts,
            "level": event.get("level", "INFO"),
            "stage": stage,
            "association_name": event.get("association", "") or "",
            "message": event.get("message", ""),
            "detail": detail or None,
        }
    if etype == "error":
        return {
            "timestamp": ts,
            "level": "ERROR",
            "stage": event.get("stage") or "error",
            "association_name": event.get("association", "") or "",
            "message": event.get("message", ""),
            "detail": {
                "error_code": event.get("error_code"),
                "session_fatal": event.get("session_fatal", False),
            },
        }
    return None


async def flush() -> None:
    """外部在 cmd_collect 的 finally 调用，兜底全量 flush。"""
    await _flush()


async def _flush() -> None:
    global _buffer, _flushing
    if _flushing:
        return
    _flushing = True
    try:
        if not _buffer:
            return
        batch = _buffer[:]
        _buffer = []
        await _post(batch)
    except Exception:
        pass
    finally:
        _flushing = False


async def _post(batch: list[dict[str, Any]]) -> None:
    """POST 一批日志到 /api/client/v1/logs。吞一切异常。"""
    from runtime.config import get_access_token, get_server_url

    token = get_access_token()
    server_url = get_server_url()
    if not token or not server_url or not batch:
        return
    payload = {"session_id": _session_id, "logs": batch}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{server_url}/api/client/v1/logs",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        pass  # 遥测失败不影响采集
