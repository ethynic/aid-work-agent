"""受限决策钩子与确定性渲染三级分流定向测试（设计 §5.4 冻结）。

- 渲染成功 → 归一化 reply（冻结正文逐字替换）；
- 必需 slot/证据缺失 → handoff(missing_evidence)（第一级，正常转人工）；
- 渲染超长 → handoff(policy_conflict)（第二级）；
- 未知占位符 / content_hash 与 frozen_template 不一致 → RenderInvariantViolation
  （第三级，不可修复终局：不消耗模型修复次数，human_required(render_invariant_violation)）；
- script_version_id+content_hash 双匹配白名单（失配 = 模型 schema 非法，可修复）；
- peer 槽位必须附当前批次 evidence_message_ids；resume 槽位模型不得输出；
- 敏感主题优先 handoff；
- resume_field 槽位服务端从绑定 resume_id 简历库取值；
- 完成判定（rounds）与 payload_ref。
"""

import json
import uuid

import pytest

from src.boss_conversation.prompts import OutputInvalid, validate_decision_output
from src.boss_conversation.render import RenderInvariantViolation, render_script
from src.boss_conversation.models import template_content_hash


def _script(template, slot_schema=None, vid=None, content_hash=None):
    return {
        "script_version_id": vid or str(uuid.uuid4()),
        "content_hash": content_hash or template_content_hash(template),
        "frozen_template": template,
        "slot_schema": slot_schema or {},
    }


def _spec(scripts, sources=None):
    return {
        "goal": "确认意向",
        "reply_policy": {"style": "简洁礼貌", "allowed_facts": [], "forbidden_commitments": []},
        "completion_rule": {"mode": "rounds", "rounds_target": 2},
        "scripts": scripts,
        "slot_evidence_sources": sources if sources is not None else {
            k: {"source": "peer_message"} for s in scripts for k in s["slot_schema"]
        },
    }


def _model_out(**kw):
    return json.dumps(kw, ensure_ascii=False)


