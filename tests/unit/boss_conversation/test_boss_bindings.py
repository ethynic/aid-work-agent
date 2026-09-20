"""BOSS 绑定管理服务定向测试（设计 §5.2/§5.5.5）：list/create-pending/invalidate/
unblock（owner-only + expected_block_epoch CAS，成功 epoch 再递增+审计，解阻≠任务恢复）
+ 控制请求 stale 不覆盖新阻断 + 五元幂等键同代不同 block epoch 共存。
"""

import uuid

import pytest

from src.session_tasks import control_requests as control_requests_mod
from src.session_tasks.constants import SessionTaskError

from tests.unit.boss_conversation.conftest import create_test_binding


def _binding(tenant_id, device_row, name="张三", job="j1", **kw):
    return create_test_binding(
        tenant_id, "user-1", device_id=str(device_row["id"]),
        account_scope_id=str(uuid.uuid4()), candidate_name=name, job_id=job, **kw
    )


class TestBindingManagement:
    def test_create_pending_starts_unverified(self, tenant_id, device_row):
        binding = _binding(tenant_id, device_row)
        assert binding["verification_status"] == "pending"
        from src.boss_conversation import bindings as svc

        rows = svc.list_bindings(tenant_id, "user-1")
        assert len(rows) == 1 and rows[0]["verification_status"] == "pending"
        # 列表不暴露加密证据/指纹
        assert "encrypted_identity_evidence" not in rows[0]

    def test_invalid_uuid_ids_controlled_400(self, tenant_id, device_row):
        """非阻断 c（六审）+ P2-1（七审）：非法 device/account/binding id（含列表
        过滤值）→ 受控 400 而非 DB 500。"""
        from src.boss_conversation import bindings as svc
        from src.session_tasks.constants import SessionTaskError

        with pytest.raises(SessionTaskError) as exc:
            svc.create_pending_binding(
                tenant_id, "user-1", device_id="not-a-uuid",
                account_scope_id=str(uuid.uuid4()), candidate_name="张三", job_id="j1",
            )
        assert exc.value.status_code == 400 and exc.value.code == "VALIDATION_FAILED"
        with pytest.raises(SessionTaskError) as exc:
            svc.create_pending_binding(
                tenant_id, "user-1", device_id=str(device_row["id"]),
                account_scope_id="bad-scope", candidate_name="张三", job_id="j1",
            )
        assert exc.value.status_code == 400
        with pytest.raises(SessionTaskError) as exc:
            svc.invalidate_binding(tenant_id, "user-1", "not-a-uuid")
        assert exc.value.status_code == 400
        with pytest.raises(SessionTaskError) as exc:
            svc.unblock_binding(tenant_id, "user-1", "not-a-uuid", expected_block_epoch=0)
        assert exc.value.status_code == 400
        # P2-1：list_bindings 的 device_id 过滤值同样预校验
        with pytest.raises(SessionTaskError) as exc:
            svc.list_bindings(tenant_id, "user-1", device_id="not-a-uuid")
        assert exc.value.status_code == 400
        # 合法设备 id 过滤照常工作
        _binding(tenant_id, device_row)
        rows = svc.list_bindings(tenant_id, "user-1", device_id=str(device_row["id"]))
        assert len(rows) == 1

    def test_owner_acl_on_list_and_invalidate(self, tenant_id, device_row):
        binding = _binding(tenant_id, device_row)
        from src.boss_conversation import bindings as svc

        assert svc.list_bindings(tenant_id, "other-user") == []
        with pytest.raises(SessionTaskError):
            svc.invalidate_binding(tenant_id, "other-user", binding["conversation_binding_id"])
        result = svc.invalidate_binding(tenant_id, "user-1", binding["conversation_binding_id"])
        assert result["verification_status"] == "invalid"
        # 已失效绑定不满足发布有效性
        from src.db.database import get_db_connection
        from src.boss_conversation.bindings import is_binding_verified_valid

        with get_db_connection() as conn:
            row = svc._load_binding_row(conn.cursor(), tenant_id, binding["conversation_binding_id"])
        assert not is_binding_verified_valid(row)

    def test_resume_must_exist_in_tenant(self, tenant_id, device_row):
        from src.boss_conversation.bindings import create_pending_binding

        with pytest.raises(SessionTaskError):
            create_pending_binding(
                tenant_id, "user-1", device_id=str(device_row["id"]),
                account_scope_id=str(uuid.uuid4()), candidate_name="李四",
                job_id="j2", resume_id=999999999,
            )

    def test_device_ownership_enforced(self, tenant_id, device_row):
        from src.boss_conversation.bindings import create_pending_binding

        with pytest.raises(SessionTaskError):
            create_pending_binding(
                "other_tenant", "user-1", device_id=str(device_row["id"]),
                account_scope_id=str(uuid.uuid4()), candidate_name="王五", job_id="j3",
            )


