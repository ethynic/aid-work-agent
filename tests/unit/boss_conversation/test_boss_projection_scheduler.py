"""BOSS 投影队列租约与 scheduler 注册隔离测试（六审非阻断 a/e；八审 P2-5 冻结）。

- a：PROCESSING_LEASE_SECONDS 租约——processing 行在租约内不被重复认领；
  到期后可重认领；完成/退避更新以 updated_at CAS 提交，丢失租约一方弃权；
- e：投影 job 注册——启用注册、禁用跳过、注册异常隔离（不拖垮其他 worker）。

**P2-5（八审）隔离库冻结语义**：_claim_rows 是全局认领（无租户过滤），共享开发库
若存在其他租户 pending/processing 行会被本套件短暂认领（副作用）或干扰断言——
TestProjectionLease 全部用例挂 `_isolated_queue` 守卫：队列存在其他租户待处理行
时跳过（需隔离库/专用测试库执行），不做"测试数据优先"的半可靠折衷。
"""

import uuid

import pytest


def _queue_isolated_for(tenant_id: str) -> bool:
    """共享库守卫（九审非阻断 3 最小修正）：复用生产认领条件（与 _claim_rows 一致
    ——pending 且到期（next_retry_at），或 processing 且租约过期），不再无条件
    计数所有 pending/processing 行（防过度跳过）。

    注意：skip **不作为可靠隔离证明**——守卫检查与认领之间存在竞态窗口，他租户
    新增的合格行仍可能被本套件认领；严格隔离需真临时 schema/专用库（已留登记）。"""
    from src.boss_conversation.projection import PROCESSING_LEASE_SECONDS
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS n FROM bs_boss_comm_log_projection_queue
            WHERE tenant_id <> %s AND (
                (status='pending' AND (next_retry_at IS NULL OR next_retry_at <= NOW()))
                OR (status='processing'
                    AND updated_at <= NOW() - (%s * INTERVAL '1 second'))
            )
            """,
            (tenant_id, PROCESSING_LEASE_SECONDS),
        )
        return int(cursor.fetchone()["n"]) == 0


@pytest.fixture()
def _isolated_queue(tenant_id):
    """认领/租约用例前置守卫（P2-5 冻结：隔离库语义，见模块 docstring）。"""
    if not _queue_isolated_for(tenant_id):
        pytest.skip("投影认领/租约互斥测试要求隔离库（共享库存在其他租户待处理行）")


def _seed_queue_row(tenant_id, *, delivery_id=None, status="pending", retry_count=0):
    from datetime import datetime, timedelta, timezone as _tz

    from src.db.database import get_db_connection

    delivery_id = delivery_id or str(uuid.uuid4())
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_boss_comm_log_projection_queue
                (tenant_id, user_id, delivery_id, binding_id, resume_id, status, retry_count)
            VALUES (%s, 'user-1', %s, %s, NULL, %s, %s) RETURNING id
            """,
            (tenant_id, delivery_id, str(uuid.uuid4()), status, retry_count),
        )
        row_id = int(cursor.fetchone()["id"])
        if status == "processing" or retry_count:
            pass
        conn.commit()
    return {"id": row_id, "delivery_id": delivery_id}


