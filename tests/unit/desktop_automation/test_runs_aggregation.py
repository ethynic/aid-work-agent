"""runs §5.4 聚合规则测试（R10，表驱动全分支）

分支：全部 applied+verified→succeeded；任一 unknown 且已有成功→partial、否则 unknown
（unknown 优先于 success）；全部未提交且取消→cancelled；部分已发送后截止→partial+
剩余 expired；纯等待超时→expired；有成功有失败（无 unknown）→partial；全失败→failed；
未全部终态→None。
"""

import pytest

from src.desktop_automation import runs

pytestmark = pytest.mark.unit


def delivery(state, effect=None, phase=None):
    return {"state": state, "effect": effect, "phase": phase}


SUCCESS = lambda: delivery("succeeded", "applied", "verified")
UNKNOWN = lambda: delivery("unknown", "unknown", "unknown")
FAILED = lambda: delivery("failed", "none", "prepared")
PENDING = lambda: delivery("pending")
SKIPPED = lambda: delivery("skipped")
EXPIRED = lambda: delivery("expired")
STARTED_UNVERIFIED = lambda: delivery("dispatched", None, "may_have_started")


CASES = [
    # (deliveries, kwargs, expected)
    ([SUCCESS(), SUCCESS()], {}, "succeeded"),
    ([SUCCESS(), SUCCESS(), SUCCESS()], {}, "succeeded"),
    # 任一 unknown：已有成功 → partial；否则 unknown（unknown 优先于 success）
    ([SUCCESS(), UNKNOWN()], {}, "partial"),
    ([UNKNOWN()], {}, "unknown"),
    ([UNKNOWN(), FAILED()], {}, "unknown"),
    # unknown 优先于 success：多数成功但一个 unknown → partial 而非 succeeded
    ([SUCCESS(), SUCCESS(), UNKNOWN()], {}, "partial"),
    # 全部未提交且取消 → cancelled
    ([PENDING(), PENDING()], {"cancelled": True}, "cancelled"),
    ([PENDING(), SKIPPED()], {"cancelled": True}, "cancelled"),
    # 部分已发送后截止 → partial（剩余条目由清扫置 expired）；纯等待超时 → expired
    ([SUCCESS(), PENDING()], {"deadline_exceeded": True}, "partial"),
    ([SUCCESS(), STARTED_UNVERIFIED(), PENDING()], {"deadline_exceeded": True}, "partial"),
    ([PENDING(), PENDING()], {"deadline_exceeded": True}, "expired"),
    ([], {"deadline_exceeded": True}, None),
    # 有成功有失败（无 unknown）→ partial（本轮部分副作用）；全失败 → failed
    ([SUCCESS(), FAILED()], {}, "partial"),
    ([FAILED(), FAILED()], {}, "failed"),
    # 全部终态但含 skipped（成功后取消/停止：已提交结果照实回传 → partial）
    ([SUCCESS(), SKIPPED()], {}, "partial"),
    # 未全部终态 → None（继续等待）
    ([SUCCESS(), PENDING()], {}, None),
    ([SUCCESS(), STARTED_UNVERIFIED()], {}, None),
]


@pytest.mark.parametrize("deliveries,kwargs,expected", CASES)
def test_aggregation_table(deliveries, kwargs, expected):
    assert runs.compute_run_terminal_state(deliveries, **kwargs) == expected


class TestPredicates:
    def test_success_requires_verified_phase(self):
        """applied 还必须有本次验证证据（phase=verified）才成功"""
        assert runs.delivery_is_success(delivery("succeeded", "applied", "verified"))
        assert not runs.delivery_is_success(delivery("dispatched", "applied", "prepared"))
        assert not runs.delivery_is_success(delivery("dispatched", "applied", "may_have_started"))
        assert not runs.delivery_is_success(delivery("succeeded", "applied", None))

    def test_unknown_detection(self):
        assert runs.delivery_is_unknown(delivery("unknown", "unknown", "unknown"))
        assert runs.delivery_is_unknown(delivery("dispatched", "unknown", "prepared"))
        assert runs.delivery_is_unknown(delivery("dispatched", "applied", "unknown"))
        assert runs.delivery_is_unknown(delivery("unknown", None, None))
        assert not runs.delivery_is_unknown(delivery("succeeded", "applied", "verified"))

    def test_started_detection(self):
        """「已发送」= 越过 prepared（may_have_started/verified/unknown 或已派发）"""
        assert runs.delivery_is_started(delivery("dispatched", None, "may_have_started"))
        assert runs.delivery_is_started(delivery("dispatched", None, "unknown"))
        assert runs.delivery_is_started(delivery("dispatched", None, None))  # state=dispatched
        assert not runs.delivery_is_started(delivery("pending", None, "prepared"))
        assert not runs.delivery_is_started(delivery("pending", None, None))
        assert not runs.delivery_is_started(delivery("skipped", None, None))

class TestDeadlineSweep:
    """P1-1 回归：截止清扫的未提交/已越过提交边界分类（真实 DB）"""

    def test_expire_unstarted_classifies_by_phase(self, tenant_id):
        import uuid as uuid_mod
        from datetime import datetime, timezone

        from src.db.database import get_db_connection
        from src.desktop_automation import deliveries as da_deliveries

        now = datetime.now(timezone.utc)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO desktop_automation_runs
                    (id, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref,
                     user_id, state, due_at)
                VALUES (%s, %s, %s, 'sc', 'task', 'rev', 'owner-1', 'running', %s)
                """,
                (str(uuid_mod.uuid4()), tenant_id, str(uuid_mod.uuid4()), now),
            )
            run_id = None
            cur.execute("SELECT id FROM desktop_automation_runs WHERE tenant_id = %s", (tenant_id,))
            run_id = str(cur.fetchone()["id"])
            for pos, state, phase in (
                (1, "pending", None),
                (2, "dispatched", None),
                (3, "dispatched", "prepared"),
                (4, "dispatched", "may_have_started"),
                (5, "dispatched", "unknown"),
            ):
                cur.execute(
                    """
                    INSERT INTO desktop_automation_deliveries
                        (tenant_id, run_id, user_id, position, operation, state, phase)
                    VALUES (%s, %s, 'owner-1', %s, 'op', %s, %s)
                    """,
                    (tenant_id, run_id, pos, state, phase),
                )
            da_deliveries.expire_unstarted_deliveries(cur, tenant_id, run_id)
            conn.commit()
        rows = da_deliveries.list_run_deliveries(run_id, tenant_id)
        by_pos = {d["position"]: d for d in rows}
        # 未提交（pending / dispatched+prepared/NULL）→ expired
        assert by_pos[1]["state"] == "expired"
        assert by_pos[2]["state"] == "expired"
        assert by_pos[3]["state"] == "expired"
        # 已越过提交边界（dispatched+may_have_started/unknown）→ unknown，绝不当 expired
        assert by_pos[4]["state"] == "unknown"
        assert by_pos[4]["effect"] == "unknown" and by_pos[4]["phase"] == "unknown"
        assert by_pos[5]["state"] == "unknown"
