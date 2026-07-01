"""上下文压缩管理后台 API（Phase 7 §7.3）

路由：
- GET    /api/observability/context-summaries                 列表（支持 session_id 过滤）
- GET    /api/observability/context-summaries/stats           指标统计
- GET    /api/observability/context-summaries/{summary_id}    详情（含被压缩原消息）
- POST   /api/observability/context-summaries/{summary_id}/rollback   回滚
- POST   /api/observability/context-summaries/sessions/{session_id}/compact?source_type=chat   手动压缩

权限：require_admin（平台管理员可看所有租户；租户管理员只能看自己租户）。
"""

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from src.core.compression_metrics import get_compression_metrics
from src.db.database import get_db_connection
from src.db.models import ContextSummaryDB
from src.saas.api.tenant_auth import require_admin
from src.saas.models.enums import ContextSummaryStatus


router = APIRouter(
    prefix="/api/observability/context-summaries",
    tags=["上下文压缩管理"],
)


# 敏感信息过滤（与 backend_dev.md SENSITIVE_PATTERNS 一致，用于错误响应 debug 字段）
SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
    r'secret["\s:=]+\S+',
]


def _sanitize_error(msg: str) -> str:
    if not msg:
        return msg
    for pat in SENSITIVE_PATTERNS:
        msg = re.sub(
            pat,
            lambda m: re.split(r'[:=]', m.group(0), maxsplit=1)[0] + '=***',
            msg,
            flags=re.IGNORECASE,
        )
    return msg


def _err_response(msg: str, exc: Exception) -> Dict[str, Any]:
    return {"success": False, "error": "操作失败，请稍后重试", "debug": _sanitize_error(f"{msg}: {exc}")}


def _resolve_tenant_filter(request: Request, admin: Dict[str, Any]) -> Optional[str]:
    """根据管理员角色 + X-Tenant-Id header 解析租户过滤值。

    优先级（v3.2.1 P0-2）：
    1. X-Tenant-Id header（平台管理员代管指定租户）：返回 header 值
    2. 平台管理员（role=platform_admin）且无 X-Tenant-Id：返回 None（全租户视图）
    3. 租户管理员（role=tenant_admin）：返回 admin['tenant_id']（自身租户）

    Args:
        request: FastAPI Request（用于读 header）
        admin: require_admin 返回的管理员字典

    Returns:
        租户 ID（限定单租户视图）或 None（全租户视图，仅平台管理员无代管时）
    """
    # 代管场景：X-Tenant-Id 优先（平台管理员选了具体租户后只看该租户数据）
    header_tenant = request.headers.get("X-Tenant-Id") if request is not None else None
    if header_tenant:
        return header_tenant
    # 平台管理员无代管：全租户视图
    if admin.get("role") == "platform_admin":
        return None
    # 租户管理员：自身 tenant_id
    return admin.get("tenant_id")


# ============== 列表 ==============

@router.get("")
async def list_summaries(
    request: Request,
    session_id: Optional[str] = Query(None, description="按 session_id 过滤"),
    source_type: Optional[str] = Query(None, description="按来源过滤"),
    limit: int = Query(50, ge=1, le=500, description="返回条数上限"),
):
    """列出最近的压缩记录。

    返回字段（不含 summary_text，详情接口才返回）：
    summary_id, session_id, source_type, status, created_at, summary_version,
    compression_ratio, compressed_message_count, fallback_used, trigger_reason

    PII 提示（v3.2.1 P1-3）：本接口不返回 summary_text，不含 PII；
    同模块详情接口 get_summary_detail 返回 summary_text + 原消息，含 PII，
    仅管理员可访问（已通过 require_admin 强制校验）。
    """
    try:
        admin = require_admin(request)
    except HTTPException:
        raise
    tenant_filter = _resolve_tenant_filter(request, admin)

    placeholder = "%s"
    conditions: List[str] = []
    params: List[Any] = []
    if tenant_filter is not None:
        conditions.append(f"tenant_id = {placeholder}")
        params.append(tenant_filter)
    if session_id:
        conditions.append(f"session_id = {placeholder}")
        params.append(session_id)
    if source_type:
        conditions.append(f"source_type = {placeholder}")
        params.append(source_type)

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = (
        f"SELECT summary_id, session_id, source_type, tenant_id, status, "
        f"       summary_version, compressed_message_count, "
        f"       original_token_count, compressed_token_count, compression_ratio, "
        f"       fallback_used, llm_provider, llm_model, "
        f"       created_at, superseded_at "
        f"FROM chat_context_summaries{where_clause} "
        f"ORDER BY created_at DESC LIMIT {placeholder}"
    )
    params.append(limit)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = [dict(r) for r in cursor.fetchall()]
        # 序列化 datetime
        for r in rows:
            for k in ("created_at", "superseded_at"):
                if r.get(k) is not None:
                    r[k] = r[k].isoformat() if hasattr(r[k], "isoformat") else str(r[k])
        return {"success": True, "items": rows, "total": len(rows)}
    except Exception as e:
        logger.exception("list_summaries failed")
        return _err_response("查询压缩记录失败", e)


