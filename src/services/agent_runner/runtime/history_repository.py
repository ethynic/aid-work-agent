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

    @staticmethod
    def _decode_json_field(value, default):
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return default
        return value

    @classmethod
    def _extract_file_items_from_row(cls, row, *, is_web: bool):
        """从单条 chat_messages / channel_messages 行提取文件元数据列表。"""
        if not isinstance(row, dict):
            return []
        role = str(row.get("role") or "")
        origin = "user_upload" if role == "user" else "assistant_generated"
        created_at = row.get("created_at")
        created_str = created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else "")
        raw_items = []
        if is_web:
            # chat_messages 无 attachments 列，用户上传附件与助手交付文件均在 metadata 中
            meta = cls._decode_json_field(row.get("metadata"), {})
            if isinstance(meta, dict):
                if role == "user":
                    att = meta.get("attachments")
                    if isinstance(att, list):
                        raw_items.extend(att)
                else:
                    for key in ("downloadableFiles", "images", "attachments"):
                        val = meta.get(key)
                        if isinstance(val, list):
                            raw_items.extend(val)
        else:
            att = cls._decode_json_field(row.get("attachments"), [])
            if isinstance(att, list):
                raw_items.extend(att)
            # 企微/企微客服/飞书/钉钉的 AI 生成交付物落在 channel_messages.metadata.downloadableFiles/images
            meta = cls._decode_json_field(row.get("metadata"), {})
            if isinstance(meta, dict):
                for key in ("downloadableFiles", "images", "attachments"):
                    val = meta.get(key)
                    if isinstance(val, list):
                        raw_items.extend(val)
        out = []
        for item in raw_items:
            if not isinstance(item, dict) or not item.get("file_id"):
                continue
            fid = str(item["file_id"]).strip()
            if not fid:
                continue
            name = str(item.get("name") or item.get("file_name") or item.get("display_name") or fid)
            size = item.get("size")
            if size is None:
                size = item.get("file_size")
            if size is None:
                size = item.get("size_bytes")
            out.append({
                "file_id": fid,
                "name": name,
                "mime_type": str(item.get("mime_type") or ""),
                "type": str(item.get("type") or ("image" if item.get("display_name") else "file")),
                "size": size,
                "origin": origin,
                "created_at": created_str,
            })
        return out

    def read_session_files(self, session_id, limit=100, *, roles=("user", "assistant"), max_files=20):
        """读取历史窗口内会话文件元数据（同时覆盖用户上传与助手生成交付物）。

        背景：历史消息重建只保留文本，较早轮次上传或 AI 生成的 PDF/Word/Excel 等
        附件元数据在跨轮后会丢失。本方法按 read_web / read_channel 同口径读取
        文件元数据，供 context_assembler 注入轻量文件清单，及 checkout_session_file
        校验会话归属。

        返回按时间正序排列、按 file_id 去重（保留最新）的文件元数据列表；
        异常 fail-open 返回 []。
        """
        role_tuple = tuple(r for r in (roles or ()) if r in ("user", "assistant"))
        if not role_tuple:
            return []
        try:
            is_web = self.identity.session_kind == "web"
            if is_web:
                query = """
                    SELECT m.role, m.metadata, m.created_at FROM chat_messages m
                    JOIN chat_sessions s ON s.session_id = m.session_id
                    WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                      AND s.user_id IS NOT DISTINCT FROM %s
                      AND m.role = ANY(%s)
                      AND (m.compacted = FALSE OR m.compacted IS NULL)
                    ORDER BY m.id DESC LIMIT %s
                """
                parameters = [session_id, self.identity.tenant_id, self.identity.user_id, list(role_tuple), limit]
            else:
                query = """
                    SELECT m.role, m.attachments, m.metadata, m.created_at FROM channel_messages m
                    JOIN channel_sessions s ON s.session_id = m.session_id
                    WHERE m.session_id = %s AND s.tenant_id IS NOT DISTINCT FROM %s
                      AND (m.tenant_id IS NOT DISTINCT FROM s.tenant_id OR m.tenant_id IS NULL)
                      AND m.role = ANY(%s)
                      AND (m.compacted = FALSE OR m.compacted IS NULL)
                      AND (m.is_recalled = FALSE OR m.is_recalled IS NULL)
                      AND (m.status = 'active' OR m.status IS NULL)
                    ORDER BY m.id DESC LIMIT %s
                """
                parameters = [session_id, self.identity.tenant_id, list(role_tuple), limit]
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(parameters))
                rows = cursor.fetchall()
        except Exception:
            logger.opt(exception=True).warning(
                "[history_repository] 会话文件元数据读取失败，跳过清单注入"
            )
            return []
        seen = set()
        newest_first = []
        for row in rows:
            for item in self._extract_file_items_from_row(row, is_web=is_web):
                fid = item["file_id"]
                if fid in seen:
                    continue
                seen.add(fid)
                newest_first.append(item)
                if max_files and len(newest_first) >= max_files:
                    break
            if max_files and len(newest_first) >= max_files:
                break
        newest_first.reverse()
        return newest_first

    def find_session_file(self, session_id, file_id, limit=200):
        """在当前会话有效历史窗口内按 file_id 查找文件元数据（用于按需检出归属校验）。"""
        target = str(file_id or "").strip()
        if not target:
            return None
        for item in self.read_session_files(session_id, limit=limit, max_files=limit):
            if item.get("file_id") == target:
                return item
        return None

    def read_user_attachments(self, session_id, limit=100):
        """兼容封装：仅读取历史窗口内 user 消息的附件元数据。"""
        return self.read_session_files(session_id, limit=limit, roles=("user",), max_files=limit)

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
