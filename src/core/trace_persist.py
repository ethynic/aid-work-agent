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
_pending_total_cost_updates = {}
_pending_total_cost_lock = threading.Lock()
_PENDING_TOTAL_COST_MAX = 10000


def _remember_pending_metadata(trace_id: str, metadata: dict) -> None:
    """登记 INSERT 前补丁并限制故障期间的进程内缓存上限。"""
    with _pending_metadata_lock:
        current = _pending_metadata_updates.setdefault(trace_id, {})
        current.update(metadata)
        while len(_pending_metadata_updates) > _PENDING_METADATA_MAX:
            oldest_trace_id = next(iter(_pending_metadata_updates))
            _pending_metadata_updates.pop(oldest_trace_id, None)


def _remember_pending_total_cost(trace_id: str, total_cost: float) -> None:
    """登记 INSERT 前成本补丁并限制故障期间的进程内缓存上限。

    同一 trace 重复登记（重复回填/连接反复失败）时按最大值合并：迟到的
    较低值不允许覆盖已登记的较高值，与数据库 GREATEST 写入保持同一语义。
    更新已有 key 不改变插入顺序，有界淘汰仍按原始顺序丢最旧。
    """
    with _pending_total_cost_lock:
        _pending_total_cost_updates[trace_id] = max(
            _pending_total_cost_updates.get(trace_id, 0), total_cost
        )
        while len(_pending_total_cost_updates) > _PENDING_TOTAL_COST_MAX:
            oldest_trace_id = next(iter(_pending_total_cost_updates))
            _pending_total_cost_updates.pop(oldest_trace_id, None)


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
                logger.opt(exception=True).error(f"Trace persist error: {e}")

    t = threading.Thread(target=worker, daemon=True, name="trace-persist")
    t.start()


