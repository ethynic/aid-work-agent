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
from typing import Any, Callable, Dict, Optional

from loguru import logger

from src.core.redis_client import redis_client


class SessionMessageQueue:
    """渠道会话消息串行处理调度器"""

    # TTL 常量
    LOCK_TTL = 120          # 会话锁 2 分钟，防死锁
    CANCEL_TTL = 10          # 取消标志 10 秒
    MERGE_TTL = 5            # 合并缓冲区 5 秒
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
        # 先清取消标志和推送标记
        self._clear_cancel(session_id)
        self._clear_responding(session_id)
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

    def set_merge(self, session_id: str, text: str) -> None:
        """设置合并缓冲区（首次或覆盖）"""
        key = self._key("session_merge", session_id)
        data = json.dumps({"text": text, "timestamp": time.time()}, ensure_ascii=False)
        redis_client.set(key, data, ex=self.MERGE_TTL)

    def append_merge(self, session_id: str, new_text: str) -> str:
        """追加新文本到合并缓冲区，返回合并后的完整文本"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if existing:
            data = json.loads(existing) if isinstance(existing, str) else existing
            original = data.get("text", "")
            # 换行分隔追加
            merged = original + "\n" + new_text
        else:
            merged = new_text
        self.set_merge(session_id, merged)
        return merged

    def get_merged_input(self, session_id: str, default: str) -> str:
        """获取合并后的完整用户输入，无合并数据时返回 default"""
        key = self._key("session_merge", session_id)
        existing = redis_client.get(key)
        if existing:
            data = json.loads(existing) if isinstance(existing, str) else existing
            return data.get("text", default)
        return default

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
    ) -> Optional[str]:
        """
        处理器返回后检查是否需要重新处理（取消 + 有合并输入）。

        返回新的回复文本，或 None（不需要重新处理）。
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
        # 检查合并输入是否与原始输入不同
        if merged_input == original_input:
            return None
        # 用合并后的输入重新处理
        logger.info(
            f"[SessionQueue] 检测到取消，重新处理合并输入 session={session_id[:20]}..., "
            f"original_len={len(original_input)}, merged_len={len(merged_input)}"
        )
        self.clear_merge(session_id)  # 清除合并缓冲区，防重复重新处理
        # 清除取消标志：重新处理是新一轮完整处理，不应继承上一轮的取消状态，
        # 否则新 processor 会在 cancel_check 时立即返回，造成 response_text 为空
        self._clear_cancel(session_id)
        cancel_check = lambda: self.check_cancel(session_id)
        response = processor(cancel_check)
        if asyncio.iscoroutine(response):
            response = await response
        return response

    async def enqueue_and_process(
        self,
        session_id: str,
        user_input: str,
        processor,
    ) -> str:
        """
        渠道消息入口调度。

        Args:
            session_id: 会话 ID
            user_input: 用户输入文本
            processor: 异步回调函数，签名 (cancel_check: Callable) -> str
                负责调用 agent.process_message_sync 并返回回复文本。
                cancel_check 是用于检测取消的函数，processor 需将其传给 process_message_sync。

        Returns:
            回复文本。返回空字符串表示消息被合并/排队，本调用方无需发送回复。

        流程：
        1. 空闲态：获取锁 -> 启动合并窗口 -> 处理 -> 返回结果
        2. 处理中 + 允许取消：设置取消 + 更新合并缓冲区 -> 等待旧请求结束
           （旧请求检测到取消后，用合并后的输入重新处理，本调用方返回空字符串）
        3. 处理中 + 不允许取消（已开始推送）：设置 pending -> 等待旧请求结束
           （旧请求完成后检测 pending 并处理，本调用方返回空字符串）
        """
        # 尝试获取锁（首次尝试）
        lock_value = self.acquire_lock(session_id)

        if lock_value is not None:
            # === 空闲态，首次请求 ===
            # 设置合并缓冲区
            self.set_merge(session_id, user_input)
            # 等待合并窗口，期间可能有追加消息
            await self._wait_merge_window(session_id)
            # 获取最终合并后的输入
            final_input = self.get_merged_input(session_id, user_input)
            logger.info(
                f"[SessionQueue] 空闲态处理 session={session_id[:20]}..., "
                f"input_len={len(final_input)}, merged={final_input != user_input}"
            )
            try:
                # 调用 processor（process_message_sync）
                cancel_check = lambda: self.check_cancel(session_id)
                response = processor(cancel_check)
                if asyncio.iscoroutine(response):
                    response = await response
                return response
            finally:
                # 1. 检查是否有取消 + 合并输入需要重新处理
                reprocessed = await self._handle_cancel_and_reprocess(
                    session_id, final_input, processor
                )
                if reprocessed is not None:
                    self.release_lock(session_id, lock_value)
                    return reprocessed
                # 2. 检查是否有排队消息（处理中到达的新消息）
                if self.has_pending(session_id):
                    logger.info(f"[SessionQueue] 检测到 pending 消息，继续处理")
                    pending_input = self.get_pending(session_id)
                    if pending_input:
                        self.clear_merge(session_id)
                        self.set_merge(session_id, pending_input)
                        cancel_check = lambda: self.check_cancel(session_id)
                        response = processor(cancel_check)
                        if asyncio.iscoroutine(response):
                            response = await response
                        # pending 处理完后也检查取消+合并
                        reprocessed = await self._handle_cancel_and_reprocess(
                            session_id, pending_input, processor
                        )
                        if reprocessed is not None:
                            self.release_lock(session_id, lock_value)
                            return reprocessed
                        self.release_lock(session_id, lock_value)
                        return response
                self.release_lock(session_id, lock_value)
            return ""  # 不应到达这里

        else:
            # === 处理中态，追加消息 ===
            if self.is_cancel_allowed(session_id):
                # 尚未开始推送，可以取消
                logger.info(
                    f"[SessionQueue] 处理中态（允许取消）session={session_id[:20]}..., "
                    f"input={user_input[:50]}"
                )
                self.set_cancel(session_id)
                self.append_merge(session_id, user_input)
                # 等待旧请求完成
                await self._wait_for_processing_end(session_id)
                # 旧请求应已处理合并后的输入，本调用方无需发送回复
                return ""
            else:
                # 已开始推送，不允许取消，加入 pending
                logger.info(
                    f"[SessionQueue] 处理中态（已推送，排队）session={session_id[:20]}..., "
                    f"input={user_input[:50]}"
                )
                self.set_pending(session_id, user_input)
                # 等待旧请求完成
                await self._wait_for_processing_end(session_id)
                return ""

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
