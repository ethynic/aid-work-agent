"""P5 R59③ 遗留收口测试：retiring→retired 收敛 / payloads+occurrences 保留期清理 /
磁盘孤儿素材扫描（只读告警）

全部沿既有 tick 模式（enabled 门控、now 注入、租户隔离）。
"""

import json
import os
import shutil
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from src.local_tools import permits, repository
from src.local_tools.operation_result import apply_operation_result
from src.local_tools.security import sha256_hex
from src.weixin_marketing import assets as wxm_assets
from src.weixin_marketing import dispatch
from src.weixin_marketing import event_sources as wxm_sources
from src.weixin_marketing import retention as wxm_retention
from src.weixin_marketing.constants import SCENARIO_KEY
from tests.unit.weixin_marketing.conftest import (
    create_and_publish,
    manual_run_pending,
    utcnow,
)

pytestmark = pytest.mark.unit


def _insert_occurrence(
    conn_cur, tenant_id, *, scenario_key=SCENARIO_KEY, created_at, status="open"
):
    occ_id = uuid.uuid4()
    conn_cur.execute(
        """
        INSERT INTO desktop_automation_occurrences
            (id, tenant_id, scenario_key, task_ref, revision_ref, user_id,
             trigger_kind, trigger_key, due_at, status, created_at)
        VALUES (%s, %s, %s, %s, %s, 'owner-1', 'time', %s, %s, %s, %s)
        """,
        (
            str(occ_id), tenant_id, scenario_key, str(uuid.uuid4()), str(uuid.uuid4()),
            f"tk-{occ_id.hex[:12]}", created_at, status, created_at,
        ),
    )
    return str(occ_id)


def _insert_run(conn_cur, tenant_id, occurrence_id, *, state="succeeded"):
    run_id = uuid.uuid4()
    conn_cur.execute(
        """
        INSERT INTO desktop_automation_runs
            (id, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref,
             user_id, state, due_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'owner-1', %s, %s)
        """,
        (
            str(run_id), tenant_id, occurrence_id, SCENARIO_KEY, str(uuid.uuid4()),
            str(uuid.uuid4()), state, utcnow(),
        ),
    )
    return str(run_id)


def _insert_payload(conn_cur, tenant_id, *, created_at, payload_hash=None):
    payload_hash = payload_hash or uuid.uuid4().hex
    conn_cur.execute(
        """
        INSERT INTO weixin_marketing_event_payloads
            (tenant_id, source_id, payload_hash, payload_json, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            tenant_id, str(uuid.uuid4()), payload_hash,
            json.dumps({"k": payload_hash}), created_at,
        ),
    )
    return payload_hash


def _insert_event(conn_cur, tenant_id, payload_hash, *, state, created_at):
    conn_cur.execute(
        """
        INSERT INTO desktop_automation_events
            (tenant_id, source_id, external_event_id, payload_ref, payload_hash,
             state, received_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tenant_id, str(uuid.uuid4()), f"ext-{uuid.uuid4().hex[:10]}",
            f"wxm-event:{payload_hash}", payload_hash, state, created_at,
        ),
    )


# ==================== retiring → retired 收敛 ====================