class TestRenderThreeTier:
    def test_render_success_normalizes_reply(self):
        script = _script("您好，{expected_time} 方便沟通吗？", {"expected_time": {"required": True}})
        spec = _spec([script])
        out = validate_decision_output(
            spec,
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"expected_time": "周五14:00"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply",
        )
        assert out["action"] == "reply"
        assert out["reply_text"] == "您好，周五14:00 方便沟通吗？"

    def test_slot_value_placeholder_literal_no_recursive_expansion(self):
        # CR 回归：槽位值（peer 原文提取）中形如 {closing} 的字面量必须逐字输出，
        # 绝不递归展开为其他槽位值（设计 §5.3 "逐字替换"冻结；防 peer 原文把
        # 服务端/其他槽位值注入正文）
        script = _script(
            "您好，{expected_time}，{closing}",
            {
                "expected_time": {"required": True},
                "closing": {"required": True},
            },
        )
        spec = _spec([script])
        out = validate_decision_output(
            spec,
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"],
                       slot_values={"expected_time": "周五 {closing} 见", "closing": "随时联系"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply",
        )
        assert out["action"] == "reply"
        assert out["reply_text"] == "您好，周五 {closing} 见，随时联系"

    def test_select_script_only_for_slot_free_scripts(self):
        script = _script("您好，方便沟通吗？")
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="select_script", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"]),
            [], "reply",
        )
        assert out["action"] == "reply"

    def test_missing_required_slot_handoff_missing_evidence(self):
        script = _script("您好，{expected_time}", {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={}),
            [], "reply",
        )
        # 必需槽位缺失 → handoff(missing_evidence)（第一级，正常业务转人工）
        assert out == {"action": "handoff", "reason_code": "missing_evidence"}

    def test_overlong_render_handoff_policy_conflict(self):
        template = "前缀。" + "长" * 495 + "{expected_time}"
        script = _script(template, {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"expected_time": "周五14:00"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply",
        )
        # slot 值合法但正文超过 500 上限 → 第二级 policy_conflict
        assert out == {"action": "handoff", "reason_code": "policy_conflict"}

    def test_unknown_placeholder_runtime_invariant_violation(self):
        # 运行时已发布冻结副本出现未知占位符（发布校验本应拒绝——纵深防御）：
        # 直接构造 spec dict 模拟篡改，渲染器抛不变量破坏而非 missing_evidence
        template = "您好 {ghost}"
        script = _script(template)
        spec = _spec([script])
        spec["scripts"][0]["frozen_template"] = "您好 {ghost}"
        spec["scripts"][0]["content_hash"] = template_content_hash("您好 {ghost}")
        with pytest.raises(RenderInvariantViolation):
            validate_decision_output(
                spec,
                _model_out(action="select_script", script_version_id=script["script_version_id"],
                           content_hash=script["content_hash"]),
                [], "reply",
            )

    def test_content_hash_template_mismatch_invariant_violation(self):
        script = _script("您好")
        tampered = dict(script)
        tampered["content_hash"] = template_content_hash("被篡改的模板")
        with pytest.raises(RenderInvariantViolation):
            render_script("您好", tampered["content_hash"], {}, {})

    def test_invariant_terminal_not_confused_with_model_error(self):
        # decision_terminal_reason 受控码：通用层据此跳过模型修复直接 human_required
        exc = RenderInvariantViolation("x")
        assert exc.decision_terminal_reason == "render_invariant_violation"


class TestDoubleMatchAndSchema:
    def test_content_hash_mismatch_is_repairable_model_error(self):
        script = _script("您好，方便沟通吗？")
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script]),
                _model_out(action="select_script", script_version_id=script["script_version_id"],
                           content_hash="wrong-hash"),
                [], "reply",
            )

    def test_unknown_script_version_rejected(self):
        script = _script("您好，方便沟通吗？")
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script]),
                _model_out(action="select_script", script_version_id=str(uuid.uuid4()),
                           content_hash=script["content_hash"]),
                [], "reply",
            )

    def test_peer_slot_without_evidence_handoff_missing_evidence(self):
        script = _script("您好，{expected_time}", {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"expected_time": "周五14:00"},
                       evidence_message_ids=[]),
            ["m1"], "reply",
        )
        # peer 取值无当前批次证据 → 证据缺失（第一级分流）
        assert out == {"action": "handoff", "reason_code": "missing_evidence"}

    def test_evidence_outside_current_batch_repairable(self):
        script = _script("您好，{expected_time}", {"expected_time": {"required": True}})
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script]),
                _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                           content_hash=script["content_hash"], slot_values={"expected_time": "周五"},
                           evidence_message_ids=["other-batch-msg"]),
                ["m1"], "reply",
            )

    def test_resume_slot_output_by_model_forbidden_repairable(self):
        script = _script("我在{company}等您", {"company": {"required": True}})
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script], sources={"company": {"source": "resume_field", "field": "current_company"}}),
                _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                           content_hash=script["content_hash"],
                           slot_values={"company": "伪造公司"}, evidence_message_ids=["m1"]),
                ["m1"], "reply",
            )

    def test_unknown_slot_key_repairable(self):
        script = _script("您好 {expected_time}", {"expected_time": {"required": True}})
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script]),
                _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                           content_hash=script["content_hash"],
                           slot_values={"ghost": "x"}, evidence_message_ids=["m1"]),
                ["m1"], "reply",
            )

    def test_handoff_pass_through(self):
        out = validate_decision_output(
            _spec([_script("您好")]),
            _model_out(action="handoff", reason_code="low_confidence"),
            [], "reply",
        )
        assert out == {"action": "handoff", "reason_code": "low_confidence"}

    def test_action_enum_and_unknown_keys(self):
        with pytest.raises(OutputInvalid):
            validate_decision_output(_spec([_script("您好")]), _model_out(action="done"), [], "reply")
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([_script("您好")]),
                _model_out(action="select_script", evil="x",
                           script_version_id=str(uuid.uuid4()), content_hash="h"),
                [], "reply",
            )


