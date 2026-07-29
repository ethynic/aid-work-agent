"""
每日工作成果复盘任务（层2）

扫描"当日有对话但无文件型成果"的会话，用 DEEPSEEK_REPORT_MODEL_CODE 小模型
分析会话内容，提取 action / decision / other 类成果（source=scheduled_review）。

设计文档：docs/system/work-outcome-record-design.md §6

性能与成本：
- 并发限流 5（asyncio.Semaphore）
- 单会话超时 30s（asyncio.wait_for）
- 跳过消息数 <2 的会话
- 失败的会话只记日志，不阻塞整批任务

复盘任务执行日志写入 log/temp/work_outcome_review.log（按 backend_dev.md tlog 规范）。
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.core.temp_logger import tlog
from src.db.database import get_db_connection
from src.reports.summarizer import get_report_model
from src.reports.work_outcome_db import WorkOutcomeDB


# ============== 常量 ==============

# 单会话复盘超时时间
_SESSION_TIMEOUT_SEC = 30
# 并发限流
_MAX_CONCURRENCY = 5
# 单会话最大消息数（避免 token 超限）
_MAX_MESSAGES_PER_SESSION = 50
# 单条消息截断长度（字符）
_MAX_MESSAGE_CHARS = 500
# 置信度阈值（小于此值不写入 DB）
_MIN_CONFIDENCE = 0.6

# 复盘提示词文件路径
_PROMPT_FILE = Path(__file__).parent / "prompts" / "work_outcome_review.md"


# ============== 数据结构 ==============

class SessionInfo:
    """复盘任务的会话上下文"""

    def __init__(
        self,
        session_id: str,
        tenant_id: str,
        user_id: Optional[str],
        subagent_id: Optional[str],
        channel: Optional[str],
    ):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.subagent_id = subagent_id
        self.channel = channel

    def __repr__(self) -> str:
        return (
            f"SessionInfo(session_id={self.session_id}, tenant={self.tenant_id}, "
            f"user={self.user_id}, subagent={self.subagent_id}, channel={self.channel})"
        )


# ============== 主入口 ==============

async def run_daily_review(target_date: Optional[date] = None) -> str:
    """每日工作成果复盘主入口

    Args:
        target_date: 复盘日期，默认昨天（便于 02:30 跑时覆盖前一自然日）

    Returns:
        review_batch_id: 本次复盘批次 ID（rb_yyyymmdd_xxxxxxxx）
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    batch_id = f"rb_{target_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
    logger.info(
        f"工作成果复盘开始: batch_id={batch_id}, date={target_date}"
    )
    tlog(
        "work_outcome_review",
        "复盘任务启动: batch_id={bid}, date={d}",
        bid=batch_id,
        d=target_date.isoformat(),
    )

    # 1. 扫描当日所有有对话的会话
    sessions = await _list_active_sessions_on_date(target_date)
    tlog(
        "work_outcome_review",
        "扫描到 {n} 个有对话的会话",
        n=len(sessions),
    )

    # 2. 过滤掉已经产生 file 型 cp_realtime 成果的会话
    # 注：_has_file_outcome 内部走同步 DB 查询，必须用 to_thread 包裹，
    # 否则会阻塞事件循环（API 入口 run_review_manually 在主事件循环中调用本函数）。
    candidate_sessions: List[SessionInfo] = []
    for s in sessions:
        has_file = await asyncio.to_thread(_has_file_outcome, s.session_id)
        if not has_file:
            candidate_sessions.append(s)
    tlog(
        "work_outcome_review",
        "过滤后候选会话数={n}（已跳过 {skip} 个有文件型成果的会话）",
        n=len(candidate_sessions),
        skip=len(sessions) - len(candidate_sessions),
    )

    # 3. 并发调小模型分析
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    total_outcomes = 0
    failed_sessions = 0

    async def _process_one(session: SessionInfo) -> int:
        nonlocal total_outcomes, failed_sessions
        async with semaphore:
            try:
                outcomes = await asyncio.wait_for(
                    _review_session_with_llm(session, target_date),
                    timeout=_SESSION_TIMEOUT_SEC,
                )
                for outcome in outcomes:
                    try:
                        # DB 写入是同步操作，用 to_thread 包裹避免阻塞事件循环
                        # （backend_dev.md 假异步规范）
                        await asyncio.to_thread(
                            WorkOutcomeDB.create,
                            tenant_id=session.tenant_id,
                            user_id=session.user_id or "",
                            subagent_id=session.subagent_id,
                            session_id=session.session_id,
                            channel=session.channel,
                            summary=outcome["summary"],
                            outcome_type=outcome.get("outcome_type", "other"),
                            metadata=outcome.get("metadata") or {},
                            source="scheduled_review",
                            chat_record_id=outcome.get("chat_record_id"),
                            review_batch_id=batch_id,
                            review_confidence=outcome.get("confidence", 0.5),
                        )
                        total_outcomes += 1
                    except Exception as e:
                        logger.error(
                            f"工作成果复盘写入失败: session={session.session_id}, "
                            f"outcome={outcome.get('summary', '')[:50]}, error={e}",
                            exc_info=True,
                        )
                return len(outcomes)
            except asyncio.TimeoutError:
                failed_sessions += 1
                logger.warning(
                    f"工作成果复盘超时({_SESSION_TIMEOUT_SEC}s): "
                    f"session={session.session_id}"
                )
                tlog(
                    "work_outcome_review",
                    "会话复盘超时: session={sid}",
                    sid=session.session_id,
                    level="WARNING",
                )
                return 0
            except Exception as e:
                failed_sessions += 1
                logger.error(
                    f"工作成果复盘失败: session={session.session_id}, {e}",
                    exc_info=True,
                )
                tlog(
                    "work_outcome_review",
                    "会话复盘失败: session={sid}, err={e}",
                    sid=session.session_id,
                    e=str(e)[:200],
                    level="ERROR",
                )
                return 0

    # 全部并发执行
    await asyncio.gather(*[_process_one(s) for s in candidate_sessions])

    logger.info(
        f"工作成果复盘完成: batch_id={batch_id}, "
        f"候选={len(candidate_sessions)}, 产出={total_outcomes}, 失败={failed_sessions}"
    )
    tlog(
        "work_outcome_review",
        "复盘任务完成: batch_id={bid}, 候选={cand}, 产出={out}, 失败={fail}",
        bid=batch_id,
        cand=len(candidate_sessions),
        out=total_outcomes,
        fail=failed_sessions,
    )
    return batch_id