class TestRetireExpiredKeys:
    def _rotate(self, tenant_id):
        source = wxm_sources.create_event_source(
            tenant_id=tenant_id, user_id="owner-1",
            source_ref=f"hook-{uuid.uuid4().hex[:8]}", source_type="webhook",
        )
        rotated = wxm_sources.rotate_key(
            tenant_id=tenant_id, source_id=source["source"]["id"],
            user_id="owner-1", rotate_window_seconds=900,
        )
        return source, rotated

    def _key_statuses(self, tenant_id, source_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT key_id, status FROM weixin_marketing_event_source_keys "
                "WHERE tenant_id = %s AND source_id = %s ORDER BY key_version",
                (tenant_id, source_id),
            )
            return [(r["key_id"], r["status"]) for r in cur.fetchall()]

    def test_window_inner_stays_retiring_then_converges(self, tenant_id, wx_config):
        """窗口内 retiring 保持（旧新并行可验签）；过窗后 retire_expired_keys 置 retired。"""
        source, rotated = self._rotate(tenant_id)
        now = utcnow()
        # 窗口内（rotate 前 now）：retiring 不被收敛
        wxm_sources.retire_expired_keys(now=now - timedelta(seconds=1))
        statuses = self._key_statuses(tenant_id, source["source"]["id"])
        assert [s for _, s in statuses] == ["retiring", "active"]
        # 过窗（now > retire_at）：收敛 retired；active 不动
        wxm_sources.retire_expired_keys(now=now + timedelta(seconds=901))
        statuses = self._key_statuses(tenant_id, source["source"]["id"])
        assert [s for _, s in statuses] == ["retired", "active"]
        # 幂等重放：本租户状态零变化
        wxm_sources.retire_expired_keys(now=now + timedelta(seconds=901))
        statuses = self._key_statuses(tenant_id, source["source"]["id"])
        assert [s for _, s in statuses] == ["retired", "active"]

    def test_converged_via_event_match_tick(self, tenant_id, wx_config):
        """收敛挂 event_match_tick 清理段：过窗 key 经 tick 一轮置 retired。"""
        source, rotated = self._rotate(tenant_id)
        beyond = utcnow() + timedelta(seconds=901)
        dispatch.event_match_tick(now=beyond, config=wx_config)
        statuses = self._key_statuses(tenant_id, source["source"]["id"])
        assert [s for _, s in statuses] == ["retired", "active"]

    def test_convergence_only_past_window_keys(self, tenant_id, wx_config):
        """清理为库级对账（同 nonce 清理口径）：过窗 retiring 收敛、窗口内 retiring
        与 active 不动——多租户共享库下按 retire_at 精确判定，无误伤。"""
        other = f"wxm_test_{uuid.uuid4().hex[:12]}"
        from src.db.database import get_db_connection

        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                source_in_window = wxm_sources.create_event_source(
                    tenant_id=other, user_id="owner-1",
                    source_ref=f"hook-{uuid.uuid4().hex[:8]}", source_type="webhook",
                )
                wxm_sources.rotate_key(
                    tenant_id=other, source_id=source_in_window["source"]["id"],
                    user_id="owner-1", rotate_window_seconds=3600,
                )
            source_mine, _ = self._rotate(tenant_id)  # 本租户 900s 窗
            wxm_sources.retire_expired_keys(now=utcnow() + timedelta(seconds=1000))
            assert [s for _, s in self._key_statuses(
                tenant_id, source_mine["source"]["id"])] == ["retired", "active"]
            assert [s for _, s in self._key_statuses(
                other, source_in_window["source"]["id"])] == ["retiring", "active"]
        finally:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM weixin_marketing_webhook_nonces WHERE source_id IN ("
                    "SELECT id FROM desktop_automation_event_sources WHERE tenant_id = %s)",
                    (other,),
                )
                cur.execute(
                    "DELETE FROM desktop_automation_event_sources WHERE tenant_id = %s",
                    (other,),
                )
                cur.execute(
                    "DELETE FROM weixin_marketing_event_source_keys WHERE tenant_id = %s",
                    (other,),
                )
                conn.commit()


# ==================== payloads / occurrences 保留期清理 ====================


