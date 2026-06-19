"""
可观测性追踪查看 API

提供追踪数据的浏览器查看接口（替代 SSH 翻 JSONL 日志）。
- GET /api/monitor/sessions - 有追踪数据的会话列表
- GET /api/monitor/sessions/{session_id}/traces - 某会话下所有 trace
- GET /api/monitor/traces - 全局 trace 列表
- GET /api/monitor/traces/{trace_id} - 追踪详情（含 span 列表）
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.saas.permissions.checker import is_platform_admin


router = APIRouter(prefix="/api/monitor", tags=["可观测性追踪"])


# ============== 响应模型 ==============

class SessionSummary(BaseModel):
    session_id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_count: int = 0
    total_tokens: int = 0
    error_count: int = 0
    last_trace_at: Optional[str] = None
    first_input: Optional[str] = None
    first_content: Optional[str] = None
    source_type: Optional[str] = None
    subagent_id: Optional[str] = None


class TraceSummary(BaseModel):
    trace_id: str
    session_id: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None
    status: str = "running"
    duration_ms: int = 0
    total_tokens: int = 0
    tool_calls_count: int = 0
    agent_iterations: int = 0
    tags: List[str] = []
    source_type: str = "chat"
    created_at: Optional[str] = None


class SpanDetail(BaseModel):
    span_id: str
    span_type: str = "span"
    name: str
    input: Optional[str] = None
    output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    status: str = "running"
    start_time: Optional[str] = None


class TraceDetail(BaseModel):
    trace_id: str
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    subagent_id: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None
    status: str = "running"
    duration_ms: int = 0
    total_tokens: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    agent_iterations: int = 0
    tool_calls_count: int = 0
    tags: List[str] = []
    source_type: str = "chat"
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    channel_info: Optional[Dict[str, Any]] = None


class SessionListResponse(BaseModel):
    success: bool
    data: List[SessionSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class TraceListResponse(BaseModel):
    success: bool
    data: List[TraceSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class TraceDetailResponse(BaseModel):
    success: bool
    trace: Optional[TraceDetail] = None
    spans: List[SpanDetail] = []
    message: Optional[str] = None


# ============== 工具函数 ==============

def _get_logs_conn():
    """获取追踪库连接"""
    from src.db.database import get_logs_connection
    return get_logs_connection()


def _check_admin(user):
    """检查平台管理员权限"""
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="需要平台管理员权限")


def _format_ts(val) -> Optional[str]:
    """格式化时间戳"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


# ============== API 端点 ==============

