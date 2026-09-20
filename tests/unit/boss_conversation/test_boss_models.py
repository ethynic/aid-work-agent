"""BossTaskSpecPayload / spec 校验（设计 §5.4 冻结；B1.3 占位替换后的定向测试）。"""

import pytest
from pydantic import ValidationError

from src.boss_conversation.models import (
    BossScriptRef,
    BossTaskSpecPayload,
    template_content_hash,
    validate_boss_task_spec,
)


def _script(version_id=None, template="您好，方便沟通吗？", slot_schema=None, content_hash=None):
    return {
        "script_version_id": version_id or "11111111-1111-1111-1111-111111111111",
        "content_hash": content_hash or template_content_hash(template),
        "frozen_template": template,
        "slot_schema": slot_schema or {},
    }


def _base_spec(**overrides):
    spec = {
        "goal": "确认意向",
        "completion_rule": {"mode": "rounds", "rounds_target": 2},
        "reply_policy": {"style": "简洁礼貌", "allowed_facts": [], "forbidden_commitments": []},
        "limits": {
            "max_replies": 10, "max_decisions": 20, "max_cost_units": 100,
            "expires_at": "2099-01-01T00:00:00Z", "peer_wait_timeout_seconds": 86400,
        },
        "scripts": [_script()],
        "slot_evidence_sources": {},
    }
    spec.update(overrides)
    return spec


class TestBossTaskSpecPayload:
    def test_valid_spec_round_trips_to_plain_dict(self):
        plain = validate_boss_task_spec(_base_spec())
        assert plain["scripts"][0]["script_version_id"]
        assert plain["completion_rule"] == {"mode": "rounds", "rounds_target": 2}

    def test_opening_text_forbidden(self):
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(opening_text="您好"))

    def test_completion_rule_only_rounds(self):
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(completion_rule={"mode": "judged", "criteria": ["a"]}))
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(completion_rule={"mode": "peer_confirmed", "fields": []}))

    def test_scripts_bounds(self):
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(scripts=[]))
        # 11 条 → 超 1..10 上限
        many = [_script(version_id=f"22222222-2222-2222-2222-{i:012d}") for i in range(11)]
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(scripts=many))

    def test_total_template_length_cap(self):
        long_template = "很" * 1500
        scripts = [_script(version_id=f"33333333-3333-3333-3333-{i:012d}", template=long_template) for i in range(7)]
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(scripts=scripts))

    def test_unknown_placeholder_rejected_at_publish(self):
        # 发布校验必须拒绝模板中的未知占位符（设计 §5.4）
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(scripts=[_script(template="您好 {ghost}")]))

    def test_slot_evidence_sources_key_set_exact_match(self):
        script = _script(
            template="您好 {expected_time}",
            slot_schema={"expected_time": {"required": True, "description": "时段"}},
        )
        spec = _base_spec(scripts=[script], slot_evidence_sources={})
        with pytest.raises(ValidationError):
            validate_boss_task_spec(spec)
        spec = _base_spec(
            scripts=[script],
            slot_evidence_sources={"expected_time": {"source": "peer_message"}, "ghost": {"source": "peer_message"}},
        )
        with pytest.raises(ValidationError):
            validate_boss_task_spec(spec)
        ok = _base_spec(
            scripts=[script],
            slot_evidence_sources={"expected_time": {"source": "peer_message"}},
        )
        assert validate_boss_task_spec(ok)["slot_evidence_sources"]["expected_time"]["source"] == "peer_message"

    def test_resume_field_whitelist_enforced(self, monkeypatch):
        import src.boss_conversation.config as boss_config

        monkeypatch.setattr(boss_config, "resume_field_whitelist", lambda *a, **k: ("current_company",))
        script = _script(
            template="我在{company}",
            slot_schema={"company": {"required": True}},
        )
        ok = _base_spec(
            scripts=[script],
            slot_evidence_sources={"company": {"source": "resume_field", "field": "current_company"}},
        )
        assert validate_boss_task_spec(ok)
        bad = _base_spec(
            scripts=[script],
            slot_evidence_sources={"company": {"source": "resume_field", "field": "hacker_field"}},
        )
        with pytest.raises(ValidationError):
            validate_boss_task_spec(bad)

    def test_peer_slot_must_not_carry_field(self):
        from src.boss_conversation.models import BossSlotSource

        with pytest.raises(ValidationError):
            BossSlotSource.model_validate({"source": "peer_message", "field": "x"})
        with pytest.raises(ValidationError):
            BossSlotSource.model_validate({"source": "resume_field"})
        ok = BossSlotSource.model_validate({"source": "resume_field", "field": "current_company"})
        assert ok.field == "current_company"

    def test_duplicate_script_version_rejected(self):
        dup = _script()
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(scripts=[dup, dict(dup)]))

    def test_rounds_target_budget(self):
        spec = _base_spec(completion_rule={"mode": "rounds", "rounds_target": 11})
        with pytest.raises(ValidationError):
            validate_boss_task_spec(spec)

    def test_content_hash_normalization(self):
        # content_hash 是模板规范化字节 sha256（\r\n→\n 等价归一）；发布层校验
        # content_hash 与 frozen_template 规范化哈希一致（CR 补强：防坏 spec 带病
        # 发布后运行期才以 render_invariant_violation 转人工）
        template = "您好，\r\n方便沟通吗？"
        assert template_content_hash(template) == template_content_hash("您好，\n方便沟通吗？")
        ref = BossScriptRef.model_validate(
            _script(template=template, content_hash=template_content_hash(template))
        )
        assert ref.frozen_template == template

    def test_content_hash_template_mismatch_rejected_at_publish(self):
        # CR：content_hash 与 frozen_template 规范化哈希不一致 → 发布即拒
        with pytest.raises(ValidationError):
            validate_boss_task_spec(_base_spec(scripts=[_script(content_hash="deadbeef")]))
