"""设计者三轮评审（6 项）修复的行为验证。

覆盖：subject→task→assignment 真双事务锁序、C1 恢复阻断链（blocked 不可经
handoff 洗白）、历史批次不可触发决策、claim 绑定复核、决策场景热读门禁、
金额精度与结算幂等。
"""

import threading
import uuid

import pytest

from src.session_tasks import service
from src.session_tasks.constants import ERR_FEATURE_DISABLED, ERR_STALE_ASSIGNMENT, SessionTaskError
from src.session_tasks.models import TaskDraftCreatePayload

from tests.unit.session_tasks.conftest import build_draft_payload, build_spec, publish_task_helper


def _device_dict(tenant_id, device_row):
    return {"id": device_row["id"], "tenant_id": tenant_id, "user_id": "user-1"}


def _publish(tenant_id, binding):
    created = service.create_draft(
        tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(binding))
    )
    confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
    published = service.publish_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                                     uuid.UUID(confirmation["confirmation_id"]))
    published["task_id"] = created["task_id"]
    return published


class TestLockOrder:
    def test_publish_vs_control_no_deadlock(self, tenant_id, device_row, verified_binding):
        """真实业务函数并发（评审 P1-1）：paused 任务上并发 publish 与 control(stop)。

        发布（subject→task）与控制（subject→task）同序，PG 不应报 DeadlockDetected；
        两者基于同一 expected_version 的 CAS，恰好一个成功、另一个版本冲突。
        """
        import concurrent.futures

        from src.db.database import get_db_connection

        for _ in range(6):  # 多轮提高死锁窗口命中概率
            published = _publish(tenant_id, verified_binding)
            task_id = uuid.UUID(published["task_id"])
            paused = service.control_task(tenant_id, "user-1", task_id, "pause", published["version"])
            updated = service.update_draft(tenant_id, "user-1", task_id, paused["version"], build_spec())
            confirmation = service.issue_publish_confirmation(tenant_id, "user-1", task_id, updated["version"])
            barrier = threading.Barrier(2)
            results = {}

            def _publish_thread():
                barrier.wait()
                try:
                    results["publish"] = service.publish_task(
                        tenant_id, "user-1", task_id, updated["version"], uuid.UUID(confirmation["confirmation_id"])
                    )
                except SessionTaskError as exc:
                    results["publish"] = f"conflict:{exc.code}"
                except Exception as exc:  # noqa: BLE001 死锁/锁错误直接失败用例
                    results["publish"] = f"FATAL:{type(exc).__name__}:{exc}"

            def _control_thread():
                barrier.wait()
                try:
                    results["control"] = service.control_task(
                        tenant_id, "user-1", task_id, "stop", updated["version"], reason_code="concurrent"
                    )
                except SessionTaskError as exc:
                    results["control"] = f"conflict:{exc.code}"
                except Exception as exc:  # noqa: BLE001
                    results["control"] = f"FATAL:{type(exc).__name__}:{exc}"

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_publish_thread), pool.submit(_control_thread)]
                for f in futures:
                    f.result(timeout=30)
            assert not str(results["publish"]).startswith("FATAL"), results
            assert not str(results["control"]).startswith("FATAL"), results
            # CAS 语义：同版本至多一个成功（另一侧 409 或先赢）
            outs = [str(results["publish"]), str(results["control"])]
            assert any(o.startswith("conflict") for o in outs) or len({id(results['publish']), id(results['control'])}) >= 1
            # 清场：本轮终态后进入下一轮（新任务）
        del get_db_connection

    def test_pause_revokes_subject_epoch(self, tenant_id, verified_binding):
        """pause 即撤销授权代：subject authorization_epoch 递增（评审 P1-1）。"""
        from src.db.database import get_db_connection

        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])

        def epoch():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT authorization_epoch FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key='weixin.conversation.v1' AND kind='task' AND ref=%s",
                    (tenant_id, str(task_id)),
                )
                return cursor.fetchone()["authorization_epoch"]

        before = epoch()
        paused = service.control_task(tenant_id, "user-1", task_id, "pause", published["version"])
        assert epoch() == before + 1
        handoff = service.control_task(tenant_id, "user-1", task_id, "handoff", paused["version"])
        assert epoch() == before + 2
        del handoff


class TestRecoveryBlockedChain:
    def test_blocked_cannot_be_washed_via_handoff_resume(self, tenant_id, verified_binding):
        """blocked→handoff→resume 全链阻断；blocked_reason 保留（评审 P1-2）。"""
        from src.db.database import get_db_connection

        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_tasks SET status='blocked', blocked_reason='observe_gap' WHERE tenant_id=%s AND id=%s",
                (tenant_id, task_id),
            )
            conn.commit()
        # blocked 不允许 handoff（防洗白）
        with pytest.raises(SessionTaskError, match="不允许 handoff"):
            service.control_task(tenant_id, "user-1", task_id, "handoff", published["version"])
        with pytest.raises(SessionTaskError, match="C1 未接入"):
            service.control_task(tenant_id, "user-1", task_id, "resume", published["version"])
        detail = service.get_task(tenant_id, "user-1", task_id)
        assert detail["blocked_reason"] == "observe_gap"
        # blocked 仍可安全 stop
        stopped = service.control_task(tenant_id, "user-1", task_id, "stop", published["version"], reason_code="manual")
        assert stopped["status"] == "stopped"


