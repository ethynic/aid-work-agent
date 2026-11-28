"""History storage only; no ingress/session processing or platform delivery."""

import json
from loguru import logger
from src.db.database import get_db_connection


class HistoryRepository:
    def __init__(self, identity):
        if identity.session_kind not in {"web", "channel"}:
            raise ValueError("UNKNOWN_SESSION_KIND")
        self.identity = identity

    def assert_authorized(self, require_user=False):
        table = "chat_sessions" if self.identity.session_kind == "web" else "channel_sessions"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = f"SELECT 1 FROM {table} WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s"
            parameters = [self.identity.session_id, self.identity.tenant_id]
            if self.identity.session_kind == "web" or require_user:
                query += " AND COALESCE(user_id, '') = COALESCE(%s, '')"
                parameters.append(self.identity.user_id)
            cursor.execute(query, tuple(parameters))
            if cursor.fetchone() is None:
                raise PermissionError("SESSION_ACCESS_DENIED")

    def update_context_tokens(self, count):
        table = "chat_sessions" if self.identity.session_kind == "web" else "channel_sessions"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE {table} SET context_token_count = %s WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s",
                           (count, self.identity.session_id, self.identity.tenant_id))
            conn.commit()

    def read_web(self, session_id, limit=100):
        if self.identity.session_kind != "web" or session_id != self.identity.session_id:
            raise ValueError("HISTORY_SESSION_KIND_MISMATCH")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.* FROM chat_messages m
                JOIN chat_sessions s ON s.session_id = m.session_id
                WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                  AND s.user_id IS NOT DISTINCT FROM %s
                  AND (m.compacted = FALSE OR m.compacted IS NULL)
                ORDER BY m.id DESC LIMIT %s
            """, (session_id, self.identity.tenant_id, self.identity.user_id, limit))
            return list(reversed([dict(row) for row in cursor.fetchall()]))

    def read_channel(self, session_id, limit=100, include_recalled=False):
        if self.identity.session_kind != "channel" or session_id != self.identity.session_id:
            raise ValueError("HISTORY_SESSION_KIND_MISMATCH")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM (
                    SELECT m.* FROM channel_messages m
                    JOIN channel_sessions s ON s.session_id = m.session_id
                    WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                      AND (m.tenant_id IS NOT DISTINCT FROM s.tenant_id OR m.tenant_id IS NULL)
                      AND (m.compacted = FALSE OR m.compacted IS NULL)
                      AND (m.is_recalled = FALSE OR m.is_recalled IS NULL)
                      AND (m.status = 'active' OR m.status IS NULL)
                    ORDER BY m.id DESC LIMIT %s
                ) recent ORDER BY id ASC
            """, (session_id, self.identity.tenant_id, limit))
            messages = [dict(row) for row in cursor.fetchall()]
        for message in messages:
            for field in ("metadata", "attachments"):
                value = message.get(field)
                if isinstance(value, str):
                    try:
                        message[field] = json.loads(value)
                    except ValueError:
                        message[field] = {} if field == "metadata" else []
        return messages

    def read_user_attachments(self, session_id, limit=100):
        """读取历史窗口内 user 消息的附件元数据（2026-11-27 跨执行附件重物化）。

        历史重建（_reload_memory_from_db）只保留 content 文本，附件元数据（含
        file_id）被丢弃，导致下一执行（含被取代后的新执行）拿不到先前轮次的
        附件实体，模型只能答「找不到」。本方法供 context_assembler 在装配时
        单独读取附件元数据，配合 attachments.prepare 重物化副本到新工作区。

        过滤口径与 read_web / read_channel 完全一致（压缩窗口、租户归属、
        渠道 status/is_recalled），保证只物化仍在 LLM 上下文里的消息附件。
        返回 [{file_id,name,mime_type,type,size}]；无附件/异常返回 []（fail-open，
        重物化为增强能力，不得阻断执行装配）。
        """
        try:
            if self.identity.session_kind == "web":
                query = """
                    SELECT attachments FROM chat_messages m
                    JOIN chat_sessions s ON s.session_id = m.session_id
                    WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                      AND s.user_id IS NOT DISTINCT FROM %s
                      AND m.role = 'user'
                      AND (m.compacted = FALSE OR m.compacted IS NULL)
                    ORDER BY m.id DESC LIMIT %s
                """
                parameters = [session_id, self.identity.tenant_id, self.identity.user_id, limit]
            else:
                query = """
                    SELECT attachments FROM channel_messages m
                    JOIN channel_sessions s ON s.session_id = m.session_id
                    WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                      AND (m.tenant_id IS NOT DISTINCT FROM s.tenant_id OR m.tenant_id IS NULL)
                      AND m.role = 'user'
                      AND (m.compacted = FALSE OR m.compacted IS NULL)
                      AND (m.is_recalled = FALSE OR m.is_recalled IS NULL)
                      AND (m.status = 'active' OR m.status IS NULL)
                    ORDER BY m.id DESC LIMIT %s
                """
                parameters = [session_id, self.identity.tenant_id, limit]
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(parameters))
                rows = cursor.fetchall()
        except Exception:
            logger.opt(exception=True).warning(
                "[history_repository] 历史附件元数据读取失败，跳过重物化")
            return []
        result = []
        for row in rows:
            value = row.get("attachments") if isinstance(row, dict) else None
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = []
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, dict) and item.get("file_id"):
                    result.append({
                        "file_id": str(item["file_id"]),
                        "name": str(item.get("name") or item["file_id"]),
                        "mime_type": str(item.get("mime_type") or ""),
                        "type": str(item.get("type") or "file"),
                        "size": item.get("size"),
                    })
        return result

    def active_summary(self, session_id, source_type):
        self._check_compression_scope(session_id, source_type)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT summary_text FROM chat_context_summaries
                WHERE session_id = %s AND source_type = %s
                AND tenant_id IS NOT DISTINCT FROM %s AND status = 'active'
                ORDER BY summary_version DESC LIMIT 1""", (session_id, source_type, self.identity.tenant_id))
            row = cursor.fetchone()
            return row["summary_text"] if row else None

    def _check_compression_scope(self, session_id, source_type):
        expected = self.identity.source if self.identity.session_kind == "channel" else "chat"
        if session_id != self.identity.session_id or source_type != expected:
            raise ValueError("COMPRESSION_SESSION_SCOPE_MISMATCH")

    def get_session(self, session_id, source_type):
        self._check_compression_scope(session_id, source_type)
        table = "chat_sessions" if self.identity.session_kind == "web" else "channel_sessions"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = f"SELECT * FROM {table} WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s"
            parameters = [session_id, self.identity.tenant_id]
            if self.identity.session_kind == "web":
                query += " AND user_id IS NOT DISTINCT FROM %s"
                parameters.append(self.identity.user_id)
            cursor.execute(query, tuple(parameters))
            row = cursor.fetchone()
            return dict(row) if row else None

    def load_messages(self, session_id, source_type, limit=10000):
        self._check_compression_scope(session_id, source_type)
        read = self.read_web if self.identity.session_kind == "web" else self.read_channel
        return read(session_id, limit=limit)

    def count_messages(self, session_id, source_type):
        self._check_compression_scope(session_id, source_type)
        table = "chat_messages" if self.identity.session_kind == "web" else "channel_messages"
        session_table = "chat_sessions" if self.identity.session_kind == "web" else "channel_sessions"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = f"""SELECT COUNT(*) AS cnt FROM {table} m JOIN {session_table} s ON s.session_id = m.session_id
                WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                AND (m.compacted = FALSE OR m.compacted IS NULL)"""
            parameters = [session_id, self.identity.tenant_id]
            if self.identity.session_kind == "channel":
                query += " AND (m.tenant_id IS NOT DISTINCT FROM s.tenant_id OR m.tenant_id IS NULL) AND (m.is_recalled = FALSE OR m.is_recalled IS NULL) AND (m.status = 'active' OR m.status IS NULL)"
            else:
                query += " AND s.user_id IS NOT DISTINCT FROM %s"
                parameters.append(self.identity.user_id)
            cursor.execute(query, tuple(parameters))
            return int(cursor.fetchone()["cnt"])

    @staticmethod
    def legacy_session_kind(session_id, tenant_id=None):
        """Only compatibility callers lack an explicit trusted SessionRef."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM channel_sessions WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s",
                           (session_id, tenant_id))
            return "channel" if cursor.fetchone() else "web"
