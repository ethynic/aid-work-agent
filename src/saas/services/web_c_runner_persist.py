"""web_c 渠道 Runner 终态落库服务（设计 docs/system/web-c-channel-design.md §4.3 步骤 3）。

终态 assistant 消息（含 downloadableFiles/images 附件投影）与 ask_user 澄清反问
落库 channel_messages。落库逻辑收敛于本模块，三处触发共享同一套幂等实现：
- C 端 SSE 代理收尾 finalize / messages 拉取补写（src/saas/api/web_c.py）
- 租户接待页 messages 接口（src/saas/api/web_c_customers.py）
- background 扫描器 WebCRunnerPersistSweeper（服务端兜底，不依赖访客在线）

背景（2026-11-23 事故）：历史上落库只由访客行为触发——访客在 Runner 完成前
关页且不再回访时，回复（含生成文件）永不落库，C 端与租户接待页均不可见。
所有落库入口均幂等（最终回复按 metadata.runner_id 判重、澄清按 wait_id 判重，
ON CONFLICT 原子拦截），多触发点并发安全。
"""

import asyncio
import json
import uuid
from typing import List, Optional, Tuple

from loguru import logger

from src.channels import ask_render
from src.channels.runner_agent import _clarification_wait
from src.db.database import get_db_connection

# 扫描窗口与节奏：覆盖常规执行时长（分钟级）；更早的遗留缺口由访客回访 /
# 接待页拉取时的会话级补写覆盖
SWEEP_WINDOW_HOURS = 4
SWEEP_INTERVAL_SECONDS = 120
SWEEP_BATCH_LIMIT = 200


def runner_files_to_attachments(row: dict) -> List[dict]:
    """runner 行附件投影（口径同 runner_agent.py downloadableFiles/images）。

    row 兼容两种形态：API public_runner 行（snapshot 键）与 DB 直读行
    （public_snapshot 键）。文件名取 name 或 file_name（snapshot 字段为
    file_name，取错键曾致文件卡片显示裸 file_id）；大小取 size 或 file_size。
    """
    snapshot = row.get("snapshot") or row.get("public_snapshot") or {}
    result = row.get("result") or {}
    attachments: List[dict] = []
    for item in snapshot.get("downloadableFiles") or []:
        if isinstance(item, dict) and item.get("file_id"):
            attachments.append({
                "type": "file",
                "file_id": item["file_id"],
                "name": item.get("name") or item.get("file_name"),
                "mime_type": item.get("mime_type"),
                "size": item.get("size") or item.get("file_size"),
                "download_url": item.get("download_url"),
            })
    images = result.get("images") or snapshot.get("images") or []
    for item in images:
        if isinstance(item, dict) and item.get("file_id"):
            attachments.append({
                "type": "image",
                "file_id": item["file_id"],
                "name": item.get("display_name"),
                "mime_type": item.get("mime_type"),
                "size": item.get("size_bytes"),
                "download_url": item.get("download_url"),
            })
    return attachments


def clarification_from_row(row: dict) -> Tuple[str, str]:
    """从 runner 行提取澄清反问文本与 wait_id（无反问返回空串二元组）。"""
    snapshot = row.get("snapshot") or row.get("public_snapshot") or {}
    wait = _clarification_wait(snapshot.get("waiting")) or {}
    if wait.get("kind") != "clarification":
        return "", ""
    wait_id = wait.get("wait_id") or ""
    form = wait.get("form")
    if isinstance(form, dict) and form.get("questions") and wait_id:
        return ask_render.render_single_message(form, ask_render.source_segment_limit("web_c")), wait_id
    return wait.get("question") or "", wait_id


def has_final_message(session_id: str, runner_id: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM channel_messages WHERE session_id = %s "
            "AND metadata->>'runner_id' = %s AND COALESCE(metadata->>'kind', '') = '' LIMIT 1",
            (session_id, runner_id),
        )
        return cursor.fetchone() is not None