def _do_persist(trace):
    """将 trace 和 spans 写入追踪库"""
    try:
        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:

            # UPSERT trace
            # total_cost 为参数而非字面量 0：初始取 trace.total_cost（默认 0），
            # session_record.save() 算出真实积分成本后回填内存 trace，
            # 本 UPSERT 随之携带真实值（覆盖计费先完成、trace 后落库的时序）。
            # 冲突分支对现值与传入值取大：迟到的 0/旧快照不回退已落库真实成本。
            cur.execute("""
                INSERT INTO obs_traces
                    (trace_id, session_id, tenant_id, user_id, subagent_id,
                     input, output, metadata, tags,
                     total_tokens, total_cost, duration_ms, agent_iterations,
                     tool_calls_count, status, error_message, source_type,
                     user_message_id,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        NOW(), NOW())
                ON CONFLICT (trace_id) DO UPDATE SET
                    output = EXCLUDED.output,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    total_tokens = EXCLUDED.total_tokens,
                    total_cost = GREATEST(
                        COALESCE(obs_traces.total_cost, 0),
                        COALESCE(EXCLUDED.total_cost, 0)
                    ),
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
                trace.tags, trace.total_tokens, getattr(trace, "total_cost", 0),
                trace.duration_ms,
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

            # update_total_cost 可能在本 INSERT 提交前执行而 UPDATE 0 行。
            # 与 metadata 使用同一 pending 补丁模式，在当前事务提交前补写真实成本；
            # 重放同样只增不减，防止较低补丁回退 UPSERT 刚落库的较高值。
            with _pending_total_cost_lock:
                pending_total_cost = _pending_total_cost_updates.pop(trace.trace_id, None)
            if pending_total_cost is not None:
                cur.execute(
                    "UPDATE obs_traces "
                    "SET total_cost = GREATEST(COALESCE(total_cost, 0), %s), "
                    "updated_at = NOW() WHERE trace_id = %s",
                    (pending_total_cost, trace.trace_id),
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


def update_total_cost(trace_id: str, total_cost: float):
    """
    回填 obs_traces.total_cost（观测成本闭环）。

    session_record.save() 在算出真实积分成本（credit_cost）后调用。与
    update_user_message_id 一样覆盖 trace_persist worker 先后两种时序：
    - worker 未处理：调用方已先把 total_cost 写入内存 trace，worker UPSERT 携带该值
    - worker 已处理：本函数 UPDATE 已落库行补救
    - worker 事务在途（UPDATE 0 行）：登记 pending 补丁，worker 在提交前补写

    obs_traces 是技术诊断数据，total_cost 不是计费权威，最终金额以
    billing / chat_records 链路为准。

    UPDATE 对现值与传入值取大（COALESCE 兼容历史可空行）：迟到的较低值
    或 0 不回退已落库真实成本；如需下调历史观测成本应走显式修正脚本。

    best-effort：失败只记 warning 并登记 pending 补丁，不影响对话主流程
    （最坏情况 total_cost 保持 0，观测口径降级，属可接受）。
    """
    try:
        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:
            cur.execute(
                "UPDATE obs_traces "
                "SET total_cost = GREATEST(COALESCE(total_cost, 0), %s), "
                "updated_at = NOW() WHERE trace_id = %s",
                (total_cost, trace_id),
            )
            if cur.rowcount == 0:
                _remember_pending_total_cost(trace_id, total_cost)
            cur.commit()
    except Exception as e:
        _remember_pending_total_cost(trace_id, total_cost)
        logger.warning(
            f"update_total_cost failed (trace_id={trace_id}): {e}"
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


def append_recap_span(
    trace_id: str,
    name: str,
    span_type: str = 'generation',
    input: str = '',
    output: str = '',
    model: str = '',
    provider: str = '',
    usage: dict = None,
    start_time: float = None,
    end_time: float = None,
    success: bool = True,
    metadata: dict = None,
) -> None:
    """recap 等进程外后台任务向既有 trace 追加 span（观测旁路，best-effort）。

    recap 在独立 background 进程执行，拿不到 TraceCollector 对象，无法走
    schedule_persist 常规通路；改为直接 INSERT obs_spans，trace_id 复用
    主对话当轮 trace。字段与 _do_persist 的 span INSERT 保持对齐。
    失败只记日志，不影响 recap 业务。
    """
    if not trace_id or not name:
        return
    import uuid
    import time as _time

    try:
        start_ts = start_time if start_time is not None else _time.time()
        end_ts = end_time if end_time is not None else _time.time()
        duration_ms = max(0, int((end_ts - start_ts) * 1000))
        usage = usage or {}
        span_meta = {"success": success}
        if provider:
            span_meta["provider"] = provider
        span_meta["recap"] = True
        if metadata:
            span_meta.update(metadata)

        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:
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
                f"sp_{uuid.uuid4().hex[:16]}", trace_id, span_type, name,
                input, output,
                json.dumps(span_meta, ensure_ascii=False),
                model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                start_ts, end_ts,
                duration_ms,
                'completed' if success else 'failed',
            ))
            cur.commit()
    except Exception as e:
        logger.warning(f"append_recap_span failed (trace_id={trace_id}, name={name}): {e}")


def append_recap_summary(trace_id: str, metadata: dict, total_cost: float = 0.0) -> None:
    """recap 任务结束时向 obs_traces 合并任务摘要 metadata 并累加观测成本。

    metadata 按 JSONB merge 写入（如 metadata.recap = {...}）；total_cost 与
    update_total_cost 同语义取 GREATEST，不回退已有值。obs_traces 主行尚未
    落库的窄窗口（rowcount=0）只记 warning，不做补丁重放——recap 执行通常
    在 trace 落库后数十秒，窗口极窄。失败不影响 recap 业务。
    """
    if not trace_id:
        return
    try:
        from src.db.database import get_logs_connection
        with get_logs_connection() as cur:
            cur.execute(
                "UPDATE obs_traces SET "
                "metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb, "
                "total_cost = GREATEST(COALESCE(total_cost, 0), %s), "
                "updated_at = NOW() WHERE trace_id = %s",
                (json.dumps(metadata, ensure_ascii=False), total_cost or 0.0, trace_id),
            )
            if cur.rowcount == 0:
                logger.warning(
                    f"append_recap_summary: obs_traces 行不存在 "
                    f"(trace_id={trace_id})，摘要未写入"
                )
            cur.commit()
    except Exception as e:
        logger.warning(f"append_recap_summary failed (trace_id={trace_id}): {e}")