class TestSensitiveTopic:
    def test_sensitive_slot_value_forces_handoff(self, monkeypatch):
        import src.boss_conversation.config as boss_config

        monkeypatch.setattr(boss_config, "sensitive_words", lambda *a, **k: ("薪资",))
        script = _script("您好，{note}", {"note": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"note": "你们薪资多少"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply",
        )
        # 敏感主题优先 handoff（服务端兜底，模型未 handoff 也强制）
        assert out == {"action": "handoff", "reason_code": "sensitive_topic"}


class TestResumeServerSideFill:
    def test_resume_slot_filled_from_binding_resume(self, tenant_id, device_row, boss_binding):
        from tests.unit.boss_conversation.conftest import seed_peer_message

        task_id = str(uuid.uuid4())
        # P1-7：服务端正文加载要求引用消息真实在库（peer 槽位值须能在其原文中核验）
        seed_peer_message(tenant_id, task_id, "m1", "我周五16:00以后有空")
        task = {
            "id": task_id, "tenant_id": tenant_id,
            "conversation_binding_id": boss_binding["conversation_binding_id"],
            "control_epoch": 0,
        }
        script = _script("您好，我是{company}的招聘，{expected_time}方便吗？",
                         {"company": {"required": True}, "expected_time": {"required": True}})
        spec = _spec(
            [script],
            sources={
                "company": {"source": "resume_field", "field": "current_company"},
                "expected_time": {"source": "peer_message"},
            },
        )
        out = validate_decision_output(
            spec,
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"expected_time": "周五16:00"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply", task=task,
        )
        # resume 槽位由服务端从简历库 key_info 取值（conftest 种子 current_company=字节跳动）
        assert out["action"] == "reply"
        assert out["reply_text"] == "您好，我是字节跳动的招聘，周五16:00方便吗？"

    def test_resume_field_missing_handoff_missing_evidence(self, tenant_id, device_row, boss_binding):
        from tests.unit.boss_conversation.conftest import seed_peer_message

        task_id = str(uuid.uuid4())
        seed_peer_message(tenant_id, task_id, "m1", "在吗")
        task = {
            "id": task_id, "tenant_id": tenant_id,
            "conversation_binding_id": boss_binding["conversation_binding_id"],
        }
        script = _script("您好，我是{company}的招聘", {"company": {"required": True}})
        spec = _spec([script], sources={"company": {"source": "resume_field", "field": "salary_expectation"}})
        out = validate_decision_output(
            spec,
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={}),
            ["m1"], "reply", task=task,
        )
        # resume 字段缺失 → 第一级 missing_evidence（不抛异常、不调模型）
        assert out == {"action": "handoff", "reason_code": "missing_evidence"}