class TestRetentionCleanup:
    """断言只看本租户行（cleanup 是全局扫描的系统任务，共享 DB 下并行测试包的
    过期行会进同一批——全局计数只作下界断言，照 TestCleanup 既有口径）。"""

    def test_payloads_and_occurrences_rules(self, tenant_id, wx_config):
        """payloads：过期无未 processed 事件引用才删；occurrences：过期且无 runs
        引用才删、他场景不动、未过期不动。"""
        cfg = replace(wx_config, data_retention_days=30)
        old = utcnow() - timedelta(days=31)
        fresh = utcnow()
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            # payloads：a=无引用(删) b=未 processed 事件引用(留) c=processed 引用(删) d=未过期(留)
            h_a = _insert_payload(cur, tenant_id, created_at=old)
            h_b = _insert_payload(cur, tenant_id, created_at=old)
            _insert_event(cur, tenant_id, h_b, state="received", created_at=old)
            h_c = _insert_payload(cur, tenant_id, created_at=old)
            _insert_event(cur, tenant_id, h_c, state="processed", created_at=old)
            _insert_payload(cur, tenant_id, created_at=fresh)
            # occurrences：a=无 runs(删) b=有 runs(留) c=未过期(留) d=他场景(不动)
            _insert_occurrence(cur, tenant_id, created_at=old)
            occ_with_run = _insert_occurrence(cur, tenant_id, created_at=old)
            _insert_run(cur, tenant_id, occ_with_run)
            _insert_occurrence(cur, tenant_id, created_at=fresh)
            _insert_occurrence(
                cur, tenant_id, scenario_key="other.scenario.v1", created_at=old
            )
            conn.commit()

        result = dispatch.retention_cleanup_tick(now=utcnow(), config=cfg)
        assert result["enabled"] is True
        assert result["payloads_deleted"] >= 2
        assert result["occurrences_deleted"] >= 1

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_hash FROM weixin_marketing_event_payloads "
                "WHERE tenant_id = %s",
                (tenant_id,),
            )
            remaining = {r["payload_hash"] for r in cur.fetchall()}
            assert h_b in remaining  # 未 processed 事件引用保留
            assert h_a not in remaining and h_c not in remaining
            cur.execute(
                """
                SELECT COUNT(*) AS c FROM desktop_automation_occurrences
                WHERE tenant_id = %s AND scenario_key = %s
                """,
                (tenant_id, SCENARIO_KEY),
            )
            # 有 runs 的旧 occurrence + 未过期 occurrence 保留
            assert cur.fetchone()["c"] == 2
            cur.execute(
                """
                SELECT COUNT(*) AS c FROM desktop_automation_occurrences
                WHERE tenant_id = %s AND scenario_key = 'other.scenario.v1'
                """,
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 1  # 他场景不动
            conn.commit()
        # 幂等重放：本租户无新删行（残留行恰为上述保留集）
        dispatch.retention_cleanup_tick(now=utcnow(), config=cfg)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM weixin_marketing_event_payloads WHERE tenant_id = %s",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 2  # b + d

    def test_disabled_zero_action(self, tenant_id, wx_config):
        """enabled=false：零删除（含已过期候选）。"""
        disabled = replace(wx_config, enabled=False, data_retention_days=30)
        old = utcnow() - timedelta(days=60)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            _insert_payload(cur, tenant_id, created_at=old)
            _insert_occurrence(cur, tenant_id, created_at=old)
            conn.commit()
        result = dispatch.retention_cleanup_tick(now=utcnow(), config=disabled)
        assert result == {"enabled": False, "payloads_deleted": 0, "occurrences_deleted": 0}
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM weixin_marketing_event_payloads WHERE tenant_id = %s",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 1

    def test_retention_days_configurable(self, tenant_id, wx_config):
        """data_retention_days 可配：60 天窗口下 31 天旧数据保留。"""
        cfg = replace(wx_config, data_retention_days=60)
        old = utcnow() - timedelta(days=31)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            _insert_payload(cur, tenant_id, created_at=old)
            _insert_occurrence(cur, tenant_id, created_at=old)
            conn.commit()
        wxm_retention.cleanup_expired(now=utcnow(), config=cfg)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM weixin_marketing_event_payloads WHERE tenant_id = %s",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 1
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_occurrences "
                "WHERE tenant_id = %s AND scenario_key = %s",
                (tenant_id, SCENARIO_KEY),
            )
            assert cur.fetchone()["c"] == 1


# ==================== P5 复审 P1-1：payload 复用 × 清理交错 ====================


