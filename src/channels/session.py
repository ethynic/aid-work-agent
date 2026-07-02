"""
渠道会话管理

管理第三方渠道的会话信息。
支持租户隔离：tenant_id 参与 session_id 生成和所有查询，防止跨租户数据串扰。
"""

import json
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.models.message import ChannelType
from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached
from src.core.temp_logger import tlog


class ChannelSessionManager:
    """
    渠道会话管理器

    为每个租户的每个渠道用户创建和管理独立的会话。
    会话包含：聊天记录、用户信息、渠道信息。

    租户隔离：
    - session_id 由 tenant_id + channel_type + channel_user_id + subagent_id 组成，确保跨租户跨智能体唯一
    - 所有查询均包含 tenant_id 过滤
    - 缓存 key 包含 tenant_id 和 subagent_id
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化会话管理器（延迟初始化）"""
        pass

    def _ensure_tables(self):
        """确保数据库表存在（延迟初始化）"""
        if self._initialized:
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 渠道会话表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    channel_type TEXT NOT NULL,
                    channel_user_id TEXT NOT NULL,
                    subagent_id TEXT,
                    channel_chat_id TEXT,
                    user_id TEXT,
                    username TEXT,
                    title TEXT,
                    context_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_message_at TIMESTAMP,
                    metadata JSONB
                )
            """)

            # 渠道消息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_messages (
                    id SERIAL PRIMARY KEY,
                    message_id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    message_type TEXT DEFAULT 'text',
                    attachments TEXT,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_recalled BOOLEAN NOT NULL DEFAULT FALSE,
                    recalled_at TIMESTAMP
                )
            """)

            # 索引：按租户+渠道+用户+智能体查找会话
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_sessions_tenant_channel
                ON channel_sessions(tenant_id, channel_type, channel_user_id, subagent_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_messages_session
                ON channel_messages(session_id, created_at)
            """)

            conn.commit()

        self._initialized = True
        logger.info("PostgreSQL: channel_sessions 表初始化完成")

    def _generate_session_id(self, tenant_id: str, channel_type: str, channel_user_id: str, subagent_id: str = "") -> str:
        """生成会话ID（包含租户和智能体信息，确保跨租户跨智能体唯一）"""
        return f"{tenant_id}_{channel_type}_{channel_user_id}_{subagent_id}"

    @staticmethod
    def _parse_json_field(value: Optional[str], default: Any = None) -> Any:
        """解析数据库中的 JSON 字段，失败时 fallback"""
        if value is None:
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            # 兼容旧数据（str(dict) 格式）
            return value

    def get_or_create_session(
        self,
        channel_type: str,
        channel_user_id: str,
        tenant_id: str = "",
        user_info: Optional[Dict[str, Any]] = None,
        channel_chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        subagent_id: str = "",
    ) -> Dict[str, Any]:
        """
        获取或创建渠道会话（优先从 Redis 缓存读取，TTL 10分钟）

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID
            tenant_id: 租户ID（用于隔离和 session_id 生成）
            user_info: 用户信息
            channel_chat_id: 渠道会话/群ID
            metadata: 额外元数据
            subagent_id: 关联的子智能体ID

        Returns:
            会话信息字典
        """
        session_id = self._generate_session_id(tenant_id, channel_type, channel_user_id, subagent_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 优先从缓存获取（缓存 key 包含 tenant_id 和 subagent_id）
        cached = get_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id)
        if cached is not None:
            return cached

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 按租户+渠道+用户+智能体查找已存在的会话
            cursor.execute("""
                SELECT * FROM channel_sessions
                WHERE tenant_id = %s AND channel_type = %s AND channel_user_id = %s AND subagent_id = %s
            """, (tenant_id, channel_type, channel_user_id, subagent_id))

            row = cursor.fetchone()

            if row:
                # 更新最后消息时间
                cursor.execute("""
                    UPDATE channel_sessions
                    SET last_message_at = %s, updated_at = %s
                    WHERE session_id = %s
                """, (now, now, session_id))
                conn.commit()

                result = dict(row)
                result["context_data"] = self._parse_json_field(result.get("context_data"), {})
                result["metadata"] = self._parse_json_field(result.get("metadata"))
                set_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id, value=result, ttl=600)
                return result
            else:
                # 创建新会话
                title = f"{channel_type}会话"
                if user_info and user_info.get("name"):
                    title = f"{user_info['name']}的{channel_type}会话"

                cursor.execute("""
                    INSERT INTO channel_sessions
                    (session_id, tenant_id, channel_type, channel_user_id, subagent_id,
                     channel_chat_id, user_id, username, title, context_data, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING created_at, updated_at, last_message_at
                """, (
                    session_id,
                    tenant_id,
                    channel_type,
                    channel_user_id,
                    subagent_id,
                    channel_chat_id,
                    user_info.get("user_id") if user_info else None,
                    user_info.get("name") if user_info else None,
                    title,
                    "{}",  # context_data
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ))
                ts_row = cursor.fetchone()
                conn.commit()

                result = {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "channel_type": channel_type,
                    "channel_user_id": channel_user_id,
                    "subagent_id": subagent_id,
                    "channel_chat_id": channel_chat_id,
                    "user_id": user_info.get("user_id") if user_info else None,
                    "username": user_info.get("name") if user_info else None,
                    "title": title,
                    "context_data": {},
                    "metadata": metadata,
                    "created_at": ts_row["created_at"],
                    "updated_at": ts_row["updated_at"],
                    "last_message_at": ts_row["last_message_at"],
                }
                set_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id, value=result, ttl=600)
                return result

    def get_session(
        self,
        channel_type: str,
        channel_user_id: str,
        tenant_id: str = "",
        subagent_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        获取渠道会话（优先从 Redis 缓存读取，TTL 10分钟）

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID
            tenant_id: 租户ID
            subagent_id: 关联的子智能体ID

        Returns:
            会话信息字典
        """
        # 优先从缓存获取
        cached = get_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id)
        if cached is not None:
            return cached

        session_id = self._generate_session_id(tenant_id, channel_type, channel_user_id, subagent_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channel_sessions WHERE session_id = %s
            """, (session_id,))

            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result["context_data"] = self._parse_json_field(result.get("context_data"), {})
            result["metadata"] = self._parse_json_field(result.get("metadata"))
            # 写入缓存
            set_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id, value=result, ttl=600)
            return result

    def is_channel_session(self, session_id: str) -> bool:
        """
        判断 session_id 是否为渠道会话（在 channel_sessions 表中登记）。

        用于上下文重建时分流：渠道会话读 channel_messages，web 会话读 chat_messages，
        两者严格分离，避免历史误写导致渠道会话读到陈旧的 chat_messages。
        channel_sessions 是渠道会话的权威登记表，web 会话不会出现在此表。
        """
        if not session_id:
            return False
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM channel_sessions WHERE session_id = %s", (session_id,))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.warning(f"后端日志：判断渠道会话失败 session={session_id}: {e}")
            return False

    def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        context_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        更新会话信息

        Args:
            session_id: 会话ID
            title: 标题
            context_data: 上下文数据
            metadata: 额外数据

        Returns:
            是否成功
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        updates = ["updated_at = %s"]
        values = [now]

        if title is not None:
            updates.append("title = %s")
            values.append(title)

        if context_data is not None:
            updates.append("context_data = %s")
            values.append(json.dumps(context_data, ensure_ascii=False))

        if metadata is not None:
            updates.append("metadata = %s")
            values.append(json.dumps(metadata, ensure_ascii=False))

        values.append(session_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE channel_sessions
                SET {', '.join(updates)}
                WHERE session_id = %s
            """, values)
            conn.commit()

            success = cursor.rowcount > 0

            # 更新成功后清除缓存，确保下次读取获取最新数据
            if success:
                cursor.execute("""
                    SELECT tenant_id, channel_type, channel_user_id, subagent_id
                    FROM channel_sessions WHERE session_id = %s
                """, (session_id,))
                row = cursor.fetchone()
                if row:
                    delete_cached(CacheKeys.CHANNEL_SESSION, row["tenant_id"], row["channel_type"], row["channel_user_id"], row["subagent_id"])

            return success

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        attachments: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: str = "",
    ) -> str:
        """
        添加消息到会话

        Args:
            session_id: 会话ID
            role: 角色 (user/assistant/system)
            content: 消息内容
            message_type: 消息类型
            attachments: 附件列表
            metadata: 额外数据
            tenant_id: 租户ID

        Returns:
            消息ID
        """
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_messages
                (message_id, session_id, tenant_id, role, content, message_type,
                 attachments, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                message_id,
                session_id,
                tenant_id,
                role,
                content,
                message_type,
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ))

            # 更新会话最后消息时间
            cursor.execute("""
                UPDATE channel_sessions
                SET last_message_at = %s, updated_at = %s
                WHERE session_id = %s
            """, (now, now, session_id))

            conn.commit()

        return message_id

    def add_messages_batch_transactional(
        self,
        session_id: str,
        tenant_id: str,
        messages: List[Dict[str, Any]],
    ) -> Optional[List[str]]:
        """
        事务性批量写入 channel_messages（P0-1）。

        所有消息作为一个原子事务写入，要么全部成功要么全部失败。
        用于同一轮对话内「user + tool 序列 + 最终 assistant」的统一持久化，
        防止部分写入导致上下文序列错乱。

        Args:
            session_id: 会话 ID
            tenant_id: 租户 ID
            messages: 消息列表，每条结构：
                {
                    "role": str,                  # user / assistant / tool
                    "content": str,
                    "message_type": str = "text",
                    "attachments": Optional[List[Dict]] = None,
                    "metadata": Optional[Dict] = None,
                }

        Returns:
            成功时返回创建的 message_id 列表（按输入顺序）；失败时 rollback 并返回 None。
        """
        if not messages:
            return []

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        created_ids: List[str] = []

        # 竞态兜底：读取 recall_pending Set，若 user 消息的 msgid 或 merged_from_msgids
        # 命中，则落库时直接打上 is_recalled=TRUE。覆盖"消息在 buffer 里被撤回、
        # 但 processor 已经跑完并进入落库"这个竞态窗口。
        recall_pending_msgids: set = set()
        try:
            from src.core.redis_client import redis_client
            pending_key = redis_client.make_key(CacheKeys.RECALL_PENDING, session_id)
            # 内存/Redis 后端都实现了 smembers 语义不一致，这里改用逐个检查更保险
            for msg in messages:
                if msg.get("role") != "user":
                    continue
                metadata = msg.get("metadata") or {}
                if not isinstance(metadata, dict):
                    continue
                candidate_msgids: List[str] = []
                single_msgid = metadata.get("msgid")
                if single_msgid:
                    candidate_msgids.append(str(single_msgid))
                merged_from = metadata.get("merged_from_msgids") or []
                for mid in merged_from:
                    if mid:
                        candidate_msgids.append(str(mid))
                for mid in candidate_msgids:
                    if redis_client.sismember(pending_key, mid):
                        recall_pending_msgids.add(mid)
        except Exception as e:
            tlog(
                "撤回消息",
                "落库前查询 recall_pending 异常: session_id={session_id}, error={error}",
                session_id=session_id,
                error=str(e),
                level="ERROR",
            )
        has_recall_col = self._has_is_recalled_column()

        # 预扫描：批次内是否有 user 命中 recall_pending；最后一条 assistant 的索引。
        # 若 user 命中撤回，配对的最终 assistant 回复也应同步标记 is_recalled=TRUE，
        # 否则下一轮上下文重建会拼接基于已撤回输入生成的回复。
        batch_has_recalled_user = False
        last_assistant_idx = -1
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant" and last_assistant_idx < i:
                last_assistant_idx = i
            if role == "user" and recall_pending_msgids:
                metadata = msg.get("metadata") or {}
                if isinstance(metadata, dict):
                    row_msgids_pre: List[str] = []
                    if metadata.get("msgid"):
                        row_msgids_pre.append(str(metadata["msgid"]))
                    for mid in metadata.get("merged_from_msgids") or []:
                        if mid:
                            row_msgids_pre.append(str(mid))
                    if any(mid in recall_pending_msgids for mid in row_msgids_pre):
                        batch_has_recalled_user = True

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                for i, msg in enumerate(messages):
                    role = msg.get("role")
                    content = msg.get("content", "")
                    message_type = msg.get("message_type", "text")
                    attachments = msg.get("attachments")
                    metadata = msg.get("metadata")

                    # 判断本条 user 消息是否命中 recall_pending
                    hit_recall = False
                    if role == "user" and recall_pending_msgids and isinstance(metadata, dict):
                        row_msgids: List[str] = []
                        if metadata.get("msgid"):
                            row_msgids.append(str(metadata["msgid"]))
                        for mid in metadata.get("merged_from_msgids") or []:
                            if mid:
                                row_msgids.append(str(mid))
                        hit_recall = any(mid in recall_pending_msgids for mid in row_msgids)

                    # 批次内 user 命中撤回时，最终 assistant 回复也标记撤回
                    is_recalled_assistant = (
                        i == last_assistant_idx and batch_has_recalled_user and role == "assistant"
                    )
                    mark_recalled = (hit_recall or is_recalled_assistant) and has_recall_col

                    message_id = f"msg_{uuid.uuid4().hex[:16]}"
                    if mark_recalled:
                        cursor.execute("""
                            INSERT INTO channel_messages
                            (message_id, session_id, tenant_id, role, content, message_type,
                             attachments, metadata, is_recalled, recalled_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW())
                        """, (
                            message_id,
                            session_id,
                            tenant_id,
                            role,
                            content,
                            message_type,
                            json.dumps(attachments, ensure_ascii=False) if attachments else None,
                            json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None,
                        ))
                        tlog(
                            "撤回消息",
                            "落库前命中 recall_pending，直接标记 is_recalled=TRUE: "
                            "session_id={session_id}, message_id={message_id}, "
                            "role={role}, hit_user={hit_user}, is_recalled_assistant={is_assistant}, "
                            "content_preview={preview!r}",
                            session_id=session_id,
                            message_id=message_id,
                            role=role,
                            hit_user=hit_recall,
                            is_assistant=is_recalled_assistant,
                            preview=(content or "")[:80],
                        )
                    else:
                        cursor.execute("""
                            INSERT INTO channel_messages
                            (message_id, session_id, tenant_id, role, content, message_type,
                             attachments, metadata)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            message_id,
                            session_id,
                            tenant_id,
                            role,
                            content,
                            message_type,
                            json.dumps(attachments, ensure_ascii=False) if attachments else None,
                            json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None,
                        ))
                    created_ids.append(message_id)

                # 更新会话最后消息时间（只调一次）
                cursor.execute("""
                    UPDATE channel_sessions
                    SET last_message_at = %s, updated_at = %s
                    WHERE session_id = %s
                """, (now, now, session_id))

                conn.commit()

                # 落库成功后，将本次已处理的 recall_pending msgid 从 Set 中移除，
                # 防止陈旧 msgid 长期占用 Redis（虽然有 TTL 兜底，但主动清理更干净）
                if recall_pending_msgids:
                    try:
                        from src.core.redis_client import redis_client
                        pending_key = redis_client.make_key(CacheKeys.RECALL_PENDING, session_id)
                        for mid in recall_pending_msgids:
                            redis_client.srem(pending_key, mid)
                        tlog(
                            "撤回消息",
                            "落库后清理 recall_pending: session_id={session_id}, cleared={cleared}",
                            session_id=session_id,
                            cleared=list(recall_pending_msgids),
                        )
                    except Exception as clr_err:
                        tlog(
                            "撤回消息",
                            "清理 recall_pending 失败: session_id={session_id}, error={error}",
                            session_id=session_id,
                            error=str(clr_err),
                            level="ERROR",
                        )

                return created_ids
            except Exception as e:
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"Failed to rollback channel_messages batch insert: {rollback_err}")
                logger.error(
                    f"后端日志：channel_messages 批量写入失败 session={session_id}: {e}",
                    exc_info=True,
                )
                return None

    def get_last_message(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取会话最后一条消息（按 id DESC）。
        用于异常兜底场景判断末尾是否为孤立 user。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channel_messages
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT 1
            """, (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            msg = dict(row)
            msg["attachments"] = self._parse_json_field(msg.get("attachments"), [])
            msg["metadata"] = self._parse_json_field(msg.get("metadata"))
            return msg

    async def process_and_persist(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_content: str,
        agent: "Any",
        send_response: Callable[[str, List[Dict[str, Any]]], Awaitable[bool]],
        user_metadata: Optional[Dict[str, Any]] = None,
        user_attachments: Optional[List[Dict[str, Any]]] = None,
        user_attachments_meta: Optional[List[Dict[str, Any]]] = None,
        message_type: str = "text",
        agent_user: Optional[Any] = None,
        agent_user_input: Optional[str] = None,
        agent_attachments: Optional[List[Dict[str, Any]]] = None,
        record_service: Optional[Any] = None,
        tool_messages_collected: Optional[List[Dict[str, Any]]] = None,
        assistant_metadata: Optional[Dict[str, Any]] = None,
        agent_extra_system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        统一渠道消息处理路径。覆盖 P0-1（事务化批量写入）+ P0-2（user 写入推迟）+ 异常兜底。

        流程：
            1. 注册 collect_files_callback 收集 downloadable_files
            2. 调用 session_queue.enqueue_and_process（user 消息此刻尚未写入）
            3. status == "merged"：直接返回，不写任何消息
            4. status == "success"：
               - 决定 user_to_write（合并方用 merged_input，否则用 user_content）
               - 构造 batch：[user, *tool_messages, assistant]，事务化批量写入
               - 写入失败 / send_response 失败时补一条 assistant 占位，避免下次加载出现连续 user
               - mark_responding → send_response → mark_idle

        Args:
            session_id: 渠道会话 ID
            tenant_id: 租户 ID
            user_content: 原始用户消息文本（用于持久化）
            agent: Agent 实例
            send_response: async (response_text, downloadable_files) -> bool
                           由各渠道自行实现 UnifiedResponse 构造 + adapter.send_message
            user_metadata: 用户消息 metadata
            user_attachments: 原始附件（含 base64，不直接入库）
            user_attachments_meta: 入库用附件元数据（不含 base64）
            message_type: 用户消息类型，默认 "text"
            agent_user: 传给 agent.process_message_sync 的 user 对象（可选）
            agent_user_input: 传给 agent 的 user_input（默认与 user_content 一致；
                               语音 ASR 等场景下 agent 输入可能与持久化文本不同）
            agent_attachments: 传给 agent 的 attachments
            record_service: 会话记录服务（可选）
            tool_messages_collected: 外部预收集的 tool 消息序列（如 wecom_kf 通过
                                     progress_callback 收集）。若为 None 则内部新建列表
            assistant_metadata: 最终 assistant 消息的 metadata 覆盖。
                                若为 None，则当 downloadable_files 非空时自动构造
                                {"downloadableFiles": downloadable_files}，否则为 None。
            agent_extra_system_prompt: 渠道级额外提示词（如 wecom_kf 的渠道能力约束），
                                透传给 agent.process_message_sync → _build_system_prompt。
                                None 时行为不变（web/其他渠道默认）。

        Returns:
            {
                "status": "success" | "merged" | "error",
                "response_text": str,
                "downloadable_files": List[Dict],
                "was_merged": bool,
                "merged_input": str,
            }
        """
        # 延迟导入避免循环依赖
        from src.core.session_queue import session_queue

        downloadable_files: List[Dict[str, Any]] = []
        # 若外部传入 tool_messages_collected 引用，则复用；否则新建（同时供 collect_files_callback 写入）
        if tool_messages_collected is None:
            tool_messages_collected = []

        async def collect_files_callback(event):
            if not isinstance(event, dict):
                return
            event_type = event.get("type")
            if (event_type == "tool_result"
                    and event.get("toolName") in ("write", "cp")
                    and event.get("success") is True):
                result = event.get("result", {}) or {}
                if result.get("file_id"):
                    downloadable_files.append({
                        "file_id": result["file_id"],
                        "file_name": result.get("download_file_name") or result.get("file_name", "未命名文件"),
                        "file_size": result.get("file_size", 0),
                        "download_url": result.get("download_url", ""),
                        "mime_type": result.get("mime_type", ""),
                    })
            elif event_type == "tool_messages":
                # 收集本轮 tool 消息序列（assistant with tool_calls + role:tool 配对）
                tool_messages_collected.extend(event.get("messages", []))

        agent_input_text = agent_user_input if agent_user_input is not None else user_content

        async def _processor(cancel_check, user_input_override=None):
            # 语音合并 / pending 重处理场景下，session_queue 会通过 override
            # 传入合并后的完整输入，必须优先于闭包绑定的 agent_input_text
            effective_input = user_input_override if user_input_override is not None else agent_input_text
            if user_input_override is not None and user_input_override != agent_input_text:
                tlog(
                    "语音合并",
                    "processor 使用 override 输入 session={sid}..., "
                    "original_len={o_len}, override_len={n_len}",
                    sid=session_id[:20],
                    o_len=len(agent_input_text),
                    n_len=len(user_input_override),
                )
            kwargs = {
                "user_input": effective_input,
                "session_id": session_id,
                "record_service": record_service,
                "progress_callback": collect_files_callback,
                "cancel_check": cancel_check,
            }
            if agent_extra_system_prompt is not None:
                kwargs["extra_system_prompt"] = agent_extra_system_prompt
            if agent_user is not None:
                kwargs["user"] = agent_user
            if agent_attachments is not None:
                kwargs["attachments"] = agent_attachments
            return await agent.process_message_sync(**kwargs)

        # ===== 调用 session_queue =====
        # 从 user_metadata 提取 msgid 透传给 session_queue，供合并缓冲区记录每段 msgid
        user_msgid = (user_metadata or {}).get("msgid", "") if isinstance(user_metadata, dict) else ""
        try:
            result = await session_queue.enqueue_and_process(
                session_id=session_id,
                user_input=agent_input_text,
                processor=_processor,
                attachments_meta=user_attachments_meta,
                msgid=user_msgid,
            )
        except Exception as e:
            logger.error(
                f"后端日志：process_and_persist enqueue_and_process 异常 session={session_id}: {e}",
                exc_info=True,
            )
            # enqueue 异常：user 写入已推迟，调用 mark_error 记录失败
            if record_service is not None:
                try:
                    record_service.mark_error(str(e))
                except Exception as mark_err:
                    logger.warning(
                        f"后端日志：enqueue 异常后 record_service.mark_error 失败: {mark_err}"
                    )
            return {
                "status": "error",
                "response_text": "",
                "downloadable_files": downloadable_files,
                "was_merged": False,
                "merged_input": "",
            }

        # ===== 合并/排队：不写任何消息，直接返回 =====
        if result.status == "merged":
            return {
                "status": "merged",
                "response_text": "",
                "downloadable_files": downloadable_files,
                "was_merged": True,
                "merged_input": result.merged_input,
            }

        # ===== status == "success" =====
        response_text = result.response_text or ""
        # 合并方应持久化「合并后的输入」，否则用原始 user_content
        user_to_write = result.merged_input if result.was_merged else user_content
        # 合并方持久化附件元数据：优先用 session_queue 透传的 merged_attachments_meta
        # （含被取消方的附件元数据，避免语音被并入前一条消息后附件 local_path 丢失）
        if result.was_merged and result.merged_attachments_meta is not None:
            attachments_to_write = result.merged_attachments_meta
        else:
            attachments_to_write = user_attachments_meta
        if result.was_merged:
            tlog(
                "语音合并",
                "持久化用户消息 session={sid}..., was_merged=True, "
                "user_content_len={uc_len}, merged_input_len={mi_len}, "
                "write_len={w_len}, write_preview={w_prev!r}, "
                "user_meta_count={um_n}, merged_meta_count={mm_n}, write_meta_count={wm_n}",
                sid=session_id[:20],
                uc_len=len(user_content),
                mi_len=len(result.merged_input),
                w_len=len(user_to_write),
                w_prev=user_to_write[:120],
                um_n=len(user_attachments_meta) if user_attachments_meta else 0,
                mm_n=len(result.merged_attachments_meta) if result.merged_attachments_meta else 0,
                wm_n=len(attachments_to_write) if attachments_to_write else 0,
            )

        # 构造批量写入的消息序列
        batch: List[Dict[str, Any]] = []
        if user_to_write:
            # 合并方用 session_queue 透传的 merged_from_msgids / merged_segments 构造 metadata，
            # 供撤回时按段重建 content。单条消息保留原 user_metadata（含 msgid）。
            if result.was_merged and result.merged_from_msgids:
                merged_user_metadata = {
                    "open_kfid": (user_metadata or {}).get("open_kfid", "") if isinstance(user_metadata, dict) else "",
                    "merged_from_msgids": result.merged_from_msgids,
                    "merged_segments": result.merged_segments or [],
                }
            else:
                merged_user_metadata = user_metadata
            batch.append({
                "role": "user",
                "content": user_to_write,
                "message_type": message_type,
                "attachments": attachments_to_write if attachments_to_write else None,
                "metadata": merged_user_metadata,
            })

        # tool 消息序列（wecom_kf 等需要持久化 tool_calls + tool 结果）
        for tm in tool_messages_collected:
            if tm.get("role") == "assistant" and tm.get("tool_calls"):
                tm_metadata = {"tool_calls": tm["tool_calls"]}
                if tm.get("reasoning_content"):
                    tm_metadata["reasoning_content"] = tm["reasoning_content"]
                batch.append({
                    "role": "assistant",
                    "content": "",
                    "message_type": "text",
                    "metadata": tm_metadata,
                })
            elif tm.get("role") == "tool":
                tc = tm.get("content", "")
                if isinstance(tc, (dict, list)):
                    tc = json.dumps(tc, ensure_ascii=False, default=str)
                batch.append({
                    "role": "tool",
                    "content": tc,
                    "message_type": "text",
                    "metadata": {"tool_call_id": tm.get("tool_call_id", "")},
                })

        # 最终 assistant 回复
        # assistant_metadata：优先用外部传入；否则当 downloadable_files 非空时自动构造
        final_assistant_metadata = assistant_metadata
        if final_assistant_metadata is None and downloadable_files:
            final_assistant_metadata = {"downloadableFiles": downloadable_files}

        batch.append({
            "role": "assistant",
            "content": response_text,
            "message_type": "text",
            "metadata": final_assistant_metadata,
        })

        # 事务化批量写入
        tlog(
            "语音合并",
            "[持久化] session={sid}..., was_merged={merged}, "
            "user_content_len={uc_len}, user_content_preview={uc_prev!r}, "
            "merged_input_len={mi_len}, merged_input_preview={mi_prev!r}, "
            "user_to_write_len={uw_len}, user_to_write_full={uw_full!r}, "
            "batch_roles={roles}, response_len={r_len}, response_preview={r_prev!r}",
            sid=session_id[:20],
            merged=result.was_merged,
            uc_len=len(user_content),
            uc_prev=user_content[:120],
            mi_len=len(result.merged_input),
            mi_prev=result.merged_input[:120],
            uw_len=len(user_to_write),
            uw_full=user_to_write,
            roles=[m.get("role") for m in batch],
            r_len=len(response_text),
            r_prev=response_text[:120],
        )
        write_ok = self.add_messages_batch_transactional(session_id, tenant_id, batch)
        if write_ok is None:
            logger.error(
                f"后端日志：channel_messages 批量写入失败 session={session_id}，"
                f"user 和 assistant 均未落库"
            )
            # 兜底：防御性补占位 assistant（新流程下 user+assistant 是同事务，
            # 要么都成功要么都失败，理论上 _ensure_last_not_orphan_user 不会触发；
            # 保留是为了兼容外部预置脏数据 / 极端历史回放场景）。
            self._ensure_last_not_orphan_user(session_id, tenant_id)
            # 记录失败到 record_service
            if record_service is not None:
                try:
                    record_service.mark_error("批量写入失败")
                except Exception as mark_err:
                    logger.warning(
                        f"后端日志：批量写入失败后 record_service.mark_error 异常: {mark_err}"
                    )
            # 通过 send_response 告知用户失败（如渠道决定），否则静默
            try:
                session_queue.mark_responding(session_id)
                await send_response("", downloadable_files)
            except Exception as send_err:
                logger.error(
                    f"后端日志：批量写入失败后 send_response 异常 session={session_id}: {send_err}",
                    exc_info=True,
                )
            finally:
                session_queue.mark_idle(session_id)
            return {
                "status": "error",
                "response_text": "",
                "downloadable_files": downloadable_files,
                "was_merged": result.was_merged,
                "merged_input": result.merged_input,
            }

        # 回填 trace.user_message_id（user 消息对应 batch[0]），用于 monitor.py
        # 精确匹配撤回状态。双轨覆盖：set_user_message_id 覆盖 trace_persist worker
        # 未处理的场景（内存 trace 携带该值写入），update_user_message_id UPDATE
        # 覆盖 worker 已处理的场景。整个回填失败只记 debug log，不影响业务。
        try:
            user_msg_id = (
                write_ok[0]
                if write_ok and batch and batch[0].get("role") == "user"
                else None
            )
            if user_msg_id and record_service is not None:
                tc = getattr(record_service, "trace_collector", None)
                trace_id = None
                if tc is not None:
                    tc.set_user_message_id(user_msg_id)  # 覆盖 worker 未处理
                    trace_id = tc.trace_id
                if trace_id:
                    from src.core.trace_persist import update_user_message_id
                    update_user_message_id(trace_id, user_msg_id)  # 覆盖 worker 已处理
        except Exception as e:
            logger.debug(
                f"后端日志：trace user_message_id 回填失败 session={session_id}: {e}"
            )

        # response_text 为空（agent 内部异常被吞掉返回空字符串）→ 标记错误状态
        if not response_text:
            logger.warning(
                f"后端日志：agent 返回空响应 session={session_id}，可能内部异常"
            )
            if record_service is not None:
                try:
                    record_service.mark_error("agent 返回空响应")
                except Exception as mark_err:
                    logger.warning(
                        f"后端日志：空响应后 record_service.mark_error 异常: {mark_err}"
                    )

        # 批量写入成功 → 记录完成
        if record_service is not None:
            try:
                record_service.complete(response_text)
            except Exception as e:
                logger.warning(f"后端日志：record_service.complete 异常: {e}")

        # 发送响应
        send_ok = False
        try:
            session_queue.mark_responding(session_id)
            send_ok = await send_response(response_text, downloadable_files)
        except Exception as e:
            logger.error(
                f"后端日志：send_response 异常 session={session_id}: {e}",
                exc_info=True,
            )
        finally:
            session_queue.mark_idle(session_id)

        # send_response 失败的兜底：保留 _ensure_last_not_orphan_user 作为防御性兜底
        # （新流程下 user+assistant 同事务，理论上末尾不会是孤立 user；保留是防御性）
        if not send_ok:
            self._ensure_last_not_orphan_user(session_id, tenant_id)

        return {
            "status": "success",
            "response_text": response_text,
            "downloadable_files": downloadable_files,
            "was_merged": result.was_merged,
            "merged_input": result.merged_input,
        }

    def make_send_response(
        self,
        *,
        adapter: Any,
        message_id: str,
        reply_to: str,
        extra_content: Optional[Dict[str, Any]] = None,
        log_tag: str = "Channel",
        pre_send: Optional[Callable[[], None]] = None,
    ) -> Callable[[str, List[Dict[str, Any]]], Awaitable[bool]]:
        """
        构造 send_response 回调，供 process_and_persist 使用。

        统一封装「构造 UnifiedResponse → adapter.send_message → 返回 bool」逻辑，
        消除各渠道调用点重复的 _send_response 内联定义。

        Args:
            adapter: 渠道适配器，必须有 async send_message(response: UnifiedResponse) -> bool
            message_id: 用于构造 resp_xxx 形式的响应消息ID
            reply_to: 回复目标（用户/群会话ID）
            extra_content: 额外的 content 字段（如 dingtalk 的 conversation_type）
            log_tag: 日志前缀（如 "[Tenant WeCom]"）
            pre_send: 发送前的钩子（如 RPA 的 set_reply_context 注入）

        Returns:
            async callable (response_text: str, downloadable_files: list) -> bool
        """
        # 方法内部 import，避免模块加载顺序问题
        from src.models.message import UnifiedResponse, DownloadableFileInfo

        async def _send_response(response_text: str, downloadable_files: list) -> bool:
            logger.info(
                f"{log_tag} 开始发送回复: reply_to={reply_to}, "
                f"content_len={len(response_text) if response_text else 0}"
            )
            try:
                if pre_send is not None:
                    pre_send()
                content: Dict[str, Any] = {"text": response_text}
                if extra_content is not None:
                    content.update(extra_content)
                response = UnifiedResponse(
                    message_id=f"resp_{message_id}",
                    reply_to=reply_to,
                    content=content,
                    downloadable_files=[DownloadableFileInfo(**f) for f in downloadable_files],
                )
                send_result = await adapter.send_message(response)
                logger.info(
                    f"{log_tag} 回复发送{'成功' if send_result else '失败'}: "
                    f"reply_to={reply_to}"
                )
                return bool(send_result)
            except Exception as e:
                logger.error(f"{log_tag} send_response 异常: {e}")
                return False

        return _send_response

    def _ensure_last_not_orphan_user(self, session_id: str, tenant_id: str) -> None:
        """
        异常兜底：检查 channel_messages 末尾是否为孤立 user（前一条也是 user 或表为空且末条是 user），
        若是则补一条 assistant 占位「[本次回复生成失败]」，避免下次加载出现连续 user。
        """
        try:
            last = self.get_last_message(session_id)
            if last and last.get("role") == "user":
                logger.warning(
                    f"后端日志：检测到孤立 user 末条 session={session_id}，补写 assistant 占位"
                )
                self.add_message(
                    session_id=session_id,
                    role="assistant",
                    content="[本次回复生成失败]",
                    message_type="text",
                    tenant_id=tenant_id,
                )
        except Exception as e:
            logger.error(
                f"后端日志：_ensure_last_not_orphan_user 异常 session={session_id}: {e}",
                exc_info=True,
            )

    def find_session(
        self,
        channel_type: str,
        channel_user_id: str,
        channel_chat_id: str,
        tenant_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        查找已存在的会话（不创建新会话）

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID
            channel_chat_id: 渠道会话/群ID
            tenant_id: 租户ID

        Returns:
            会话信息字典，不存在返回 None
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channel_sessions
                WHERE tenant_id = %s AND channel_type = %s AND channel_user_id = %s
            """, (tenant_id, channel_type, channel_user_id))
            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result["context_data"] = self._parse_json_field(result.get("context_data"), {})
            result["metadata"] = self._parse_json_field(result.get("metadata"))
            return result

    def _has_is_recalled_column(self) -> bool:
        """检查 channel_messages 表是否有 is_recalled 列（迁移兼容性）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'channel_messages' AND column_name = 'is_recalled'
            """)
            return cursor.fetchone() is not None

    def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        before_message_id: Optional[str] = None,
        include_recalled: bool = False,
        include_compacted: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        获取会话消息

        Args:
            session_id: 会话ID
            limit: 限制条数
            before_message_id: 分页基准消息ID
            include_recalled: 是否包含已撤回的消息（默认 False，LLM 上下文用）
            include_compacted: 是否包含 compacted=true 的消息（v3.1）。
                默认 False：前端展示和 Agent 重建 memory 都自动跳过被压缩的历史消息。

        Returns:
            消息列表（按 created_at ASC 时间正序）
        """
        from src.core.temp_logger import tlog

        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = "%s"
            # v3.1: 默认过滤掉 compacted=true 的消息
            compacted_clause = "" if include_compacted else " AND (compacted = FALSE OR compacted IS NULL)"

            # 撤回消息过滤条件（兼容迁移前的数据库：is_recalled 列不存在时不启用过滤）
            has_recall_column = self._has_is_recalled_column()
            recall_condition = "" if (include_recalled or not has_recall_column) else "AND is_recalled = FALSE"

            if before_message_id:
                cursor.execute(f"""
                    SELECT * FROM channel_messages
                    WHERE session_id = {placeholder} {recall_condition} AND id < (
                        SELECT id FROM channel_messages WHERE message_id = {placeholder}
                    )
                    {compacted_clause}
                    ORDER BY id ASC
                    LIMIT {limit}
                """, (session_id, before_message_id))
            else:
                # 取最近 N 条（按 id 倒序取 N 条），再正序返回，保证时间正序且保留最新上下文。
                # 直接 ORDER BY id ASC LIMIT N 会返回最老的 N 条，长会话会丢掉最近一轮对话。
                # v3.1: compacted_clause 用于过滤已压缩消息（默认排除）
                cursor.execute(f"""
                    SELECT * FROM (
                        SELECT * FROM channel_messages
                        WHERE session_id = {placeholder} {recall_condition}
                        {compacted_clause}
                        ORDER BY id DESC
                        LIMIT {limit}
                    ) AS recent
                    ORDER BY id ASC
                """, (session_id,))

            rows = cursor.fetchall()
            messages = []
            for row in rows:
                msg = dict(row)
                msg["attachments"] = self._parse_json_field(msg.get("attachments"), [])
                msg["metadata"] = self._parse_json_field(msg.get("metadata"))
                messages.append(msg)

            # 记录撤回消息过滤情况（仅当列存在时统计）
            if not include_recalled and has_recall_column:
                recalled_count = sum(1 for m in messages if m.get("is_recalled"))
                if recalled_count > 0:
                    tlog(
                        "撤回消息",
                        "get_messages 过滤已撤回消息: session_id={session_id}, filtered={filtered}, total={total}",
                        session_id=session_id,
                        filtered=recalled_count,
                        total=len(messages),
                    )

            return messages

    def _mark_following_assistant_recalled(
        self,
        cursor,
        session_id: str,
        after_id: int,
    ) -> Optional[int]:
        """
        标记紧随某条 user 消息之后的第一条 assistant 回复为已撤回。

        用户撤回输入后，基于该输入生成的 assistant 回复在下一轮上下文重建中
        也应被排除，否则 LLM 会把已失效的回复当作历史继续拼接。

        Args:
            cursor: 数据库游标（由调用方负责 commit）
            session_id: 会话 ID
            after_id: 被撤回的 user 消息 row id

        Returns:
            被标记的 assistant 消息 row id；未找到返回 None
        """
        cursor.execute("""
            SELECT id FROM channel_messages
            WHERE session_id = %s AND id > %s AND role = 'assistant'
            ORDER BY id ASC
            LIMIT 1
        """, (session_id, after_id))
        row = cursor.fetchone()
        if not row:
            return None
        assistant_id = row["id"]
        cursor.execute("""
            UPDATE channel_messages
            SET is_recalled = TRUE, recalled_at = NOW()
            WHERE id = %s
        """, (assistant_id,))
        return assistant_id

    def mark_recalled_message(
        self,
        session_id: str,
        recall_msgid: str,
        tenant_id: str = "",
    ) -> int:
        """
        标记已撤回的消息（单条消息或合并消息的部分撤回）

        Args:
            session_id: 会话ID
            recall_msgid: 被撤回的微信原始 msgid
            tenant_id: 租户ID

        Returns:
            被标记的消息数量
        """
        from src.core.temp_logger import tlog
        import json

        # 迁移兼容：is_recalled 列不存在时，直接返回 0
        if not self._has_is_recalled_column():
            tlog(
                "撤回消息",
                "数据库尚未迁移，is_recalled 列不存在，跳过标记: session_id={session_id}, msgid={msgid}",
                session_id=session_id,
                msgid=recall_msgid,
            )
            return 0

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # ===== 情况1：单条消息命中（metadata.msgid = recall_msgid）=====
            cursor.execute("""
                SELECT id, content, metadata FROM channel_messages
                WHERE session_id = %s AND metadata::jsonb->>'msgid' = %s
            """, (session_id, recall_msgid))
            single_row = cursor.fetchone()

            if single_row:
                cursor.execute("""
                    UPDATE channel_messages
                    SET is_recalled = TRUE, recalled_at = NOW()
                    WHERE id = %s
                """, (single_row["id"],))
                # 同步标记紧随其后的 assistant 回复：用户撤回输入后，
                # 基于该输入生成的回复在下一轮上下文里也应失效。
                assistant_row_id = self._mark_following_assistant_recalled(
                    cursor, session_id, single_row["id"]
                )
                conn.commit()
                tlog(
                    "撤回消息",
                    "标记单条消息已撤回: session_id={session_id}, msgid={msgid}, "
                    "row_id={row_id}, assistant_row_id={assistant_row_id}",
                    session_id=session_id,
                    msgid=recall_msgid,
                    row_id=single_row["id"],
                    assistant_row_id=assistant_row_id,
                )
                return 1

            # ===== 情况2：合并消息命中（metadata.merged_from_msgids 包含 recall_msgid）=====
            cursor.execute("""
                SELECT id, content, metadata FROM channel_messages
                WHERE session_id = %s
                  AND jsonb_exists(metadata::jsonb->'merged_from_msgids', %s)
            """, (session_id, recall_msgid))
            merged_row = cursor.fetchone()

            if merged_row:
                metadata = self._parse_json_field(merged_row.get("metadata"), {})
                merged_segments = metadata.get("merged_segments", [])
                merged_from_msgids = metadata.get("merged_from_msgids", [])
                recalled_part_msgids = metadata.get("recalled_part_msgids", [])

                # 过滤掉被撤回的段，重建 content
                remaining_segments = [
                    seg for seg in merged_segments
                    if seg.get("msgid") != recall_msgid
                ]

                if len(remaining_segments) == len(merged_segments):
                    # 未找到对应段，降级为整条标记撤回
                    cursor.execute("""
                        UPDATE channel_messages
                        SET is_recalled = TRUE, recalled_at = NOW()
                        WHERE id = %s
                    """, (merged_row["id"],))
                    conn.commit()
                    tlog(
                        "撤回消息",
                        "合并消息部分撤回降级为整条撤回（未找到对应段）: "
                        "session_id={session_id}, msgid={msgid}, row_id={row_id}",
                        session_id=session_id,
                        msgid=recall_msgid,
                        row_id=merged_row["id"],
                    )
                    return 1

                # 更新 metadata
                recalled_part_msgids.append(recall_msgid)
                metadata["recalled_part_msgids"] = recalled_part_msgids

                # 仅首次撤回时保存原始内容
                if "original_content_before_recall" not in metadata:
                    metadata["original_content_before_recall"] = merged_row["content"]

                # 重建 content
                new_content = "\n".join([seg.get("text", "") for seg in remaining_segments])
                metadata["merged_segments"] = remaining_segments

                # 所有段都被撤回时，整条标记为已撤回
                all_recalled = len(remaining_segments) == 0
                assistant_row_id: Optional[int] = None
                if all_recalled:
                    cursor.execute("""
                        UPDATE channel_messages
                        SET is_recalled = TRUE, recalled_at = NOW(), content = %s, metadata = %s
                        WHERE id = %s
                    """, (new_content, json.dumps(metadata, ensure_ascii=False), merged_row["id"]))
                    # 合并消息整条撤回时，同样标记配对 assistant 回复
                    assistant_row_id = self._mark_following_assistant_recalled(
                        cursor, session_id, merged_row["id"]
                    )
                else:
                    cursor.execute("""
                        UPDATE channel_messages
                        SET content = %s, metadata = %s
                        WHERE id = %s
                    """, (new_content, json.dumps(metadata, ensure_ascii=False), merged_row["id"]))

                conn.commit()
                tlog(
                    "撤回消息",
                    "合并消息部分撤回重建: session_id={session_id}, msgid={msgid}, "
                    "row_id={row_id}, segments_before={before}, segments_after={after}, "
                    "all_recalled={all_recalled}, assistant_row_id={assistant_row_id}",
                    session_id=session_id,
                    msgid=recall_msgid,
                    row_id=merged_row["id"],
                    before=len(merged_segments),
                    after=len(remaining_segments),
                    all_recalled=all_recalled,
                    assistant_row_id=assistant_row_id,
                )
                return 1

            # ===== 情况3：未命中持久化消息，兜底清理合并缓冲区 =====
            # 撤回事件可能在消息已进 session_queue 合并窗口、尚未落库时到达。
            # 此时消息在 session_merge 缓冲区里，按 msgid 移除该段。
            try:
                from src.core.session_queue import session_queue
                removed = session_queue.remove_merge_segment(session_id, recall_msgid)
            except Exception as buf_err:
                tlog(
                    "撤回消息",
                    "清理合并缓冲区异常: session_id={session_id}, msgid={msgid}, error={error}",
                    session_id=session_id,
                    msgid=recall_msgid,
                    error=str(buf_err),
                )
                removed = False

            # 竞态兜底：processor 可能已经把 buffer 里的输入读走并在跑 agent，
            # 后续落库时不会再走情况1/2；此处把 msgid 写入 recall_pending Set，
            # 让 add_messages_batch_transactional 落库前查一次，命中则直接
            # 打上 is_recalled=TRUE，避免撤回消息被当正常消息保存。
            try:
                from src.core.redis_client import redis_client
                pending_key = redis_client.make_key(CacheKeys.RECALL_PENDING, session_id)
                redis_client.sadd(pending_key, recall_msgid)
                redis_client.expire(pending_key, 300)  # 300s 覆盖 processor 最长运行时间
                tlog(
                    "撤回消息",
                    "已登记 recall_pending: session_id={session_id}, msgid={msgid}, pending_key={key}",
                    session_id=session_id,
                    msgid=recall_msgid,
                    key=pending_key,
                )
            except Exception as pend_err:
                tlog(
                    "撤回消息",
                    "登记 recall_pending 失败: session_id={session_id}, msgid={msgid}, error={error}",
                    session_id=session_id,
                    msgid=recall_msgid,
                    error=str(pend_err),
                    level="ERROR",
                )

            tlog(
                "撤回消息",
                "未命中持久化消息，兜底清理合并缓冲区: "
                "session_id={session_id}, msgid={msgid}, buffer_removed={removed}",
                session_id=session_id,
                msgid=recall_msgid,
                removed=removed,
            )
            return 0

    def count_messages_by_session(
        self,
        session_id: str,
        include_compacted: bool = False,
        include_recalled: bool = False,
    ) -> int:
        """统计会话消息条数（v3.2 新增，用于压缩阈值快路径检查）。

        只 SELECT COUNT(*)，不拉数据，走 (session_id, created_at) 索引。

        Args:
            session_id: 会话 ID
            include_compacted: 是否包含 compacted=true 的消息。默认 False。
            include_recalled: 是否包含已撤回的消息。默认 False，与 get_messages
                保持一致。撤回消息不参与压缩（既不会被摘要、也不会被打 compacted
                标记），若计入 COUNT 会导致阈值误触发 + 死循环（撤回消息永远
                compacted=FALSE，每轮 COUNT 都重新数进去）。

        Returns:
            消息条数；查询异常时返回 0（容错，让阈值判断降级到 token 缓存）
        """
        placeholder = "%s"
        compacted_clause = "" if include_compacted else " AND (compacted = FALSE OR compacted IS NULL)"
        # 撤回消息过滤条件（兼容迁移前的数据库：is_recalled 列不存在时不启用过滤）
        has_recall_column = self._has_is_recalled_column()
        recall_condition = "" if (include_recalled or not has_recall_column) else " AND is_recalled = FALSE"
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT COUNT(*) AS cnt FROM channel_messages "
                    f"WHERE session_id = {placeholder}{compacted_clause}{recall_condition}",
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return 0
                try:
                    return int(row.get("cnt") or 0)
                except (TypeError, ValueError):
                    return 0
        except Exception as e:
            logger.warning(
                f"ChannelSessionManager.count_messages_by_session failed: "
                f"sid={session_id}, err={e}"
            )
            return 0

    def get_conversation_context(
        self,
        session_id: str,
        max_messages: int = 20,
    ) -> List[Dict[str, str]]:
        """
        获取对话上下文（用于LLM）

        Args:
            session_id: 会话ID
            max_messages: 最大消息数

        Returns:
            LLM格式的消息列表
        """
        messages = self.get_messages(session_id, limit=max_messages)

        context = []
        for msg in messages:
            context.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        return context

    def list_sessions(
        self,
        channel_type: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        列出会话

        Args:
            channel_type: 渠道类型过滤
            user_id: 用户ID过滤
            tenant_id: 租户ID过滤
            limit: 限制条数

        Returns:
            会话列表
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = []
            values = []

            if tenant_id:
                conditions.append("tenant_id = %s")
                values.append(tenant_id)

            if channel_type:
                conditions.append("channel_type = %s")
                values.append(channel_type)

            if user_id:
                conditions.append(f"user_id = %s")
                values.append(user_id)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # PostgreSQL 不支持 LIMIT %s，需要直接拼接
            cursor.execute(f"""
                SELECT * FROM channel_sessions
                WHERE {where_clause}
                ORDER BY last_message_at DESC
                LIMIT {limit}
            """, values)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def update_session_by_channel_user(
        self,
        tenant_id: str,
        channel_type: str,
        channel_user_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        按渠道用户更新所有匹配会话的 metadata。

        Returns:
            更新的会话数量
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE channel_sessions
                SET metadata = %s, updated_at = %s
                WHERE tenant_id = %s AND channel_type = %s AND channel_user_id = %s
            """, (json.dumps(metadata, ensure_ascii=False) if metadata else None, now,
                 tenant_id, channel_type, channel_user_id))
            conn.commit()
            updated_count = cursor.rowcount

            if updated_count > 0:
                cursor.execute("""
                    SELECT subagent_id FROM channel_sessions
                    WHERE tenant_id = %s AND channel_type = %s AND channel_user_id = %s
                """, (tenant_id, channel_type, channel_user_id))
                for row in cursor.fetchall():
                    delete_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type,
                                  channel_user_id, row["subagent_id"] or "")

            return updated_count

    def get_session_by_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        按 session_id 直接查询渠道会话

        Args:
            session_id: 会话ID

        Returns:
            会话信息字典，不存在返回 None
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channel_sessions WHERE session_id = %s
            """, (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result["context_data"] = self._parse_json_field(result.get("context_data"), {})
            result["metadata"] = self._parse_json_field(result.get("metadata"))
            return result

    def update_context_token_count(self, session_id: str, token_count: int) -> bool:
        """更新渠道 session 的上下文 token 缓存（v3.1）。

        Agent 主循环每次 LLM 调用后写入最后一次 prompt_tokens + completion_tokens，
        供 ContextCompressionService._should_compress 优先读取。

        Args:
            session_id: 会话 ID
            token_count: 最后一次 LLM 调用的 prompt+completion token 数

        Returns:
            是否更新成功（session 不存在时返回 False）
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE channel_sessions SET context_token_count = %s "
                    "WHERE session_id = %s",
                    (int(token_count), session_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.exception(
                    f"Failed to update channel context_token_count: session={session_id}, err={e}"
                )
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"rollback failed: {rollback_err}")
                return False

    def get_messages_paginated(
        self,
        session_id: str,
        content_search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """
        分页获取渠道会话消息（支持内容搜索，后台视图用，包含已撤回消息）

        Args:
            session_id: 会话ID
            content_search: 聊天内容搜索（可选）
            page: 页码
            page_size: 每页数量

        Returns:
            {"messages": [...], "total": int, "page": int, "page_size": int}
            消息含 is_recalled / recalled_at 字段（迁移兼容：列不存在时返回 False/None），
            部分撤回的合并消息 metadata.recalled_part_msgids 非空。
        """
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 当前会话下的消息，工具调用类型消息不展示，且内容不为空
            conditions = ["session_id = %s", "role != 'tool'", "content != ''"]
            params = [session_id]

            if content_search:
                conditions.append("content LIKE %s")
                params.append(f"%{content_search}%")

            where_clause = " AND ".join(conditions)

            cursor.execute(f"SELECT COUNT(*) as cnt FROM channel_messages WHERE {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            # 撤回字段（迁移兼容：is_recalled 列不存在时用 NULL 占位，前端统一按 False 处理）
            has_recall_column = self._has_is_recalled_column()
            recall_select = "is_recalled, recalled_at" if has_recall_column else "FALSE AS is_recalled, NULL AS recalled_at"

            cursor.execute(f"""
                SELECT message_id, session_id, role, content, message_type, attachments, metadata, created_at,
                       {recall_select}
                FROM channel_messages
                WHERE {where_clause}
                ORDER BY id DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            messages = []
            for row in cursor.fetchall():
                msg = dict(row)
                msg["attachments"] = self._parse_json_field(msg.get("attachments"), [])
                msg["metadata"] = self._parse_json_field(msg.get("metadata"))
                # 统一 is_recalled 类型为 bool（迁移前/NULL 场景）
                msg["is_recalled"] = bool(msg.get("is_recalled"))
                messages.append(msg)

        return {"messages": messages, "total": total, "page": page, "page_size": page_size}

    def delete_session(self, session_id: str, tenant_id: Optional[str] = None) -> bool:
        """
        删除会话。目前该方法暂时没用，启用时需要注意“接待外部客户”页面需要展示 channel_sessions 和 channel_messages 的内容，所以原则上不应删除，而只能禁用（增加状态字段控制）

        Args:
            session_id: 会话ID
            tenant_id: 租户ID（可选，提供时额外校验租户归属）

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if tenant_id:
                # 带租户校验的删除
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s AND tenant_id = %s
                """, (session_id, tenant_id))
                cursor.execute("""
                    DELETE FROM channel_sessions WHERE session_id = %s AND tenant_id = %s
                """, (session_id, tenant_id))
            else:
                # 删除消息
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s
                """, (session_id,))
                # 删除会话
                cursor.execute("""
                    DELETE FROM channel_sessions WHERE session_id = %s
                """, (session_id,))

            conn.commit()

            return cursor.rowcount > 0

    def delete_messages(self, session_id: str, tenant_id: Optional[str] = None) -> bool:
        """
        仅删除会话中的消息，保留会话本身。隐藏命令“新会话”触发本方法

        Args:
            session_id: 会话ID
            tenant_id: 租户ID（可选，提供时额外校验租户归属）

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if tenant_id:
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s AND tenant_id = %s
                """, (session_id, tenant_id))
            else:
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s
                """, (session_id,))

            conn.commit()
            logger.info(f"后端日志：channel_messages 已清空: session_id={session_id}")
            return cursor.rowcount > 0# 全局会话管理器
channel_session_manager = ChannelSessionManager()
