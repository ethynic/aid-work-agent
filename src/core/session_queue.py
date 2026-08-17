"""
渠道会话消息串行处理调度器

同一会话（session_id）的消息必须串行处理。采用「先发后等 + 可撤销」策略：
首条消息立即发给 LLM，短时间窗口内（2 秒）用户追加的消息合并后重新请求；
若已开始推送 SSE 到渠道则不取消，新消息排队作为下一轮对话处理。

核心数据结构（Redis）：
- session_lock:{sid}     — 会话锁，标识当前处理该 session 的 worker
- session_cancel:{sid}   — 取消标志
- session_merge:{sid}    — 合并缓冲区，存储原始用户输入
- session_pending:{sid}  — 排队中的最新用户输入（已开始推送 SSE 后的新消息）
- session_responding:{sid} — SSE 推送状态标记
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

from loguru import logger

from src.core.redis_client import redis_client
from src.core.temp_logger import tlog


_ATTACHMENTS_UNSET = object()


@dataclass
class EnqueueResult:
    """
    enqueue_and_process 的统一返回类型。

    字段说明：
        status: "success" 表示本调用方应继续发送回复；"merged" 表示本调用方无需发送回复；
                "error" 表示 processor 抛异常或处理失败，调用方应走错误路径（mark_error + 返回 error）
        response_text: 本轮最终回复文本（仅 success 非空）
        merged_input: 合并方实际处理的输入；独立处理时 == 原 user_input
        was_merged: 是否是合并方（持有锁并跑了最终响应，但用了合并后的输入）
        merged_attachments_meta: 合并方累积的附件元数据（被取消方的 attachments_meta 也并入）；
                                 独立处理时 == 调用方传入的 attachments_meta（或 None）
        merged_from_msgids: 合并方所有段的微信 msgid 列表（按顺序）；独立处理时为 None
        merged_segments: 合并方所有段 [{msgid, text}]；独立处理时为 None。
                         供持久化写入 channel_messages.metadata.merged_segments，撤回时按段重建。
        lease_token: success 时的 ownership token；调用方完成持久化和首个出站边界后
                     必须调用 finish_processing。merged/error 时为 None。
    """
    status: Literal["success", "merged", "error"]
    response_text: str = ""
    merged_input: str = ""
    was_merged: bool = False
    merged_attachments_meta: Optional[list] = None
    merged_from_msgids: Optional[list] = None
    merged_segments: Optional[list] = None
    lease_token: Optional[str] = None


class SessionMessageQueue:
    """渠道会话消息串行处理调度器"""

    # TTL 常量
    LOCK_TTL = 120          # 会话锁 2 分钟，防死锁
    # 取消标志 TTL 必须覆盖 processor 最长执行时间（含工具链如生成 Word/PPT/PDF）。
    # 历史问题：TTL=10s 时，processor 跑报价+生成文档耗时 >1 分钟，cancel 标志提前过期，
    # _handle_cancel_and_reprocess 入口 is_cancelled 返回 False 直接退出 →
    # 重处理分支不触发，追加合并的消息被永久丢弃。
    # 现 TTL 与 LOCK_TTL 对齐为 120s，并由 watchdog 在持锁期间持续续期，
    # 仅作为进程崩溃兜底；正常情况下由 release_lock 显式清除。
    CANCEL_TTL = 120
    MERGE_TTL = 120          # 合并缓冲区 2 分钟，必须覆盖 processor 整个执行时长
    PENDING_TTL = 30         # 排队消息 30 秒
    RESPONDING_TTL = 10      # 推送标记 10 秒
    MERGE_WINDOW = 2         # 合并窗口 2 秒
    PROCESSING_WAIT_TIMEOUT = 120  # 等待旧请求完成最大 120 秒
    # watchdog 续期间隔（秒）。小于 LOCK_TTL/CANCEL_TTL/MERGE_TTL 的一半，确保 TTL 不会过期。
    KEEPALIVE_INTERVAL = 30

    def __init__(self):
        # 内存中的活跃会话 cancel_check 注册表（同进程内即时取消）
        # session_id -> cancel_check callable
        self._active_cancel_checks: Dict[str, Callable[[], bool]] = {}

    # ==================== Redis Key 构建 ====================

    @staticmethod
    def _key(prefix: str, session_id: str) -> str:
        return redis_client.make_key(prefix, session_id)

    # ==================== 锁管理 ====================

    def acquire_lock(self, session_id: str) -> Optional[str]:
        """尝试获取会话锁。返回 lock_value（成功）或 None（失败）"""
        lock_key = self._key("session_lock", session_id)
        lock_value = f"{self._pid()}:{time.time()}"
        if redis_client.acquire_lock(lock_key, lock_value, ex=self.LOCK_TTL):
            return lock_value
        return None

    def release_lock(self, session_id: str, lock_value: str) -> bool:
        """释放会话锁"""
        lock_key = self._key("session_lock", session_id)
        # 诊断：释放锁前检查合并缓冲区是否仍有未消费内容
        # 若有，说明重处理期间又有新消息进入合并缓冲区但未被消费 → 该消息会丢失
        try:
            merge_key = self._key("session_merge", session_id)
            leftover = redis_client.get(merge_key)
            if leftover:
                data = json.loads(leftover) if isinstance(leftover, str) else leftover
                leftover_text = data.get("text", "")
                # tlog(
                #     "语音合并",
                #     "[release_lock] 释放锁时合并缓冲区仍有内容"
                #     " session={sid}..., leftover_len={l_len}, leftover_preview={l_prev!r}"
                #     "（若该内容已通过 user_input_override 消费则正常；否则为丢消息）",
                #     sid=session_id[:20],
                #     l_len=len(leftover_text),
                #     l_prev=leftover_text[:120],
                #     level="WARNING",
                # )
        except Exception as _e:
            # tlog("语音合并", "[release_lock] 检查残留合并缓冲区异常: {err}", err=str(_e), level="ERROR")
            pass
        # 先清取消标志、推送标记和合并缓冲区
        self._clear_cancel(session_id)
        self._clear_responding(session_id)
        self._clear_finalizing(session_id)
        self.clear_merge(session_id)
        return redis_client.release_lock(lock_key, lock_value)

    def is_locked(self, session_id: str) -> bool:
        """检查会话是否正在被处理"""
        lock_key = self._key("session_lock", session_id)
        return redis_client.exists(lock_key)

    @staticmethod
    def _pid() -> str:
        import os
        return str(os.getpid())

    # ==================== 合并缓冲区 ====================
    # 结构：{"text": str, "attachments_meta": List[Dict] | None, "timestamp": float,
    #        "segments": [{"msgid": str, "text": str}]}
    # text 为各段用 "\n\n[用户追加消息] " 拼接的合并输入（给 agent 用）
    # attachments_meta 为各段 attachments_meta 的并集（给持久化用）
    # segments 保留每段的微信 msgid 和原始文本，供撤回时按段重建 content

    def set_merge(
        self,
        session_id: str,
        text: str,
        attachments_meta: Optional[list] = None,
        msgid: str = "",
        agent_attachments: Any = _ATTACHMENTS_UNSET,
    ) -> None:
        """设置合并缓冲区（首次或覆盖）"""
        key = self._key("session_merge", session_id)
        payload = {
            "text": text,
            "attachments_meta": attachments_meta,
            "timestamp": time.time(),
            "segments": [{"msgid": msgid, "text": text}],
        }
        if agent_attachments is not _ATTACHMENTS_UNSET:
            payload["agent_attachments"] = agent_attachments
        data = json.dumps(payload, ensure_ascii=False)
        redis_client.set(key, data, ex=self.MERGE_TTL)

    def append_merge(
        self,
        session_id: str,
        new_text: str,
        new_attachments_meta: Optional[list] = None,
        new_msgid: str = "",
        new_agent_attachments: Any = _ATTACHMENTS_UNSET,
    ) -> str:
        """追加新文本到合并缓冲区，返回合并后的完整文本"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if existing:
            data = json.loads(existing) if isinstance(existing, str) else existing
            original = data.get("text", "")
            existing_meta = data.get("attachments_meta") or []
            existing_segments = data.get("segments") or []
            existing_agent_attachments = data.get("agent_attachments", _ATTACHMENTS_UNSET)
            # 用显式分隔标记拼接，让 LLM 能识别这是用户在短时间内连续发送的
            # 多条独立消息，而非单条多句消息。避免 LLM 只处理最后一个意图
            # 而忽略前面的指令（如"不想去小七孔了。\n天眼那边住的酒店是哪一间？"）
            merged = original + "\n\n[用户追加消息] " + new_text
            merged_meta = list(existing_meta)
            if new_attachments_meta:
                merged_meta.extend(new_attachments_meta)
            merged_segments = list(existing_segments) + [{"msgid": new_msgid, "text": new_text}]
            if existing_agent_attachments is _ATTACHMENTS_UNSET:
                merged_agent_attachments = new_agent_attachments
            elif new_agent_attachments is _ATTACHMENTS_UNSET:
                merged_agent_attachments = existing_agent_attachments
            else:
                merged_agent_attachments = list(existing_agent_attachments or [])
                merged_agent_attachments.extend(new_agent_attachments or [])
        else:
            merged = new_text
            merged_meta = list(new_attachments_meta) if new_attachments_meta else []
            merged_segments = [{"msgid": new_msgid, "text": new_text}]
            merged_agent_attachments = new_agent_attachments
        # 写回缓冲区（含 segments）
        key = self._key("session_merge", session_id)
        payload = {
            "text": merged,
            "attachments_meta": merged_meta,
            "timestamp": time.time(),
            "segments": merged_segments,
        }
        if merged_agent_attachments is not _ATTACHMENTS_UNSET:
            payload["agent_attachments"] = merged_agent_attachments
        data = json.dumps(payload, ensure_ascii=False)
        redis_client.set(key, data, ex=self.MERGE_TTL)
        # tlog(
        #     "语音合并",
        #     "追加合并 session={sid}..., new_text_len={n_len}, "
        #     "new_text_preview={n_prev!r}, merged_len={m_len}, merged_preview={m_prev!r}, "
        #     "existing_meta_count={em_n}, new_meta_count={nm_n}, merged_meta_count={mm_n}, "
        #     "segment_count={seg_n}",
        #     sid=session_id[:20],
        #     n_len=len(new_text),
        #     n_prev=new_text[:50],
        #     m_len=len(merged),
        #     m_prev=merged[:80],
        #     em_n=len(existing_meta) if existing else 0,
        #     nm_n=len(new_attachments_meta) if new_attachments_meta else 0,
        #     mm_n=len(merged_meta),
        #     seg_n=len(merged_segments),
        # )
        # Bug 1 验证：重处理期间到达的消息应累积到现有缓冲区（em_n > 0），
        # 若 em_n=0 说明缓冲区被提前清空（Bug 1 复发）
        # if not existing:
            # tlog(
            #     "语音合并",
            #     "[append_merge 警告] 缓冲区为空，新建缓冲区 session={sid}, "
            #     "new_text_preview={n_prev!r}（若此时正在重处理，说明缓冲区被提前清空）",
            #     sid=session_id[:20],
            #     n_prev=new_text[:50],
            #     level="WARNING",
            # )
        return merged

    def get_merged_input(self, session_id: str, default: str) -> str:
        """获取合并后的完整用户输入，无合并数据时返回 default"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if existing:
            data = json.loads(existing) if isinstance(existing, str) else existing
            return data.get("text", default)
        return default

    def get_merged_attachments_meta(self, session_id: str) -> Optional[list]:
        """获取合并缓冲区累积的附件元数据，无合并数据时返回 None"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if not existing:
            return None
        data = json.loads(existing) if isinstance(existing, str) else existing
        meta = data.get("attachments_meta")
        return list(meta) if meta else None

    def get_merged_agent_attachments(self, session_id: str) -> Any:
        """获取传给 Agent 的合并附件；旧 Redis 格式返回未设置标记。"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if not existing:
            return _ATTACHMENTS_UNSET
        data = json.loads(existing) if isinstance(existing, str) else existing
        return data.get("agent_attachments", _ATTACHMENTS_UNSET)

    def get_merged_segments(self, session_id: str) -> Optional[list]:
        """获取合并缓冲区的分段列表 [{msgid, text}]，无合并数据或旧格式返回 None"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if not existing:
            return None
        data = json.loads(existing) if isinstance(existing, str) else existing
        segments = data.get("segments")
        return list(segments) if segments else None

    def remove_merge_segment(self, session_id: str, msgid: str) -> bool:
        """从合并缓冲区移除指定 msgid 的段（撤回兜底）。

        段被移除后，剩余段用 "\n\n[用户追加消息] " 重新拼接 text。
        若所有段都被移除，clear_merge + set_cancel（取消正在跑的 agent）。

        Returns:
            True 表示命中并移除；False 表示缓冲区不存在、旧格式无 segments、或无匹配段。
        """
        if not msgid:
            return False
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if not existing:
            return False
        data = json.loads(existing) if isinstance(existing, str) else existing
        segments = data.get("segments")
        if not segments:
            # 旧格式缓冲区无 segments，无法部分撤回
            return False
        remaining = [seg for seg in segments if seg.get("msgid") != msgid]
        if len(remaining) == len(segments):
            # 无匹配段
            return False

        if not remaining:
            # 所有段都被撤回：清缓冲区 + 设置取消标志（触发重处理时 processor 拿到空输入，
            # 由调用方决定如何处理；此处仅清理状态）
            self.clear_merge(session_id)
            self.set_cancel(session_id)
            # tlog(
            #     "撤回消息",
            #     "合并缓冲区所有段被撤回，清空缓冲区并设置取消: session_id={sid}, msgid={msgid}",
            #     sid=session_id[:20],
            #     msgid=msgid,
            # )
            return True

        # 重新拼接 text：第一段原样，后续段加 "[用户追加消息] " 前缀（保持 append_merge 风格）
        new_text = remaining[0].get("text", "")
        for seg in remaining[1:]:
            new_text += "\n\n[用户追加消息] " + seg.get("text", "")
        data["text"] = new_text
        data["segments"] = remaining
        data["timestamp"] = time.time()
        redis_client.set(key, json.dumps(data, ensure_ascii=False), ex=self.MERGE_TTL)
        # tlog(
        #     "撤回消息",
        #     "合并缓冲区移除段并重建: session_id={sid}, msgid={msgid}, "
        #     "segments_before={before}, segments_after={after}, new_text_len={t_len}",
        #     sid=session_id[:20],
        #     msgid=msgid,
        #     before=len(segments),
        #     after=len(remaining),
        #     t_len=len(new_text),
        # )
        return True

    def clear_merge(self, session_id: str) -> None:
        """清除合并缓冲区"""
        key = self._key("session_merge", session_id)
        redis_client.delete(key)

    # ==================== 取消标志 ====================

    def set_cancel(self, session_id: str) -> None:
        """设置取消标志"""
        key = self._key("session_cancel", session_id)
        redis_client.set(key, "1", ex=self.CANCEL_TTL)

    def is_cancelled(self, session_id: str) -> bool:
        """检查是否被取消（检查 Redis 标志）"""
        key = self._key("session_cancel", session_id)
        return redis_client.exists(key)

    def _clear_cancel(self, session_id: str) -> None:
        """清除取消标志"""
        key = self._key("session_cancel", session_id)
        redis_client.delete(key)

    def is_cancel_allowed(self, session_id: str) -> bool:
        """是否允许取消：只有尚未推送 SSE 时才允许取消"""
        responding_key = self._key("session_responding", session_id)
        return (
            not redis_client.exists(responding_key)
            and not self.is_finalizing(session_id)
        )

    # ==================== 推送状态标记 ====================

    def mark_responding(self, session_id: str) -> None:
        """标记已开始推送响应到渠道（取消阈值）"""
        key = self._key("session_responding", session_id)
        redis_client.set(key, "1", ex=self.RESPONDING_TTL)

    def mark_idle(self, session_id: str) -> None:
        """标记推送完成，恢复空闲状态"""
        self._clear_responding(session_id)

    def is_responding(self, session_id: str) -> bool:
        """检查是否正在推送中"""
        key = self._key("session_responding", session_id)
        return redis_client.exists(key)

    def _clear_responding(self, session_id: str) -> None:
        """清除推送标记"""
        key = self._key("session_responding", session_id)
        redis_client.delete(key)

    def is_finalizing(self, session_id: str) -> bool:
        return redis_client.exists(self._key("session_finalizing", session_id))

    def _clear_finalizing(self, session_id: str) -> None:
        redis_client.delete(self._key("session_finalizing", session_id))

    def finish_processing(self, session_id: str, lease_token: Optional[str]) -> None:
        """持久化和首个出站边界完成后释放 queue ownership。"""
        if not lease_token:
            return
        released = redis_client.session_release_finalized(
            self._key("session_lock", session_id),
            lease_token,
            self._key("session_cancel", session_id),
            self._key("session_merge", session_id),
            self._key("session_responding", session_id),
            self._key("session_finalizing", session_id),
        )
        if not released:
            logger.error(
                f"[SessionQueue] finalizing ownership release failed "
                f"session={session_id[:20]}"
            )

    async def _acquire_state_guard(self, session_id: str) -> tuple[str, str]:
        key = self._key("session_state_guard", session_id)
        while True:
            value = f"{self._pid()}:{time.time()}"
            if redis_client.acquire_lock(key, value, ex=5):
                return key, value
            # 只让出事件循环；正确性依赖 guard/Redis 原子检查，不依赖等待时长。
            await asyncio.sleep(0)

    async def _wait_finalizing_end(self, session_id: str) -> None:
        deadline = time.time() + self.PROCESSING_WAIT_TIMEOUT
        while self.is_finalizing(session_id) and time.time() < deadline:
            await asyncio.sleep(0.05)

    def _try_mark_finalizing(
        self, session_id: str, lock_value: str, expected_input: str
    ) -> bool:
        return redis_client.session_finalize_if_quiet(
            self._key("session_lock", session_id),
            lock_value,
            self._key("session_cancel", session_id),
            self._key("session_merge", session_id),
            self._key("session_pending", session_id),
            self._key("session_finalizing", session_id),
            self._key("session_state_guard", session_id),
            expected_input,
            self.LOCK_TTL,
        )

    # ==================== Pending 队列 ====================

    def set_pending(
        self,
        session_id: str,
        text: str,
        attachments_meta: Optional[list] = None,
        msgid: str = "",
        agent_attachments: Any = _ATTACHMENTS_UNSET,
    ) -> None:
        """累积排队消息（已开始推送后到达的多条消息不得互相覆盖）。"""
        key = self._key("session_pending", session_id)
        lock_key = self._key("session_pending_write_lock", session_id)
        lock_value = f"{self._pid()}:{time.time()}"
        acquired = False
        for _ in range(100):
            if redis_client.acquire_lock(lock_key, lock_value, ex=5):
                acquired = True
                break
            time.sleep(0.01)
        if not acquired:
            raise RuntimeError(f"pending write lock timeout session={session_id[:20]}")
        try:
            existing = redis_client.get(key)
            data = json.loads(existing) if isinstance(existing, str) else (existing or {})
            old_text = data.get("text", "")
            merged_text = (
                old_text + "\n\n[用户追加消息] " + text if old_text else text
            )
            merged_meta = list(data.get("attachments_meta") or [])
            if attachments_meta:
                merged_meta.extend(attachments_meta)
            segments = list(data.get("segments") or [])
            if not segments and old_text:
                segments.append({"msgid": data.get("msgid", ""), "text": old_text})
            segments.append({"msgid": msgid, "text": text})
            payload = {
                "text": merged_text,
                "attachments_meta": merged_meta or None,
                "msgid": msgid,
                "segments": segments,
                "timestamp": time.time(),
            }
            existing_agent_attachments = data.get(
                "agent_attachments", _ATTACHMENTS_UNSET
            )
            if existing_agent_attachments is _ATTACHMENTS_UNSET:
                merged_agent_attachments = agent_attachments
            elif agent_attachments is _ATTACHMENTS_UNSET:
                merged_agent_attachments = existing_agent_attachments
            else:
                merged_agent_attachments = list(existing_agent_attachments or [])
                merged_agent_attachments.extend(agent_attachments or [])
            if merged_agent_attachments is not _ATTACHMENTS_UNSET:
                payload["agent_attachments"] = merged_agent_attachments
            redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=self.PENDING_TTL)
        finally:
            redis_client.release_lock(lock_key, lock_value)

    def get_pending_payload(self, session_id: str) -> Optional[dict]:
        """获取并清除 pending；兼容仅含 text/timestamp 的旧 Redis 格式。"""
        key = self._key("session_pending", session_id)
        lock_key = self._key("session_pending_write_lock", session_id)
        lock_value = f"{self._pid()}:{time.time()}"
        acquired = False
        for _ in range(100):
            if redis_client.acquire_lock(lock_key, lock_value, ex=5):
                acquired = True
                break
            time.sleep(0.01)
        if not acquired:
            raise RuntimeError(f"pending read lock timeout session={session_id[:20]}")
        try:
            existing = redis_client.get(key)
            if not existing:
                return None
            redis_client.delete(key)
        finally:
            redis_client.release_lock(lock_key, lock_value)
        data = json.loads(existing) if isinstance(existing, str) else existing
        if not isinstance(data, dict):
            return None
        payload = {
            "text": data.get("text", ""),
            "attachments_meta": data.get("attachments_meta") or None,
            "msgid": data.get("msgid", ""),
            "segments": data.get("segments") or None,
        }
        if "agent_attachments" in data:
            payload["agent_attachments"] = data["agent_attachments"]
        return payload

    def get_pending(self, session_id: str) -> Optional[str]:
        """获取并清除排队消息"""
        payload = self.get_pending_payload(session_id)
        return payload.get("text") if payload else None

    def has_pending(self, session_id: str) -> bool:
        """检查是否有排队消息"""
        key = self._key("session_pending", session_id)
        return redis_client.exists(key)

    # ==================== Cancel Check 注册 ====================

    def register_cancel_check(self, session_id: str, cancel_check: Callable[[], bool]) -> None:
        """注册当前活跃会话的 cancel_check（同进程即时取消）"""
        self._active_cancel_checks[session_id] = cancel_check

    def unregister_cancel_check(self, session_id: str) -> None:
        """注销 cancel_check"""
        self._active_cancel_checks.pop(session_id, None)

    def check_cancel(self, session_id: str) -> bool:
        """取消检查：Redis 标志 + 内存注册 cancel_check（供 channel_routes 使用）"""
        # 1. 检查 Redis 取消标志（跨进程）
        if self.is_cancelled(session_id):
            return True
        # 2. 检查内存注册的 cancel_check（同进程）
        func = self._active_cancel_checks.get(session_id)
        if func and func():
            return True
        return False

    # ==================== 核心调度方法 ====================

    async def _handle_cancel_and_reprocess(
        self,
        session_id: str,
        original_input: str,
        processor,
        on_before_reprocess=None,
    ) -> Optional[tuple]:
        """
        处理器返回后检查是否需要重新处理（取消 + 有合并输入）。

        循环重处理：每次重处理前若有 cancel + 新合并输入，则用新输入重新跑 processor；
        重处理后再次检查，若又被取消且有更新的合并输入，继续重处理。
        直到无 cancel 或无新合并输入为止，避免重处理期间到达的新消息被丢弃。

        返回 (response, merged_input, merged_attachments_meta, merged_from_msgids,
        merged_segments, agent_attachments) 元组，
        或 None（不需要重处理）。
        """
        # 检查是否被取消
        if not self.is_cancelled(session_id):
            return None
        # 检查是否有合并输入
        merge_key = self._key("session_merge", session_id)
        existing = redis_client.get(merge_key)
        if not existing:
            return None
        data = json.loads(existing) if isinstance(existing, str) else existing
        merged_input = data.get("text", "")
        merged_meta = data.get("attachments_meta") or None
        merged_agent_attachments = data.get("agent_attachments", _ATTACHMENTS_UNSET)
        merged_segments = data.get("segments") or None
        # 检查合并输入是否与原始输入不同
        if merged_input == original_input:
            return None

        # 循环重处理
        iteration = 0
        max_iterations = 10  # 防止极端情况下死循环
        current_merged_input = merged_input
        current_merged_meta = merged_meta
        current_agent_attachments = merged_agent_attachments
        current_merged_segments = merged_segments
        response = ""
        while iteration < max_iterations:
            iteration += 1
            # tlog(
            #     "语音合并",
            #     "[重处理] 第 {iter} 轮 session={sid}..., "
            #     "merged_len={m_len}, merged_preview={m_prev!r}, merged_meta_count={mm_n}",
            #     iter=iteration,
            #     sid=session_id[:20],
            #     m_len=len(current_merged_input),
            #     m_prev=current_merged_input[:80],
            #     mm_n=len(current_merged_meta) if current_merged_meta else 0,
            # )
            # 不清除合并缓冲区：重处理期间新到达的消息需要 append_merge 累积到现有缓冲区，
            # 否则 append_merge 走 else 分支（新建），上一轮的合并内容会丢失。
            # 重复处理由下方 new_merged_input == current_merged_input 检查兜底。
            # 清除取消标志：重新处理是新一轮完整处理，不应继承上一轮的取消状态，
            # 否则新 processor 会在 cancel_check 时立即返回，造成 response_text 为空
            self._clear_cancel(session_id)
            if on_before_reprocess is not None:
                try:
                    on_before_reprocess()
                except Exception as e:
                    logger.debug(
                        f"[SessionQueue] merge trace callback failed session={session_id[:20]}: {e}"
                    )
            cancel_check = lambda: self.check_cancel(session_id)
            # 关键：通过 user_input_override 把合并后的完整输入传给 processor，
            # 否则 processor 闭包绑定的还是原始输入，合并内容会被丢弃
            processor_kwargs = {"user_input_override": current_merged_input}
            if current_agent_attachments is not _ATTACHMENTS_UNSET:
                processor_kwargs["agent_attachments_override"] = current_agent_attachments
            response = processor(cancel_check, **processor_kwargs)
            if asyncio.iscoroutine(response):
                response = await response
            # tlog(
            #     "语音合并",
            #     "[重处理] 第 {iter} 轮完成 session={sid}..., "
            #     "response_len={r_len}, response_preview={r_prev!r}, "
            #     "was_cancelled_after={cancelled}",
            #     iter=iteration,
            #     sid=session_id[:20],
            #     r_len=len(response) if response else 0,
            #     r_prev=(response or "")[:80],
            #     cancelled=self.is_cancelled(session_id),
            # )
            # 验证：processor 执行期间合并缓冲区是否被保留（Bug 1 修复后应为 True）
            # 若 preserved=False 且 has_new_content=True，说明缓冲区被提前清空（Bug 1 复发）
            try:
                _buffer_after = redis_client.get(self._key("session_merge", session_id))
                _buffer_after_text = ""
                if _buffer_after:
                    _data_ba = json.loads(_buffer_after) if isinstance(_buffer_after, str) else _buffer_after
                    _buffer_after_text = _data_ba.get("text", "")
                _preserved = bool(current_merged_input) and _buffer_after_text.startswith(current_merged_input)
                # tlog(
                #     "语音合并",
                #     "[重处理] 第{iter}轮 processor 后缓冲区验证 session={sid}, "
                #     "buffer_preserved={preserved}, buffer_len={bl}, buffer_preview={bp!r}, "
                #     "prev_merged_len={pm_len}, has_new_content={has_new}",
                #     iter=iteration,
                #     sid=session_id[:20],
                #     preserved=_preserved,
                #     bl=len(_buffer_after_text),
                #     bp=_buffer_after_text[:80],
                #     pm_len=len(current_merged_input),
                #     has_new=_buffer_after_text != current_merged_input,
                # )
            except Exception as _e:
                # tlog("语音合并", "[重处理] 缓冲区验证异常: {err}", err=str(_e), level="ERROR")
                pass
            # 重处理后若被取消，检查是否有新合并输入；有则继续重处理，无则跳出
            if not self.is_cancelled(session_id):
                break
            merge_key2 = self._key("session_merge", session_id)
            existing2 = redis_client.get(merge_key2)
            if not existing2:
                break
            data2 = json.loads(existing2) if isinstance(existing2, str) else existing2
            new_merged_input = data2.get("text", "")
            new_merged_meta = data2.get("attachments_meta") or None
            new_agent_attachments = data2.get("agent_attachments", _ATTACHMENTS_UNSET)
            new_merged_segments = data2.get("segments") or None
            # if new_merged_input == current_merged_input:
                # 取消标志存在但没有新内容，跳出（避免无意义重跑）
                # tlog(
                #     "语音合并",
                #     "[重处理] 第 {iter} 轮后取消但无新内容，跳出 session={sid}...",
                #     iter=iteration,
                #     sid=session_id[:20],
                #     level="WARNING",
                # )
                # break
            # tlog(
            #     "语音合并",
            #     "[重处理] 第 {iter} 轮后又取消且有新合并输入，继续重处理 session={sid}..., "
            #     "old_merged_len={om_len}, new_merged_len={nm_len}, new_merged_preview={nm_prev!r}",
            #     iter=iteration,
            #     sid=session_id[:20],
            #     om_len=len(current_merged_input),
            #     nm_len=len(new_merged_input),
            #     nm_prev=new_merged_input[:80],
            # )
            current_merged_input = new_merged_input
            current_merged_meta = new_merged_meta
            current_agent_attachments = new_agent_attachments
            current_merged_segments = new_merged_segments

        # 提取 msgid 列表
        current_merged_from_msgids = None
        if current_merged_segments:
            current_merged_from_msgids = [seg.get("msgid", "") for seg in current_merged_segments]

        return (response, current_merged_input, current_merged_meta,
                current_merged_from_msgids, current_merged_segments,
                current_agent_attachments)

    async def enqueue_and_process(
        self,
        session_id: str,
        user_input: str,
        processor,
        attachments_meta: Optional[list] = None,
        msgid: str = "",
        on_before_reprocess=None,
        agent_attachments: Any = _ATTACHMENTS_UNSET,
    ) -> EnqueueResult:
        """
        渠道消息入口调度。

        Args:
            session_id: 会话 ID
            user_input: 用户输入文本
            processor: 异步回调函数，签名 (cancel_check: Callable, user_input_override: Optional[str]) -> str
                负责调用 agent.process_message_sync 并返回回复文本。
                cancel_check 是用于检测取消的函数，processor 需将其传给 process_message_sync。
            attachments_meta: 本条用户消息的附件元数据（含 local_path、media_id 等，不含 base64）。
                合并方会把被取消方的 attachments_meta 累积进来，最终透传给调用方用于持久化。
            agent_attachments: 传给 Agent 的附件输入。调用方未提供时保持旧 processor
                调用签名；提供后会随 merge/pending 跨 worker 传递并通过 override 回调。
            msgid: 本条用户消息的微信 msgid，合并方会把所有段的 msgid 累积进 merged_from_msgids，
                供持久化写入 metadata.merged_from_msgids，撤回时按段重建。

        Returns:
            EnqueueResult。
                status="success": 本调用方为最终回复方，应发送 response_text
                status="merged": 本调用方消息已被合并/排队，无需发送回复

        流程：
        1. 空闲态：获取锁 -> 启动合并窗口 -> 处理 -> 返回 success
        2. 处理中 + 允许取消：设置取消 + 更新合并缓冲区 -> 等待旧请求结束
           （旧请求检测到取消后，用合并后的输入重新处理，本调用方返回 merged）
        3. 处理中 + 不允许取消（已开始推送）：设置 pending -> 等待旧请求结束
           （旧请求完成后检测 pending 并处理，本调用方返回 merged）
        """
        # 尝试获取锁（首次尝试）
        lock_value = self.acquire_lock(session_id)

        if lock_value is not None:
            # === 空闲态，首次请求 ===
            self._clear_cancel(session_id)
            self._clear_finalizing(session_id)
            # 启动 watchdog，持锁期间续期 lock/cancel/merge 的 TTL，
            # 避免长耗时 processor（生成 Word/PPT/PDF 等）导致 cancel/merge 标志提前过期，
            # 进而触发「追加消息被丢弃」的 bug。
            watchdog_task = asyncio.create_task(
                self._keep_alive_loop(session_id, lock_value)
            )
            # 设置合并缓冲区
            self.set_merge(
                session_id, user_input, attachments_meta, msgid=msgid,
                agent_attachments=agent_attachments,
            )
            # 等待合并窗口，期间可能有追加消息
            await self._wait_merge_window(session_id)
            # 获取最终合并后的输入
            final_input = self.get_merged_input(session_id, user_input)
            final_meta = self.get_merged_attachments_meta(session_id)
            final_agent_attachments = self.get_merged_agent_attachments(session_id)
            final_segments = self.get_merged_segments(session_id)
            was_merged = final_input != user_input
            # 合并方才有 merged_from_msgids / merged_segments
            final_from_msgids = None
            if was_merged and final_segments:
                final_from_msgids = [seg.get("msgid", "") for seg in final_segments]
            # tlog(
            #     "语音合并",
            #     "空闲态处理 session={sid}..., original_len={o_len}, "
            #     "final_len={f_len}, merged={merged}, final_preview={f_prev!r}, "
            #     "original_meta_count={om_n}, final_meta_count={fm_n}, segment_count={seg_n}",
            #     sid=session_id[:20],
            #     o_len=len(user_input),
            #     f_len=len(final_input),
            #     merged=was_merged,
            #     f_prev=final_input[:80],
            #     om_n=len(attachments_meta) if attachments_meta else 0,
            #     fm_n=len(final_meta) if final_meta else 0,
            #     seg_n=len(final_segments) if final_segments else 0,
            # )

            # P0-5：用 error_result 记录 processor 异常时的返回值。
            # 调用方（process_and_persist）拿到 status="error" 会走错误路径（mark_error + 返回 error），
            # 不会误判为 merged 而跳过 user 写入 / 不调 mark_error。
            error_result: Optional[EnqueueResult] = None
            try:
                # 调用 processor（process_message_sync）
                # 传入 user_input_override=final_input 确保 processor 使用合并后的输入，
                # 而非闭包绑定的原始 user_input（语音合并场景的关键）
                cancel_check = lambda: self.check_cancel(session_id)
                try:
                    processor_kwargs = {"user_input_override": final_input}
                    if final_agent_attachments is not _ATTACHMENTS_UNSET:
                        processor_kwargs["agent_attachments_override"] = final_agent_attachments
                    response = processor(cancel_check, **processor_kwargs)
                    if asyncio.iscoroutine(response):
                        response = await response
                except Exception as e:
                    logger.opt(exception=True).error(
                        f"[SessionQueue] processor 异常 session={session_id[:20]}...: {e}",
                    )
                    error_result = EnqueueResult(
                        status="error",
                        response_text="",
                        merged_input=final_input,
                        was_merged=was_merged,
                        merged_attachments_meta=final_meta,
                        merged_from_msgids=final_from_msgids,
                        merged_segments=final_segments,
                    )
                else:
                    # processor 成功，立即记录正常返回值
                    success_result = EnqueueResult(
                        status="success",
                        response_text=response or "",
                        merged_input=final_input,
                        was_merged=was_merged,
                        merged_attachments_meta=final_meta,
                        merged_from_msgids=final_from_msgids,
                        merged_segments=final_segments,
                    )
            finally:
                if not watchdog_task.done():
                    watchdog_task.cancel()
                    try:
                        await watchdog_task
                    except (asyncio.CancelledError, Exception):
                        pass

            if error_result is not None:
                self.release_lock(session_id, lock_value)
                return error_result

            current = success_result
            while True:
                try:
                    reprocessed = await self._handle_cancel_and_reprocess(
                        session_id, current.merged_input, processor,
                        on_before_reprocess,
                    )
                except Exception as e:
                    logger.opt(exception=True).error(
                        f"[SessionQueue] cancel 重处理异常 session={session_id[:20]}...: {e}",
                    )
                    self.release_lock(session_id, lock_value)
                    return EnqueueResult(
                        status="error", merged_input=current.merged_input,
                        was_merged=True,
                        merged_attachments_meta=current.merged_attachments_meta,
                        merged_from_msgids=current.merged_from_msgids,
                        merged_segments=current.merged_segments,
                    )

                if reprocessed is not None:
                    (text, merged_input, merged_meta, from_msgids, segments,
                     _agent_attachments) = reprocessed
                    current = EnqueueResult(
                        status="success", response_text=text or "",
                        merged_input=merged_input, was_merged=True,
                        merged_attachments_meta=merged_meta,
                        merged_from_msgids=from_msgids,
                        merged_segments=segments,
                    )
                    continue

                # Redis 原子确认：没有晚到 cancel/merge/pending 后切 finalizing。
                # 成功后 ownership 交给 ChannelSessionManager，直到持久化和首个出站完成。
                if self._try_mark_finalizing(
                    session_id, lock_value, current.merged_input
                ):
                    current.lease_token = lock_value
                    return current

                if self.has_pending(session_id):
                    pending_payload = self.get_pending_payload(session_id)
                    pending_input = pending_payload.get("text") if pending_payload else None
                    if pending_input:
                        pending_meta = pending_payload.get("attachments_meta")
                        pending_msgid = pending_payload.get("msgid", "")
                        pending_segments = pending_payload.get("segments")
                        pending_agent_attachments = pending_payload.get(
                            "agent_attachments", []
                        )
                        self.clear_merge(session_id)
                        self.set_merge(
                            session_id, pending_input, pending_meta,
                            msgid=pending_msgid,
                            agent_attachments=pending_agent_attachments,
                        )
                        if pending_segments:
                            merge_key = self._key("session_merge", session_id)
                            merge_data = redis_client.get(merge_key)
                            merge_data = json.loads(merge_data) if isinstance(
                                merge_data, str
                            ) else merge_data
                            merge_data["segments"] = pending_segments
                            redis_client.set(
                                merge_key, json.dumps(merge_data, ensure_ascii=False),
                                ex=self.MERGE_TTL,
                            )
                        try:
                            kwargs = {"user_input_override": pending_input}
                            kwargs["agent_attachments_override"] = pending_agent_attachments
                            pending_response = processor(
                                lambda: self.check_cancel(session_id), **kwargs
                            )
                            if asyncio.iscoroutine(pending_response):
                                pending_response = await pending_response
                        except Exception as e:
                            logger.opt(exception=True).error(
                                f"[SessionQueue] pending processor 异常 session={session_id[:20]}...: {e}",
                            )
                            self.release_lock(session_id, lock_value)
                            return EnqueueResult(
                                status="error", merged_input=pending_input,
                                merged_attachments_meta=pending_meta,
                            )
                        current = EnqueueResult(
                            status="success", response_text=pending_response or "",
                            merged_input=pending_input, was_merged=False,
                            merged_attachments_meta=pending_meta,
                            merged_from_msgids=[
                                seg.get("msgid", "") for seg in pending_segments
                            ] if pending_segments else (
                                [pending_msgid] if pending_msgid else None
                            ),
                            merged_segments=pending_segments,
                        )
                        continue

                # state guard 正由 follower 持有；让出调度后重新检查。
                await asyncio.sleep(0)

        else:
            # === 处理中态，追加消息 ===
            guard_key, guard_value = await self._acquire_state_guard(session_id)
            try:
                if self.is_finalizing(session_id):
                    mode = "finalizing"
                elif not self.is_locked(session_id):
                    mode = "retry"
                elif self.is_cancel_allowed(session_id):
                    # set_cancel + append_merge 与 owner 的 finalizing 切换由
                    # session_state_guard / Redis Lua 串行化，避免最终检查后丢消息。
                    self.set_cancel(session_id)
                    self.append_merge(
                        session_id, user_input, attachments_meta, new_msgid=msgid,
                        new_agent_attachments=agent_attachments,
                    )
                    mode = "merged"
                else:
                    self.set_pending(
                        session_id, user_input, attachments_meta, msgid=msgid,
                        agent_attachments=agent_attachments,
                    )
                    mode = "pending"
            finally:
                redis_client.release_lock(guard_key, guard_value)

            if mode in {"finalizing", "retry"}:
                if mode == "finalizing":
                    await self._wait_finalizing_end(session_id)
                return await self.enqueue_and_process(
                    session_id=session_id,
                    user_input=user_input,
                    processor=processor,
                    attachments_meta=attachments_meta,
                    msgid=msgid,
                    on_before_reprocess=on_before_reprocess,
                    agent_attachments=agent_attachments,
                )

            if mode == "merged":
                # 尚未开始推送，可以取消
                # tlog(
                #     "语音合并",
                #     "处理中态（允许取消）session={sid}..., "
                #     "input_len={i_len}, input_preview={i_prev!r}, meta_count={m_n}",
                #     sid=session_id[:20],
                #     i_len=len(user_input),
                #     i_prev=user_input[:50],
                #     m_n=len(attachments_meta) if attachments_meta else 0,
                # )
                # 等待旧请求完成
                await self._wait_for_processing_end(session_id)
                # 旧请求应已处理合并后的输入，本调用方无需发送回复
                return EnqueueResult(status="merged")
            else:
                # 已开始推送，不允许取消，连同附件元数据和 msgid 加入 pending
                logger.info(
                    f"[SessionQueue] 处理中态（已推送，排队）"
                    f"session={session_id[:20]}..., input_len={len(user_input)}"
                )
                # 等待旧请求完成
                await self._wait_for_processing_end(session_id)
                return EnqueueResult(status="merged")

    async def _keep_alive_loop(self, session_id: str, lock_value: str) -> None:
        """持锁期间续期 lock/cancel/merge 的 TTL，避免长耗时 processor 导致标志过期。

        背景：processor 可能因工具链（生成 Word/PPT/PDF、跑报价）耗时数分钟，
        若期间 cancel/merge 标志 TTL 过期，会导致：
        - cancel 过期 → _handle_cancel_and_reprocess 入口直接返回 None，追加消息被丢弃
        - merge 过期 → append_merge 新建缓冲区，丢历史合并内容
        - lock 过期 → 其它 worker 抢锁成功，并发处理同一会话

        解决：持锁 worker 启动本协程，每 KEEPALIVE_INTERVAL 秒续期三个 key 的 TTL。
        正常退出路径（release_lock）会先 cancel 本协程，故不会误续期他人持有的锁；
        worker 崩溃时本协程随之终止，TTL 兜底自然过期。

        续期失败（如 Redis 抖动）仅记录告警，不中断循环——后续轮次会重试。
        """
        while True:
            try:
                await asyncio.sleep(self.KEEPALIVE_INTERVAL)
                # 校验锁仍属于本 worker：lock_value 匹配才续期，防止误续期
                # （极端场景：watchdog 未及时 cancel，锁已被 release 并被他人 acquire）
                lock_key = self._key("session_lock", session_id)
                # redis_client.get 会 json.loads，对纯字符串 lock_value 会失败返回 None，
                # 故用底层 raw 读取做校验更稳。降级到只检查存在性。
                lock_alive = redis_client.exists(lock_key)
                if not lock_alive:
                    # tlog(
                    #     "语音合并",
                    #     "[watchdog] 锁已不存在，停止续期 session={sid}",
                    #     sid=session_id[:20],
                    #     level="WARNING",
                    # )
                    return
                # 续期 lock/cancel/merge 三个 key；cancel/merge 可能已被清（正常），
                # expire 对不存在的 key 是 no-op，不会重建
                redis_client.expire(lock_key, self.LOCK_TTL)
                redis_client.expire(
                    self._key("session_cancel", session_id), self.CANCEL_TTL
                )
                redis_client.expire(
                    self._key("session_merge", session_id), self.MERGE_TTL
                )
            except asyncio.CancelledError:
                # 正常退出路径（release_lock 前 cancel watchdog）
                raise
            # except Exception as e:
                # tlog(
                #     "语音合并",
                #     "[watchdog] 续期异常 session={sid}, err={err}",
                #     sid=session_id[:20],
                #     err=str(e),
                #     level="ERROR",
                # )

    async def _wait_merge_window(self, session_id: str) -> None:
        """等待合并窗口，期间持续检查取消"""
        start = time.time()
        while time.time() - start < self.MERGE_WINDOW:
            await asyncio.sleep(0.2)
            # 如果在窗口期内被取消（理论上不应该发生，因为持有锁），立即退出
            if self.is_cancelled(session_id):
                logger.warning(
                    f"[SessionQueue] 合并窗口期内检测到取消（异常）session={session_id[:20]}..."
                )
                break

    async def _wait_for_processing_end(self, session_id: str, timeout: int = None) -> None:
        """等待旧请求完成（轮询锁状态）"""
        if timeout is None:
            timeout = self.PROCESSING_WAIT_TIMEOUT
        start = time.time()
        lock_key = self._key("session_lock", session_id)
        while time.time() - start < timeout:
            if not redis_client.exists(lock_key):
                return
            await asyncio.sleep(0.5)
        logger.warning(
            f"[SessionQueue] 等待旧请求超时 session={session_id[:20]}..., "
            f"继续执行"
        )


# 全局单例
session_queue = SessionMessageQueue()
