from __future__ import annotations
from typing import Any, List, Dict, Tuple
from loguru import logger
from src.config.settings import settings

from src.core.agent_engine.context import reorder_history

class SessionHistory:
    def __init__(self, memory, source_type, reader, tolerate_read_failure=False, session_kind=None):
        self.memory, self.source_type, self.reader = memory, source_type, reader
        self.tolerate_read_failure = tolerate_read_failure
        self.session_kind = session_kind or ("channel" if source_type in {
            "wecom", "wecom_kf", "wecom_personal_rpa", "feishu", "dingtalk", "channel"} else "web")

    def _detect_source_type(self):
        return self.source_type
    def _reload_memory_from_db(self, session_id: str, current_user_input: str = "") -> None:
        """从 DB 重新加载 memory（v3.1 Phase 4）。

        压缩完成后调用：compacted=true 已写入 DB，重新加载后 memory 中自动
        过滤掉被压缩的消息，本轮 _build_messages 拿到的就是压缩后的新上下文。

        复用 _process_message_impl 中既有的重建逻辑，区分 chat / channel 两套消息表。
        """
        try:
            import json as _json
            source_type = self._detect_source_type()
            if self.session_kind == "web":
                
                db_messages = self.reader.read_web(
                    session_id,
                    limit=self.memory.short_term.max_messages,
                )
                self.memory.clear(session_id)
                if not db_messages:
                    return
                history_messages: List[Dict[str, Any]] = []
                for msg in db_messages:
                    role = msg["role"]
                    content = msg["content"] or ""
                    meta = msg.get("metadata") or {}
                    if isinstance(meta, str):
                        try:
                            meta = _json.loads(meta)
                        except Exception:
                            meta = {}
                    if role == "tool":
                        history_messages.append({
                            "role": "tool",
                            "tool_call_id": meta.get("tool_call_id", ""),
                            "content": content,
                        })
                    elif role == "assistant":
                        entry: Dict[str, Any] = {
                            "role": "assistant",
                            "content": content,
                        }
                        if meta.get("tool_calls"):
                            entry["tool_calls"] = meta["tool_calls"]
                        if meta.get("reasoning_content"):
                            entry["reasoning_content"] = meta["reasoning_content"]
                        history_messages.append(entry)
                    else:
                        history_messages.append({"role": role, "content": content})
                self.memory.load_history(session_id, history_messages)
            else:
                # 渠道消息：走 channel_messages 表
                history_messages = self._load_channel_history(session_id, current_user_input)
                self.memory.clear(session_id)
                if history_messages:
                    self.memory.load_history(session_id, history_messages)
        except Exception as e:
            if not self.tolerate_read_failure:
                raise
            logger.warning(
                f"ContextCompression _reload_memory_from_db failed, sid={session_id}: {e}"
            )

    def _load_channel_history(
        self,
        session_id: str,
        current_user_input: str,
    ) -> List[Dict[str, Any]]:
        """
        从 channel_messages 表加载渠道（企业微信/钉钉/飞书）的对话历史。

        只在可信会话类型明确为渠道时读取，绝不跨消息表降级。
        渠道处理器会预先将当前用户消息存入 channel_messages，
        因此需要跳过最后一条 user 消息以避免重复。
        """
        try:
            
            # 多加载一条，用于判断最后一条是否是当前用户消息
            channel_msgs = self.reader.read_channel(
                session_id,
                limit=self.memory.short_term.max_messages + 1,
                include_recalled=False,  # LLM 上下文剔除已撤回消息
            )
            if not channel_msgs:
                return []

            # get_messages 已返回 ASC（时间正序，SQL 中 ORDER BY created_at ASC）
            # 无需反转，无需截断（SQL 中 LIMIT 已控制数量）

            # 如果最后一条是 user 消息，说明是渠道处理器预先存入的当前消息，
            # 需要移除（process_message 后续会通过 memory.add 添加 enhanced 版本）
            if channel_msgs and channel_msgs[-1].get("role") == "user":
                last_content = channel_msgs[-1].get("content", "")
                if last_content == current_user_input:
                    channel_msgs.pop()

            # 字段映射与 chat_messages 主分支（agent.py:1602-1626）对称：
            # 从 metadata 恢复 tool_calls / tool_call_id / reasoning_content
            history: List[Dict[str, Any]] = []
            for msg in channel_msgs:
                role = msg["role"]
                content = msg["content"] or ""
                meta = msg.get("metadata") or {}
                if role == "tool":
                    history.append({
                        "role": "tool",
                        "tool_call_id": meta.get("tool_call_id", ""),
                        "content": content,
                        "timestamp": msg.get("created_at", ""),
                    })
                elif role == "assistant":
                    entry: Dict[str, Any] = {
                        "role": "assistant",
                        "content": content,
                        "timestamp": msg.get("created_at", ""),
                    }
                    if meta.get("tool_calls"):
                        entry["tool_calls"] = meta["tool_calls"]
                    if meta.get("reasoning_content"):
                        entry["reasoning_content"] = meta["reasoning_content"]
                    history.append(entry)
                else:  # user / system
                    # 人工客服消息（source=servicer）转为 assistant：客服侧发言（AI + 人工）
                    # 统一对齐到 assistant 侧，避免与真实 user 消息形成连续 user 被
                    # _reorder_messages_for_llm 的 P0-3 清洗整条丢弃（2026-08-31 实证：
                    # servicer 确认消息被丢弃后 AI 答称"看不到实际进度"）。
                    # 保留 "[人工客服] " 前缀，让 LLM 区分人工同事与自身发言。
                    if role == "user" and isinstance(meta, dict) and meta.get("source") == "servicer":
                        history.append({
                            "role": "assistant",
                            "content": content,
                            "timestamp": msg.get("created_at", ""),
                        })
                    else:
                        history.append({
                            "role": role,
                            "content": content,
                            "timestamp": msg.get("created_at", ""),
                        })
            # 诊断：记录加载到的历史消息概览，用于对比上下文是否缺消息
            try:
                # from src.core.temp_logger import tlog as _tlog
                _roles_preview = []
                for m in history:
                    c = m.get("content", "")
                    if not isinstance(c, str):
                        c = str(c)
                    _roles_preview.append(f"{m.get('role')}:{c[:40]!r}")
                # _tlog(
                #     "语音合并",
                #     "[加载历史] session={sid}..., count={n}, msgs={preview}, "
                #     "current_user_input_len={cu_len}, current_user_input_preview={cu_prev!r}",
                #     sid=session_id[:20],
                #     n=len(history),
                #     preview=_roles_preview,
                #     cu_len=len(current_user_input),
                #     cu_prev=current_user_input[:80],
                # )
            except Exception:
                pass
            return history
        except Exception as e:
            if not self.tolerate_read_failure:
                raise
            logger.warning(f"Failed to load channel history for session {session_id}: {e}")
            return []

    def active_summary(self, session_id):
        if not settings.memory.mid_term.enabled:
            return None
        return self.reader.active_summary(session_id, self.source_type)

    def _build_messages(
        self,
        session_id: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Build message list for LLM from memory.

        返回 (messages, system_markers)：
        - messages：对话消息序列（不含 system 消息）
        - system_markers：历史中 role=system 的标记消息（如转人工标记），由上层拼接到
          system_prompt 注入 LLM

        健壮性保障（输出无论 DB 返回顺序如何都满足 LLM API 约束）：
        1. 跳过空 content 的 user/assistant 消息（防止空 user 导致 API 报错）
        2. 每条 assistant(tool_calls) 后「立即、连续」跟随其匹配的 tool 结果（按 tool_calls
           声明顺序）；无任何匹配结果的 tool_calls 被丢弃，assistant 降级为普通内容
        3. system 消息从对话序列中提取（部分 LLM API 不允许在对话序列中插入 system），
           不再转成 user（转成 user 会与其后的真实 user 形成连续 user，被清洗丢弃）
        4. 孤立的 tool 消息（无对应 assistant(tool_calls)）一律跳过
        背景：单事务批量写入会让同轮消息 created_at 相同，若查询缺二级排序键，返回顺序会
        错乱；这里按 tool_call_id 重新配对重建合法序列，不依赖 DB 返回顺序。
        """
        history = self.memory.get_context(session_id)
        history_roles = []
        for m in history:
            role = m.get('role', '?')
            content = m.get('content', '')
            if not isinstance(content, str):
                content = str(content)[:30]
            else:
                content = content[:30]
            history_roles.append(f"{role}:{content}")
        logger.debug(f"_build_messages: session_id={session_id}, history_count={len(history)}, msgs={history_roles}")

        # v3.1 Phase 4: 注入 active 摘要到最前面（user+assistant 对，避免连续 user 被合并）
        try:
            if settings.memory.mid_term.enabled:
                active_summary = self.active_summary(session_id)
                if active_summary:
                    history = [
                        {"role": "user", "content": f"[📋 之前对话摘要]\n{active_summary}"},
                        {"role": "assistant", "content": "好的，已了解之前对话的要点。"},
                        *history,
                    ]
        except Exception as e:
            if not self.tolerate_read_failure:
                raise
            logger.warning(f"读取 active summary 失败, sid={session_id}: {e}")

        # 提取 role=system 的历史标记消息（如转人工标记 transfer_to_human_marker），
        # 从对话序列中移除，改由上层拼接到 system_prompt 注入 LLM。
        # 不能转成 user 放进对话序列：会与其后的真实 user 形成连续 user，
        # 触发 _reorder_messages_for_llm 的连续-user 清洗，既产生告警又使标记失效。
        system_markers = [m for m in history if m.get("role") == "system"]
        if system_markers:
            history = [m for m in history if m.get("role") != "system"]

        messages = self._reorder_messages_for_llm(history)

        result_roles = []
        for m in messages:
            role = m.get('role', '?')
            content = m.get('content', '')
            if not isinstance(content, str):
                content = str(content)[:30]
            else:
                content = content[:30]
            result_roles.append(f"{role}:{content}")
        logger.debug(f"_build_messages result: session_id={session_id}, count={len(messages)}, msgs={result_roles}")

        return messages, system_markers

    _reorder_messages_for_llm = staticmethod(reorder_history)