def _queue_row(tenant_id, row_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_boss_comm_log_projection_queue WHERE tenant_id=%s AND id=%s",
            (tenant_id, row_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


@pytest.mark.usefixtures("_isolated_queue")
class TestProjectionLease:
    """非阻断 a：processing 租约 + updated_at CAS（P2-5：隔离库守卫，见模块 docstring）。"""

    def test_processing_row_not_reclaimed_within_lease(self, tenant_id):
        from src.boss_conversation.projection import _claim_rows

        row = _seed_queue_row(tenant_id)
        claimed = _claim_rows(10)
        assert [r["id"] for r in claimed] == [row["id"]]
        assert _queue_row(tenant_id, row["id"])["status"] == "processing"
        # 租约内：第二个 worker 立即认领不到（此前 processing 行会被立即重复认领）
        assert _claim_rows(10) == []

    def test_expired_lease_reclaimable(self, tenant_id):
        from src.boss_conversation.projection import (
            PROCESSING_LEASE_SECONDS,
            _claim_rows,
        )

        row = _seed_queue_row(tenant_id)
        first = _claim_rows(10)
        assert [r["id"] for r in first] == [row["id"]]
        # 把租约时间戳回拨超过 PROCESSING_LEASE_SECONDS → 可被重认领
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_comm_log_projection_queue SET updated_at=NOW() - "
                "(%s * INTERVAL '1 second') WHERE tenant_id=%s AND id=%s",
                (PROCESSING_LEASE_SECONDS + 5, tenant_id, row["id"]),
            )
            conn.commit()
        second = _claim_rows(10)
        assert [r["id"] for r in second] == [row["id"]]

    def test_mark_retry_cas_lost_lease_skipped(self, tenant_id):
        """租约 CAS：持有旧 updated_at 令牌的 worker 在行被重认领后弃权（skipped）。"""
        from src.boss_conversation.projection import (
            PROCESSING_LEASE_SECONDS,
            _claim_rows,
            _mark_retry,
        )

        row = _seed_queue_row(tenant_id)
        claimed = _claim_rows(10)[0]
        # 另一 worker 在租约过期后重认领（updated_at 已前进）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_comm_log_projection_queue SET updated_at=NOW() - "
                "(%s * INTERVAL '1 second') WHERE tenant_id=%s AND id=%s",
                (PROCESSING_LEASE_SECONDS + 5, tenant_id, row["id"]),
            )
            conn.commit()
        reclaimed = _claim_rows(10)[0]
        assert reclaimed["id"] == row["id"]
        # 原 worker 按旧令牌提交退避 → CAS 失败 → skipped，不覆盖新 owner
        stale_row = dict(claimed)
        assert _mark_retry(stale_row, "reply_text_unresolvable") == "skipped"
        current = _queue_row(tenant_id, row["id"])
        assert current["status"] == "processing"  # 新 owner 状态未被覆盖
        # 新 owner 按自己的令牌提交 → retried
        assert _mark_retry(dict(reclaimed), "reply_text_unresolvable") == "retried"
        current = _queue_row(tenant_id, row["id"])
        assert current["status"] == "pending" and current["retry_count"] == 1

    def test_dual_worker_projection_tick_single_claim(self, tenant_id):
        """双 worker tick：同一行只被一个 tick 认领（租约内另一个 tick 认领数为 0）。"""
        from src.boss_conversation.projection import _claim_rows

        _seed_queue_row(tenant_id)
        _seed_queue_row(tenant_id)
        first = _claim_rows(10)
        second = _claim_rows(10)
        assert len(first) == 2 and second == []

    def test_dual_worker_parallel_claim_mutual_exclusion(self, tenant_id):
        """P2-3（七审）：两 worker 真并发（独立连接 + barrier 同步起跑）——
        FOR UPDATE SKIP LOCKED + 租约互斥：任一行至多被一方认领，两行并集恰为全量。"""
        import threading

        from src.boss_conversation.projection import _claim_rows

        _seed_queue_row(tenant_id)
        _seed_queue_row(tenant_id)
        results: dict = {}
        errors: list = []
        barrier = threading.Barrier(2, timeout=10)

        def _worker(key):
            try:
                barrier.wait()  # 同步起跑，最大化锁竞争窗口
                results[key] = _claim_rows(10)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=_worker, args=("a",))
        t2 = threading.Thread(target=_worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(20)
        t2.join(20)
        assert errors == []
        ids_a = {r["id"] for r in results["a"]}
        ids_b = {r["id"] for r in results["b"]}
        assert not (ids_a & ids_b)  # 无一行被双方同时认领（SKIP LOCKED 互斥）
        assert len(ids_a) + len(ids_b) == 2  # 两行都被认领且各恰好一次

    def test_skip_locked_second_worker_nonblocking(self, tenant_id):
        """九审非阻断 5：SKIP LOCKED 非阻塞性——worker A 事务认领 2 行并持锁未提交
        期间，worker B 必须**立即**返回并恰好认领其余 2 行（互不重叠）。变异验证：
        若移除 SKIP LOCKED，B 将阻塞在 A 的行锁上直至 A 提交（本用例持锁 ~6s，
        DB lock_timeout=10s 不会先行中断）→ 耗时断言失败。"""
        import threading
        import time

        from src.db.database import get_db_connection
        from src.boss_conversation.projection import _claim_rows

        for _ in range(4):
            _seed_queue_row(tenant_id)

        conn_ctx = get_db_connection()
        conn = conn_ctx.__enter__()
        locked = threading.Event()
        commit_now = threading.Event()
        a_ids: set = set()
        a_errors: list = []

        def _holder():
            try:
                cursor = conn.cursor()
                # 模拟 worker A 的认领事务（与 _claim_rows 同形）：持行锁不提交
                cursor.execute(
                    """
                    UPDATE bs_boss_comm_log_projection_queue
                    SET status='processing', updated_at=NOW()
                    WHERE id IN (
                        SELECT id FROM bs_boss_comm_log_projection_queue
                        WHERE tenant_id=%s AND status='pending'
                        ORDER BY created_at, id LIMIT 2
                        FOR UPDATE
                    )
                    RETURNING id
                    """,
                    (tenant_id,),
                )
                a_ids.update(r["id"] for r in cursor.fetchall())
                locked.set()
                if not commit_now.wait(15):
                    a_errors.append(RuntimeError("worker B 未在 15s 内完成认领"))
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                a_errors.append(exc)
                locked.set()

        thread = threading.Thread(target=_holder)
        thread.start()
        try:
            assert locked.wait(10) and a_errors == []
            t0 = time.monotonic()
            b_rows = _claim_rows(2)
            elapsed = time.monotonic() - t0
        finally:
            commit_now.set()
            thread.join(15)
        assert a_errors == []
        assert len(b_rows) == 2  # 恰好认领其余 2 行（不受 A 持锁影响）
        b_ids = {r["id"] for r in b_rows}
        assert not (b_ids & a_ids)  # 与 A 持锁行互不重叠
        assert elapsed < 4.0, (
            f"SKIP LOCKED 非阻塞性失效：worker B 阻塞 {elapsed:.1f}s（应为立即返回，"
            "若阻塞至 A 提交约 6s）"
        )
        # 状态收口：A 提交后 4 行全部 processing（两 worker 各认领 2 行且无重置）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM bs_boss_comm_log_projection_queue WHERE tenant_id=%s",
                (tenant_id,),
            )
            statuses = sorted(r["status"] for r in cursor.fetchall())
        assert statuses == ["processing"] * 4


