"""observability 单元测试（纯逻辑，无 IO）

覆盖 build_metrics（指标组装 + 比率 + 零分母安全）与 evaluate_alerts（4 条规则边界）。
observability.py 不 import db，故无需 mock 数据库——直接喂入合成数据验证意图。
"""
from datetime import datetime, timedelta

from src.channels.wecom_personal_rpa.observability import (
    CLIENT_ONLINE_THRESHOLD_SECONDS,
    build_metrics,
    evaluate_alerts,
)

NOW = datetime(2026, 6, 23, 12, 0, 0)


def _online(seconds_ago: int):
    return NOW - timedelta(seconds=seconds_ago)


# ============================ build_metrics ============================


def test_build_metrics_rates_exclude_disabled_and_never_seen():
    """意图：客户端在线率分母只算 active；disabled 不计；stale/从未上报不算在线。"""
    client_liveness = [
        {"id": "c1", "name": "C1", "status": "active", "last_seen_at": _online(60)},   # 在线
        {"id": "c2", "name": "C2", "status": "active", "last_seen_at": _online(3600)},  # 心跳过期
        {"id": "c3", "name": "C3", "status": "disabled", "last_seen_at": NOW},          # disabled 不计入
        {"id": "c4", "name": "C4", "status": "active", "last_seen_at": None},           # 从未上报
    ]
    m = build_metrics(
        client_liveness=client_liveness,
        account_states=[],
        audit_counts={},
        outcome_counts={},
        binding_counts={},
        window_hours=24,
        now=NOW,
    )
    # active = c1/c2/c4 = 3；在线仅 c1
    assert m["clients"]["total"] == 3
    assert m["clients"]["online"] == 1
    assert m["clients"]["online_rate"] == round(1 / 3, 4)


def test_build_metrics_accounts_by_status_and_action_success_rate():
    """意图：账号状态分布；action 成功率 = succeeded/(succeeded+failed)。"""
    account_states = [
        {"id": "a1", "client_id": "c1", "display_name": "A1", "status": "online"},
        {"id": "a2", "client_id": "c1", "display_name": "A2", "status": "online"},
        {"id": "a3", "client_id": "c1", "display_name": "A3", "status": "offline"},
        {"id": "a4", "client_id": "c1", "display_name": "A4", "status": "paused"},
    ]
    m = build_metrics(
        client_liveness=[],
        account_states=account_states,
        audit_counts={"inbound_message": 10, "agent_reply": 8, "pause_resume": 2},
        outcome_counts={"succeeded": 18, "failed": 2, "retryable": 1, "running": 3},
        binding_counts={"active": 5, "needs_review": 3},
        window_hours=24,
        now=NOW,
    )
    assert m["accounts"]["total"] == 4
    assert m["accounts"]["online"] == 2
    assert m["accounts"]["online_rate"] == 0.5
    assert m["accounts"]["by_status"]["online"] == 2
    assert m["accounts"]["by_status"]["paused"] == 1
    # 18 / (18+2) = 0.9
    assert m["actions"]["success_rate"] == 0.9
    assert m["actions"]["succeeded"] == 18
    assert m["actions"]["by_status"]["running"] == 3
    assert m["audit_counts"]["inbound_message"] == 10
    assert m["bindings"]["needs_review"] == 3


def test_build_metrics_zero_denominator_safe():
    """意图：无客户端/账号/动作时比率返回 0.0，不抛零除。"""
    m = build_metrics(
        client_liveness=[],
        account_states=[],
        audit_counts={},
        outcome_counts={},
        binding_counts={},
        window_hours=1,
        now=NOW,
    )
    assert m["clients"]["online_rate"] == 0.0
    assert m["accounts"]["online_rate"] == 0.0
    assert m["actions"]["success_rate"] == 0.0


# ============================ evaluate_alerts ============================


def test_client_offline_alerts_stale_and_never_seen_skips_disabled():
    """意图：心跳过期或从未上报的 active 客户端告警；在线/disabled 不告警。"""
    alerts = evaluate_alerts(
        client_liveness=[
            {"id": "c1", "name": "C1", "status": "active", "last_seen_at": _online(3600)},  # 过期
            {"id": "c2", "name": "C2", "status": "active", "last_seen_at": None},            # 从未
            {"id": "c3", "name": "C3", "status": "active", "last_seen_at": NOW},             # 在线
            {"id": "c4", "name": "C4", "status": "disabled", "last_seen_at": None},          # disabled 不告警
        ],
        account_states=[],
        recent_outcomes=[],
        binding_counts={},
        now=NOW,
    )
    offline = [a for a in alerts if a["rule"] == "client_offline"]
    assert sorted(a["entity_id"] for a in offline) == ["c1", "c2"]
    assert all(a["severity"] == "danger" for a in offline)


