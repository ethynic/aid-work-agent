"""WP2 受理层纯函数：结构化答案归一化、判定表校验与 canonical 文本。

docs/system/design-ask-user-interactive-question.md §4.2/§5.4：
- 归一化（qid 小写、去空白、重复 key 去重）；
- 判定表全组合（skip×allow_skip、结构非法逐题明细、required 遍历整个 form、
  全空提交、部分未答、纯文本兼容）；
- canonical（题序全量、label「、」、自定义「；自定义：」、未回答占位、跳过文案）。
数据库受理与幂等归 tests/integration/agent_runner_service/，不在此重复。
"""

import pytest
from pydantic import ValidationError

from src.services.agent_runner import ask_form
from src.services.agent_runner.control_contracts import AnswerItem, RunnerControl

pytestmark = pytest.mark.unit


def form(**overrides):
    """与 contracts/runtime-host/v1/fixtures/ask-form.json 首例同构的混合题型表单。"""
    value = {
        "form_id": "frm_wp2-1", "wait_id": "wait_wp2-1",
        "title": "归档范围", "intro": "请确认归档范围。",
        "questions": [
            {"id": "q1", "tag": "归档范围", "prompt": "以下哪些任务移入 ideas_finished.md？",
             "type": "multi", "required": True, "max_select": 3,
             "options": [
                 {"key": "q1o1", "label": "queue_order 序列自愈"},
                 {"key": "q1o2", "label": "wecom_kf 限频修复"},
                 {"key": "q1o3", "label": "file_assets 观察项"}],
             "allow_custom_text": True},
            {"id": "q2", "tag": "后续独立事项", "prompt": "哪个事项需要单独立任务？",
             "type": "single",
             "options": [{"key": "q2o1", "label": "飞书钉钉接入 AgentRunner"},
                         {"key": "q2o2", "label": "暂无"}]},
            {"id": "q3", "tag": "补充说明", "prompt": "还有什么需要补充？", "type": "text"},
        ],
        "allow_skip": True, "expires_at": None,
    }
    value.update(overrides)
    return value


def answer(qid, keys=(), text=None):
    return {"question_id": qid, "selected_keys": list(keys), "text": text}


def errors_of(form_value, answers, *, skip=False):
    """与受理层一致的调用顺序：先归一化再判定表校验。"""
    return ask_form.validate_answers(form_value, ask_form.normalize_answers(answers), skip=skip)


def codes(details):
    return [(item["question_id"], item["code"]) for item in details]


class TestNormalize:
    def test_question_id_lowercased_and_stripped(self):
        normalized = ask_form.normalize_answers([answer(" Q1 ", ["q1o1"])])
        assert normalized == [{"question_id": "q1", "selected_keys": ["q1o1"], "text": None}]

    def test_duplicate_selected_keys_deduplicated_keeping_first_order(self):
        normalized = ask_form.normalize_answers(
            [answer("q1", ["q1o2", "q1o1", "q1o2", "q1o1"])])
        assert normalized[0]["selected_keys"] == ["q1o2", "q1o1"]

    def test_accepts_pydantic_models_and_empty_input(self):
        from src.services.agent_runner.control_contracts import AnswerItem
        normalized = ask_form.normalize_answers(
            [AnswerItem(question_id="Q3", selected_keys=["q3o1"], text=" 说明 ")])
        assert normalized == [{"question_id": "q3", "selected_keys": ["q3o1"], "text": " 说明 "}]
        assert ask_form.normalize_answers(None) == []

    def test_entry_model_strips_question_id_before_acceptance(self):
        # 评审 P2-1：归一化测试必须经过真实入口模型（RunnerControl），不得绕过——
        # 入口先剥离无害空白再验格式，" Q1 " 可受理；未知形状在入口即被拒绝。
        control = RunnerControl(
            client_request_id="k", action="reply", target_execution_id="runner-1",
            wait_id="wait-1",
            answers=[AnswerItem(question_id=" Q1 ", selected_keys=["q1o1"], text=None)])
        normalized = ask_form.normalize_answers(control.answers)
        assert normalized[0]["question_id"] == "q1"
        assert ask_form.validate_answers(form(), normalized) == []
        with pytest.raises(ValidationError):
            AnswerItem(question_id=" question-1 ", selected_keys=[], text=None)


