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
            for row in cursor.fetchall():
                inst = dict(row)

                # 增强前端显示信息
                if inst["status"] == "busy":
                    # 判断是否是当前用户自己在使用（可接管）
                    if current_user_id and inst["current_user_id"] == current_user_id:
                        inst["status_text"] = "正在您的另一台设备上对话"
                        inst["can_take_over"] = True
                    else:
                        inst["status_text"] = "忙碌中"
                        inst["can_take_over"] = False
                elif inst["status"] == "idle":
                    inst["status_text"] = "空闲可用"
                    inst["can_take_over"] = False
                else:
                    inst["status_text"] = "离线"
                    inst["can_take_over"] = False

                instances.append(inst)

            return instances

    @staticmethod
    def try_lock_instance(
        instance_id: str,
        session_id: str,
        user_id: str,
        lock_timeout_minutes: int = 30
    ) -> Dict[str, Any]:
        """
        尝试锁定实例（原子操作）

        Args:
            instance_id: 实例ID
            session_id: 会话ID
            user_id: 用户ID
            lock_timeout_minutes: 锁超时时间（默认30分钟）

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

            current_status = row["status"]

            # 3. 如果空闲，直接锁定
            if current_status == "idle":
                cursor.execute("""
                    UPDATE agent_instances
                    SET
                        status = 'busy',
                        current_session_id = %s,
                        current_user_id = %s,
                        locked_at = CURRENT_TIMESTAMP,
                        lock_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE instance_id = %s AND status = 'idle'
                    RETURNING instance_id, instance_name, avatar, status
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
                current_status = "busy"

            # 4. 如果忙碌，检查是否已经在队列中（防重复排队）
            if current_status == "busy":
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

            # offline 状态
            return {"success": False, "error": "Instance is offline"}

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
        检查排队状态

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

            # 如果是 ready 状态，检查是否真的可以锁定（防止过期）
            if status == "ready":
                cursor.execute("""
                    SELECT status FROM agent_instances
                    WHERE instance_id = %s AND status = 'idle'
                """, (instance_id,))
                if not cursor.fetchone():
                    # 实例又被别人占用了，重新排队
                    # 这种情况理论上不会发生，但为了健壮性处理
                    return {
                        "in_queue": True,
                        "status": "waiting",
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

            # 预估等待时间：假设每个对话平均5分钟
            estimated_wait = position * 300

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
    def _cleanup_expired_locks(conn):
        """清理所有过期的实例锁"""
        cursor = conn.cursor()

        # 查找过期锁
        cursor.execute("""
            SELECT instance_id FROM agent_instances
            WHERE status = 'busy' AND lock_expires_at < CURRENT_TIMESTAMP
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
            WHERE status = 'busy' AND lock_expires_at < CURRENT_TIMESTAMP
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
            WHERE instance_id = %s AND status = 'busy' AND lock_expires_at < CURRENT_TIMESTAMP
            RETURNING instance_id
        """, (instance_id,))
        if cursor.fetchone():
            logger.info(f"Auto released expired lock for instance: {instance_id}")
            InstanceService._wake_up_next_waiter(conn, instance_id)

    @staticmethod
    def _cleanup_expired_queue_items(conn):
        """清理超时的排队项"""
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE agent_instance_queue
            SET status = 'expired', updated_at = CURRENT_TIMESTAMP
            WHERE wait_timeout_at < CURRENT_TIMESTAMP AND status = 'waiting'
        """)
        count = cursor.rowcount
        if count > 0:
            logger.info(f"Cleaned up {count} expired queue items")

    @staticmethod
    def _wake_up_next_waiter(conn, instance_id: str) -> Optional[str]:
        """
        唤醒队列头部的用户，标记为 ready

        Returns:
            被唤醒的会话ID，如果没有则返回 None
        """
        cursor = conn.cursor()

        # 找到队列头部第一个
        cursor.execute("""
            SELECT queue_id, session_id FROM agent_instance_queue
            WHERE instance_id = %s AND status = 'waiting'
            ORDER BY position
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (instance_id,))

        row = cursor.fetchone()
        if not row:
            return None

        # 标记为 ready 状态
        cursor.execute("""
            UPDATE agent_instance_queue
            SET status = 'ready', updated_at = CURRENT_TIMESTAMP
            WHERE queue_id = %s
        """, (row["queue_id"],))

        logger.info(
            f"Woke up session {row['session_id']} for instance {instance_id}"
        )
        return row["session_id"]


# 全局单例
instance_service = InstanceService()