class TestPayloadReuseCleanupInterleave:
    """统一锁协议交错矩阵：任一交错下「新事件可加载 payload 非 None」。

    - A 接纳中（事务持 FOR SHARE）→ 清理 SKIP LOCKED 跳过、不等待、不删；
    - B 接纳先提交 → 清理 NOT EXISTS 看到新事件引用、拦截删除；
    - C 清理先提交 → 接纳侧 INSERT 重插成功（行由接纳事务持有）。
    """

    def _old_payload(self, tenant_id):
        """旧 payload（过期+无事件引用）→ payload_hash"""
        from src.db.database import get_db_connection

        old = utcnow() - timedelta(days=40)
        with get_db_connection() as conn:
            cur = conn.cursor()
            payload_hash = _insert_payload(cur, tenant_id, created_at=old)
            conn.commit()
        return payload_hash

    def _cfg(self, wx_config):
        return replace(wx_config, data_retention_days=30)

    def _accept_in_txn(self, conn, tenant_id, payload_hash, envelope):
        """接纳事务（游标级）：payload 持久化（复用即 FOR SHARE）+ 事件行"""
        cur = conn.cursor()
        wxm_sources.store_event_payload_on(
            cur, tenant_id, str(uuid.uuid4()), payload_hash, envelope
        )
        _insert_event(cur, tenant_id, payload_hash, state="received", created_at=utcnow())
        return cur

    def test_interleave_a_cleanup_skips_shared_locked_row(self, tenant_id, wx_config):
        """交错 A（双线程 barrier）：接纳事务持 FOR SHARE 期间清理并发执行——
        SKIP LOCKED 跳过该行（不阻塞不删除），接纳提交后 payload 仍可加载。"""
        import threading
        import time

        payload_hash = self._old_payload(tenant_id)
        cfg = self._cfg(wx_config)
        barrier = threading.Barrier(2)
        cleanup_done = threading.Event()
        outcomes: dict = {}

        def acceptor():
            from src.db.database import get_db_connection

            try:
                with get_db_connection() as conn:
                    self._accept_in_txn(
                        conn, tenant_id, payload_hash, {"k": "新事件"}
                    )  # 复用旧行 → FOR SHARE 持锁至提交
                    barrier.wait(timeout=10)
                    time.sleep(1.0)  # 持锁窗口：清理在此期间并发执行
                    conn.commit()
                outcomes["accept"] = "ok"
            except Exception as e:  # noqa: BLE001
                outcomes["accept"] = repr(e)

        def cleaner():
            try:
                barrier.wait(timeout=10)
                outcomes["cleanup"] = wxm_retention.cleanup_expired(
                    now=utcnow(), config=cfg
                )
                cleanup_done.set()
            except Exception as e:  # noqa: BLE001
                outcomes["cleanup"] = repr(e)
                cleanup_done.set()

        threads = [threading.Thread(target=acceptor), threading.Thread(target=cleaner)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "交错 A 死锁：线程未在超时内结束"
        assert outcomes.get("accept") == "ok", outcomes
        # 清理在接纳持锁窗口内完成（未阻塞等待——旧实现会阻塞在行锁上）
        assert cleanup_done.wait(timeout=1), "清理被接纳共享锁阻塞（应 SKIP LOCKED 跳过）"
        # 交错安全不变量：新事件可加载 payload（行未被清理删除）
        payload = wxm_sources.load_event_payload(tenant_id, f"wxm-event:{payload_hash}")
        assert payload is not None, "接纳中行被清理删除：条件匹配载荷悬空"

    def test_interleave_b_committed_reference_blocks_cleanup(self, tenant_id, wx_config):
        """交错 B：接纳先提交（新 received 事件引用旧 payload）→ 清理 NOT EXISTS
        拦截，行保留可加载。"""
        from src.db.database import get_db_connection

        payload_hash = self._old_payload(tenant_id)
        with get_db_connection() as conn:
            self._accept_in_txn(conn, tenant_id, payload_hash, {"k": "新事件"})
            conn.commit()
        wxm_retention.cleanup_expired(now=utcnow(), config=self._cfg(wx_config))
        assert wxm_sources.load_event_payload(
            tenant_id, f"wxm-event:{payload_hash}"
        ) is not None

    def test_interleave_c_cleanup_first_reinsert_on_accept(self, tenant_id, wx_config):
        """交错 C：清理先提交（旧行删除）→ 接纳事务重插成功（行由接纳持有）→
        事件提交后可加载。"""
        from src.db.database import get_db_connection

        payload_hash = self._old_payload(tenant_id)
        wxm_retention.cleanup_expired(now=utcnow(), config=self._cfg(wx_config))
        with get_db_connection() as conn:
            self._accept_in_txn(conn, tenant_id, payload_hash, {"k": "新事件"})
            conn.commit()
        assert wxm_sources.load_event_payload(
            tenant_id, f"wxm-event:{payload_hash}"
        ) is not None


# ==================== P5 复审 P1-2：回滚实时门控与在途回执 ====================


def _count_permits(tenant_id: str) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM local_tool_operation_permits WHERE tenant_id = %s",
            (tenant_id,),
        )
        return int(cur.fetchone()["c"])


