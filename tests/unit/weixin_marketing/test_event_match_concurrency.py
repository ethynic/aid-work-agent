"""事件匹配 worker 并发与瞬时竞争永久测试（P0-1/P2-1 复审修复回归）

- P0-1 回归：task_locked（SKIP LOCKED 瞬时竞争）绝不与业务 skip 同路——
  不推进游标、不 processed、audit 记 event_match_deferred（可重试性质），
  锁释放后下轮正常处理（事件零丢失）。
- 并发探针（验证者固化）：两线程 Barrier 同步并发 event_match_tick，
  同批事件 ×5 轮 + 单线程收尾 → 断言零丢失（每事件恰好一个 occurrence、
  全部 processed）+ 线程限时退出（无死锁）。
- P2-1 回归：eligible > 单 tick 窗口且 cursor>0 时切片随游标前移
  （eligible[cursor:cursor+MAX]），多轮后 processed——旧实现 [cursor:MAX]
  在 cursor>=MAX 时卡死游标。
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.db.database import get_db_connection
from src.desktop_automation import events as da_events
from src.weixin_marketing import dispatch
from src.weixin_marketing import event_sources as wxm_sources
from tests.unit.weixin_marketing.conftest import create_and_publish, utcnow
from tests.unit.weixin_marketing.test_event_match_worker import (
    _accept_webhook,
    _event_row,
    _occurrence_count,
    _register_source,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def event_automation(service, tenant_id, bindings, wx_config, adapter):
    """已发布 event 触发自动化（与 test_event_match_worker 同构，fixture 不跨
    测试模块共享故本文件内定义）"""
    _, group_id = bindings

    def _make(trigger=None):
        return create_and_publish(
            service, tenant_id, group_id,
            trigger=trigger or {
                "type": "event", "source_ref": None, "event_type": "order.completed",
                "delay_seconds": 0,
            },
            blocks=[{"type": "text", "text_content": "事件触发内容"}],
        )

    return _make


class TestTaskLockedNoLoss:
    def test_task_locked_defers_not_skips(self, tenant_id, wx_config, event_automation):
        """task_locked：不推进游标、不 processed、记 deferred；锁释放后下轮处理。

        直接构造竞争：外部事务阻塞 FOR UPDATE 持有 task subject（worker 的
        SKIP LOCKED 拿不到锁 → accept_event_trigger_on 返回 task_locked）。
        """
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        _accept_webhook(tenant_id, source, "ev-lock")

        # 外部长事务持有 task subject 行锁（模拟并发发布/暂停等）。
        # get_db_connection 是 contextmanager——手动 __enter__ 保持事务跨语句存活
        blocker = get_db_connection()
        blocker_conn = blocker.__enter__()
        bcur = blocker_conn.cursor()
        bcur.execute(
            "SELECT id FROM desktop_automation_subjects "
            "WHERE tenant_id = %s AND kind = 'task' AND ref = %s FOR UPDATE",
            (tenant_id, str(automation_id)),
        )
        assert bcur.fetchone() is not None
        try:
            stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
            # 瞬时放弃：不 matched/skipped（不与业务 skip 同路），deferred=1
            assert stats["matched"] == 0
            assert stats["skipped"] == 0
            assert stats["deferred"] == 1
            assert stats["processed_events"] == 0
            row = _event_row(tenant_id, "ev-lock")
            assert row["state"] != "processed"
            assert int(row["match_cursor"]) == 0, "task_locked 不得推进游标"
            # 观测留痕：deferred（独立短事务，非业务 skip）
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT kind, detail FROM desktop_automation_audit_events "
                    "WHERE tenant_id = %s AND kind IN "
                    "('event_match_deferred', 'event_match_skipped')",
                    (tenant_id,),
                )
                audits = [dict(r) for r in cur.fetchall()]
            assert any(
                a["kind"] == "event_match_deferred"
                and a["detail"].get("reason") == "task_locked" for a in audits
            ), audits
            assert not any(a["kind"] == "event_match_skipped" for a in audits), \
                "task_locked 不得记业务性 skip（会被误读为持久结论）"
        finally:
            blocker_conn.rollback()
            blocker.__exit__(None, None, None)

        # 锁释放后下轮重试：正常接纳（零丢失）
        stats2 = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats2["matched"] == 1 and stats2["processed_events"] == 1
        assert _occurrence_count(tenant_id, automation_id) == 1
        assert _event_row(tenant_id, "ev-lock")["state"] == "processed"


class TestConcurrentWorkersProbe:
    def test_two_thread_ticks_no_loss_no_deadlock(
        self, tenant_id, wx_config, event_automation
    ):
        """并发探针（验证者固化）：两线程并发 event_match_tick 同批 5 事件 ×5 轮。

        零丢失断言：单线程收尾 tick 后——
        ①全部事件 processed；②每事件恰好 1 个 occurrence（UNIQUE 触发键保证
        不重，processed 判定保证不丢）；③线程限时退出（无死锁）。
        """
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        event_ids = [f"ev-race-{i}" for i in range(5)]
        for eid in event_ids:
            assert _accept_webhook(tenant_id, source, eid)["accepted"]

        now = utcnow()
        for round_no in range(5):
            barrier = threading.Barrier(2)
            errors: list = []

            def worker():
                try:
                    barrier.wait(timeout=30)
                    dispatch.event_match_tick(now=now, config=wx_config)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker) for _ in range(2)]
                for f in futures:
                    f.result(timeout=120)  # 限时：死锁即超时失败
            assert not errors, f"轮次 {round_no} 并发 tick 异常: {errors}"
            # 中间不变量：任何时刻事件要么在处理要么已 processed，绝不消失
            for eid in event_ids:
                row = _event_row(tenant_id, eid)
                assert row is not None
                assert row["state"] in ("received", "processing", "processed")

        # 单线程收尾：全部收敛 processed
        dispatch.event_match_tick(now=now, config=wx_config)
        for eid in event_ids:
            row = _event_row(tenant_id, eid)
            assert row["state"] == "processed", f"事件丢失/未收敛: {eid} -> {row['state']}"
        # 零丢失且不重复：5 事件 ×1 候选 = 恰好 5 个 occurrence
        assert _occurrence_count(tenant_id, automation_id) == 5


class TestCandidateWindowSlice:
    def test_slice_advances_from_nonzero_cursor(
        self, tenant_id, wx_config, monkeypatch
    ):
        """P2-1：cursor>0 且 eligible 超单轮窗口时，切片随游标前移直至 processed。

        旧实现 eligible[cursor:MAX] 在 cursor>=MAX 时切出空集 → 游标卡死、
        事件永停 processing。新实现 [cursor:cursor+MAX] 每轮推进至多 MAX 个。
        构造：10 个假候选（假 schedule_id → subscription_inactive 业务 skip）、
        cursor 置 5、窗口压到 3 → 至多 2 轮内 processed。
        """
        source_ref = f"hook-slice-{uuid.uuid4().hex[:6]}"
        wxm_sources.create_event_source(
            tenant_id=tenant_id, user_id="owner-1", source_ref=source_ref,
            source_type="webhook", allowed_event_types=["order.completed"],
        )
        # 直接落一条事件行：eligible=10 个假候选（id 唯一即可，处理时定向锁定
        # 不存在的 schedule → subscription_inactive 持久 skip 并推进游标）
        fake_candidates = [
            {
                "scenario_key": "weixin.fixed_content.v1",
                "task_ref": str(uuid.uuid4()),
                "revision_ref": str(uuid.uuid4()),
                "id": str(uuid.uuid4()),
                "condition_ref": None,
                "delay_seconds": 0,
            }
            for _ in range(10)
        ]
        from psycopg2.extras import Json as _Json

        with get_db_connection() as conn:
            cur = conn.cursor()
            src = da_events.get_event_source_by_ref(tenant_id, source_ref)
            cur.execute(
                """
                INSERT INTO desktop_automation_events
                    (tenant_id, source_id, external_event_id, event_type,
                     payload_ref, payload_hash, state, eligible_revision_refs,
                     match_cursor)
                VALUES (%s, %s, 'ev-slice', 'order.completed', '', '',
                        'processing', %s, 5)
                """,
                (tenant_id, str(src["id"]), _Json(fake_candidates)),
            )
            conn.commit()

        monkeypatch.setattr(dispatch, "MAX_EVENT_CANDIDATES_PER_TICK", 3)
        stats1 = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        row = _event_row(tenant_id, "ev-slice")
        if row["state"] != "processed":
            # 第一轮至多推进 3 个（cursor 5→8），第二轮 8→10 收敛 processed
            stats2 = dispatch.event_match_tick(now=utcnow(), config=wx_config)
            row = _event_row(tenant_id, "ev-slice")
            assert row["state"] == "processed", (
                f"P2-1 回归：游标卡死 state={row['state']} cursor={row['match_cursor']}"
            )
        # 旧实现下 cursor=5、MAX=3 切片为空 → stats1.skipped=0 且永不 processed；
        # 新实现两轮内必然 processed，且 5 个剩余候选全部产生持久 skip 审计
        assert int(row["match_cursor"]) == 10
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_audit_events "
                "WHERE tenant_id = %s AND kind = 'event_match_skipped'",
                (tenant_id,),
            )
            assert int(cur.fetchone()["c"]) == 5