# ============== 指标统计 ==============

@router.get("/stats")
async def get_stats(request: Request):
    """返回 CompressionMetrics.get_stats()"""
    try:
        admin = require_admin(request)
    except HTTPException:
        raise
    try:
        stats = get_compression_metrics().get_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.exception("get_stats failed")
        return _err_response("查询指标统计失败", e)


# ============== 详情 ==============

@router.get("/{summary_id}")
async def get_summary_detail(summary_id: str, request: Request):
    """详情：含完整 summary_text + 被压缩原消息列表。

    PII 提示（v3.2.1 P1-3）：summary_text 和被压缩原消息可能含用户 PII
    （用户姓名、联系方式、对话内容等）。本接口仅限管理员（require_admin）访问，
    且按租户隔离过滤（平台管理员需携 X-Tenant-Id 代管目标租户）。
    """
    try:
        admin = require_admin(request)
    except HTTPException:
        raise
    tenant_filter = _resolve_tenant_filter(request, admin)

    try:
        summary = ContextSummaryDB.get_by_id(summary_id, tenant_id=tenant_filter)
        if not summary:
            raise HTTPException(status_code=404, detail="摘要不存在或无权访问")
        # 序列化 datetime
        for k in ("created_at", "superseded_at"):
            if summary.get(k) is not None and hasattr(summary[k], "isoformat"):
                summary[k] = summary[k].isoformat()

        # 拉取被压缩的原消息
        compressed_message_ids: List[int] = summary.get("compressed_message_ids") or []
        messages: List[Dict[str, Any]] = []
        if compressed_message_ids:
            try:
                messages = await _fetch_compressed_messages(
                    summary.get("source_type") or "chat",
                    [int(x) for x in compressed_message_ids if x is not None],
                )
            except Exception as me:
                logger.warning(f"fetch compressed messages failed: {me}")
                messages = []

        return {"success": True, "summary": summary, "messages": messages}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_summary_detail failed")
        return _err_response("查询压缩详情失败", e)


async def _fetch_compressed_messages(source_type: str, msg_ids: List[int]) -> List[Dict[str, Any]]:
    """根据 source_type 拉取被压缩的原消息（含 compacted=true 的，给运维查看）。

    chat → chat_messages；其他 → channel_messages。
    """
    import asyncio
    placeholder = "%s"
    if source_type == "chat":
        sql = (
            f"SELECT id, role, content, tool_calls, metadata, compacted, created_at "
            f"FROM chat_messages WHERE id = ANY({placeholder}::bigint[]) "
            f"ORDER BY id ASC"
        )
    else:
        sql = (
            f"SELECT id, role, content, tool_calls, metadata, compacted, created_at "
            f"FROM channel_messages WHERE id = ANY({placeholder}::bigint[]) "
            f"ORDER BY id ASC"
        )

    def _query():
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (list(msg_ids),))
            return [dict(r) for r in cur.fetchall()]

    rows = await asyncio.to_thread(_query)
    for r in rows:
        for k in ("created_at",):
            if r.get(k) is not None and hasattr(r[k], "isoformat"):
                r[k] = r[k].isoformat()
    return rows


# ============== 回滚 ==============

