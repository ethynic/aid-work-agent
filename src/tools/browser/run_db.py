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