def persist_assistant_message(tenant_id: str, session_id: str, runner_id: str,
                              output: str, attachments: List[dict],
                              kind: str = "", client_request_id: Optional[str] = None) -> None:
    """assistant 消息落库（幂等：唯一部分索引 + ON CONFLICT，并发双写原子拦截）。

    direct_reply 类（runner_id 为空）按 client_request_id 判重——同会话多次
    纠正/重发回复内容各不相同，仅按 (runner_id, kind) 判重会吞掉后续回复。
    """
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    metadata = {"runner_id": runner_id}
    if kind:
        metadata["kind"] = kind
    if client_request_id:
        metadata["client_request_id"] = client_request_id
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO channel_messages
            (message_id, session_id, tenant_id, role, content, message_type, attachments, metadata)
            VALUES (%s, %s, %s, 'assistant', %s, 'text', %s::jsonb, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                message_id, session_id, tenant_id, output or "",
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "UPDATE channel_sessions SET last_message_at = %s, updated_at = %s "
                "WHERE session_id = %s",
                (now, now, session_id),
            )
        conn.commit()
        if not inserted:
            logger.debug(f"[web_c] 终态消息已存在，跳过重复落库 runner={runner_id}")


def persist_clarification_message(tenant_id: str, session_id: str, runner_id: str,
                                  question: str, wait_id: str) -> bool:
    """澄清反问落库（接待页/历史可见，设计 §4.3 步骤 4 的代理层落库半边）。

    幂等：metadata.client_request_id = clarify_{wait_id}，命中
    uq_web_c_messages_client_request 唯一部分索引 + ON CONFLICT 原子拦截；
    先 SELECT 存在检查避免高频轮询路径反复空写。同一 runner 多次 ask_user
    按 wait_id 各自落库，互不吞并。
    """
    if not question or not wait_id:
        return False
    dedup_key = f"clarify_{wait_id}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM channel_messages WHERE session_id = %s "
            "AND metadata->>'client_request_id' = %s LIMIT 1",
            (session_id, dedup_key),
        )
        if cursor.fetchone() is not None:
            return False
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        metadata = {"runner_id": runner_id, "kind": "clarification",
                    "client_request_id": dedup_key}
        cursor.execute(
            """
            INSERT INTO channel_messages
            (message_id, session_id, tenant_id, role, content, message_type, attachments, metadata)
            VALUES (%s, %s, %s, 'assistant', %s, 'text', NULL, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                message_id, session_id, tenant_id, question,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "UPDATE channel_sessions SET last_message_at = %s, updated_at = %s "
                "WHERE session_id = %s",
                (now, now, session_id),
            )
        conn.commit()
        return inserted


def persist_runner_result(tenant_id: str, session_id: str, row: dict) -> None:
    """终态 assistant 消息落库（completed 幂等落库；失败/取消不落）。"""
    if row.get("status") != "completed":
        return
    runner_id = row.get("runner_id") or ""
    if not runner_id or has_final_message(session_id, runner_id):
        return
    result = row.get("result") or {}
    output = result.get("output") or (row.get("snapshot") or row.get("public_snapshot") or {}).get("output") or ""
    attachments = runner_files_to_attachments(row)
    persist_assistant_message(tenant_id, session_id, runner_id, output, attachments)


def _load_runner_rows(where_sql: str, params: tuple) -> List[dict]:
    """按条件直读 agent_runners（JOIN 会话行过滤已删除会话，投影为统一行形态）。"""
    sql = f"""
        SELECT r.runner_id, r.tenant_id, r.session_id, r.status,
               r.public_snapshot, r.result
        FROM agent_runners r
        JOIN channel_sessions s ON s.session_id = r.session_id
        WHERE r.source = 'web_c' AND {where_sql}
        ORDER BY r.updated_at DESC
        LIMIT %s
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params + (SWEEP_BATCH_LIMIT,))
        rows = cursor.fetchall()
    out: List[dict] = []
    for r in rows:
        out.append({
            "runner_id": r["runner_id"],
            "tenant_id": r["tenant_id"],
            "session_id": r["session_id"],
            "status": r["status"],
            "snapshot": r["public_snapshot"] if isinstance(r["public_snapshot"], dict) else {},
            "result": r["result"] if isinstance(r["result"], dict) else {},
        })
    return out