@router.post("/{summary_id}/rollback")
async def rollback_summary(summary_id: str, request: Request):
    """回滚：把 compacted 标记清除，summary 置 'rolled_back'。

    事务：
    1. UPDATE chat_messages / channel_messages
       SET compacted=false, compacted_by=NULL WHERE compacted_by = summary_id
    2. UPDATE chat_context_summaries SET status='rolled_back' WHERE summary_id = ?

    Args:
        summary_id: 摘要 ID

    注意：回滚后，原 active summary 的位置可能已经被新的 active 替代（status
    从 'active' → 'superseded' → 'rolled_back'）。回滚主要是恢复 messages 的
    compacted 标记，让被压缩的历史消息重新参与 LLM 上下文构建。
    """
    try:
        admin = require_admin(request)
    except HTTPException:
        raise
    tenant_filter = _resolve_tenant_filter(request, admin)

    placeholder = "%s"
    try:
        # 先确认 summary 存在 + 租户隔离
        summary = ContextSummaryDB.get_by_id(summary_id, tenant_id=tenant_filter)
        if not summary:
            raise HTTPException(status_code=404, detail="摘要不存在或无权访问")
        source_type = summary.get("source_type") or "chat"

        affected_messages = 0
        with get_db_connection() as conn:
            cur = conn.cursor()
            try:
                # ① 清除消息的 compacted 标记
                if source_type == "chat":
                    cur.execute(
                        f"UPDATE chat_messages SET compacted = FALSE, compacted_by = NULL "
                        f"WHERE compacted_by = {placeholder}",
                        (summary_id,),
                    )
                else:
                    cur.execute(
                        f"UPDATE channel_messages SET compacted = FALSE, compacted_by = NULL "
                        f"WHERE compacted_by = {placeholder}",
                        (summary_id,),
                    )
                affected_messages = cur.rowcount

                # ② summary 状态 → rolled_back（v3.2.1 P1-2：改用枚举常量）
                cur.execute(
                    f"UPDATE chat_context_summaries SET status = {placeholder} "
                    f"WHERE summary_id = {placeholder}",
                    (ContextSummaryStatus.ROLLED_BACK.value, summary_id),
                )

                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception as re:
                    logger.error(f"rollback_summary rollback failed: {re}")
                raise

        logger.info(
            f"context_summary rolled back: id={summary_id}, "
            f"source={source_type}, restored_msgs={affected_messages}, admin={admin.get('user_id')}"
        )
        return {
            "success": True,
            "summary_id": summary_id,
            "restored_message_count": affected_messages,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rollback_summary failed")
        return _err_response("回滚失败", e)


# ============== 手动压缩 ==============

@router.post("/sessions/{session_id}/compact")
async def manual_compress(
    session_id: str,
    request: Request,
    source_type: str = Query("chat", description="会话来源类型"),
):
    """手动触发压缩（运维用，对应 compress_session(force=True)）。

    v3.2.1 P0-1：租户隔离校验必须在调用 compress_session **之前**完成。
    原实现先执行压缩（写入摘要 + 标记消息 compacted）后才比对 tenant_id，
    一旦越权将留下脏数据。现先通过 service.get_session_tenant_id 解析
    session 归属租户，与当前 admin 的 tenant_filter 比对，不匹配或
    session 不存在直接返回 403，再触发压缩。

    PII 提示：本接口返回内容不含 PII（仅压缩元数据）；但管理后台同模块的
    详情接口（get_summary_detail）会返回完整 summary_text + 被压缩原消息，
    那些内容可能含 PII，仅管理员可访问。
    """
    try:
        admin = require_admin(request)
    except HTTPException:
        raise

    tenant_filter = _resolve_tenant_filter(request, admin)

    try:
        from src.memory.mid_term import get_compression_service

        service = get_compression_service()

        # P0-1：压缩前先校验租户归属，避免跨租户越权压缩
        if tenant_filter is not None:
            session_tenant = await service.get_session_tenant_id(session_id, source_type)
            # session 不存在（None）或归属租户不一致 → 拒绝
            # （session 不存在时 compress_session 本就会返回 None，这里直接 403 更安全）
            if session_tenant is None:
                logger.warning(
                    f"manual_compress session not found: sid={session_id}, "
                    f"source={source_type}, admin_tenant={tenant_filter}"
                )
                raise HTTPException(status_code=403, detail="会话不存在或无权访问")
            if session_tenant != tenant_filter:
                logger.warning(
                    f"manual_compress tenant mismatch: admin_tenant={tenant_filter}, "
                    f"session_tenant={session_tenant}, sid={session_id}"
                )
                raise HTTPException(status_code=403, detail="无权压缩其他租户的会话")

        result = await service.compress_session(session_id, source_type, force=True)

        if result is None:
            return {
                "success": True,
                "result": None,
                "message": "未触发压缩（消息数不足或 COMPRESS 区为空）",
            }
        return {
            "success": True,
            "result": {
                "summary_id": result.summary_id,
                "compressed_message_count": result.compressed_message_count,
                "original_token_count": result.original_token_count,
                "compressed_token_count": result.compressed_token_count,
                "compression_ratio": result.compression_ratio,
                "fallback_used": result.fallback_used,
                "trigger_reason": result.trigger_reason,
                "llm_provider": result.llm_provider,
                "llm_model": result.llm_model,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("manual_compress failed")
        return _err_response("手动压缩失败", e)
