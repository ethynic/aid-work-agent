"""completion_rule 三互斥 schema 与发布校验（C1 计划 §4 冻结补充；纯内存无 DB）。"""

import pytest
from pydantic import ValidationError

from src.session_tasks.models import validate_task_spec

from tests.unit.session_tasks.conftest import build_spec


def _payload(spec: dict) -> dict:
    return {"spec": spec}


class TestCompletionRuleSchemas:
    def test_rounds_valid(self):
        validated = validate_task_spec(build_spec("rounds"))
        assert validated.completion_rule.mode == "rounds"
        assert validated.completion_rule.rounds_target == 5

    def test_rounds_target_must_be_positive(self):
        spec = build_spec("rounds")
        spec["completion_rule"]["rounds_target"] = 0
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_rounds_plus_opening_exceeds_max_replies_rejected(self):
        spec = build_spec("rounds", opening=True)
        spec["completion_rule"]["rounds_target"] = 10  # 10 + 开场白 1 > max_replies 10
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_rounds_plus_opening_within_limit_accepted(self):
        spec = build_spec("rounds", opening=True)
        spec["completion_rule"]["rounds_target"] = 9
        assert validate_task_spec(spec).completion_rule.rounds_target == 9

    def test_peer_confirmed_valid(self):
        validated = validate_task_spec(build_spec("peer_confirmed"))
        rule = validated.completion_rule
        assert rule.require_all is True
        assert rule.fields[0].key == "willing"

    def test_peer_confirmed_accepted_not_subset_rejected(self):
        spec = build_spec("peer_confirmed")
        spec["completion_rule"]["fields"][0]["accepted_values"] = ["maybe"]
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_peer_confirmed_bad_key_format_rejected(self):
        spec = build_spec("peer_confirmed")
        spec["completion_rule"]["fields"][0]["key"] = "Bad-Key"
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_peer_confirmed_require_all_false_rejected(self):
        spec = build_spec("peer_confirmed")
        spec["completion_rule"]["require_all"] = False
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_judged_duplicate_criteria_rejected(self):
        spec = build_spec("judged")
        spec["completion_rule"]["criteria"] = ["同一条件", "同一条件"]
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_cross_mode_field_mixing_rejected(self):
        spec = build_spec("rounds")
        spec["completion_rule"]["criteria"] = ["对方同意"]  # rounds 不接受 judged 字段
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_unknown_mode_rejected(self):
        spec = build_spec()
        spec["completion_rule"] = {"mode": "oracle"}
        with pytest.raises(ValidationError):
            validate_task_spec(spec)


class TestLimitsAndExtras:
    def test_missing_limit_rejected(self):
        spec = build_spec()
        del spec["limits"]["max_cost_units"]
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_expires_at_past_rejected(self):
        spec = build_spec()
        spec["limits"]["expires_at"] = "2020-01-01T00:00:00Z"
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_naive_expires_at_rejected(self):
        spec = build_spec()
        spec["limits"]["expires_at"] = "2099-01-01T00:00:00"
        with pytest.raises(ValidationError):
            validate_task_spec(spec)

    def test_extra_spec_field_rejected(self):
        spec = build_spec()
        spec["tool_whitelist"] = ["rm -rf"]
        with pytest.raises(ValidationError):
            validate_task_spec(spec)