class TestJudgementTableSkip:
    def test_skip_rejected_when_allow_skip_false(self):
        details = errors_of(form(allow_skip=False), None, skip=True)
        assert codes(details) == [("form", "FORM_SKIP_NOT_ALLOWED")]

    def test_server_authoritative_skip_passes_even_with_unanswered_required(self):
        assert errors_of(form(), [answer("q2")], skip=True) == []
        assert errors_of(form(), [], skip=True) == []

    def test_skip_takes_priority_over_structural_violations(self):
        # 判定表顺序即优先级：skip 路径不做 answers 结构校验。
        assert errors_of(form(allow_skip=True), [answer("q9", ["q9o9"])], skip=True) == []


class TestJudgementTableStructure:
    def test_unknown_question_id_reports_missing_question(self):
        details = errors_of(form(), [answer("q9", ["q9o1"])])
        assert codes(details) == [("q9", "FORM_ANSWER_INVALID")]
        assert details[0]["message"] == "问题不存在于当前表单"

    def test_out_of_range_option_key(self):
        details = errors_of(form(), [answer("q1", ["q1o9"])])
        assert codes(details) == [("q1", "FORM_ANSWER_INVALID")]
        assert details[0]["message"] == "选择的选项不属于该题"

    def test_text_question_cannot_carry_selected_keys(self):
        details = errors_of(form(), [answer("q3", ["q3o1"])])
        assert codes(details) == [("q3", "FORM_ANSWER_INVALID")]

    def test_custom_text_rejected_when_not_allowed(self):
        questions = form()["questions"]
        questions[1]["allow_custom_text"] = False  # single/multi 缺省 true，显式关闭才拒绝
        details = errors_of(form(questions=questions),
                            [answer("q1", ["q1o1"]), answer("q2", ["q2o1"], text="补充")])
        assert codes(details) == [("q2", "FORM_ANSWER_INVALID")]

    def test_custom_text_rejected_when_merged_from_duplicate(self):
        # 评审 P2-3 复现场景：首项 text 为空、重复项携带文本——合并采纳的
        # 文本同样必须过 allow_custom_text 业务校验，不能进 canonical。
        questions = form()["questions"]
        questions[0]["allow_custom_text"] = False
        details = errors_of(form(questions=questions),
                            [answer("q1", ["q1o1"]),
                             answer("q1", ["q1o1"], text="改为其他处理")])
        assert codes(details) == [("q1", "FORM_ANSWER_INVALID")]
        # 对照：重复项不带文本时合并仍是无害的（保留 P2-2 行为）。
        assert errors_of(form(questions=questions),
                         [answer("q1", ["q1o1"]), answer("q1", ["q1o1"])]) == []

    def test_single_question_rejects_two_keys(self):
        details = errors_of(form(), [answer("q1", ["q1o1"]), answer("q2", ["q2o1", "q2o2"])])
        assert details[0]["message"] == "该题为单选，最多选择一项"

    def test_multi_question_rejects_over_max_select(self):
        questions = form()["questions"]
        questions[0]["max_select"] = 2
        details = errors_of(form(questions=questions), [answer("q1", ["q1o1", "q1o2", "q1o3"])])
        assert details[0]["message"] == "该题最多选择 2 项"

    def test_multi_without_max_select_defaults_to_option_count(self):
        questions = form()["questions"]
        del questions[0]["max_select"]
        assert errors_of(form(), [answer("q1", ["q1o1", "q1o2", "q1o3"])]) == []

    def test_contradictory_duplicates_rejected_identical_duplicates_counted_once(self):
        details = errors_of(form(), [answer("q1", ["q1o1"]),
                                     answer("q2", ["q2o1"]), answer("q2", ["q2o2"])])
        assert codes(details) == [("q2", "FORM_ANSWER_INVALID")]
        assert details[0]["message"] == "同一问题重复提交且答案矛盾"
        # 归一化（qid 小写、key 去重）后完全相同的重复项按一次计。
        assert errors_of(form(), [answer("q1", ["q1o1"]),
                                  answer("q2", ["q2o1"]), answer("Q2", ["q2o1", "q2o1"])]) == []

    def test_same_selection_in_different_order_is_not_contradictory(self):
        # 评审 P2-2：相同多选集合仅顺序不同，不要求用户纠正；非空文本优先保留。
        assert errors_of(form(), [answer("q1", ["q1o1", "q1o2"]),
                                  answer("q1", ["q1o2", "q1o1"])]) == []
        assert errors_of(form(), [answer("q1", ["q1o1"]), answer("q1", ["q1o1"], "补充说明")]) == []

    def test_duplicate_with_different_selections_or_conflicting_text_is_contradictory(self):
        # 集合不同、或两段非空白文本不同，才是真正无法解释的矛盾。
        details = errors_of(form(), [answer("q1", ["q1o1"]),
                                     answer("q1", ["q1o1", "q1o2"], "x")])
        assert codes(details) == [("q1", "FORM_ANSWER_INVALID")]
        details = errors_of(form(), [answer("q3", [], "先做第一项"),
                                     answer("q3", [], "不要继续")])
        assert codes(details) == [("q3", "FORM_ANSWER_INVALID")]

    def test_canonical_and_validation_merge_duplicates_identically(self):
        merged = [answer("q1", ["q1o1"]), answer("q1", ["q1o1"], "自定义内容")]
        assert errors_of(form(), merged) == []
        text = ask_form.canonical_text(form(), ask_form.normalize_answers(merged))
        assert "自定义内容" in text  # 非空文本不被空白项覆盖（canonical 与判定表同源合并）

    def test_multiple_structural_violations_reported_per_question(self):
        details = errors_of(form(), [answer("q9"), answer("q3", ["q3o1"]),
                                     answer("q2", ["q2o1", "q2o2"])])
        assert codes(details) == [("q9", "FORM_ANSWER_INVALID"),
                                  ("q3", "FORM_ANSWER_INVALID"),
                                  ("q2", "FORM_ANSWER_INVALID")]

    def test_structural_violations_take_priority_over_required(self):
        # 归一化后仍有无法解释项时，不再叠加 required 明细（判定表顺序即优先级）。
        details = errors_of(form(), [answer("q9")])
        assert codes(details) == [("q9", "FORM_ANSWER_INVALID")]


