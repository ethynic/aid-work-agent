"""系统健康监控纯逻辑单测（采集器活性指纹 / 研判阈值离散化与 LLM 解析）。

参考：docs/system/system-health-monitor-design.md
纯函数测试，无数据库 / Redis / LLM 外部依赖。
"""

import pytest

from src.services.system_health.collector import (
    RETRY_SHARE_MIN_SAMPLES,
    STALL_ROUND_THRESHOLD,
    compute_stall,
    is_runner_stalled,
    retried_share_of,
)
from src.services.system_health.assessment import (
    THRESHOLDS,
    _parse_llm_verdict,
    discretize,
    overall_of,
    rule_fallback,
    state_hash,
)


# ==================== collector: 活性指纹 ====================


class TestComputeStall:
    def test_first_round_no_stall(self):
        prev = {}
        result = compute_stall(prev, ["r1:0:queued", "r2:0:running"])
        assert result["stalled_rounds"] == 0
        assert result["active_count"] == 2

    def test_unchanged_fingerprint_increments(self):
        fp = ["r1:0:queued"]
        prev = {"fp": fp, "stalled_rounds": 3}
        result = compute_stall(prev, fp)
        assert result["stalled_rounds"] == 4

    def test_changed_fingerprint_resets(self):
        prev = {"fp": ["r1:0:queued"], "stalled_rounds": 10}
        result = compute_stall(prev, ["r1:0:queued", "r2:0:running"])
        assert result["stalled_rounds"] == 0

    def test_empty_fingerprint_resets(self):
        prev = {"fp": ["r1:0:queued"], "stalled_rounds": 5}
        result = compute_stall(prev, [])
        assert result["stalled_rounds"] == 0
        assert result["active_count"] == 0

    def test_order_insensitive(self):
        prev = {"fp": ["a:0:queued", "b:0:running"]}
        result = compute_stall(prev, ["b:0:running", "a:0:queued"])
        assert result["stalled_rounds"] >= 1


class TestIsRunnerStalled:
    def test_stalled_at_threshold(self):
        stall = {"stalled_rounds": STALL_ROUND_THRESHOLD}
        assert is_runner_stalled(stall, queued_count=1) is True

    def test_no_queued_not_stalled(self):
        stall = {"stalled_rounds": 999}
        assert is_runner_stalled(stall, queued_count=0) is False

    def test_below_threshold_not_stalled(self):
        stall = {"stalled_rounds": STALL_ROUND_THRESHOLD - 1}
        assert is_runner_stalled(stall, queued_count=2) is False


class TestRetriedShareOf:
    def test_small_sample_returns_none(self):
        # 3/18 之类的少重试日占比被小基数放大，不足最小样本时置 None
        assert retried_share_of(RETRY_SHARE_MIN_SAMPLES - 1, 3) is None

    def test_min_sample_boundary_computes(self):
        assert retried_share_of(RETRY_SHARE_MIN_SAMPLES, 1) == round(1 / RETRY_SHARE_MIN_SAMPLES, 4)

    def test_zero_started_returns_none(self):
        assert retried_share_of(0, 0) is None

    def test_large_sample(self):
        assert retried_share_of(100, 25) == 0.25


# ==================== assessment: 阈值离散化 ====================


def _make_snapshot(**overrides):
    """构造一份全部 normal 的快照基线。"""
    snapshot = {
        "collected_at": "2026-11-10T12:00:00",
        "runner": {
            "queued": 0, "running": 1, "finalizing": 0, "waiting": 0,
            "paused": 0, "interrupted": 0,
            "oldest_running_seconds": 60,
            "oldest_queued_seconds": 0,
            "wait_p50_seconds": 5, "wait_p95_seconds": 10,
            "retried_share_today": 0.0,
            "stalled_rounds": 0, "runner_stalled": False,
            "active_alerts": 0,
        },
        "system": {
            "available": True, "cpu_percent": 10.0, "cpu_count": 4,
            "load_1": 0.5, "mem_used_percent": 30.0,
            "disk_used_percent_storage": 40.0, "disk_used_percent_log": 40.0,
        },
        "business": {
            "online_sessions_5m": 5, "requests_today": 100,
            "error_rate_today": 0.0, "duration_p50_ms": 1000,
            "duration_p95_ms": 5000, "errors_last_hour": 0,
        },
        "middleware": {
            "db_select1_ms": 5, "db_connections": 10, "db_max_connections": 100,
            "redis_available": True, "key_pool_active": 1, "key_pool_max": 10,
        },
    }
    for section, patch in overrides.items():
        snapshot[section].update(patch)
    return snapshot


