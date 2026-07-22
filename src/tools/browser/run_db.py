"""浏览器 run 与人工协作审计数据库服务。

所有同步 PostgreSQL 调用均通过 ``asyncio.to_thread``，查询和更新始终带
``tenant_id`` 条件；页面正文、URL、cookie、表单值不进入本服务。
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.db.database import get_db_connection


class BrowserRunDB:
    async def create_run(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._create_run, data)

    @staticmethod
    def _create_run(data: dict[str, Any]) -> None:
        with get_db_connection() as conn:
            conn.execute(
                """INSERT INTO bs_browser_runs
                (tenant_id,user_id,run_id,parent_run_id,session_id,execution_target,
                 executor_client_id,state,routing_reason,failure_class,evidence_level,
                 escalation_count,started_at,finished_at,close_reason,error_code,steps_count)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    data["tenant_id"], data["user_id"], data["run_id"], data.get("parent_run_id"),
                    data["session_id"], data.get("execution_target", "server"),
                    data.get("executor_client_id"), data.get("state", "CREATED"),
                    data.get("routing_reason"), data.get("failure_class"), data.get("evidence_level"),
                    data.get("escalation_count", 0), data.get("started_at"), data.get("finished_at"),
                    data.get("close_reason"), data.get("error_code"), data.get("steps_count", 0),
                ),
            )
            conn.commit()

    async def get_run(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_run, tenant_id, run_id)

    @staticmethod
    def _get_run(tenant_id: str, run_id: str) -> dict[str, Any] | None:
        with get_db_connection() as conn:
            conn.execute("SELECT * FROM bs_browser_runs WHERE tenant_id=%s AND run_id=%s", (tenant_id, run_id))
            row = conn.fetchone()
            return dict(row) if row else None

    async def update_run_state(
        self, tenant_id: str, run_id: str, state: str, *, close_reason: str | None = None,
        error_code: str | None = None, steps_count: int | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._update_run_state, tenant_id, run_id, state, close_reason, error_code, steps_count
        )

    @staticmethod
    def _update_run_state(tenant_id, run_id, state, close_reason, error_code, steps_count) -> bool:
        with get_db_connection() as conn:
            conn.execute(
                """UPDATE bs_browser_runs SET state=%s, close_reason=COALESCE(%s,close_reason),
                error_code=COALESCE(%s,error_code), steps_count=COALESCE(%s,steps_count),
                finished_at=CASE WHEN %s IN ('SUCCEEDED','FAILED','TIMED_OUT','EXPIRED','CANCELLED')
                    THEN CURRENT_TIMESTAMP ELSE finished_at END, updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND run_id=%s""",
                (state, close_reason, error_code, steps_count, state, tenant_id, run_id),
            )
            changed = conn.rowcount > 0
            conn.commit()
            return changed

    async def create_assistance(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._create_assistance, data)

    @staticmethod
    def _create_assistance(data: dict[str, Any]) -> None:
        with get_db_connection() as conn:
            conn.execute(
                """INSERT INTO bs_browser_assistance_requests
                (tenant_id,user_id,assistance_id,run_id,agent_execution_id,tool_call_id,state,
                 reason_code,instruction_code,completion_mode,predicate_type,expires_at,error_code)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    data["tenant_id"], data["user_id"], data["assistance_id"], data["run_id"],
                    data["agent_execution_id"], data["tool_call_id"], data.get("state", "pending"),
                    data["reason_code"], data["instruction_code"], data["completion_mode"],
                    data.get("predicate_type"), data["expires_at"], data.get("error_code"),
                ),
            )
            conn.commit()

    async def get_assistance(self, tenant_id: str, assistance_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_assistance, tenant_id, assistance_id)

    @staticmethod
    def _get_assistance(tenant_id: str, assistance_id: str) -> dict[str, Any] | None:
        with get_db_connection() as conn:
            conn.execute(
                "SELECT * FROM bs_browser_assistance_requests WHERE tenant_id=%s AND assistance_id=%s",
                (tenant_id, assistance_id),
            )
            row = conn.fetchone()
            return dict(row) if row else None

    async def update_assistance_state(
        self, tenant_id: str, assistance_id: str, expected_state: str, new_state: str,
        error_code: str | None = None,
    ) -> bool:
        def op():
            with get_db_connection() as conn:
                conn.execute(
                    """UPDATE bs_browser_assistance_requests SET state=%s,error_code=%s,
                    completed_at=CASE WHEN %s='completed' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                    resumed_at=CASE WHEN %s='resumed' THEN CURRENT_TIMESTAMP ELSE resumed_at END,
                    updated_at=CURRENT_TIMESTAMP
                    WHERE tenant_id=%s AND assistance_id=%s AND state=%s""",
                    (new_state, error_code, new_state, new_state, tenant_id, assistance_id, expected_state),
                )
                changed = conn.rowcount > 0
                conn.commit()
                return changed
        return await asyncio.to_thread(op)


class BrowserResumeJobDB:
    """PostgreSQL 持久 lease 队列（Phase 3R 替代 Redis Stream）。

    所有同步 PostgreSQL 调用均通过 ``asyncio.to_thread``；查询和更新始终带
    ``tenant_id`` 条件。``assistance_id`` 唯一约束保证幂等入队；worker 用
    ``FOR UPDATE SKIP LOCKED`` 领取，lease 过期可被回收。不保存异常正文，
    只存白名单错误码。
    """

    @staticmethod
    def available() -> bool:
        """惰性探活 PostgreSQL 连接池，用于决定是否启用分布式恢复。

        必须惰性调用，禁止在模块顶层或 ``__init__`` 中触发（避免包初始化副作用）。
        """
        try:
            with get_db_connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def enqueue(
        self, *, tenant_id: str, assistance_id: str, run_id: str, job_id: str,
    ) -> bool:
        """插入 resume job。``assistance_id`` UNIQUE 保证重复入队幂等（返回 False）。"""
        def op() -> bool:
            with get_db_connection() as conn:
                conn.execute(
                    """INSERT INTO bs_browser_resume_jobs
                    (job_id,tenant_id,assistance_id,run_id,state,available_at)
                    VALUES (%s,%s,%s,%s,'pending',CURRENT_TIMESTAMP)
                    ON CONFLICT (assistance_id) DO NOTHING""",
                    (job_id, tenant_id, assistance_id, run_id),
                )
                changed = conn.rowcount > 0
                conn.commit()
                return changed
        return await asyncio.to_thread(op)

    async def enqueue_from_assistance(
        self, *, tenant_id: str, assistance_id: str, run_id: str, job_id: str,
    ) -> bool:
        """Atomically transition the audit row and enqueue its resume job."""
        def op() -> bool:
            with get_db_connection() as conn:
                conn.execute(
                    """WITH transitioned AS (
                        UPDATE bs_browser_assistance_requests
                        SET state='resume_queued', updated_at=CURRENT_TIMESTAMP
                        WHERE tenant_id=%s AND assistance_id=%s AND run_id=%s
                          AND state='controlling'
                        RETURNING tenant_id, assistance_id, run_id
                    )
                    INSERT INTO bs_browser_resume_jobs
                        (job_id,tenant_id,assistance_id,run_id,state,available_at)
                    SELECT %s,tenant_id,assistance_id,run_id,'pending',CURRENT_TIMESTAMP
                    FROM transitioned
                    ON CONFLICT (assistance_id) DO NOTHING""",
                    (tenant_id, assistance_id, run_id, job_id),
                )
                changed = conn.rowcount > 0
                conn.commit()
                return changed
        return await asyncio.to_thread(op)

    async def claim_batch(
        self, *, lease_owner: str, lease_seconds: int = 600, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """领取 pending 到期或 processing 租约过期的 job，同事务标记 processing。

        ``FOR UPDATE SKIP LOCKED`` 让多 worker 互不阻塞、各领不同行。
        """
        def op() -> list[dict[str, Any]]:
            with get_db_connection() as conn:
                conn.execute(
                    """SELECT job_id, tenant_id, assistance_id, run_id, attempts
                    FROM bs_browser_resume_jobs
                    WHERE (state = 'pending' AND available_at <= CURRENT_TIMESTAMP)
                       OR (state = 'processing' AND lease_until < CURRENT_TIMESTAMP)
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s""",
                    (limit,),
                )
                rows = conn.fetchall()
                if not rows:
                    conn.commit()
                    return []
                job_ids = [row["job_id"] for row in rows]
                conn.execute(
                    """UPDATE bs_browser_resume_jobs
                    SET state = 'processing',
                        lease_owner = %s,
                        lease_until = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                        attempts = attempts + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = ANY(%s)""",
                    (lease_owner, str(lease_seconds), job_ids),
                )
                conn.commit()
                return [dict(row) for row in rows]
        return await asyncio.to_thread(op)

    async def mark_completed(
        self, *, tenant_id: str, job_id: str, lease_owner: str,
    ) -> bool:
        def op() -> bool:
            with get_db_connection() as conn:
                conn.execute(
                    """UPDATE bs_browser_resume_jobs SET state='completed',
                    completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                    WHERE tenant_id=%s AND job_id=%s AND state='processing'
                      AND lease_owner=%s""",
                    (tenant_id, job_id, lease_owner),
                )
                changed = conn.rowcount > 0
                conn.commit()
                return changed
        return await asyncio.to_thread(op)

    async def mark_failed(
        self, *, tenant_id: str, job_id: str, lease_owner: str,
        error_code: str, permanent: bool, backoff_seconds: int = 30,
    ) -> None:
        """确定性失败或超过最大重试标记 failed；可重试失败重置为 pending 并退避。"""
        def op():
            with get_db_connection() as conn:
                if permanent:
                    conn.execute(
                        """UPDATE bs_browser_resume_jobs SET state='failed',
                        last_error_code=%s, updated_at=CURRENT_TIMESTAMP
                        WHERE tenant_id=%s AND job_id=%s AND state='processing'
                          AND lease_owner=%s""",
                        (error_code, tenant_id, job_id, lease_owner),
                    )
                else:
                    conn.execute(
                        """UPDATE bs_browser_resume_jobs SET state='pending',
                        last_error_code=%s,
                        available_at=CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                        lease_owner=NULL, lease_until=NULL, updated_at=CURRENT_TIMESTAMP
                        WHERE tenant_id=%s AND job_id=%s AND state='processing'
                          AND lease_owner=%s""",
                        (error_code, str(backoff_seconds), tenant_id, job_id, lease_owner),
                    )
                conn.commit()
        await asyncio.to_thread(op)
