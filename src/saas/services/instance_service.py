"""
数字员工实例管理服务
- 实例列表查询（含状态、排队人数）
- 实例锁定/释放
- 排队管理
- 同用户多设备会话接管
"""

import uuid
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger

from src.db.database import get_db_connection
from src.saas.models.enums import QueueStatus
import psycopg2.errors


class InstanceService:
    """数字员工实例管理服务"""

    @staticmethod
    def list_tenant_instances(
        tenant_id: str,
        subagent_type: Optional[str] = None,
        current_user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取租户的数字员工实例列表（含实时状态）

        Args:
            tenant_id: 租户ID
            subagent_type: 可选，按子智能体类型过滤
            current_user_id: 当前用户ID，用于判断是否显示"可接管"

        Returns:
            实例列表，包含每个实例的实时状态、排队人数等信息
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 先清理过期锁
            InstanceService._cleanup_expired_locks(conn)

            if tenant_id == 'demo':
                # 演示模式：返回所有实例，不受租户限制
                where_clause = ""
                params = []
            else:
                where_clause = "WHERE ai.tenant_id = %s"
                params = [tenant_id]

            if subagent_type:
                where_clause += " AND ai.subagent_type = %s"
                params.append(subagent_type)

            cursor.execute(f"""
                SELECT
                    ai.instance_id,
                    ai.tenant_id,
                    ai.subagent_type,
                    ai.display_name,
                    ai.instance_name,
                    ai.avatar,
                    ai.description,
                    ai.personality_traits,
                    ai.status,
                    ai.current_session_id,
                    ai.current_user_id,
                    ai.locked_at,
                    ai.lock_expires_at,
                    ai.total_chats,
                    ai.total_messages,
                    ai.created_at,
                    ai.updated_at,
                    COALESCE(q.queue_length, 0) as queue_length
                FROM agent_instances ai
                LEFT JOIN (
                    SELECT instance_id, COUNT(*) as queue_length
                    FROM agent_instance_queue
                    WHERE status = 'waiting'
                    GROUP BY instance_id
                ) q ON ai.instance_id = q.instance_id
                {where_clause}
                ORDER BY ai.created_at ASC
            """, params)

            instances = []
            instances_to_update = []
            for row in cursor.fetchall():
                inst = dict(row)

                # 增强前端显示信息（二元状态：idle = 空闲，busy = 忙碌）
                # 规范化状态：所有非busy状态根据current_session_id判断
                if inst["status"] != "busy":
                    if inst["current_session_id"] is None:
                        # 没有会话锁定，视为空闲
                        if inst["status"] != "idle":
                            inst["status"] = "idle"
                            instances_to_update.append(inst["instance_id"])
                    else:
                        # 有会话锁定但状态不是busy，视为busy
                        inst["status"] = "busy"
                        instances_to_update.append(inst["instance_id"])

                if inst["status"] == "busy":
                    # 判断是否是当前用户自己在使用（可接管）
                    if current_user_id and inst["current_user_id"] == current_user_id:
                        inst["status_text"] = "正在您的另一台设备上对话"
                        inst["can_take_over"] = True
                    else:
                        inst["status_text"] = "忙碌中"
                        inst["can_take_over"] = False
                else:
                    inst["status_text"] = "空闲可用"
                    inst["can_take_over"] = False

                instances.append(inst)

            # 批量更新需要状态规范化的实例
            if instances_to_update:
                logger.info(f"Auto updating {len(instances_to_update)} instances with non-standard status: {instances_to_update}")
                # 这里不需要单独更新，因为状态已经在inst字典中更新了
                # 数据库更新将在下一次查询时由相同的逻辑处理
                # 为了保持数据一致性，我们仍然更新数据库
                for instance_id in instances_to_update:
                    # 获取该实例在内存中的状态
                    target_status = None
                    for inst in instances:
                        if inst["instance_id"] == instance_id:
                            target_status = inst["status"]
                            break
                    if target_status:
                        cursor.execute("""
                            UPDATE agent_instances
                            SET status = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE instance_id = %s
                        """, (target_status, instance_id))
                conn.commit()

            return instances

    @staticmethod
    def try_lock_instance(
        instance_id: str,
        session_id: str,
        user_id: str,
        tenant_id: str = None,
        lock_timeout_minutes: int = 3
    ) -> Dict[str, Any]:
        """
        尝试锁定实例（原子操作）

        Args:
            instance_id: 实例ID
            session_id: 会话ID
            user_id: 用户ID
            tenant_id: 租户ID（演示模式为 'demo'）
            lock_timeout_minutes: 锁超时时间（默认3分钟）

        Returns:
            {
                success: bool,
                was_idle: bool,  # 锁定前是否空闲
                is_queued: bool,  # 是否进入排队
                queue_position: int,  # 排队位置（0开始）
                queue_length: int,   # 队列总长度
                instance: dict,      # 锁定后的实例信息
            }
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. 先清理该实例的过期锁
            InstanceService._cleanup_instance_expired_locks(conn, instance_id)

            # 2. 检查实例当前状态
            cursor.execute("""
                SELECT status, current_session_id, current_user_id,
                       instance_name, avatar, status
                FROM agent_instances
                WHERE instance_id = %s
                FOR UPDATE  -- 行锁，防止并发争抢
            """, (instance_id,))
            row = cursor.fetchone()

            if not row:
                return {"success": False, "error": "Instance not found"}

            # 3. 用 current_session_id 判断是否空闲（NULL = 空闲）
            is_busy = row["current_session_id"] is not None

            # 演示模式：跳过排队，直接锁定（无限并发）
            if tenant_id == 'demo':
                cursor.execute("""
                    UPDATE agent_instances
                    SET
                        status = 'busy',
                        current_session_id = %s,
                        current_user_id = %s,
                        locked_at = CURRENT_TIMESTAMP,
                        lock_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE instance_id = %s
                    RETURNING instance_id, instance_name, avatar, status, current_session_id
                """, (session_id, user_id, lock_timeout_minutes, instance_id))
                locked = cursor.fetchone()
                conn.commit()
                if locked:
                    logger.info(
                        f"[Demo] Instance {instance_id} locked by session {session_id}, "
                        f"user {user_id}, timeout {lock_timeout_minutes}min"
                    )
                    return {
                        "success": True,
                        "was_idle": True,
                        "is_queued": False,
                        "instance": dict(locked),
                    }
                return {"success": False, "error": "演示模式实例锁定失败"}

            if not is_busy:
                # 空闲，直接锁定（用 current_session_id IS NULL 保证原子性）
                cursor.execute("""
                    UPDATE agent_instances
                    SET
                        status = 'busy',
                        current_session_id = %s,
                        current_user_id = %s,
                        locked_at = CURRENT_TIMESTAMP,
                        lock_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE instance_id = %s AND current_session_id IS NULL
                    RETURNING instance_id, instance_name, avatar, status, current_session_id
                """, (session_id, user_id, lock_timeout_minutes, instance_id))

                locked = cursor.fetchone()
                conn.commit()

                if locked:
                    logger.info(
                        f"Instance {instance_id} locked by session {session_id}, "
                        f"user {user_id}, timeout {lock_timeout_minutes}min"
                    )
                    return {
                        "success": True,
                        "was_idle": True,
                        "is_queued": False,
                        "instance": dict(locked),
                    }

                # 竞态：有人抢先锁定了，继续往下走进入排队
                is_busy = True

            # 4. 如果忙碌（current_session_id 有值），检查是否已经在队列中（防重复排队）
            if is_busy:
                cursor.execute("""
                    SELECT position FROM agent_instance_queue
                    WHERE instance_id = %s AND session_id = %s AND status = 'waiting'
                    LIMIT 1
                """, (instance_id, session_id))
                existing = cursor.fetchone()

                if existing:
                    # 已经在队列中了，返回当前位置
                    cursor.execute("""
                        SELECT COUNT(*) as total FROM agent_instance_queue
                        WHERE instance_id = %s AND status = 'waiting'
                    """, (instance_id,))
                    total = cursor.fetchone()["total"]
                    return {
                        "success": False,
                        "was_idle": False,
                        "is_queued": True,
                        "queue_position": existing["position"],
                        "queue_length": total,
                    }

                # 计算新的排队位置
                cursor.execute("""
                    SELECT COALESCE(MAX(position), -1) + 1 as next_pos
                    FROM agent_instance_queue
                    WHERE instance_id = %s AND status = 'waiting'
                """, (instance_id,))
                position = cursor.fetchone()["next_pos"]

                # 插入队列
                queue_id = f"q_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO agent_instance_queue (
                        queue_id, instance_id, tenant_id, session_id, user_id,
                        position, wait_timeout_at
                    )
                    SELECT
                        %s, %s, ai.tenant_id, %s, %s, %s,
                        CURRENT_TIMESTAMP + '30 minutes'
                    FROM agent_instances ai
                    WHERE ai.instance_id = %s
                    RETURNING (
                        SELECT COUNT(*) FROM agent_instance_queue
                        WHERE instance_id = %s AND status = 'waiting'
                    ) as total
                """, (queue_id, instance_id, session_id, user_id, position, instance_id, instance_id))

                total = cursor.fetchone()["total"]
                conn.commit()

                logger.info(
                    f"Session {session_id} queued for instance {instance_id}, "
                    f"position {position}/{total}"
                )

                return {
                    "success": False,
                    "was_idle": False,
                    "is_queued": True,
                    "queue_position": position,
                    "queue_length": total,
                }

    @staticmethod
    def release_instance(instance_id: str, session_id: str) -> bool:
        """
        释放实例，并唤醒队列头部的等待者

        Args:
            instance_id: 实例ID
            session_id: 当前会话ID（验证锁持有者）

        Returns:
            是否释放成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. 释放锁（只有持有锁的会话才能释放）
            cursor.execute("""
                UPDATE agent_instances
                SET
                    status = 'idle',
                    current_session_id = NULL,
                    current_user_id = NULL,
                    locked_at = NULL,
                    lock_expires_at = NULL,
                    total_chats = total_chats + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE instance_id = %s AND current_session_id = %s
                RETURNING instance_id
            """, (instance_id, session_id))

            released = cursor.fetchone()
            conn.commit()

            if not released:
                return False

            logger.info(f"Instance {instance_id} released by session {session_id}")

            # 2. 唤醒队列头部的等待者
            InstanceService._wake_up_next_waiter(conn, instance_id)
            conn.commit()

            return True

    @staticmethod
    def refresh_lock(
        instance_id: str,
        session_id: str,
        extend_minutes: int = 3
    ) -> bool:
        """
        刷新锁的过期时间（智能体回答完毕后调用）

        Args:
            instance_id: 实例ID
            session_id: 会话ID
            extend_minutes: 延长的分钟数（默认3分钟思考窗口）

        Returns:
            是否刷新成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE agent_instances
                SET lock_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval,
                    updated_at = CURRENT_TIMESTAMP
                WHERE instance_id = %s AND current_session_id = %s
                RETURNING instance_id
            """, (extend_minutes, instance_id, session_id))

            success = cursor.fetchone() is not None
            conn.commit()

            if success:
                logger.debug(f"Lock refreshed: {instance_id}, window {extend_minutes}min")
            return success

    @staticmethod
    def check_queue_status(
        instance_id: str,
        session_id: str
    ) -> Dict[str, Any]:
        """
        检查排队状态（每次调用自动更新心跳）

        Returns:
            {
                in_queue: bool,
                status: 'waiting' | 'ready' | 'expired' | 'not_in_queue',
                position: int,
                queue_length: int,
                estimated_wait_seconds: int,
            }
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 清理过期项
            InstanceService._cleanup_expired_queue_items(conn)

            cursor.execute("""
                SELECT status, position FROM agent_instance_queue
                WHERE instance_id = %s AND session_id = %s
                LIMIT 1
            """, (instance_id, session_id))
            row = cursor.fetchone()

            if not row:
                return {
                    "in_queue": False,
                    "status": "not_in_queue",
                }

            status = row["status"]
            position = row["position"]

            # 更新心跳（只有 waiting 状态需要）
            if status == QueueStatus.WAITING:
                cursor.execute("""
                    UPDATE agent_instance_queue
                    SET last_heartbeat_at = CURRENT_TIMESTAMP
                    WHERE instance_id = %s AND session_id = %s
                """, (instance_id, session_id))
                conn.commit()

            # 如果是 ready 状态，检查是否真的可以锁定（防止过期）
            if status == QueueStatus.READY:
                cursor.execute("""
                    SELECT status FROM agent_instances
                    WHERE instance_id = %s AND status != 'busy'
                """, (instance_id,))
                if not cursor.fetchone():
                    # 实例又被别人占用了，重新排队
                    # 这种情况理论上不会发生，但为了健壮性处理
                    return {
                        "in_queue": True,
                        "status": QueueStatus.WAITING,
                        "position": 999,
                        "queue_length": 999,
                        "estimated_wait_seconds": 600,
                    }

            # 获取队列总长度
            cursor.execute("""
                SELECT COUNT(*) as total FROM agent_instance_queue
                WHERE instance_id = %s AND status = 'waiting'
            """, (instance_id,))
            total = cursor.fetchone()["total"]

            # 基于最近10个已完成会话的平均耗时计算预估等待时间
            avg_duration = None
            try:
                cursor.execute("""
                    SELECT AVG(EXTRACT(EPOCH FROM (cs.ended_at - cs.created_at))) as avg_duration
                    FROM chat_sessions cs
                    WHERE cs.instance_id = %s
                      AND cs.ended_at IS NOT NULL
                      AND cs.created_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'
                    ORDER BY cs.created_at DESC
                    LIMIT 10
                """, (instance_id,))
                avg_row = cursor.fetchone()
                avg_duration = avg_row.get("avg_duration") if avg_row else None
            except psycopg2.errors.UndefinedColumn:
                # 如果 ended_at 列不存在，使用默认值（历史数据不可用）
                logger.warning(f"[QueueStats] ended_at column not found in chat_sessions, using default duration")
                avg_duration = None

            if avg_duration and avg_duration > 0:
                # 有历史数据，用真实平均值
                estimated_wait = int(position * avg_duration)
                logger.debug(f"[QueueStats] Using real avg duration: {avg_duration:.1f}s for instance {instance_id}")
            else:
                # 没有历史数据，用默认值3分钟（比原来的5分钟更保守）
                estimated_wait = position * 180

            return {
                "in_queue": True,
                "status": status,
                "position": position,
                "queue_length": total,
                "estimated_wait_seconds": estimated_wait,
            }

    @staticmethod
    def cancel_queue(instance_id: str, session_id: str) -> bool:
        """
        取消排队

        Returns:
            是否取消成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE agent_instance_queue
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE instance_id = %s AND session_id = %s AND status = 'waiting'
            """, (instance_id, session_id))

            cancelled = cursor.rowcount > 0
            conn.commit()

            if cancelled:
                logger.info(f"Cancelled queue: session {session_id}, instance {instance_id}")
            return cancelled

    @staticmethod
    def take_over_instance(
        instance_id: str,
        new_session_id: str,
        user_id: str,
        lock_timeout_minutes: int = 30
    ) -> Dict[str, Any]:
        """
        同一用户将实例从一台设备接管到另一台设备

        Args:
            instance_id: 实例ID
            new_session_id: 新的会话ID
            user_id: 用户ID（必须与原持有者相同）
            lock_timeout_minutes: 新锁的超时时间

        Returns:
            {
                success: bool,
                old_session_id: str,  # 原会话ID
                instance: dict,       # 新的实例信息
            }
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. 验证确实是同一用户在持有
            cursor.execute("""
                SELECT current_session_id, instance_name, avatar, status
                FROM agent_instances
                WHERE instance_id = %s AND current_user_id = %s
                FOR UPDATE
            """, (instance_id, user_id))
            row = cursor.fetchone()

            if not row:
                return {"success": False, "error": "Instance not found or not owned by user"}

            old_session_id = row["current_session_id"]

            # 2. 更新会话绑定（原子操作）
            cursor.execute("""
                UPDATE agent_instances
                SET
                    current_session_id = %s,
                    lock_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval,
                    updated_at = CURRENT_TIMESTAMP
                WHERE instance_id = %s AND current_user_id = %s
                RETURNING instance_id, instance_name, avatar, status
            """, (new_session_id, lock_timeout_minutes, instance_id, user_id))

            updated = cursor.fetchone()
            conn.commit()

            if not updated:
                return {"success": False, "error": "Failed to take over"}

            logger.info(
                f"Instance {instance_id} taken over: {old_session_id} -> {new_session_id}, "
                f"user {user_id}"
            )

            return {
                "success": True,
                "old_session_id": old_session_id,
                "instance": dict(updated),
            }

    @staticmethod
    def cleanup_all_expired_locks() -> int:
        """
        全局清理过期的实例锁（供定时任务调用）

        Returns:
            清理的过期锁数量
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 查找过期锁
            cursor.execute("""
                SELECT instance_id, current_session_id FROM agent_instances
                WHERE current_session_id IS NOT NULL AND lock_expires_at < CURRENT_TIMESTAMP
            """)
            expired = cursor.fetchall()

            if not expired:
                return 0

            instance_ids = [row["instance_id"] for row in expired]

            # 释放过期锁
            cursor.execute("""
                UPDATE agent_instances
                SET
                    status = 'idle',
                    current_session_id = NULL,
                    current_user_id = NULL,
                    locked_at = NULL,
                    lock_expires_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE current_session_id IS NOT NULL AND lock_expires_at < CURRENT_TIMESTAMP
            """)
            conn.commit()

            # 唤醒每个被释放锁的下一位等待者
            for inst_id in instance_ids:
                InstanceService._wake_up_next_waiter(conn, inst_id)
            conn.commit()

            logger.info(f"[InstanceLock] Cleaned up {len(expired)} expired locks: {instance_ids}")
            return len(expired)

    @staticmethod
    def _cleanup_expired_locks(conn):
        """清理所有过期的实例锁（内部方法，已有事务上下文）"""
        cursor = conn.cursor()

        # 查找过期锁
        cursor.execute("""
            SELECT instance_id FROM agent_instances
            WHERE current_session_id IS NOT NULL AND lock_expires_at < CURRENT_TIMESTAMP
        """)
        expired = [row["instance_id"] for row in cursor.fetchall()]

        if not expired:
            return

        # 释放过期锁
        cursor.execute("""
            UPDATE agent_instances
            SET
                status = 'idle',
                current_session_id = NULL,
                current_user_id = NULL,
                locked_at = NULL,
                lock_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE current_session_id IS NOT NULL AND lock_expires_at < CURRENT_TIMESTAMP
        """)

        # 唤醒每个被释放锁的下一位等待者
        for inst_id in expired:
            InstanceService._wake_up_next_waiter(conn, inst_id)

        logger.info(f"Cleaned up {len(expired)} expired locks: {expired}")

    @staticmethod
    def _cleanup_instance_expired_locks(conn, instance_id: str):
        """清理指定实例的过期锁"""
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE agent_instances
            SET
                status = 'idle',
                current_session_id = NULL,
                current_user_id = NULL,
                locked_at = NULL,
                lock_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE instance_id = %s AND current_session_id IS NOT NULL AND lock_expires_at < CURRENT_TIMESTAMP
            RETURNING instance_id
        """, (instance_id,))
        if cursor.fetchone():
            logger.info(f"[InstanceLock] Auto released expired lock for instance: {instance_id}")
            InstanceService._wake_up_next_waiter(conn, instance_id)

    @staticmethod
    def _cleanup_expired_queue_items(conn):
        """清理超时的排队项（包括等待超时和20秒无心跳）"""
        cursor = conn.cursor()
        # 1. 清理等待超时（30分钟）
        cursor.execute("""
            UPDATE agent_instance_queue
            SET status = 'expired', updated_at = CURRENT_TIMESTAMP
            WHERE wait_timeout_at < CURRENT_TIMESTAMP AND status = 'waiting'
        """)
        count_timeout = cursor.rowcount
        if count_timeout > 0:
            logger.info(f"Cleaned up {count_timeout} expired queue items (timeout)")

        # 2. 清理超过20秒无心跳的排队项（用户可能关闭了浏览器）
        cursor.execute("""
            UPDATE agent_instance_queue
            SET status = 'abandoned', updated_at = CURRENT_TIMESTAMP
            WHERE last_heartbeat_at < CURRENT_TIMESTAMP - INTERVAL '20 seconds'
              AND status = 'waiting'
        """)
        count_heartbeat = cursor.rowcount
        if count_heartbeat > 0:
            logger.info(f"Cleaned up {count_heartbeat} abandoned queue items (no heartbeat)")

    @staticmethod
    def _wake_up_next_waiter(conn, instance_id: str) -> Optional[str]:
        """
        唤醒队列头部的用户，标记为 ready，并记录等待时间统计

        Returns:
            被唤醒的会话ID，如果没有则返回 None
        """
        cursor = conn.cursor()

        # 找到队列头部第一个
        cursor.execute("""
            SELECT queue_id, session_id, queued_at FROM agent_instance_queue
            WHERE instance_id = %s AND status = 'waiting'
            ORDER BY position
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (instance_id,))

        row = cursor.fetchone()
        if not row:
            return None

        queue_id = row["queue_id"]
        session_id = row["session_id"]
        queued_at = row["queued_at"]

        # 计算等待时长（秒）
        wait_duration = None
        if queued_at:
            cursor.execute("SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - %s)) AS duration", (queued_at,))
            duration_row = cursor.fetchone()
            if duration_row:
                wait_duration = int(duration_row["duration"]) if duration_row["duration"] else 0

        # 标记为 ready 状态，记录开始服务时间和等待时长
        cursor.execute("""
            UPDATE agent_instance_queue
            SET status = 'ready',
                started_at = CURRENT_TIMESTAMP,
                wait_duration_seconds = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE queue_id = %s
        """, (wait_duration, queue_id))

        # 输出等待时间统计日志
        logger.info(
            f"[QueueStats] Woke up session {session_id} for instance {instance_id}, "
            f"waited {wait_duration if wait_duration else 'N/A'}s"
        )
        return session_id


# 全局单例
instance_service = InstanceService()