# ============== 内部工具 ==============

async def _list_active_sessions_on_date(target_date: date) -> List[SessionInfo]:
    """列出当日有对话的会话

    从 chat_records 表反查当日有消息的 session_id（web 端），
    从 channel_messages 表反查当日有消息的 session_id（渠道端），
    关联 chat_sessions / channel_sessions 拿到 tenant_id / user_id / subagent_id / channel。

    遵循 database_dev.md 消息表分离规则：web 走 chat_sessions/chat_messages，
    渠道走 channel_sessions/channel_messages。
    """
    next_day = target_date + timedelta(days=1)

    def _query_sync() -> List[SessionInfo]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # web 端：chat_records -> chat_sessions
            cursor.execute(
                """
                SELECT DISTINCT s.session_id, s.tenant_id, s.user_id, s.subagent_id
                FROM chat_records r
                JOIN chat_sessions s ON r.session_id = s.session_id
                WHERE r.created_at >= %s AND r.created_at < %s
                  AND s.tenant_id IS NOT NULL
                """,
                (target_date, next_day),
            )
            sessions: List[SessionInfo] = []
            for row in cursor.fetchall():
                sessions.append(SessionInfo(
                    session_id=row["session_id"],
                    tenant_id=row["tenant_id"],
                    user_id=row.get("user_id"),
                    subagent_id=row.get("subagent_id"),
                    channel="web",
                ))

            # 渠道端：channel_messages -> channel_sessions
            cursor.execute(
                """
                SELECT DISTINCT s.session_id, s.tenant_id, s.user_id,
                                 s.subagent_id, s.channel_type
                FROM channel_messages m
                JOIN channel_sessions s ON m.session_id = s.session_id
                WHERE m.created_at >= %s AND m.created_at < %s
                  AND s.tenant_id IS NOT NULL AND s.tenant_id != ''
                """,
                (target_date, next_day),
            )
            for row in cursor.fetchall():
                sessions.append(SessionInfo(
                    session_id=row["session_id"],
                    tenant_id=row["tenant_id"],
                    user_id=row.get("user_id"),
                    subagent_id=row.get("subagent_id"),
                    channel=row.get("channel_type"),
                ))

            return sessions

    return await asyncio.to_thread(_query_sync)


