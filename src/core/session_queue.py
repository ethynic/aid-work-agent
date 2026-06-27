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
    """
    status: Literal["success", "merged", "error"]
    response_text: str = ""
    merged_input: str = ""
    was_merged: bool = False
    merged_attachments_meta: Optional[list] = None


class SessionMessageQueue:
    """渠道会话消息串行处理调度器"""

    # TTL 常量
    LOCK_TTL = 120          # 会话锁 2 分钟，防死锁
    CANCEL_TTL = 10          # 取消标志 10 秒
    MERGE_TTL = 120          # 合并缓冲区 2 分钟，必须覆盖 processor 整个执行时长
    PENDING_TTL = 30         # 排队消息 30 秒
    RESPONDING_TTL = 10      # 推送标记 10 秒
    MERGE_WINDOW = 2         # 合并窗口 2 秒
    PROCESSING_WAIT_TIMEOUT = 120  # 等待旧请求完成最大 120 秒

    def __init__(self):
        # 内存中的活跃会话 cancel_check 注册表（同进程内即时取消）
        # session_id -> cancel_check callable
        self._active_cancel_checks: Dict[str, Callable[[], bool]] = {}

    # ==================== Redis Key 构建 ====================

    @staticmethod
    def _key(prefix: str, session_id: str) -> str:
        return f"{prefix}:{session_id}"

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
                tlog(
                    "语音合并",
                    "[release_lock] 释放锁时合并缓冲区仍有内容"
                    " session={sid}..., leftover_len={l_len}, leftover_preview={l_prev!r}"
                    "（若该内容已通过 user_input_override 消费则正常；否则为丢消息）",
                    sid=session_id[:20],
                    l_len=len(leftover_text),
                    l_prev=leftover_text[:120],
                    level="WARNING",
                )
        except Exception as _e:
            tlog("语音合并", "[release_lock] 检查残留合并缓冲区异常: {err}", err=str(_e), level="ERROR")
        # 先清取消标志、推送标记和合并缓冲区
        self._clear_cancel(session_id)
        self._clear_responding(session_id)
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
    # 结构：{"text": str, "attachments_meta": List[Dict] | None, "timestamp": float}
    # text 为各段用 "\n\n[用户追加消息] " 拼接的合并输入（给 agent 用）
    # attachments_meta 为各段 attachments_meta 的并集（给持久化用）

    def set_merge(
        self,
        session_id: str,
        text: str,
        attachments_meta: Optional[list] = None,
    ) -> None:
        """设置合并缓冲区（首次或覆盖）"""
        key = self._key("session_merge", session_id)
        data = json.dumps(
            {
                "text": text,
                "attachments_meta": attachments_meta,
                "timestamp": time.time(),
            },
            ensure_ascii=False,
        )
        redis_client.set(key, data, ex=self.MERGE_TTL)

    def append_merge(
        self,
        session_id: str,
        new_text: str,
        new_attachments_meta: Optional[list] = None,
    ) -> str:
        """追加新文本到合并缓冲区，返回合并后的完整文本"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if existing:
            data = json.loads(existing) if isinstance(existing, str) else existing
            original = data.get("text", "")
            existing_meta = data.get("attachments_meta") or []
            # 用显式分隔标记拼接，让 LLM 能识别这是用户在短时间内连续发送的
            # 多条独立消息，而非单条多句消息。避免 LLM 只处理最后一个意图
            # 而忽略前面的指令（如"不想去小七孔了。\n天眼那边住的酒店是哪一间？"）
            merged = original + "\n\n[用户追加消息] " + new_text
            merged_meta = list(existing_meta)
            if new_attachments_meta:
                merged_meta.extend(new_attachments_meta)
        else:
            merged = new_text
            merged_meta = list(new_attachments_meta) if new_attachments_meta else []
        self.set_merge(session_id, merged, merged_meta)
        tlog(
            "语音合并",
            "追加合并 session={sid}..., new_text_len={n_len}, "
            "new_text_preview={n_prev!r}, merged_len={m_len}, merged_preview={m_prev!r}, "
            "existing_meta_count={em_n}, new_meta_count={nm_n}, merged_meta_count={mm_n}",
            sid=session_id[:20],
            n_len=len(new_text),
            n_prev=new_text[:50],
            m_len=len(merged),
            m_prev=merged[:80],
            em_n=len(existing_meta) if existing else 0,
            nm_n=len(new_attachments_meta) if new_attachments_meta else 0,
            mm_n=len(merged_meta),
        )
        # Bug 1 验证：重处理期间到达的消息应累积到现有缓冲区（em_n > 0），
        # 若 em_n=0 说明缓冲区被提前清空（Bug 1 复发）
        if not existing:
            tlog(
                "语音合并",
                "[append_merge 警告] 缓冲区为空，新建缓冲区 session={sid}, "
                "new_text_preview={n_prev!r}（若此时正在重处理，说明缓冲区被提前清空）",
                sid=session_id[:20],
                n_prev=new_text[:50],
                level="WARNING",
            )
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
        return not redis_client.exists(responding_key)

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

    # ==================== Pending 队列 ====================

    def set_pending(self, session_id: str, text: str) -> None:
        """设置排队消息（已开始推送 SSE 后的新消息）"""
        key = self._key("session_pending", session_id)
        data = json.dumps({"text": text, "timestamp": time.time()}, ensure_ascii=False)
        redis_client.set(key, data, ex=self.PENDING_TTL)

    def get_pending(self, session_id: str) -> Optional[str]:
        """获取并清除排队消息"""
        key = self._key("session_pending", session_id)
        existing = redis_client.get(key)
        if existing:
            redis_client.delete(key)
            data = json.loads(existing) if isinstance(existing, str) else existing
            return data.get("text")
        return None

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
    ) -> Optional[tuple]:
        """
        处理器返回后检查是否需要重新处理（取消 + 有合并输入）。

        循环重处理：每次重处理前若有 cancel + 新合并输入，则用新输入重新跑 processor；
        重处理后再次检查，若又被取消且有更新的合并输入，继续重处理。
        直到无 cancel 或无新合并输入为止，避免重处理期间到达的新消息被丢弃。

        返回 (response, merged_input, merged_attachments_meta) 元组，或 None（不需要重处理）。
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
        # 检查合并输入是否与原始输入不同
        if merged_input == original_input:
            return None

        # 循环重处理
        iteration = 0
        max_iterations = 10  # 防止极端情况下死循环
        current_merged_input = merged_input
        current_merged_meta = merged_meta
        response = ""
        while iteration < max_iterations:
            iteration += 1
            tlog(
                "语音合并",
                "[重处理] 第 {iter} 轮 session={sid}..., "
                "merged_len={m_len}, merged_preview={m_prev!r}, merged_meta_count={mm_n}",
                iter=iteration,
                sid=session_id[:20],
                m_len=len(current_merged_input),
                m_prev=current_merged_input[:80],
                mm_n=len(current_merged_meta) if current_merged_meta else 0,
            )
            # 不清除合并缓冲区：重处理期间新到达的消息需要 append_merge 累积到现有缓冲区，
            # 否则 append_merge 走 else 分支（新建），上一轮的合并内容会丢失。
            # 重复处理由下方 new_merged_input == current_merged_input 检查兜底。
            # 清除取消标志：重新处理是新一轮完整处理，不应继承上一轮的取消状态，
            # 否则新 processor 会在 cancel_check 时立即返回，造成 response_text 为空
            self._clear_cancel(session_id)
            cancel_check = lambda: self.check_cancel(session_id)
            # 关键：通过 user_input_override 把合并后的完整输入传给 processor，
            # 否则 processor 闭包绑定的还是原始输入，合并内容会被丢弃
            response = processor(cancel_check, user_input_override=current_merged_input)
            if asyncio.iscoroutine(response):
                response = await response
            tlog(
                "语音合并",
                "[重处理] 第 {iter} 轮完成 session={sid}..., "
                "response_len={r_len}, response_preview={r_prev!r}, "
                "was_cancelled_after={cancelled}",
                iter=iteration,
                sid=session_id[:20],
                r_len=len(response) if response else 0,
                r_prev=(response or "")[:80],
                cancelled=self.is_cancelled(session_id),
            )
            # 验证：processor 执行期间合并缓冲区是否被保留（Bug 1 修复后应为 True）
            # 若 preserved=False 且 has_new_content=True，说明缓冲区被提前清空（Bug 1 复发）
            try:
                _buffer_after = redis_client.get(self._key("session_merge", session_id))
                _buffer_after_text = ""
                if _buffer_after:
                    _data_ba = json.loads(_buffer_after) if isinstance(_buffer_after, str) else _buffer_after
                    _buffer_after_text = _data_ba.get("text", "")
                _preserved = bool(current_merged_input) and _buffer_after_text.startswith(current_merged_input)
                tlog(
                    "语音合并",
                    "[重处理] 第{iter}轮 processor 后缓冲区验证 session={sid}, "
                    "buffer_preserved={preserved}, buffer_len={bl}, buffer_preview={bp!r}, "
                    "prev_merged_len={pm_len}, has_new_content={has_new}",
                    iter=iteration,
                    sid=session_id[:20],
                    preserved=_preserved,
                    bl=len(_buffer_after_text),
                    bp=_buffer_after_text[:80],
                    pm_len=len(current_merged_input),
                    has_new=_buffer_after_text != current_merged_input,
                )
            except Exception as _e:
                tlog("语音合并", "[重处理] 缓冲区验证异常: {err}", err=str(_e), level="ERROR")
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
            if new_merged_input == current_merged_input:
                # 取消标志存在但没有新内容，跳出（避免无意义重跑）
                tlog(
                    "语音合并",
                    "[重处理] 第 {iter} 轮后取消但无新内容，跳出 session={sid}...",
                    iter=iteration,
                    sid=session_id[:20],
                    level="WARNING",
                )
                break
            tlog(
                "语音合并",
                "[重处理] 第 {iter} 轮后又取消且有新合并输入，继续重处理 session={sid}..., "
                "old_merged_len={om_len}, new_merged_len={nm_len}, new_merged_preview={nm_prev!r}",
                iter=iteration,
                sid=session_id[:20],
                om_len=len(current_merged_input),
                nm_len=len(new_merged_input),
                nm_prev=new_merged_input[:80],
            )
            current_merged_input = new_merged_input
            current_merged_meta = new_merged_meta

        return response, current_merged_input, current_merged_meta

    async def enqueue_and_process(
        self,
        session_id: str,
        user_input: str,
        processor,
        attachments_meta: Optional[list] = None,
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
            # 设置合并缓冲区
            self.set_merge(session_id, user_input, attachments_meta)
            # 等待合并窗口，期间可能有追加消息
            await self._wait_merge_window(session_id)
            # 获取最终合并后的输入
            final_input = self.get_merged_input(session_id, user_input)
            final_meta = self.get_merged_attachments_meta(session_id)
            was_merged = final_input != user_input
            tlog(
                "语音合并",
                "空闲态处理 session={sid}..., original_len={o_len}, "
                "final_len={f_len}, merged={merged}, final_preview={f_prev!r}, "
                "original_meta_count={om_n}, final_meta_count={fm_n}",
                sid=session_id[:20],
                o_len=len(user_input),
                f_len=len(final_input),
                merged=was_merged,
                f_prev=final_input[:80],
                om_n=len(attachments_meta) if attachments_meta else 0,
                fm_n=len(final_meta) if final_meta else 0,
            )

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
                    response = processor(cancel_check, user_input_override=final_input)
                    if asyncio.iscoroutine(response):
                        response = await response
                except Exception as e:
                    logger.error(
                        f"[SessionQueue] processor 异常 session={session_id[:20]}...: {e}",
                        exc_info=True,
                    )
                    error_result = EnqueueResult(
                        status="error",
                        response_text="",
                        merged_input=final_input,
                        was_merged=was_merged,
                        merged_attachments_meta=final_meta,
                    )
                else:
                    # processor 成功，立即记录正常返回值
                    success_result = EnqueueResult(
                        status="success",
                        response_text=response or "",
                        merged_input=final_input,
                        was_merged=was_merged,
                        merged_attachments_meta=final_meta,
                    )
            finally:
                # 1. 检查是否有取消 + 合并输入需要重新处理（仅在 processor 未异常时）
                if error_result is None:
                    try:
                        reprocessed = await self._handle_cancel_and_reprocess(
                            session_id, final_input, processor
                        )
                    except Exception as e:
                        logger.error(
                            f"[SessionQueue] cancel 重处理异常 session={session_id[:20]}...: {e}",
                            exc_info=True,
                        )
                        reprocessed = None
                        error_result = EnqueueResult(
                            status="error",
                            response_text="",
                            merged_input=final_input,
                            was_merged=True,
                            merged_attachments_meta=final_meta,
                        )
                    if reprocessed is not None:
                        reprocessed_text, reprocessed_merged_input, reprocessed_merged_meta = reprocessed
                        # override 已消费合并缓冲区，显式清除以避免 release_lock 误报
                        self.clear_merge(session_id)
                        self.release_lock(session_id, lock_value)
                        # 必须使用 _handle_cancel_and_reprocess 透传回来的
                        # reprocessed_merged_input（合并后的完整输入），
                        # 不能用 final_input —— final_input 是合并窗口结束时读到的值，
                        # 此时后续追加的消息尚未进入合并缓冲区，持久化 final_input
                        # 会导致追加的消息丢失（参见会话历史缺消息的 bug）。
                        # merged_attachments_meta 同理：透传重处理后的累积值，含被取消方的附件元数据
                        tlog(
                            "语音合并",
                            "[idle 路径] 返回重处理结果 session={sid}..., "
                            "final_input_len={fi_len}, remerged_len={rm_len}, "
                            "remerged_preview={rm_prev!r}, remerged_meta_count={rmm_n}",
                            sid=session_id[:20],
                            fi_len=len(final_input),
                            rm_len=len(reprocessed_merged_input),
                            rm_prev=reprocessed_merged_input[:80],
                            rmm_n=len(reprocessed_merged_meta) if reprocessed_merged_meta else 0,
                        )
                        return EnqueueResult(
                            status="success",
                            response_text=reprocessed_text or "",
                            merged_input=reprocessed_merged_input,
                            was_merged=True,
                            merged_attachments_meta=reprocessed_merged_meta,
                        )
                    # 2. 检查是否有排队消息（处理中到达的新消息）
                    if self.has_pending(session_id):
                        tlog(
                            "语音合并",
                            "检测到 pending 消息，继续处理 session={sid}...",
                            sid=session_id[:20],
                        )
                        pending_input = self.get_pending(session_id)
                        if pending_input:
                            # pending 消息无 attachments_meta 通道（pending 只存 text），
                            # 故此分支合并的附件元数据仅来自 final_meta（即 idle 窗口内累积的）
                            self.clear_merge(session_id)
                            self.set_merge(session_id, pending_input, None)
                            cancel_check = lambda: self.check_cancel(session_id)
                            tlog(
                                "语音合并",
                                "处理 pending 输入 session={sid}..., "
                                "pending_len={p_len}, pending_preview={p_prev!r}",
                                sid=session_id[:20],
                                p_len=len(pending_input),
                                p_prev=pending_input[:80],
                            )
                            try:
                                pending_response = processor(
                                    cancel_check, user_input_override=pending_input
                                )
                                if asyncio.iscoroutine(pending_response):
                                    pending_response = await pending_response
                            except Exception as e:
                                logger.error(
                                    f"[SessionQueue] pending processor 异常 session={session_id[:20]}...: {e}",
                                    exc_info=True,
                                )
                                self.release_lock(session_id, lock_value)
                                return EnqueueResult(
                                    status="error",
                                    response_text="",
                                    merged_input=pending_input,
                                    was_merged=False,
                                    merged_attachments_meta=final_meta,
                                )
                            # pending 处理完后也检查取消+合并
                            try:
                                reprocessed = await self._handle_cancel_and_reprocess(
                                    session_id, pending_input, processor
                                )
                            except Exception as e:
                                logger.error(
                                    f"[SessionQueue] pending cancel 重处理异常 session={session_id[:20]}...: {e}",
                                    exc_info=True,
                                )
                                reprocessed = None
                            # override 已消费合并缓冲区，显式清除以避免 release_lock 误报
                            self.clear_merge(session_id)
                            self.release_lock(session_id, lock_value)
                            if reprocessed is not None:
                                reprocessed_text, reprocessed_merged_input, reprocessed_merged_meta = reprocessed
                                # 同上：使用透传回来的合并输入，而非 pending_input
                                # merged_meta 合并 final_meta（idle 窗口内）+ 重处理累积的
                                merged_meta_combined = list(final_meta) if final_meta else []
                                if reprocessed_merged_meta:
                                    merged_meta_combined.extend(reprocessed_merged_meta)
                                tlog(
                                    "语音合并",
                                    "[pending 路径] 返回重处理结果 session={sid}..., "
                                    "pending_input_len={pi_len}, remerged_len={rm_len}, "
                                    "remerged_preview={rm_prev!r}, merged_meta_count={mm_n}",
                                    sid=session_id[:20],
                                    pi_len=len(pending_input),
                                    rm_len=len(reprocessed_merged_input),
                                    rm_prev=reprocessed_merged_input[:80],
                                    mm_n=len(merged_meta_combined),
                                )
                                return EnqueueResult(
                                    status="success",
                                    response_text=reprocessed_text or "",
                                    merged_input=reprocessed_merged_input,
                                    was_merged=True,
                                    merged_attachments_meta=merged_meta_combined if merged_meta_combined else None,
                                )
                            return EnqueueResult(
                                status="success",
                                response_text=pending_response or "",
                                merged_input=pending_input,
                                was_merged=False,
                                merged_attachments_meta=final_meta,
                            )
                # idle 路径正常完成（无 cancel、无 pending）：override 已消费合并缓冲区，
                # 显式清除以避免 release_lock 误报"疑似丢消息"
                self.clear_merge(session_id)
                self.release_lock(session_id, lock_value)
            # error_result 优先（processor 抛异常）
            if error_result is not None:
                return error_result
            return success_result

        else:
            # === 处理中态，追加消息 ===
            if self.is_cancel_allowed(session_id):
                # 尚未开始推送，可以取消
                tlog(
                    "语音合并",
                    "处理中态（允许取消）session={sid}..., "
                    "input_len={i_len}, input_preview={i_prev!r}, meta_count={m_n}",
                    sid=session_id[:20],
                    i_len=len(user_input),
                    i_prev=user_input[:50],
                    m_n=len(attachments_meta) if attachments_meta else 0,
                )
                self.set_cancel(session_id)
                self.append_merge(session_id, user_input, attachments_meta)
                # 等待旧请求完成
                await self._wait_for_processing_end(session_id)
                # 旧请求应已处理合并后的输入，本调用方无需发送回复
                return EnqueueResult(status="merged")
            else:
                # 已开始推送，不允许取消，加入 pending
                # pending 通道仅存 text，attachments_meta 无法累积 → 此处记录告警
                if attachments_meta:
                    tlog(
                        "语音合并",
                        "[pending 丢附件] 已推送后到达的消息含附件，pending 通道无法累积附件元数据 → "
                        "该附件元数据将丢失（仅影响持久化，不影响 LLM）session={sid}..., "
                        "meta_count={m_n}, input_preview={i_prev!r}",
                        sid=session_id[:20],
                        m_n=len(attachments_meta),
                        i_prev=user_input[:50],
                        level="WARNING",
                    )
                logger.info(
                    f"[SessionQueue] 处理中态（已推送，排队）session={session_id[:20]}..., "
                    f"input={user_input[:50]}"
                )
                self.set_pending(session_id, user_input)
                # 等待旧请求完成
                await self._wait_for_processing_end(session_id)
                return EnqueueResult(status="merged")

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
