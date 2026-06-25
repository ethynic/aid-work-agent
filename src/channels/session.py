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
                    metadata TEXT
                )
            """)

            # 渠道消息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_messages (
                    message_id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    message_type TEXT DEFAULT 'text',
                    attachments TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                for msg in messages:
                    role = msg.get("role")
                    content = msg.get("content", "")
                    message_type = msg.get("message_type", "text")
                    attachments = msg.get("attachments")
                    metadata = msg.get("metadata")

                    message_id = f"msg_{uuid.uuid4().hex[:16]}"
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
            # 调试 override 输入是否正确透传时启用：
            # if user_input_override is not None and user_input_override != agent_input_text:
            #     tlog(
            #         "语音合并",
            #         "processor 使用 override 输入 session={sid}..., "
            #         "original_len={o_len}, override_len={n_len}",
            #         sid=session_id[:20],
            #         o_len=len(agent_input_text),
            #         n_len=len(user_input_override),
            #     )
            kwargs = {
                "user_input": effective_input,
                "session_id": session_id,
                "record_service": record_service,
                "progress_callback": collect_files_callback,
                "cancel_check": cancel_check,
            }
            if agent_user is not None:
                kwargs["user"] = agent_user
            if agent_attachments is not None:
                kwargs["attachments"] = agent_attachments
            return await agent.process_message_sync(**kwargs)

        # ===== 调用 session_queue =====
        try:
            result = await session_queue.enqueue_and_process(
                session_id=session_id,
                user_input=agent_input_text,
                processor=_processor,
                attachments_meta=user_attachments_meta,
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
        # 调试合并方持久化内容（user_to_write / attachments）时启用：
        # if result.was_merged:
        #     tlog(
        #         "语音合并",
        #         "持久化用户消息 session={sid}..., was_merged=True, "
        #         "user_content_len={uc_len}, merged_input_len={mi_len}, "
        #         "write_len={w_len}, write_preview={w_prev!r}, "
        #         "user_meta_count={um_n}, merged_meta_count={mm_n}, write_meta_count={wm_n}",
        #         sid=session_id[:20],
        #         uc_len=len(user_content),
        #         mi_len=len(result.merged_input),
        #         w_len=len(user_to_write),
        #         w_prev=user_to_write[:120],
        #         um_n=len(user_attachments_meta) if user_attachments_meta else 0,
        #         mm_n=len(result.merged_attachments_meta) if result.merged_attachments_meta else 0,
        #         wm_n=len(attachments_to_write) if attachments_to_write else 0,
        #     )

        # 构造批量写入的消息序列
        batch: List[Dict[str, Any]] = []
        if user_to_write:
            batch.append({
                "role": "user",
                "content": user_to_write,
                "message_type": message_type,
                "attachments": attachments_to_write if attachments_to_write else None,
                "metadata": user_metadata,
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

    def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        before_message_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取会话消息

        Args:
            session_id: 会话ID
            limit: 限制条数
            before_message_id: 分页基准消息ID

        Returns:
            消息列表（按 created_at ASC 时间正序）
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = "%s"

            if before_message_id:
                cursor.execute(f"""
                    SELECT * FROM channel_messages
                    WHERE session_id = {placeholder} AND id < (
                        SELECT id FROM channel_messages WHERE message_id = {placeholder}
                    )
                    ORDER BY id ASC
                    LIMIT {limit}
                """, (session_id, before_message_id))
            else:
                # 取最近 N 条（按 id 倒序取 N 条），再正序返回，保证时间正序且保留最新上下文。
                # 直接 ORDER BY id ASC LIMIT N 会返回最老的 N 条，长会话会丢掉最近一轮对话。
                cursor.execute(f"""
                    SELECT * FROM (
                        SELECT * FROM channel_messages
                        WHERE session_id = {placeholder}
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
            return messages

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

    def get_messages_paginated(
        self,
        session_id: str,
        content_search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """
        分页获取渠道会话消息（支持内容搜索）

        Args:
            session_id: 会话ID
            content_search: 聊天内容搜索（可选）
            page: 页码
            page_size: 每页数量

        Returns:
            {"messages": [...], "total": int, "page": int, "page_size": int}
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

            cursor.execute(f"""
                SELECT message_id, session_id, role, content, message_type, attachments, metadata, created_at
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
        仅删除会话中的消息，保留会话本身。

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