class TestJudgementTableRequired:
    def test_missing_required_question_reports_its_id(self):
        details = errors_of(form(), [answer("q2", ["q2o1"]), answer("q3", text="没有")])
        assert codes(details) == [("q1", "FORM_ANSWER_INVALID")]
        assert details[0]["message"] == "该题为必答"

    def test_required_satisfied_by_text_only_multi_question(self):
        assert errors_of(form(), [answer("q1", [], text="都不归档")]) == []

    def test_required_satisfied_by_keys_only(self):
        assert errors_of(form(), [answer("q1", ["q1o1"])]) == []

    def test_whitespace_only_text_does_not_satisfy_required(self):
        details = errors_of(form(), [answer("q1", [], text="   ")])
        assert codes(details) == [("q1", "FORM_ANSWER_INVALID")]

    def test_all_empty_answers_is_legitimate_when_nothing_required(self):
        relaxed = form(questions=[form()["questions"][1], form()["questions"][2]])
        assert errors_of(relaxed, []) == []
        assert errors_of(relaxed, [answer("q2")]) == []  # 部分未答同样合法

    def test_all_empty_answers_with_required_reports_missing_questions(self):
        required_two = form(questions=[
            {**form()["questions"][0], "id": "q1"},
            {**form()["questions"][2], "id": "q2", "required": True}])
        details = errors_of(required_two, [])
        assert codes(details) == [("q1", "FORM_ANSWER_INVALID"), ("q2", "FORM_ANSWER_INVALID")]


class TestCanonical:
    def test_design_example_mixed_answers_and_unanswered(self):
        answers = [answer("q1", ["q1o1", "q1o2"], "file_assets 建议观察一周再归档"),
                   answer("q2", ["q2o1"]), answer("q3")]
        assert ask_form.canonical_text(form(), answers) == (
            "Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档\n"
            "Q2【后续独立事项】飞书钉钉接入 AgentRunner\n"
            "Q3【补充说明】（未回答）")

    def test_text_only_answer_has_no_custom_prefix(self):
        text = ask_form.canonical_text(form(), [answer("q1", ["q1o1"]), answer("q3", text="没有其他补充")])
        assert text.splitlines()[2] == "Q3【补充说明】没有其他补充"

    def test_tag_falls_back_to_prompt_then_id(self):
        bare = form(questions=[{"id": "q1", "prompt": "还要补充什么？", "type": "text"}])
        assert ask_form.canonical_text(bare, [answer("q1", text="好")]) == "Q1【还要补充什么？】好"
        naked = form(questions=[{"id": "q1", "type": "text"}])
        assert ask_form.canonical_text(naked, []) == "Q1【q1】（未回答）"

    def test_skip_path_generates_fixed_text(self):
        assert ask_form.canonical_text(form(), None, skip=True) == (
            "（用户跳过了本次提问，请按已知信息继续）")


class TestErrorContract:
    def test_error_details_match_registered_dto_shape(self):
        details = errors_of(form(), [answer("q1", ["q1o9"]), answer("q2", ["q2o1"], text="x")])
        for item in details:
            assert set(item) == {"question_id", "code", "message"}
            assert all(isinstance(value, str) and value for value in item.values())