def _has_file_outcome(session_id: str) -> bool:
    """检查会话是否已经产生过文件型工作成果（cp_realtime 记录）

    有文件型成果的会话不再复盘，避免重复提取。
    注意：仍可能复盘出 action/decision 类成果（如先修改订单、再生成文件）。
    """
    return WorkOutcomeDB.exists_by_session_and_type(
        session_id=session_id, outcome_type="file"
    )


async def _review_session_with_llm(
    session: SessionInfo, target_date: date
) -> List[Dict[str, Any]]:
    """用小模型分析会话内容，提取非文件型工作成果

    Returns:
        成果列表，每个元素形如：
        {
            "summary": str,
            "outcome_type": "action"|"decision"|"other",
            "metadata": dict,
            "chat_record_id": int|None,
            "confidence": float,
        }
        已过滤掉 confidence < _MIN_CONFIDENCE 的结果。
    """
    # 1. 拉取会话消息（仅当日部分）
    messages = await _load_session_messages(session.session_id, target_date)
    if len(messages) < 2:
        # 单条消息的会话不复盘
        return []

    # 2. 构造小模型 prompt
    system_prompt = _load_review_prompt()
    user_prompt = _format_session_for_review(messages, session)

    # 3. 调用 DEEPSEEK 小模型（非流式，JSON 输出）
    from src.llm.gateway import llm_gateway
    report_model = get_report_model()
    try:
        result = await llm_gateway.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=report_model,
            temperature=0.1,  # 低温度保证稳定
            max_tokens=2048,
        )
    except Exception as e:
        logger.error(
            f"复盘小模型调用失败: session={session.session_id}, model={report_model}, "
            f"error={e}",
            exc_info=True,
        )
        return []

    content = result.get("content", "") or ""
    outcomes = _parse_review_response(content)

    # 4. 过滤低置信度结果
    filtered = [o for o in outcomes if o.get("confidence", 0) >= _MIN_CONFIDENCE]
    if len(filtered) < len(outcomes):
        logger.debug(
            f"复盘会话 {session.session_id}: 共 {len(outcomes)} 条成果，"
            f"过滤后 {len(filtered)} 条（confidence >= {_MIN_CONFIDENCE}）"
        )
    return filtered


async def _load_session_messages(
    session_id: str, target_date: date
) -> List[Dict[str, Any]]:
    """加载会话当日消息（含 id 用于 chat_record_id 溯源）

    遵循消息表分离规则：web 端查 chat_records（含 user_message + assistant_message），
    渠道端查 channel_messages。
    """
    next_day = target_date + timedelta(days=1)

    def _query_sync() -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 先查 chat_records（web 端）
            cursor.execute(
                """
                SELECT id, user_message, assistant_message, created_at
                FROM chat_records
                WHERE session_id = %s
                  AND created_at >= %s AND created_at < %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (session_id, target_date, next_day, _MAX_MESSAGES_PER_SESSION),
            )
            rows = cursor.fetchall()
            if rows:
                return [
                    {
                        "id": r["id"],
                        "user_message": r.get("user_message") or "",
                        "assistant_message": r.get("assistant_message") or "",
                        "created_at": r.get("created_at"),
                    }
                    for r in rows
                ]

            # 渠道端：channel_messages
            cursor.execute(
                """
                SELECT id, role, content, created_at
                FROM channel_messages
                WHERE session_id = %s
                  AND created_at >= %s AND created_at < %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (session_id, target_date, next_day, _MAX_MESSAGES_PER_SESSION),
            )
            rows = cursor.fetchall()
            # 把 channel_messages 整理成类似 chat_records 的结构
            # 每对 user+assistant 合成一条 "对话"
            paired: List[Dict[str, Any]] = []
            current_user_msg: Optional[Dict[str, Any]] = None
            for r in rows:
                role = r.get("role")
                content = r.get("content") or ""
                if role == "user":
                    if current_user_msg:
                        # 上一个 user 还没等到 assistant，先入队
                        paired.append(current_user_msg)
                    current_user_msg = {
                        "id": r["id"],
                        "user_message": content,
                        "assistant_message": "",
                        "created_at": r.get("created_at"),
                    }
                elif role == "assistant" and current_user_msg is not None:
                    current_user_msg["assistant_message"] = content
                    paired.append(current_user_msg)
                    current_user_msg = None
            if current_user_msg:
                paired.append(current_user_msg)
            return paired

    return await asyncio.to_thread(_query_sync)