class TestDiscretize:
    def test_all_normal_baseline(self):
        states = discretize(_make_snapshot())
        assert states, "应产出指标状态"
        assert set(states.values()) == {"normal"}

    def test_runner_watch_and_critical_bands(self):
        snap = _make_snapshot(runner={"oldest_running_seconds": 700})
        assert discretize(snap)["runner.oldest_running_seconds"] == "watch"
        snap = _make_snapshot(runner={"oldest_running_seconds": 2000})
        assert discretize(snap)["runner.oldest_running_seconds"] == "critical"

    def test_runner_stalled_boolean(self):
        snap = _make_snapshot(runner={"runner_stalled": True})
        assert discretize(snap)["runner.runner_stalled"] == "critical"

    def test_system_cpu_bands(self):
        snap = _make_snapshot(system={"cpu_percent": 85.0})
        assert discretize(snap)["system.cpu_percent"] == "watch"
        snap = _make_snapshot(system={"cpu_percent": 96.0})
        assert discretize(snap)["system.cpu_percent"] == "critical"

    def test_load_1_scales_with_cpu_count(self):
        # watch = cpu_count = 2，critical = cpu_count*2 = 4
        snap = _make_snapshot(system={"cpu_count": 2, "load_1": 2.5})
        assert discretize(snap)["system.load_1"] == "watch"
        snap = _make_snapshot(system={"cpu_count": 2, "load_1": 4.5})
        assert discretize(snap)["system.load_1"] == "critical"

    def test_missing_metric_skipped(self):
        snap = _make_snapshot(system={"cpu_percent": None})
        states = discretize(snap)
        assert "system.cpu_percent" not in states

    def test_redis_unavailable_critical(self):
        snap = _make_snapshot(middleware={"redis_available": False})
        assert discretize(snap)["middleware.redis_available"] == "critical"


class TestStateHashAndOverall:
    def test_hash_order_insensitive(self):
        a = {"x": "normal", "y": "watch"}
        b = {"y": "watch", "x": "normal"}
        assert state_hash(a) == state_hash(b)

    def test_hash_changes_with_value(self):
        a = {"x": "normal"}
        b = {"x": "critical"}
        assert state_hash(a) != state_hash(b)

    def test_overall_worst_wins(self):
        assert overall_of({"a": "normal", "b": "watch"}) == "watch"
        assert overall_of({"a": "watch", "b": "critical"}) == "critical"
        assert overall_of({"a": "normal"}) == "normal"


class TestRuleFallback:
    def test_builds_verdict_with_items(self):
        snap = _make_snapshot(runner={"queued": 3, "oldest_queued_seconds": 700})
        states = discretize(snap)
        verdict = rule_fallback(states)
        assert verdict["source"] == "rule"
        assert verdict["overall"] in ("normal", "watch", "critical")
        assert verdict["items"]
        assert verdict["summary"]
        assert "suggestion" in verdict


# ==================== assessment: LLM 解析 ====================


class TestParseLlmVerdict:
    def test_plain_json(self):
        content = '{"overall": "watch", "summary": "队列有积压", "items": [{"group": "runner", "status": "watch", "note": "排队3"}], "suggestion": "关注"}'
        verdict = _parse_llm_verdict(content)
        assert verdict is not None
        assert verdict["overall"] == "watch"
        assert verdict["source"] == "llm"

    def test_fenced_json(self):
        content = '```json\n{"overall": "normal", "summary": "一切正常", "items": [], "suggestion": "无"}\n```'
        verdict = _parse_llm_verdict(content)
        assert verdict is not None
        assert verdict["overall"] == "normal"

    def test_invalid_overall_rejected(self):
        assert _parse_llm_verdict('{"overall": "bad", "summary": "x"}') is None

    def test_malformed_json_rejected(self):
        assert _parse_llm_verdict('not json at all') is None

    def test_missing_summary_rejected(self):
        assert _parse_llm_verdict('{"overall": "normal"}') is None


# ==================== 阈值表完整性 ====================


class TestThresholds:
    def test_threshold_entries_have_watch_and_critical(self):
        for key, cfg in THRESHOLDS.items():
            assert "watch" in cfg and "critical" in cfg, f"{key} 缺少阈值档位"