class TestUnblockCas:
    def _blocked(self, tenant_id, device_row):
        binding = _binding(tenant_id, device_row)
        from src.db.database import get_db_connection
        from src.boss_conversation.gate import BossBindingGuard

        guard = BossBindingGuard()
        with get_db_connection() as conn:
            epoch = guard.block_binding(
                conn.cursor(),
                {"tenant_id": tenant_id, "conversation_binding_id": binding["conversation_binding_id"]},
                "rate_ledger_anomaly",
            )
            conn.commit()
        return binding, epoch

    def test_unblock_cas_and_epoch_increment(self, tenant_id, device_row):
        from src.db.database import get_db_connection

        from src.boss_conversation import bindings as svc

        binding, epoch = self._blocked(tenant_id, device_row)
        bid = binding["conversation_binding_id"]
        # 旧 epoch（错误 CAS）→ 409，不得清除较新阻断
        with pytest.raises(SessionTaskError):
            svc.unblock_binding(tenant_id, "user-1", bid, expected_block_epoch=epoch - 1)
        row = _load_row(tenant_id, bid)
        assert row["automation_blocked"] is True
        # 正确 epoch → 解阻成功，epoch 再递增（下次解阻需携带新 epoch）
        result = svc.unblock_binding(tenant_id, "user-1", bid, expected_block_epoch=epoch)
        assert result["automation_blocked"] is False
        assert result["automation_block_epoch"] == epoch + 1
        row = _load_row(tenant_id, bid)
        assert row["automation_blocked"] is False and row["automation_block_reason"] is None
        # 再次解阻：未阻断 → 409
        with pytest.raises(SessionTaskError):
            svc.unblock_binding(tenant_id, "user-1", bid, expected_block_epoch=epoch + 1)
        # 解阻审计（脱敏 detail）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT kind FROM desktop_automation_audit_events WHERE tenant_id=%s AND kind='boss_binding_unblock'",
                (tenant_id,),
            )
            assert cursor.fetchone() is not None

    def test_unblock_owner_only(self, tenant_id, device_row):
        from src.boss_conversation import bindings as svc

        binding, epoch = self._blocked(tenant_id, device_row)
        with pytest.raises(SessionTaskError):
            svc.unblock_binding(tenant_id, "other-user", binding["conversation_binding_id"],
                                expected_block_epoch=epoch)

    def test_unblock_does_not_resume_task(self, tenant_id, device_row, boss_binding):
        """解阻 ≠ 任务恢复：human_required 任务不因解阻变化（无任何任务行写操作）。"""
        from src.db.database import get_db_connection

        from src.boss_conversation import bindings as svc

        binding, epoch = self._blocked(tenant_id, device_row)
        svc.unblock_binding(tenant_id, "user-1", binding["conversation_binding_id"],
                            expected_block_epoch=epoch)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS n FROM session_tasks WHERE tenant_id=%s", (tenant_id,)
            )
            assert int(cursor.fetchone()["n"]) == 0  # 解阻路径零任务行触达


def _load_row(tenant_id, bid):
    from src.db.database import get_db_connection

    from src.boss_conversation.bindings import _load_binding_row

    with get_db_connection() as conn:
        return _load_binding_row(conn.cursor(), tenant_id, bid)