class TestPeerTextClosedLoop:
    """P1-7（六审）：服务端加载本批 peer 正文——敏感兜底 + 槽位证据确定性来源核验。"""

    def _task(self, tenant_id, boss_binding, task_id=None):
        return {
            "id": task_id or str(uuid.uuid4()), "tenant_id": tenant_id,
            "conversation_binding_id": boss_binding["conversation_binding_id"],
        }

    def test_no_slot_select_script_sensitive_peer_message_handoff(self, tenant_id, boss_binding):
        """无槽位 select_script 对含敏感词对方消息必须 handoff（此前只扫 slot value）。"""
        from tests.unit.boss_conversation.conftest import seed_peer_message

        task_id = str(uuid.uuid4())
        seed_peer_message(tenant_id, task_id, "m1", "你们这个岗位薪资多少？有offer吗")
        script = _script("您好，看到您投递了岗位，方便聊聊吗？")
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="select_script", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"]),
            ["m1"], "reply", task=self._task(tenant_id, boss_binding, task_id),
        )
        assert out == {"action": "handoff", "reason_code": "sensitive_topic"}

    def test_fabricated_slot_with_legit_evidence_id_rejected(self, tenant_id, boss_binding):
        """编造 slot 值挂合法消息 ID：值不在引用消息原文中 → missing_evidence。"""
        from tests.unit.boss_conversation.conftest import seed_peer_message

        task_id = str(uuid.uuid4())
        seed_peer_message(tenant_id, task_id, "m1", "周五下午三点可以")
        script = _script("好的，那我们{expected_time}见", {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"],
                       slot_values={"expected_time": "周六上午十点"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply", task=self._task(tenant_id, boss_binding, task_id),
        )
        assert out == {"action": "handoff", "reason_code": "missing_evidence"}

    def test_quoted_from_peer_text_passes(self, tenant_id, boss_binding):
        """引用原文（跨空白/大小写规范化）→ 正常渲染。"""
        from tests.unit.boss_conversation.conftest import seed_peer_message

        task_id = str(uuid.uuid4())
        seed_peer_message(tenant_id, task_id, "m1", "周五 下午三点 可以")
        script = _script("好的，那我们{expected_time}见", {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"],
                       slot_values={"expected_time": "周五 下午三点"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply", task=self._task(tenant_id, boss_binding, task_id),
        )
        assert out["action"] == "reply"
        assert out["reply_text"] == "好的，那我们周五 下午三点见"

    def test_unreadable_peer_text_fail_closed(self, tenant_id, boss_binding):
        """引用消息不可读（缺行/密文损坏）→ fail-closed missing_evidence，绝不放行。"""
        task_id = str(uuid.uuid4())  # 不落任何消息行
        script = _script("好的，那我们{expected_time}见", {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"],
                       slot_values={"expected_time": "周五"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply", task=self._task(tenant_id, boss_binding, task_id),
        )
        assert out == {"action": "handoff", "reason_code": "missing_evidence"}


class TestReasonCodeEnum:
    """P1-6（六审）：reason_code 严格限定 REASON_CODES 受控枚举。"""

    def test_free_text_reason_rejected(self):
        script = _script("您好")
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script]),
                _model_out(action="handoff", reason_code="我觉得还是转人工比较好"),
                [], "reply",
            )

    def test_non_string_reason_rejected(self):
        script = _script("您好")
        with pytest.raises(OutputInvalid):
            validate_decision_output(
                _spec([script]),
                _model_out(action="handoff", reason_code=123),
                [], "reply",
            )

    def test_enum_reason_passes(self):
        script = _script("您好")
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="handoff", reason_code="ambiguous"),
            [], "reply",
        )
        assert out == {"action": "handoff", "reason_code": "ambiguous"}


class TestOptionalSlotSemantics:
    """非阻断 b（六审）冻结语义：可选槽位（required=False）缺值 → 空串替换照常
    发送；必需槽位缺失 → missing_evidence（required 标记因此有真实区分度）。"""

    def test_optional_slot_missing_renders_empty(self):
        template = "您好{polite}，方便沟通吗？"
        script = _script(template, {"polite": {"required": False}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="select_script", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"]),
            [], "reply",
        )
        assert out["action"] == "reply"
        assert out["reply_text"] == "您好，方便沟通吗？"

    def test_optional_slot_present_renders_value(self):
        template = "您好{polite}，方便沟通吗？"
        script = _script(template, {"polite": {"required": False}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"polite": "张先生"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply",
        )
        assert out["reply_text"] == "您好张先生，方便沟通吗？"

    def test_required_slot_still_missing_evidence_at_renderer(self):
        # select_script 对含必需槽位话术在 action 层已被拒（OutputInvalid）；
        # 渲染器级必需缺失语义用 fill_slots + 空 slot_values 直测 render_script
        template = "您好{name}，方便沟通吗？"
        text, reason = render_script(template, template_content_hash(template),
                                     {"name": {"required": True}}, {})
        assert text is None and reason == "missing_evidence"
        # 可选槽位缺值：渲染器返回正文（空串替换），不是 missing_evidence
        template2 = "您好{polite}，方便沟通吗？"
        text2, reason2 = render_script(template2, template_content_hash(template2),
                                       {"polite": {"required": False}}, {})
        assert text2 == "您好，方便沟通吗？" and reason2 is None

    def test_invalid_case_and_malformed_braces_sent_literal(self):
        """V1.10 变更记录 5：仅 {slot_name}（小写语法名）参与替换——非法大小写
        （{Company}）与畸形花括号（{ x }）按字面量原样发送，发布与渲染行为一致
        （发布校验不视为未知占位符、渲染不做二次扫描）。"""
        template = "您好{Company}，{ x }{expected_time}见"
        script = _script(template, {"expected_time": {"required": True}})
        out = validate_decision_output(
            _spec([script]),
            _model_out(action="fill_slots", script_version_id=script["script_version_id"],
                       content_hash=script["content_hash"], slot_values={"expected_time": "周五"},
                       evidence_message_ids=["m1"]),
            ["m1"], "reply",
        )
        assert out["action"] == "reply"
        assert out["reply_text"] == "您好{Company}，{ x }周五见"


class TestCompletionAndPayloadRef:
    def test_rounds_completion(self):
        from src.boss_conversation import completion

        spec = {"limits": {"max_replies": 5, "expires_at": "2099-01-01T00:00:00Z"},
                "completion_rule": {"mode": "rounds", "rounds_target": 2}}
        links = [
            {"delivery_state": "succeeded", "decision_kind": "reply"},
            {"delivery_state": "succeeded", "decision_kind": "reply"},
        ]
        verdict = completion.evaluate_completion(
            spec=spec, links=links, has_pending_sends=False, latest_reply_evidence=None,
            review_decision=None, last_peer_activity_at=None, now="2026-09-18T00:00:00Z",
            pending_decision_count=0,
        )
        assert verdict == {"status": "completed", "reason": "rounds_reached"}
        # 未达标不完成
        verdict2 = completion.evaluate_completion(
            spec=spec, links=links[:1], has_pending_sends=False, latest_reply_evidence=None,
            review_decision=None, last_peer_activity_at=None, now="2026-09-18T00:00:00Z",
            pending_decision_count=0,
        )
        assert verdict2 is None

    def test_payload_ref_roundtrip(self):
        from src.boss_conversation.render import build_payload_ref, parse_payload_ref

        ref = build_payload_ref("d-1")
        assert ref.startswith("da:boss.chat_reply.v1:boss-reply:")
        assert parse_payload_ref(ref) == "d-1"
        with pytest.raises(ValueError):
            parse_payload_ref("da:weixin.conversation.v1:session-reply:d-1")
