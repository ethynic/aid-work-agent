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
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, 0, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (trace_id) DO UPDATE SET
                    output = EXCLUDED.output,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    total_tokens = EXCLUDED.total_tokens,
                    duration_ms = EXCLUDED.duration_ms,
                    agent_iterations = EXCLUDED.agent_iterations,
                    tool_calls_count = EXCLUDED.tool_calls_count,
                    tags = EXCLUDED.tags,
                    updated_at = NOW()
            """, (
                trace.trace_id, trace.session_id, trace.tenant_id,
                trace.user_id, trace.subagent_id,
                trace.input, trace.output,
                json.dumps({"model": trace.model, "provider": trace.provider},
                           ensure_ascii=False),
                trace.tags, trace.total_tokens, trace.duration_ms,
                trace.agent_iterations, len(trace.spans),
                trace.status, trace.error_message, trace.source_type,
            ))

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