def test_account_not_logged_in_only_when_owning_client_live():
    """意图：账号 offline/need_login 仅当归属客户端在线才告警（避免整租户未启用误报）。"""
    alerts = evaluate_alerts(
        client_liveness=[
            {"id": "c1", "name": "C1", "status": "active", "last_seen_at": NOW},              # 在线
            {"id": "c2", "name": "C2", "status": "active", "last_seen_at": _online(3600)},     # 离线
        ],
        account_states=[
            {"id": "a1", "client_id": "c1", "display_name": "A1", "status": "offline"},     # c1 在线 -> 告警
            {"id": "a2", "client_id": "c2", "display_name": "A2", "status": "offline"},     # c2 离线 -> 不告警
            {"id": "a3", "client_id": "c1", "display_name": "A3", "status": "online"},      # 已在线 -> 不告警
            {"id": "a4", "client_id": "c1", "display_name": "A4", "status": "need_login"},  # c1 在线 -> 告警
        ],
        recent_outcomes=[],
        binding_counts={},
        now=NOW,
    )
    anli = [a for a in alerts if a["rule"] == "account_not_logged_in"]
    assert sorted(a["entity_id"] for a in anli) == ["a1", "a4"]


def test_consecutive_failures_threshold_and_streak_break():
    """意图：末尾连续 failed≥3 才告警；中间 succeeded 会打断计数。

    recent_outcomes 按约定为 created_at DESC（最新在前）。
    """
    # DESC：每行越靠前越新
    recent = [
        # x：failed,failed,failed（streak=3 -> 告警）
        {"account_id": "x", "status": "failed", "created_at": NOW - timedelta(seconds=30)},
        {"account_id": "x", "status": "failed", "created_at": NOW - timedelta(seconds=60)},
        {"account_id": "x", "status": "failed", "created_at": NOW - timedelta(seconds=90)},
        # y：failed,failed（streak=2 -> 不达阈值）
        {"account_id": "y", "status": "failed", "created_at": NOW - timedelta(seconds=30)},
        {"account_id": "y", "status": "failed", "created_at": NOW - timedelta(seconds=60)},
        # z：failed(新),succeeded,failed（streak=1 -> 不告警）
        {"account_id": "z", "status": "failed", "created_at": NOW - timedelta(seconds=30)},
        {"account_id": "z", "status": "succeeded", "created_at": NOW - timedelta(seconds=60)},
        {"account_id": "z", "status": "failed", "created_at": NOW - timedelta(seconds=90)},
    ]
    alerts = evaluate_alerts(
        client_liveness=[],
        account_states=[
            {"id": "x", "client_id": "c1", "display_name": "X", "status": "online"},
            {"id": "y", "client_id": "c1", "display_name": "Y", "status": "online"},
            {"id": "z", "client_id": "c1", "display_name": "Z", "status": "online"},
        ],
        recent_outcomes=recent,
        binding_counts={},
        now=NOW,
    )
    cf = [a for a in alerts if a["rule"] == "consecutive_action_failures"]
    assert [a["entity_id"] for a in cf] == ["x"]
    assert cf[0]["severity"] == "danger"


def test_consecutive_failures_custom_threshold():
    """意图：failure_threshold 可调。"""
    recent = [{"account_id": "a", "status": "failed", "created_at": NOW}] * 2  # streak=2
    alerts = evaluate_alerts(
        client_liveness=[],
        account_states=[],
        recent_outcomes=recent,
        binding_counts={},
        now=NOW,
        failure_threshold=2,
    )
    assert any(a["rule"] == "consecutive_action_failures" for a in alerts)


def test_needs_review_backlog_alert_and_silence():
    """意图：存在 needs_review 绑定告警(info)；为 0 不告警。"""
    with_backlog = evaluate_alerts(
        client_liveness=[], account_states=[], recent_outcomes=[],
        binding_counts={"needs_review": 5}, now=NOW,
    )
    assert any(a["rule"] == "needs_review_backlog" and a["severity"] == "info" for a in with_backlog)

    without = evaluate_alerts(
        client_liveness=[], account_states=[], recent_outcomes=[],
        binding_counts={"needs_review": 0}, now=NOW,
    )
    assert not any(a["rule"] == "needs_review_backlog" for a in without)


def test_online_threshold_boundary():
    """意图：last_seen 恰好在阈值内算在线，超出算离线（边界值）。"""
    boundary_online = NOW - timedelta(seconds=CLIENT_ONLINE_THRESHOLD_SECONDS)
    boundary_offline = NOW - timedelta(seconds=CLIENT_ONLINE_THRESHOLD_SECONDS + 1)
    alerts = evaluate_alerts(
        client_liveness=[
            {"id": "c1", "name": "C1", "status": "active", "last_seen_at": boundary_online},   # 在线
            {"id": "c2", "name": "C2", "status": "active", "last_seen_at": boundary_offline},  # 离线
        ],
        account_states=[], recent_outcomes=[], binding_counts={}, now=NOW,
    )
    offline_ids = [a["entity_id"] for a in alerts if a["rule"] == "client_offline"]
    assert offline_ids == ["c2"]