async def backfill_session_runners(tenant_id: str, session_id: str) -> int:
    """会话级终态补写（DB 直读 agent_runners，不依赖访客身份/Runner API）。

    completed 补最终回复（含文件附件投影）；waiting（ask_user 停车）补澄清
    反问。供 C 端 messages 拉取与租户接待页 messages 接口触发（设计 §4.3
    步骤 3/4）。返回实际新落库条数。
    """
    count = 0
    rows = await asyncio.to_thread(
        _load_runner_rows, "r.session_id = %s AND r.status IN ('completed', 'waiting')",
        (session_id,))
    for row in rows:
        runner_id = row.get("runner_id") or ""
        row_tenant = row.get("tenant_id") or tenant_id or ""
        if not runner_id or not row_tenant:
            continue
        if row.get("status") == "completed":
            if await asyncio.to_thread(has_final_message, session_id, runner_id):
                continue
            await asyncio.to_thread(persist_runner_result, row_tenant, session_id, row)
            count += 1
        elif row.get("status") == "waiting":
            question, wait_id = clarification_from_row(row)
            if not question or not wait_id:
                continue
            if await asyncio.to_thread(
                persist_clarification_message, row_tenant, session_id,
                runner_id, question, wait_id,
            ):
                count += 1
    return count


def _load_recent_runner_rows() -> List[dict]:
    """扫描窗口内的 completed/waiting web_c runner 行（updated_at 倒序）。"""
    return _load_runner_rows(
        f"r.status IN ('completed', 'waiting') "
        f"AND r.updated_at >= NOW() - INTERVAL '{SWEEP_WINDOW_HOURS} hours'",
        ())


class WebCRunnerPersistSweeper:
    """web_c 终态落库兜底扫描器（background 容器单副本运行）。

    周期扫描近期 completed/waiting 的 web_c runner 行，补写访客离线期间无人
    触发的终态回复/澄清反问。落库幂等（runner_id/wait_id 判重 + ON CONFLICT），
    多副本并发安全，无需 Redis 锁；扫描窗口内重复扫描仅多做存在性 SELECT。
    """

    def __init__(self):
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        if self._task is not None and not self._task.done():
            logger.warning("web_c 终态落库扫描器已在运行，跳过重复启动")
            return True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="web-c-persist-sweeper")
        logger.info("web_c 终态落库扫描器已启动 interval={}s window={}h",
                    SWEEP_INTERVAL_SECONDS, SWEEP_WINDOW_HOURS)
        return True

    async def stop(self) -> None:
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=10)
            except Exception:  # noqa: BLE001
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _run_loop(self) -> None:
        # 首轮延迟：避开进程启动高峰，给在途执行留结算时间；
        # 用可中断等待而非 sleep，停止信号在启动窗口内也能立即退出
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=15)
            return
        except asyncio.TimeoutError:
            pass
        while not self._stop_event.is_set():
            try:
                await self.sweep_once()
            except Exception:
                logger.opt(exception=True).warning("web_c 终态落库扫描器本轮失败（下轮重试）")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=SWEEP_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def sweep_once(self) -> int:
        """扫描一轮并补写，返回新落库条数（供测试与观测）。"""
        rows = await asyncio.to_thread(_load_recent_runner_rows)
        count = 0
        for row in rows:
            tenant_id = row.get("tenant_id") or ""
            session_id = row.get("session_id") or ""
            runner_id = row.get("runner_id") or ""
            if not (tenant_id and session_id and runner_id):
                continue
            if row.get("status") == "completed":
                if await asyncio.to_thread(has_final_message, session_id, runner_id):
                    continue
                try:
                    await asyncio.to_thread(persist_runner_result, tenant_id, session_id, row)
                    count += 1
                except Exception:
                    logger.opt(exception=True).warning(
                        f"[web_c] 扫描器终态落库失败 runner={runner_id[:40]}")
            elif row.get("status") == "waiting":
                question, wait_id = clarification_from_row(row)
                if not question or not wait_id:
                    continue
                if await asyncio.to_thread(
                    persist_clarification_message, tenant_id, session_id,
                    runner_id, question, wait_id,
                ):
                    count += 1
        if count:
            logger.info(f"[web_c] 终态落库扫描器本轮补写 {count} 条")
        return count
