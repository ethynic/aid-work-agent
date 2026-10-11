"""防滥用引擎单元测试：绑定匹配优先级 / 参数校验 / 事件 1h 去重 / 预算聚合 / 扫描闭环。"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.config.settings import settings
from src.services.anti_abuse import rules
from src.services.anti_abuse.rules import validate_policy_params
from src.services.anti_abuse.service import AntiAbuseService
from src.services.anti_abuse import scanner as abuse_scanner
from src.services.anti_abuse.scanner import run_abuse_scan_once


# ============== 参数校验 ==============

class TestValidatePolicyParams:
    def test_valid_params(self):
        ok, err = validate_policy_params(
            "credit_budget",
            {"period": "day", "warn_threshold": 400, "hard_threshold": 800},
        )
        assert ok and err == ""

    def test_unknown_rule(self):
        ok, err = validate_policy_params("no_such_rule", {})
        assert not ok and "未知规则" in err

    def test_missing_required(self):
        ok, err = validate_policy_params("credit_budget", {"period": "day"})
        assert not ok and "hard_threshold" in err

    def test_invalid_enum(self):
        ok, err = validate_policy_params("credit_budget", {"period": "week", "hard_threshold": 10})
        assert not ok and "period" in err

    def test_non_number(self):
        ok, err = validate_policy_params(
            "credit_budget", {"period": "day", "hard_threshold": "abc"}
        )
        assert not ok and "必须为数字" in err

    def test_bool_is_not_number(self):
        ok, _ = validate_policy_params(
            "credit_budget", {"period": "day", "hard_threshold": True}
        )
        assert not ok

    def test_below_min(self):
        ok, _ = validate_policy_params(
            "credit_budget", {"period": "day", "hard_threshold": -1}
        )
        assert not ok

    def test_warn_greater_than_hard(self):
        ok, err = validate_policy_params(
            "credit_budget",
            {"period": "day", "warn_threshold": 900, "hard_threshold": 800},
        )
        assert not ok and "告警线" in err

    def test_extra_param_rejected(self):
        ok, _ = validate_policy_params("credit_budget", {"foo": 1, "hard_threshold": 10})
        assert not ok


# ============== 绑定匹配优先级 ==============

class TestResolveBinding:
    @pytest.fixture
    def rows(self, monkeypatch):
        store = {}
        monkeypatch.setattr(
            AntiAbuseService, "load_policy_row",
            staticmethod(lambda tid, st, sid: store.get((tid, st, sid))),
        )
        monkeypatch.setattr(
            AntiAbuseService, "load_channel_policy_row",
            staticmethod(lambda tid, ctype: store.get((tid, "channel", f"cfg:{ctype}"))),
        )
        return store

    def _row(self, tenant_id, scope_type, scope_id, mode="observe"):
        return {"tenant_id": tenant_id, "scope_type": scope_type, "scope_id": scope_id,
                "mode": mode, "policies": [{"rule_id": "credit_budget", "params": {}}]}

    def test_internal_user_uses_user_binding(self, rows):
        rows[("t1", "user", "u1")] = self._row("t1", "user", "u1")
        b = AntiAbuseService.resolve_binding("t1", "u1", source=None)
        assert b["scope_type"] == "user" and b["scope_id"] == "u1"

    def test_internal_user_falls_back_to_tenant_row(self, rows):
        rows[("t1", "tenant", "t1")] = self._row("t1", "tenant", "t1")
        b = AntiAbuseService.resolve_binding("t1", "u1", source=None)
        assert b["scope_type"] == "tenant" and not b.get("is_platform_default")

    def test_channel_user_uses_channel_binding_not_user(self, rows):
        # 渠道用户即使存在 user 绑定也不查（不并集不合并）
        rows[("t1", "user", "u1")] = self._row("t1", "user", "u1")
        rows[("t1", "channel", "cfg:wecom_kf")] = self._row("t1", "channel", "cfg:wecom_kf")
        b = AntiAbuseService.resolve_binding("t1", "u1", source="wecom_kf")
        assert b["scope_type"] == "channel" and b["scope_id"] == "cfg:wecom_kf"

    def test_channel_user_falls_back_to_tenant_row(self, rows):
        rows[("t1", "tenant", "t1")] = self._row("t1", "tenant", "t1")
        b = AntiAbuseService.resolve_binding("t1", "u1", source="wecom")
        assert b["scope_type"] == "tenant"

    def test_no_rows_returns_platform_default(self, rows):
        default = settings.anti_abuse.tenant_default
        b = AntiAbuseService.resolve_binding("t1", "u1", source=None)
        assert b["is_platform_default"] is True
        assert b["mode"] == default.mode
        assert b["policies"] == default.policies


# ============== 预算评估 / 事件落库 ==============

class TestEvaluateCreditBudget:
    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            AntiAbuseService.evaluate_credit_budget("t1", "u1", "week")

    def test_hour_interval_in_sql(self, monkeypatch):
        captured = {}

        @contextmanager
        def fake_conn():
            yield captured["cursor"]

        cur = MagicMock()
        cur.fetchone.return_value = {"total": 12.5}
        captured["cursor"] = cur
        monkeypatch.setattr(
            "src.services.anti_abuse.service.get_db_connection", fake_conn
        )
        total = AntiAbuseService.evaluate_credit_budget("t1", "u1", "hour")
        assert total == 12.5
        sql, params = cur.execute.call_args[0]
        assert "interval '1 hour'" in sql
        assert list(rules.SESSION_SOURCE_TYPES) == params[2]

    def test_day_interval_in_sql(self, monkeypatch):
        @contextmanager
        def fake_conn():
            cur = MagicMock()
            cur.fetchone.return_value = {"total": 0}
            yield cur

        monkeypatch.setattr(
            "src.services.anti_abuse.service.get_db_connection", fake_conn
        )
        assert AntiAbuseService.evaluate_credit_budget("t1", "u1", "day") == 0.0


class TestRecordEvent:
    def _cursor(self, returning_row):
        cur = MagicMock()
        cur.fetchone.return_value = returning_row
        return cur

    def test_insert_returns_id(self):
        cur = self._cursor({"id": 7})
        event_id = AntiAbuseService.record_event(
            cur, tenant_id="t1", scope_type="user", scope_id="u1",
            rule_id="credit_budget", action="warned", user_id="u1",
            channel_type="wecom_kf", detail={"level": "warn"},
        )
        assert event_id == 7
        sql, params = cur.execute.call_args[0]
        assert "WHERE NOT EXISTS" in sql and "RETURNING id" in sql
        # 去重子查询参数在尾部：(tenant_id, rule_id, action, user_id)
        assert params[-4:] == ("t1", "credit_budget", "warned", "u1")
        # detail 走 jsonb 文本
        assert params[8] == '{"level": "warn"}'

    def test_duplicate_returns_none(self):
        cur = self._cursor(None)
        event_id = AntiAbuseService.record_event(
            cur, tenant_id="t1", scope_type="user", scope_id="u1",
            rule_id="credit_budget", action="warned", user_id="u1",
        )
        assert event_id is None

    def test_user_id_none_dedup_safe(self):
        # user_id IS NOT DISTINCT FROM 保证 NULL 键也能去重
        cur = self._cursor({"id": 1})
        AntiAbuseService.record_event(
            cur, tenant_id="t1", scope_type="tenant", scope_id="t1",
            rule_id="credit_budget", action="warned", user_id=None,
        )
        sql = cur.execute.call_args[0][0]
        assert "user_id IS NOT DISTINCT FROM" in sql


# ============== 扫描闭环 ==============

NOW = datetime(2026, 10, 11, 12, 0, 0)


def make_repo(enabled=True, tenant_events=True):
    from src.config.settings import NotificationsConfig
    repo = MagicMock()
    repo.config = NotificationsConfig(enabled=enabled, tenant_events_enabled=tenant_events)
    return repo


def make_cursor(candidates, users, admins):
    cur = MagicMock()
    state = {"admins": admins}

    def execute(sql, params=None):
        if "GROUP BY tenant_id, user_id" in sql:
            cur.fetchall = lambda: candidates
        elif "user_id = ANY" in sql:
            cur.fetchall = lambda: users
        elif "role = 'tenant_admin'" in sql:
            cur.fetchall = lambda: state["admins"]
        elif "clock_timestamp" in sql:
            cur.fetchone = lambda: {"now": NOW}

    cur.execute.side_effect = execute
    return cur


@pytest.fixture
def scanner_env(monkeypatch):
    """monkeypatch 扫描器的连接 / 服务 / 通知边界，返回（收集器, cursor 容器）"""
    published = []
    container = {"cursor": None, "binding": {
        "scope_type": "user", "scope_id": "u1", "mode": "observe",
        "policies": [{"rule_id": "credit_budget",
                      "params": {"period": "day", "warn_threshold": 400, "hard_threshold": 800}}],
    }, "credits": 850.0, "event_id": 42}

    fake_svc = MagicMock()
    fake_svc.resolve_binding.side_effect = lambda *a, **kw: container["binding"]
    fake_svc.evaluate_credit_budget.side_effect = (
        lambda *a, **kw: container["credits"])
    fake_svc.record_event.side_effect = lambda cursor, **kw: container["event_id"]

    def fake_publish(cursor, **kw):
        published.append(kw)
        return f"ref-{len(published)}"

    monkeypatch.setattr(abuse_scanner, "AntiAbuseService", fake_svc)
    monkeypatch.setattr(abuse_scanner, "publish_in_tx", fake_publish)

    @contextmanager
    def fake_conn():
        yield container["cursor"]

    monkeypatch.setattr(abuse_scanner, "get_db_connection", fake_conn)
    monkeypatch.setattr(abuse_scanner, "notification_repository", lambda: make_repo())
    return container, published, fake_svc


def _candidate(tenant_id="t1", user_id="u1", total=100.0):
    return {"tenant_id": tenant_id, "user_id": user_id, "total": total}


def _user_row(user_id="u1", status="active", source=None):
    return {"user_id": user_id, "username": "usera", "nickname": "小明",
            "phone": "13800000000", "source": source, "status": status}


class TestRunAbuseScanOnce:
    def test_disabled_returns_zero_no_connection(self, monkeypatch, scanner_env):
        monkeypatch.setattr(settings.anti_abuse, "scanner_enabled", False)
        stats = run_abuse_scan_once()
        assert stats == {"scanned": 0, "events": 0, "notified": 0}

    def test_hit_hard_records_event_and_notifies(self, monkeypatch, scanner_env):
        container, published, fake_svc = scanner_env
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row()], [{"user_id": "admin1"}])
        monkeypatch.setattr(settings.anti_abuse, "scanner_enabled", True)
        stats = run_abuse_scan_once()
        assert stats["scanned"] == 1 and stats["events"] == 1 and stats["notified"] == 1
        event = published[0]
        assert event["event_type"] == "abuse.rule_triggered"
        assert event["event_key"] == "abuse:t1:u1:credit_budget"
        assert event["resource_ref"] == {"type": "abuse_event", "id": "42"}
        assert event["policy_key"] == "tenant_admin_event"
        assert event["category"] == "abuse"
        assert event["producer"] == abuse_scanner.PRODUCER
        assert event["expires_at"] == NOW + timedelta(seconds=86400)
        params = event["parameters"]
        assert params["window_credits"] == 850.0
        assert params["threshold_credits"] == 800.0  # 硬限命中取硬限阈值
        assert params["observe_mode"] is True
        assert params["subject_label"] == "小明"
        # 事件详情标注 level=hard
        detail = fake_svc.record_event.call_args.kwargs["detail"]
        assert detail["level"] == "hard" and detail["mode"] == "observe"
        # 事件与通知同事务提交
        container["cursor"].commit.assert_called()

    def test_hit_warn_only_uses_warn_threshold(self, scanner_env):
        container, published, _ = scanner_env
        container["credits"] = 450.0
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row()], [{"user_id": "admin1"}])
        run_abuse_scan_once()
        assert published[0]["parameters"]["threshold_credits"] == 400.0

    def test_below_threshold_no_event(self, scanner_env):
        container, published, fake_svc = scanner_env
        container["credits"] = 100.0
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row()], [{"user_id": "admin1"}])
        stats = run_abuse_scan_once()
        assert stats["events"] == 0 and published == []
        fake_svc.record_event.assert_not_called()

    def test_duplicate_event_skips_notify(self, scanner_env):
        container, published, fake_svc = scanner_env
        container["event_id"] = None  # 1h 内已有同键，record_event 返回 None
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row()], [{"user_id": "admin1"}])
        stats = run_abuse_scan_once()
        assert stats["events"] == 0 and stats["notified"] == 0 and published == []

    def test_suspended_user_skipped(self, scanner_env):
        container, published, fake_svc = scanner_env
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row(status="suspended")], [{"user_id": "admin1"}])
        stats = run_abuse_scan_once()
        assert stats["scanned"] == 0
        fake_svc.resolve_binding.assert_not_called()

    def test_unmapped_user_skipped(self, scanner_env):
        container, published, fake_svc = scanner_env
        container["cursor"] = make_cursor(
            [_candidate()], [], [{"user_id": "admin1"}])
        stats = run_abuse_scan_once()
        assert stats["scanned"] == 0
        fake_svc.resolve_binding.assert_not_called()

    def test_no_credit_budget_rule_skipped(self, scanner_env):
        container, published, _ = scanner_env
        container["binding"] = {"scope_type": "user", "scope_id": "u1",
                                "mode": "observe", "policies": []}
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row()], [{"user_id": "admin1"}])
        stats = run_abuse_scan_once()
        assert stats["scanned"] == 0 and published == []

    def test_notify_disabled_still_records_event(self, monkeypatch, scanner_env):
        container, published, _ = scanner_env
        monkeypatch.setattr(
            abuse_scanner, "notification_repository",
            lambda: make_repo(enabled=False),
        )
        container["cursor"] = make_cursor(
            [_candidate()], [_user_row()], [{"user_id": "admin1"}])
        stats = run_abuse_scan_once()
        assert stats["events"] == 1 and stats["notified"] == 0 and published == []

    def test_tenant_isolation_of_admins(self, scanner_env):
        # 两个租户各取各的管理员，通知不串租户
        container, published, _ = scanner_env
        container["cursor"] = make_cursor(
            [_candidate("t1", "u1"), _candidate("t2", "u2")],
            [_user_row("u1"), _user_row("u2")],
            [{"user_id": "admin1"}])
        run_abuse_scan_once()
        keys = {p["event_key"] for p in published}
        assert keys == {"abuse:t1:u1:credit_budget", "abuse:t2:u2:credit_budget"}