class TestRollbackRealtimeGate:
    """回滚实时门控（P5 增量复核：真热读实证）+ 在途回执。

    文件级传播：真实复制 configs/config.yaml 为可写副本，进程内连续两次读数之间
    改文件 enabled true→false→true，断言授权门控读数跟随（无需重启）——直接实证
    「免重启」声明（替换原 monkeypatch 接线测试：settings 为 import 快照，
    monkeypatch 模块配置函数无法证明文件传播）。
    """

    _REPO_YAML = Path(__file__).parents[3] / "configs" / "config.yaml"

    @staticmethod
    def _write_gate_yaml(path, *, enabled, tenant_allowlist=None):
        """改写副本 weixin_marketing 门控键；mtime 确定性递增（防同秒同 size 写入
        落入缓存键盲区——ns+1ms 显式推进）"""
        import yaml as _yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
        node = data.setdefault("weixin_marketing", {})
        node["enabled"] = enabled
        if tenant_allowlist is not None:
            node["tenant_allowlist"] = tenant_allowlist
        with open(path, "w", encoding="utf-8") as fh:
            _yaml.safe_dump(data, fh, allow_unicode=True)
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    @pytest.fixture()
    def gate_yaml(self, tmp_path, monkeypatch):
        """可写 yaml 副本 + 门控热读路径指向副本（enabled=true 起步）"""
        copy = tmp_path / "config.yaml"
        shutil.copyfile(self._REPO_YAML, copy)
        self._write_gate_yaml(copy, enabled=True)
        import src.weixin_marketing.config as wxm_config_module

        monkeypatch.setattr(wxm_config_module, "_gate_yaml_path", lambda: copy)
        return copy

    def test_hot_gate_cache_matches_fresh_parse(self, gate_yaml, monkeypatch):
        """mtime 缓存路径与直接重读等价：文件变更后/未变时，热读结果均与
        fresh 解析（load_yaml_config 同链）一致，无旧值残留。"""
        import src.weixin_marketing.config as wxm_config_module

        def fresh() -> tuple:
            parsed = wxm_config_module._parse_gate_from_yaml(gate_yaml)
            return (parsed.enabled, tuple(parsed.tenant_allowlist))

        v1 = wxm_config_module.get_hot_gate_config()
        assert (v1.enabled, tuple(v1.tenant_allowlist)) == fresh()
        assert v1.enabled is True
        # 变更（true→false + allowlist 写入）→ 热读即见新值且等于 fresh 解析
        self._write_gate_yaml(gate_yaml, enabled=False, tenant_allowlist=["tenant_x"])
        v2 = wxm_config_module.get_hot_gate_config()
        assert (v2.enabled, tuple(v2.tenant_allowlist)) == fresh()
        assert v2.enabled is False and v2.tenant_allowlist == ["tenant_x"]
        # 未变 → 命中缓存，结果仍与 fresh 解析一致（无漂移）
        v3 = wxm_config_module.get_hot_gate_config()
        assert (v3.enabled, tuple(v3.tenant_allowlist)) == fresh() == (
            v2.enabled, tuple(v2.tenant_allowlist)
        )
        # 文件不可达 → fail-closed（拒新授权方向）
        monkeypatch.setattr(
            wxm_config_module, "_gate_yaml_path", lambda: gate_yaml.parent / "absent.yaml"
        )
        v4 = wxm_config_module.get_hot_gate_config()
        assert v4.enabled is False

    def _publish(self, service, tenant_id, group_id):
        """发布单内容块自动化，返回 (automation_id, revision_id)"""
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[{"type": "text", "text_content": "门控内容"}],
        )
        return automation_id, revision_id

    def _manual_run_to_running_invocation(self, service, tenant_id, automation_id, revision_id):
        """手动 run → 领取派发首条 → 设备 claim/start；返回 (stepped, token, device_id)。

        可重复调用（每次独立 run/invocation）——门控用例需要「禁用前已派发、禁用
        后才授权」的不同 invocation：同 delivery 重复授权会被许可幂等守卫
        PERMIT_ALREADY_ISSUED 先拦，走不到适配器门控。"""
        from src.desktop_automation import executor as da_executor
        from src.local_tools.security import generate_claim_token, sha256_hex

        manual_run_pending(service, tenant_id, automation_id)
        revision_config = service.load_revision_config(tenant_id, revision_id)
        device_id = str(uuid.uuid4())
        prepared = da_executor.claim_and_prepare_run(
            revision_config=revision_config, device_id=device_id,
            tenant_id=tenant_id, lease_seconds=300,
        )
        assert prepared["prepared"] is True, prepared
        stepped = da_executor.execute_next_delivery(prepared["run"])
        assert stepped is not None
        token = generate_claim_token()
        claimed = repository.claim_next(device_id, tenant_id, sha256_hex(token), 300)
        assert claimed is not None and str(claimed["id"]) == str(stepped["invocation_id"])
        started = repository.mark_started(
            str(stepped["invocation_id"]), tenant_id, sha256_hex(token)
        )
        assert started is not None and started["state"] == "running"
        return stepped, token, device_id

    def _authorize(self, tenant_id, stepped, token, device_id):
        return permits.write_authorize(
            tenant_id=tenant_id, device_id=device_id,
            invocation_id=str(stepped["invocation_id"]),
            claim_token_hash=sha256_hex(token), request_id=stepped["request_id"],
            target_version=stepped["delivery"].get("target_version"),
            payload_hash=stepped["delivery"]["payload_hash"],
        )

    def test_authorize_follows_yaml_file_true_false_true(
        self, service, tenant_id, bindings, wx_config, adapter, gate_yaml
    ):
        """文件级传播：生产路径（adapter 无注入，门控热读 yaml 副本）下改文件
        enabled true→false→true，授权结果跟随、无需重启；禁用期许可零新签发。"""
        _, group_id = bindings
        adapter._config = None  # 生产注册路径：门控走 get_hot_gate_config 热读
        automation_id, revision_id = self._publish(service, tenant_id, group_id)
        # 禁用前派发并启动两个独立 invocation（A 已授权；B 待禁用后授权）
        stepped_a, token_a, device_a = self._manual_run_to_running_invocation(
            service, tenant_id, automation_id, revision_id
        )
        permit = self._authorize(tenant_id, stepped_a, token_a, device_a)  # enabled=true
        assert permit["permit_id"]
        stepped_b, token_b, device_b = self._manual_run_to_running_invocation(
            service, tenant_id, automation_id, revision_id
        )

        # 文件翻 false（进程不重启）→ invocation B 授权被拒、零新许可
        self._write_gate_yaml(gate_yaml, enabled=False)
        with pytest.raises(permits.PermitError) as exc_info:
            self._authorize(tenant_id, stepped_b, token_b, device_b)
        assert exc_info.value.code == "ADAPTER_DENIED"
        assert "weixin_marketing_disabled" in str(exc_info.value)
        assert _count_permits(tenant_id) == 1  # 仅此前 true 期签发的一条

        # 文件翻回 true（进程不重启）→ 恢复签发
        self._write_gate_yaml(gate_yaml, enabled=True)
        again = self._authorize(tenant_id, stepped_b, token_b, device_b)
        assert again["permit_id"]

    def test_authorize_rejects_tenant_outside_hot_allowlist(
        self, service, tenant_id, bindings, wx_config, adapter, gate_yaml
    ):
        """热读 allowlist 门控：enabled=true 但租户不在 tenant_allowlist → 拒新授权
        （回滚粒度：从 allowlist 移除租户即停其新许可，同样免重启）。"""
        _, group_id = bindings
        adapter._config = None
        automation_id, revision_id = self._publish(service, tenant_id, group_id)
        stepped, token, device_id = self._manual_run_to_running_invocation(
            service, tenant_id, automation_id, revision_id
        )
        self._write_gate_yaml(gate_yaml, enabled=True, tenant_allowlist=["tenant_other"])
        with pytest.raises(permits.PermitError) as exc_info:
            self._authorize(tenant_id, stepped, token, device_id)
        assert exc_info.value.code == "ADAPTER_DENIED"
        assert "tenant_not_allowed" in str(exc_info.value)
        assert _count_permits(tenant_id) == 0

    def test_late_applied_receipt_lands_while_disabled(
        self, service, tenant_id, bindings, wx_config, adapter, gate_yaml
    ):
        """enabled=true 期签发许可并派发 → 文件翻 false（免重启）→ 迟到
        applied+verified 回执照常落账（operation-result 接纳不受总门控，§4④）。"""
        _, group_id = bindings
        adapter._config = None
        automation_id, revision_id = self._publish(service, tenant_id, group_id)
        stepped, token, device_id = self._manual_run_to_running_invocation(
            service, tenant_id, automation_id, revision_id
        )
        permit = self._authorize(tenant_id, stepped, token, device_id)
        self._write_gate_yaml(gate_yaml, enabled=False)
        result = apply_operation_result(
            tenant_id=tenant_id, device_id=device_id,
            invocation_id=str(stepped["invocation_id"]),
            claim_token_hash=sha256_hex(token), request_id=stepped["request_id"],
            effect="applied", phase="verified",
            evidence_ref=f"weixin-evidence:{stepped['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["state"] == "succeeded"
        detail = service.get_run_detail(
            tenant_id, str(stepped["delivery"]["run_id"]), "owner-1"
        )
        assert detail["deliveries"][0]["state"] == "succeeded"


# ==================== 磁盘孤儿素材扫描（只读告警）====================


@pytest.fixture(autouse=True)
def orphan_scan_tmp_root(tmp_path, tenant_id):
    """隔离的租户存储根（不触真实 storage/；测后随 tmp_path 回收）"""
    root = tmp_path / "tenants"
    (root / tenant_id / "weixin-marketing").mkdir(parents=True)
    return str(root)


class TestAssetsOrphanScan:
    def _tracked_file(self, root, tenant_id, name):
        path = os.path.join(root, tenant_id, "weixin-marketing", name)
        with open(path, "wb") as fh:
            fh.write(b"png-bytes")
        return path

    def _track_row(self, tenant_id, storage_ref):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO bs_weixin_marketing_assets
                    (id, tenant_id, user_id, storage_ref, sha256, mime, size,
                     width, height, status, retention_until)
                VALUES (%s, %s, 'owner-1', %s, %s, 'image/png', 9, 1, 1, 'active', NULL)
                """,
                (str(uuid.uuid4()), tenant_id, storage_ref, uuid.uuid4().hex),
            )
            conn.commit()

    def test_orphans_reported_not_deleted(self, tenant_id, wx_config, orphan_scan_tmp_root):
        """孤立文件仅进告警清单不删除；登记文件计 tracked。"""
        cfg = replace(wx_config, assets_orphan_scan_enabled=True)
        tracked = self._tracked_file(orphan_scan_tmp_root, tenant_id, "tracked.png")
        self._track_row(tenant_id, tracked)
        orphan = self._tracked_file(orphan_scan_tmp_root, tenant_id, "orphan.png")

        result = dispatch.assets_orphan_scan_tick(
            config=cfg, tenants_root=orphan_scan_tmp_root
        )
        assert result["enabled"] is True and result["scan_enabled"] is True
        assert result["scanned_files"] == 2
        assert result["tracked_files"] == 1
        assert result["orphan_count"] == 1
        assert [os.path.abspath(p) for p in result["orphans"]] == [os.path.abspath(orphan)]
        # 只读：孤儿文件仍在磁盘
        assert os.path.isfile(orphan)

    def test_switch_off_zero_scan(self, tenant_id, wx_config, orphan_scan_tmp_root):
        """assets_orphan_scan_enabled 默认关：零扫描零报告（文件不动）。"""
        orphan = self._tracked_file(orphan_scan_tmp_root, tenant_id, "orphan.png")
        result = dispatch.assets_orphan_scan_tick(
            config=wx_config, tenants_root=orphan_scan_tmp_root
        )
        assert result["scan_enabled"] is False
        assert result["scanned_files"] == 0 and result["orphans"] == []
        assert os.path.isfile(orphan)

    def test_module_disabled_zero_scan(self, tenant_id, wx_config, orphan_scan_tmp_root):
        """enabled=false：即使开关打开也零动作。"""
        cfg = replace(wx_config, enabled=False, assets_orphan_scan_enabled=True)
        result = dispatch.assets_orphan_scan_tick(
            config=cfg, tenants_root=orphan_scan_tmp_root
        )
        assert result["enabled"] is False and result["orphans"] == []

    def test_missing_root_zero_scan(self, tenant_id, wx_config, tmp_path):
        """存储根不存在：零扫描不报错。"""
        cfg = replace(wx_config, assets_orphan_scan_enabled=True)
        result = wxm_assets.scan_asset_orphans(
            config=cfg, tenants_root=str(tmp_path / "nonexistent")
        )
        assert result["scanned_files"] == 0 and result["orphan_count"] == 0