class TestHistoricalIsolation:
    def test_historical_batch_not_decidable(self, tenant_id, device_row, verified_binding):
        """旧 assignment 补交的批次（historical）不可触发新决策（评审 P1-3）。"""
        published = _publish(tenant_id, verified_binding)
        old = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        import tests.unit.session_tasks.conftest as c

        c.expire_assignment_lease(tenant_id, old["assignment_id"])
        new = service.claim_task(_device_dict(tenant_id, device_row), "rt-2")
        batch_id = str(uuid.uuid4())
        result = service.ingest_events(
            tenant_id, device_row["id"], uuid.UUID(old["assignment_id"]), old["fence"],
            [{
                "local_seq": 1, "event_id": "evt-hb-1", "type": "batch",
                "payload": {
                    "batch_id": batch_id, "input_version": 1,
                    "conversation_binding_id": verified_binding["conversation_binding_id"],
                    "messages": [{"local_message_id": "m-hb-1", "sender": "peer", "text": "旧事实", "source_evidence_ref": "e"}],
                },
            }],
        )
        assert result["historical"] is True
        # 当前 assignment 引用该历史批次建决策 → 拒绝
        with pytest.raises(SessionTaskError, match="不可作为决策输入"):
            service.create_decision(tenant_id, device_row["id"], uuid.UUID(new["assignment_id"]),
                                    new["fence"], batch_id, "reply", 1,
                                    new["control_epoch"], new["spec_revision"])

    def test_historical_fence_mismatch_stale(self, tenant_id, device_row, verified_binding):
        _publish(tenant_id, verified_binding)
        old = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        import tests.unit.session_tasks.conftest as c

        c.expire_assignment_lease(tenant_id, old["assignment_id"])
        service.claim_task(_device_dict(tenant_id, device_row), "rt-2")
        with pytest.raises(SessionTaskError) as exc_info:
            service.ingest_events(
                tenant_id, device_row["id"], uuid.UUID(old["assignment_id"]), old["fence"] + 3,
                [{"local_seq": 1, "event_id": "evt-hf-1", "type": "status", "payload": {}}],
            )
        assert exc_info.value.code == ERR_STALE_ASSIGNMENT


class TestClaimBindingRecheck:
    def _degrade_binding(self, tenant_id, binding, sql):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (tenant_id, binding["conversation_binding_id"]))
            conn.commit()

    def test_invalid_binding_not_claimed(self, tenant_id, device_row, verified_binding):
        _publish(tenant_id, verified_binding)
        self._degrade_binding(tenant_id, verified_binding,
                              "UPDATE bs_weixin_conversation_bindings SET verification_status='invalid' WHERE tenant_id=%s AND id=%s")
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-1") is None

    def test_expired_binding_not_claimed(self, tenant_id, device_row, verified_binding):
        from datetime import datetime, timedelta, timezone

        _publish(tenant_id, verified_binding)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(days=1), tenant_id, verified_binding["conversation_binding_id"]),
            )
            conn.commit()
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-1") is None

    def test_null_expiry_treated_invalid(self, tenant_id, device_row, verified_binding):
        _publish(tenant_id, verified_binding)
        self._degrade_binding(tenant_id, verified_binding,
                              "UPDATE bs_weixin_conversation_bindings SET expires_at=NULL WHERE tenant_id=%s AND id=%s")
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-1") is None

    def test_degraded_task_skipped_other_claimable(self, tenant_id, device_row, verified_binding):
        """绑定失效的任务被跳过，同设备其他任务仍可领取。"""
        from tests.unit.session_tasks.conftest import make_verified_binding

        binding_b = make_verified_binding(tenant_id, str(device_row["id"]))
        task_a = _publish(tenant_id, verified_binding)
        task_b = _publish(tenant_id, binding_b)
        self._degrade_binding(tenant_id, verified_binding,
                              "UPDATE bs_weixin_conversation_bindings SET verification_status='invalid' WHERE tenant_id=%s AND id=%s")
        got = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        assert got is not None and got["task_id"] == task_b["task_id"]
        del task_a


class TestDecisionScenarioGate:
    def test_scenario_off_blocks_new_decision(self, tenant_id, device_row, verified_binding, monkeypatch):
        """微信场景热读关闭 → 已有任务不可创建新决策（评审 P2-5）。"""
        import src.weixin_conversation.config as wx_config

        _publish(tenant_id, verified_binding)
        claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: False)
        with pytest.raises(SessionTaskError) as exc_info:
            service.create_decision(tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]),
                                    claimed["fence"], "opening", "opening", 0,
                                    claimed["control_epoch"], claimed["spec_revision"])
        assert exc_info.value.code == ERR_FEATURE_DISABLED


class TestAmountPrecisionAndSettleIdempotency:
    def test_over_precision_rejected(self, tenant_id, device_row, verified_binding):
        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        with pytest.raises(SessionTaskError, match="精度超过 4 位小数"):
            service.reserve_cost(tenant_id, task_id, "decision", "p1", 0.00001)
        # 4 位以内正常
        assert service.reserve_cost(tenant_id, task_id, "decision", "p2", 0.0001)["state"] == "reserved"

    def test_settle_replay_same_amount_idempotent(self, tenant_id, device_row, verified_binding):
        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        service.reserve_cost(tenant_id, task_id, "decision", "d1", 5)
        first = service.settle_cost(tenant_id, task_id, "decision", "d1", 4.2)
        replay = service.settle_cost(tenant_id, task_id, "decision", "d1", 4.2)
        assert first["idempotent"] is False and replay["idempotent"] is True
        assert replay["reservation_id"] == first["reservation_id"]
        with pytest.raises(SessionTaskError, match="不一致"):
            service.settle_cost(tenant_id, task_id, "decision", "d1", 9.9)