def _load_review_prompt() -> str:
    """加载复盘系统提示词（从 prompts/work_outcome_review.md）"""
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(
            f"复盘提示词文件加载失败，使用内置默认: {e}"
        )
        return (
            "你是工作成果复盘助手。请分析会话内容，提取非文件型重要成果"
            "（action/decision/other），输出 JSON 格式：\n"
            '{"outcomes": [{"summary": "...", "outcome_type": "action", '
            '"metadata": {}, "chat_record_id": null, "confidence": 0.8}]}\n'
            "如无成果，返回 {\"outcomes\": []}。"
        )


def _format_session_for_review(
    messages: List[Dict[str, Any]], session: SessionInfo
) -> str:
    """把会话消息格式化为小模型输入"""
    lines: List[str] = []
    lines.append(f"会话ID: {session.session_id}")
    lines.append(f"子智能体: {session.subagent_id or '(主智能体)'}")
    lines.append(f"渠道: {session.channel or 'unknown'}")
    lines.append("")
    lines.append("【对话记录】")
    for i, m in enumerate(messages, start=1):
        user_msg = (m.get("user_message") or "").strip()[:_MAX_MESSAGE_CHARS]
        asst_msg = (m.get("assistant_message") or "").strip()[:_MAX_MESSAGE_CHARS]
        msg_id = m.get("id")
        lines.append(f"{i}. [record_id={msg_id}]")
        if user_msg:
            lines.append(f"  用户: {user_msg}")
        if asst_msg:
            lines.append(f"  助手: {asst_msg}")
    return "\n".join(lines)


def _parse_review_response(content: str) -> List[Dict[str, Any]]:
    """解析小模型返回的 JSON

    容错：去除可能的 Markdown 代码块标记、提取 JSON 部分。
    """
    if not content:
        return []
    text = content.strip()
    # 去除 ```json ... ``` 包裹
    if text.startswith("```"):
        # 去掉第一行 ```json 或 ```
        lines = text.split("\n")
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    # 尝试找到 JSON 对象的边界
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.warning(f"复盘响应无 JSON 对象: {content[:200]}")
        return []
    json_str = text[start:end + 1]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(
            f"复盘响应 JSON 解析失败: {e}, content={content[:200]}"
        )
        return []
    outcomes = data.get("outcomes") or []
    if not isinstance(outcomes, list):
        return []
    # 字段校验：summary 必填，outcome_type 限定值
    valid_types = {"action", "decision", "other"}
    result: List[Dict[str, Any]] = []
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        summary = (o.get("summary") or "").strip()
        if not summary:
            continue
        otype = o.get("outcome_type") or "other"
        if otype not in valid_types:
            otype = "other"
        confidence = o.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else 0.5
        except (TypeError, ValueError):
            confidence = 0.5
        result.append({
            "summary": summary,
            "outcome_type": otype,
            "metadata": o.get("metadata") or {},
            "chat_record_id": o.get("chat_record_id"),
            "confidence": confidence,
        })
    return result
