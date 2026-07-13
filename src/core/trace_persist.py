"""
追踪数据异步持久化 — 后台线程队列写入追踪库。

追踪数据在请求完成后异步写入数据库，不阻塞主流程。
"""

import threading
import queue
import json
from loguru import logger


_persist_queue: queue.Queue = queue.Queue()
_worker_started = False
_pending_metadata_updates = {}
_pending_metadata_lock = threading.Lock()
_PENDING_METADATA_MAX = 10000


def _remember_pending_metadata(trace_id: str, metadata: dict) -> None:
    """登记 INSERT 前补丁并限制故障期间的进程内缓存上限。"""
    with _pending_metadata_lock:
        current = _pending_metadata_updates.setdefault(trace_id, {})
        current.update(metadata)
        while len(_pending_metadata_updates) > _PENDING_METADATA_MAX:
            oldest_trace_id = next(iter(_pending_metadata_updates))
            _pending_metadata_updates.pop(oldest_trace_id, None)


def schedule_persist(trace: 'TraceRecord'):
    """将 trace 数据放入异步持久化队列"""
    global _worker_started
    if not _worker_started:
        _start_persist_worker()
        _worker_started = True
    _persist_queue.put(trace)


def _start_persist_worker():
    """启动后台持久化线程"""
    def worker():
        while True:
            try:
                trace = _persist_queue.get(timeout=1)
                _do_persist(trace)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Trace persist error: {e}", exc_info=True)

    t = threading.Thread(target=worker, daemon=True, name="trace-persist")
    t.start()


def _do_persist(trace):
    """将 trace 和 spans 写入追踪库"""
    try:
        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:

            # UPSERT trace
            cur.execute("""
                INSERT INTO obs_traces
                    (trace_id, session_id, tenant_id, user_id, subagent_id,
                     input, output, metadata, tags,
                     total_tokens, total_cost, duration_ms, agent_iterations,
                     tool_calls_count, status, error_message, source_type,
                     user_message_id,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, 0, %s, %s, %s, %s, %s, %s, %s,
                        NOW(), NOW())
                ON CONFLICT (trace_id) DO UPDATE SET
                    output = EXCLUDED.output,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    total_tokens = EXCLUDED.total_tokens,
                    duration_ms = EXCLUDED.duration_ms,
                    agent_iterations = EXCLUDED.agent_iterations,
                    tool_calls_count = EXCLUDED.tool_calls_count,
                    tags = EXCLUDED.tags,
                    metadata = COALESCE(obs_traces.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                    user_message_id = COALESCE(EXCLUDED.user_message_id, obs_traces.user_message_id),
                    updated_at = NOW()
            """, (
                trace.trace_id, trace.session_id, trace.tenant_id,
                trace.user_id, trace.subagent_id,
                trace.input, trace.output,
                json.dumps({
                    "model": trace.model,
                    "provider": trace.provider,
                    **(getattr(trace, "metadata", None) or {}),
                }, ensure_ascii=False),
                trace.tags, trace.total_tokens, trace.duration_ms,
                trace.agent_iterations, len(trace.spans),
                trace.status, trace.error_message, trace.source_type,
                getattr(trace, 'user_message_id', None),
            ))

            # update_trace_metadata 可能早于本次 INSERT 执行而 UPDATE 0 行。
            # 将该极窄窗口内登记的补丁在同一事务提交前再次合并，避免语义丢失。
            with _pending_metadata_lock:
                pending_metadata = _pending_metadata_updates.pop(trace.trace_id, None)
            if pending_metadata:
                cur.execute(
                    "UPDATE obs_traces "
                    "SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb, "
                    "updated_at = NOW() WHERE trace_id = %s",
                    (json.dumps(pending_metadata, ensure_ascii=False), trace.trace_id),
                )

            # INSERT spans
            for span in trace.spans:
                # 构造 metadata
                span_meta = {"success": span.success}
                if span.model:
                    span_meta["model"] = span.model
                if span.provider:
                    span_meta["provider"] = span.provider
                if span.usage:
                    span_meta["usage"] = span.usage
                if span.request_id:
                    span_meta["request_id"] = span.request_id
                # Phase 7 §7.1：压缩 span 的元数据（前端展示用）
                if span.compression_info:
                    span_meta["compression_info"] = span.compression_info

                cur.execute("""
                    INSERT INTO obs_spans
                        (span_id, trace_id, parent_span_id, span_type, name,
                         input, output, metadata, model,
                         prompt_tokens, completion_tokens,
                         start_time, end_time, duration_ms, status,
                         error_message, created_at)
                    VALUES (%s, %s, NULL, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s,
                            to_timestamp(%s), to_timestamp(%s), %s, %s,
                            NULL, NOW())
                    ON CONFLICT (span_id) DO NOTHING
                """, (
                    span.span_id, trace.trace_id, span.span_type, span.name,
                    span.tool_args, span.result,
                    json.dumps(span_meta, ensure_ascii=False),
                    span.model,
                    (span.usage or {}).get("prompt_tokens", 0),
                    (span.usage or {}).get("completion_tokens", 0),
                    span.start_time, span.end_time,
                    span.duration_ms,
                    'completed' if span.success else 'failed',
                ))

            cur.commit()
            logger.debug(f"Trace persisted: {trace.trace_id}, spans={len(trace.spans)}")
    except Exception as e:
        logger.error(f"Failed to persist trace {getattr(trace, 'trace_id', '?')}: {e}")


def update_user_message_id(trace_id: str, user_message_id: str):
    """
    process_and_persist 在写入 channel_messages 后回填 obs_traces.user_message_id。

    用途：trace_persist worker 是独立线程，可能在 process_and_persist 拿到
    created_ids[0] 之前或之后写入 obs_traces。
    - worker 未处理：set_user_message_id 已在内存 trace 设置，worker 写入时携带该值
    - worker 已处理：obs_traces 已有记录但 user_message_id 为 NULL，本函数 UPDATE 补救

    失败只记 debug log，不影响业务（最坏情况是 trace.user_message_id 为 NULL，
    monitor.py 不显示撤回标记，属可接受降级）。
    """
    try:
        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:
            cur.execute(
                "UPDATE obs_traces "
                "SET user_message_id = %s, updated_at = NOW() "
                "WHERE trace_id = %s",
                (user_message_id, trace_id),
            )
            cur.commit()
    except Exception as e:
        logger.debug(
            f"update_user_message_id failed (trace_id={trace_id}, "
            f"user_message_id={user_message_id}): {e}"
        )


def update_trace_metadata(trace_id: str, metadata: dict) -> None:
    """合并更新 Trace JSON metadata，覆盖异步持久化先后竞态。"""
    if not trace_id or not metadata:
        return
    try:
        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:
            cur.execute(
                "UPDATE obs_traces SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb, "
                "updated_at = NOW() WHERE trace_id = %s",
                (json.dumps(metadata, ensure_ascii=False), trace_id),
            )
            if cur.rowcount == 0:
                _remember_pending_metadata(trace_id, metadata)
            cur.commit()
    except Exception as e:
        # 数据库暂不可用时也保留进程内补丁；异步 worker 随后的 UPSERT
        # 若成功，仍可在提交前把 metadata 合并进去。
        _remember_pending_metadata(trace_id, metadata)
        logger.debug(f"update_trace_metadata failed (trace_id={trace_id}): {e}")