class TestControlRequestsInterplay:
    def test_stale_request_does_not_clear_newer_block(self, tenant_id, device_row):
        """控制请求 stale 不覆盖新阻断：旧 block epoch 的请求被标 stale，阻断保持。"""
        from src.db.database import get_db_connection

        binding = _binding(tenant_id, device_row)
        bid = binding["conversation_binding_id"]
        task_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO session_tasks (id, tenant_id, user_id, scenario_key, device_id,
                    account_binding_id, conversation_binding_id, status, control_epoch)
                VALUES (%s, %s, 'user-1', 'boss.chat_reply.v1', %s, %s, %s, 'active', 0)
                """,
                (task_id, tenant_id, device_row["id"], str(uuid.uuid4()), bid),
            )
            conn.commit()
        # 请求基于旧 block epoch（0），随后阻断推进到 epoch 1
        with _cursor_of() as cursor:
            assert control_requests_mod.insert_control_request(
                cursor, tenant_id, task_id,
                expected_control_epoch=0, expected_block_epoch=0,
                reason="rate_ledger_anomaly", source_type="rate_settlement", source_ref="probe",
            )
        from src.boss_conversation.gate import BossBindingGuard

        with get_db_connection() as conn:
            new_epoch = BossBindingGuard().block_binding(
                conn.cursor(),
                {"tenant_id": tenant_id, "conversation_binding_id": bid},
                "rate_ledger_anomaly",
            )
            conn.commit()
        assert new_epoch == 1
        # 同代不同 block epoch 的新请求成行（五元键不吞新请求）
        with _cursor_of() as cursor:
            assert control_requests_mod.insert_control_request(
                cursor, tenant_id, task_id,
                expected_control_epoch=0, expected_block_epoch=1,
                reason="rate_ledger_anomaly", source_type="rate_settlement", source_ref="probe",
            )
        stats = control_requests_mod.run_control_request_tick()
        # 旧 epoch 请求 stale（不覆盖新阻断）；新 epoch 请求正常 applied 迁移
        assert stats["claimed"] == 2 and stats["stale"] == 1 and stats["applied"] == 1
        # 阻断保持（处理器无自动解阻路径）
        from src.boss_conversation.bindings import _load_binding_row

        with get_db_connection() as conn:
            row = _load_binding_row(conn.cursor(), tenant_id, bid)
        assert row["automation_blocked"] is True
        rows = control_requests_rows(tenant_id)
        assert {r["status"] for r in rows} == {"stale", "applied"}

    def test_five_key_coexistence_same_epoch_different_block_epoch(self, tenant_id, device_row):
        """五元幂等键：同代同原因、不同 block epoch 两行共存（CR 三审 P1-1 回归）。"""
        from src.db.database import get_db_connection

        task_id = str(uuid.uuid4())
        from src.session_tasks import control_requests as cr

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO session_tasks (id, tenant_id, user_id, scenario_key, device_id,
                    account_binding_id, conversation_binding_id, status, control_epoch)
                VALUES (%s, %s, 'user-1', 'boss.chat_reply.v1', %s, %s, %s, 'active', 3)
                """,
                (task_id, tenant_id, device_row["id"], str(uuid.uuid4()), str(uuid.uuid4())),
            )
            conn.commit()
        with _cursor_of() as cursor:
            assert cr.insert_control_request(
                cursor, tenant_id, task_id,
                expected_control_epoch=3, expected_block_epoch=1,
                reason="rate_window_race", source_type="permit_denied", source_ref="a",
            )
            assert cr.insert_control_request(
                cursor, tenant_id, task_id,
                expected_control_epoch=3, expected_block_epoch=2,
                reason="rate_window_race", source_type="permit_denied", source_ref="b",
            )
            # 同键重复写入幂等（False=已存在）
            assert not cr.insert_control_request(
                cursor, tenant_id, task_id,
                expected_control_epoch=3, expected_block_epoch=2,
                reason="rate_window_race", source_type="permit_denied", source_ref="b",
            )
        rows = control_requests_rows(tenant_id)
        assert len(rows) == 2
        assert {int(r["expected_block_epoch"]) for r in rows} == {1, 2}


def _cursor_of():
    """独立短连接游标（insert_control_request 的调用方事务职责由测试提交）。"""
    from src.db.database import get_db_connection

    class _CursorCtx:
        def __enter__(self):
            self._conn = get_db_connection()
            self.conn = self._conn.__enter__()
            return self.conn.cursor()

        def __exit__(self, *exc):
            if not exc[0]:
                self.conn.commit()
            else:
                self.conn.rollback()
            return self._conn.__exit__(*exc)

    return _CursorCtx()


def control_requests_rows(tenant_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session_task_control_requests WHERE tenant_id=%s ORDER BY id", (tenant_id,)
        )
        return [dict(r) for r in cursor.fetchall()]