class TestProjectionSchedulerRegistration:
    """非阻断 e：投影 job 注册启用/禁用/异常隔离（热读门控在注册时判定）。"""

    def _fresh_manager(self):
        from apscheduler.schedulers.background import BackgroundScheduler

        from src.scheduler.manager import ScheduledTaskManager

        manager = ScheduledTaskManager()
        manager._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        return manager

    def _job_ids(self, manager):
        return {job.id for job in manager._scheduler.get_jobs()}

    def test_enabled_registers_projection_job(self, monkeypatch):
        import src.boss_conversation.config as boss_config

        monkeypatch.setattr(boss_config, "scenario_enabled_gate", lambda: True)
        manager = self._fresh_manager()
        manager._register_system_jobs()
        assert "job_system_boss_comm_log_projection" in self._job_ids(manager)

    def test_disabled_skips_projection_job(self, monkeypatch):
        import src.boss_conversation.config as boss_config

        monkeypatch.setattr(boss_config, "scenario_enabled_gate", lambda: False)
        manager = self._fresh_manager()
        manager._register_system_jobs()
        assert "job_system_boss_comm_log_projection" not in self._job_ids(manager)

    def test_registration_failure_isolated(self, monkeypatch):
        """boss 投影注册异常（门控抛错/挂载失败）被独立 try 域吸收，不向调用方
        传播、不影响同批其他系统 job 注册。"""
        import src.boss_conversation.config as boss_config

        def _boom():
            raise RuntimeError("simulated gate failure")

        monkeypatch.setattr(boss_config, "scenario_enabled_gate", _boom)
        manager = self._fresh_manager()
        manager._register_system_jobs()  # 不抛 = 隔离
        assert "job_system_boss_comm_log_projection" not in self._job_ids(manager)
        # 其他系统 job 不受影响（reconcile 对账每环境必注册）
        assert any(jid.startswith("job_system_") for jid in self._job_ids(manager))