@router.get("/sessions", response_model=SessionListResponse)
async def list_traced_sessions(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tenant_id: Optional[str] = Query(None),
    time_range: Optional[str] = Query(None, description="时间范围：1h/24h/7d/30d"),
    status: Optional[str] = Query(None, description="状态筛选：completed/failed/cancelled"),
    source_type: Optional[str] = Query(None, description="来源筛选：chat/wecom/wecom_kf/dingtalk/feishu"),
    search: Optional[str] = Query(None, description="搜索会话ID"),
):
    """获取有追踪数据的会话列表"""
    user = get_current_user(request)
    _check_admin(user)

    try:
        with _get_logs_conn() as cur:

            where_clauses = []
            params = []

            if tenant_id:
                where_clauses.append("tenant_id = %s")
                params.append(tenant_id)

            if status:
                where_clauses.append("status = %s")
                params.append(status)

            if source_type:
                where_clauses.append("source_type = %s")
                params.append(source_type)

            if search:
                where_clauses.append("session_id LIKE %s")
                params.append(f"%{search}%")

            if time_range:
                delta_map = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days"}
                interval = delta_map.get(time_range)
                if interval:
                    where_clauses.append(f"created_at >= NOW() - INTERVAL '{interval}'")

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            # 计算总数
            cur.execute(f"SELECT COUNT(DISTINCT session_id) as cnt FROM obs_traces {where_sql}", params)
            total = cur.fetchone()["cnt"]

            # 分页查询
            offset = (page - 1) * page_size
            cur.execute(f"""
                SELECT
                    session_id,
                    tenant_id,
                    user_id,
                    COUNT(*) as trace_count,
                    SUM(total_tokens) as total_tokens,
                    COUNT(*) FILTER (WHERE status = 'failed') as error_count,
                    MAX(created_at) as last_trace_at,
                    (array_agg(input ORDER BY created_at ASC))[1] as first_input,
                    MIN(source_type) as source_type,
                    MIN(subagent_id) as subagent_id
                FROM obs_traces
                {where_sql}
                GROUP BY session_id, tenant_id, user_id
                ORDER BY MAX(created_at) DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cur.fetchall()
            sessions = [
                SessionSummary(
                    session_id=r["session_id"],
                    tenant_id=r.get("tenant_id"),
                    user_id=r.get("user_id"),
                    trace_count=r["trace_count"] or 0,
                    total_tokens=r["total_tokens"] or 0,
                    error_count=r["error_count"] or 0,
                    last_trace_at=_format_ts(r.get("last_trace_at")),
                    first_input=(r.get("first_input") or "")[:200],
                    source_type=r.get("source_type"),
                    subagent_id=r.get("subagent_id"),
                )
                for r in rows
            ]

            # 从 channel_messages 补充首次消息内容（ASR 语音识别文字）
            if sessions:
                session_ids = [s.session_id for s in sessions]
                placeholders = ",".join(["%s"] * len(session_ids))
                from src.db.database import get_db_connection
                with get_db_connection() as cur:
                    cur.execute(f"""
                        SELECT session_id, content
                        FROM (
                            SELECT session_id, content,
                                   ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY created_at ASC) as rn
                            FROM channel_messages
                            WHERE session_id IN ({placeholders})
                              AND role = 'user'
                        ) t WHERE rn = 1
                    """, session_ids)
                    content_map = {r["session_id"]: (r["content"] or "")[:200] for r in cur.fetchall()}
                for s in sessions:
                    s.first_content = content_map.get(s.session_id)
                    if not s.first_content:
                        s.first_content = s.first_input

            total_pages = (total + page_size - 1) // page_size

            return SessionListResponse(
                success=True,
                data=sessions,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )
    except RuntimeError as e:
        if "追踪库" in str(e):
            return SessionListResponse(success=True, data=[], total=0, page=1, page_size=page_size, total_pages=0)
        raise
    except Exception as e:
        logger.error(f"Failed to list traced sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/traces")
async def list_session_traces(
    session_id: str,
    request: Request,
):
    """获取某会话下的所有 trace"""
    user = get_current_user(request)
    _check_admin(user)

    try:
        with _get_logs_conn() as cur:
            cur.execute("""
                SELECT
                    trace_id, session_id, input, output, status,
                    duration_ms, total_tokens, tool_calls_count,
                    agent_iterations, tags, source_type, created_at
                FROM obs_traces
                WHERE session_id = %s
                ORDER BY created_at DESC
            """, (session_id,))

            rows = cur.fetchall()
            traces = [
                TraceSummary(
                    trace_id=r["trace_id"],
                    session_id=r.get("session_id"),
                    input=(r.get("input") or "")[:500],
                    output=(r.get("output") or "")[:500],
                    status=r.get("status", "running"),
                    duration_ms=r.get("duration_ms", 0),
                    total_tokens=r.get("total_tokens", 0),
                    tool_calls_count=r.get("tool_calls_count", 0),
                    agent_iterations=r.get("agent_iterations", 0),
                    tags=r.get("tags") or [],
                    source_type=r.get("source_type", "chat"),
                    created_at=_format_ts(r.get("created_at")),
                )
                for r in rows
            ]

            return {"success": True, "traces": traces}
    except RuntimeError as e:
        if "追踪库" in str(e):
            return {"success": True, "traces": []}
        raise
    except Exception as e:
        logger.error(f"Failed to list session traces: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/traces", response_model=TraceListResponse)
async def list_traces(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    time_range: Optional[str] = Query(None),
):
    """全局 trace 列表"""
    user = get_current_user(request)
    _check_admin(user)

    try:
        with _get_logs_conn() as cur:

            where_clauses = []
            params = []

            if status:
                where_clauses.append("status = %s")
                params.append(status)
            if tenant_id:
                where_clauses.append("tenant_id = %s")
                params.append(tenant_id)
            if time_range:
                delta_map = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days"}
                interval = delta_map.get(time_range)
                if interval:
                    where_clauses.append(f"created_at >= NOW() - INTERVAL '{interval}'")

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            cur.execute(f"SELECT COUNT(*) as cnt FROM obs_traces {where_sql}", params)
            total = cur.fetchone()["cnt"]

            offset = (page - 1) * page_size
            cur.execute(f"""
                SELECT
                    trace_id, session_id, input, output, status,
                    duration_ms, total_tokens, tool_calls_count,
                    agent_iterations, tags, source_type, created_at
                FROM obs_traces
                {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cur.fetchall()
            traces = [
                TraceSummary(
                    trace_id=r["trace_id"],
                    session_id=r.get("session_id"),
                    input=(r.get("input") or "")[:500],
                    output=(r.get("output") or "")[:500],
                    status=r.get("status", "running"),
                    duration_ms=r.get("duration_ms", 0),
                    total_tokens=r.get("total_tokens", 0),
                    tool_calls_count=r.get("tool_calls_count", 0),
                    agent_iterations=r.get("agent_iterations", 0),
                    tags=r.get("tags") or [],
                    source_type=r.get("source_type", "chat"),
                    created_at=_format_ts(r.get("created_at")),
                )
                for r in rows
            ]

            total_pages = (total + page_size - 1) // page_size

            return TraceListResponse(
                success=True,
                data=traces,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )
    except RuntimeError as e:
        if "追踪库" in str(e):
            return TraceListResponse(success=True, data=[], total=0, page=1, page_size=page_size, total_pages=0)
        raise
    except Exception as e:
        logger.error(f"Failed to list traces: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/traces/{trace_id}", response_model=TraceDetailResponse)
async def get_trace_detail(
    trace_id: str,
    request: Request,
):
    """获取追踪详情（含完整 span 列表）"""
    user = get_current_user(request)
    _check_admin(user)

    try:
        with _get_logs_conn() as cur:

            # 查询 trace
            cur.execute("""
                SELECT
                    trace_id, session_id, tenant_id, user_id, subagent_id,
                    input, output, status, duration_ms, total_tokens,
                    metadata, agent_iterations, tool_calls_count, tags,
                    source_type, error_message, created_at
                FROM obs_traces
                WHERE trace_id = %s
            """, (trace_id,))

            trace_row = cur.fetchone()
            if not trace_row:
                return TraceDetailResponse(success=False, message="Trace not found")

            metadata = trace_row.get("metadata") or {}
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)

            trace = TraceDetail(
                trace_id=trace_row["trace_id"],
                session_id=trace_row.get("session_id"),
                tenant_id=trace_row.get("tenant_id"),
                user_id=trace_row.get("user_id"),
                subagent_id=trace_row.get("subagent_id"),
                input=trace_row.get("input"),
                output=trace_row.get("output"),
                status=trace_row.get("status", "running"),
                duration_ms=trace_row.get("duration_ms", 0),
                total_tokens=trace_row.get("total_tokens", 0),
                model=metadata.get("model"),
                provider=metadata.get("provider"),
                agent_iterations=trace_row.get("agent_iterations", 0),
                tool_calls_count=trace_row.get("tool_calls_count", 0),
                tags=trace_row.get("tags") or [],
                source_type=trace_row.get("source_type", "chat"),
                error_message=trace_row.get("error_message"),
                created_at=_format_ts(trace_row.get("created_at")),
            )

            # 渠道来源时，跨库补充 channel_sessions 业务信息（失败不影响 trace 返回）
            if trace.source_type in ('wecom', 'wecom_kf', 'dingtalk', 'feishu'):
                try:
                    from src.db.database import get_db_connection
                    with get_db_connection() as biz_cur:
                        biz_cur.execute("""
                            SELECT title, username, channel_type, channel_user_id, channel_chat_id
                            FROM channel_sessions WHERE session_id = %s
                        """, (trace.session_id,))
                        ch = biz_cur.fetchone()
                        if ch:
                            trace.channel_info = {
                                "title": ch.get("title"),
                                "username": ch.get("username"),
                                "channel_type": ch.get("channel_type"),
                                "channel_user_id": ch.get("channel_user_id"),
                                "channel_chat_id": ch.get("channel_chat_id"),
                            }
                except Exception as e:
                    logger.warning(f"Failed to load channel info for session {trace.session_id}: {e}")

            # 查询 spans
            cur.execute("""
                SELECT
                    span_id, span_type, name, input, output, metadata,
                    model, prompt_tokens, completion_tokens,
                    duration_ms, status, start_time
                FROM obs_spans
                WHERE trace_id = %s
                ORDER BY start_time ASC
            """, (trace_id,))

            span_rows = cur.fetchall()
            spans = []
            for r in span_rows:
                span_meta = r.get("metadata") or {}
                if isinstance(span_meta, str):
                    import json
                    span_meta = json.loads(span_meta)

                spans.append(SpanDetail(
                    span_id=r["span_id"],
                    span_type=r.get("span_type", "span"),
                    name=r.get("name", ""),
                    input=r.get("input"),
                    output=r.get("output"),
                    metadata=span_meta,
                    model=r.get("model"),
                    prompt_tokens=r.get("prompt_tokens", 0),
                    completion_tokens=r.get("completion_tokens", 0),
                    duration_ms=r.get("duration_ms", 0),
                    status=r.get("status", "running"),
                    start_time=_format_ts(r.get("start_time")),
                ))

            return TraceDetailResponse(success=True, trace=trace, spans=spans)
    except RuntimeError as e:
        if "追踪库" in str(e):
            return TraceDetailResponse(success=False, message="追踪库未配置")
        raise
    except Exception as e:
        logger.error(f"Failed to get trace detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))
