"""WP1 模型层契约：结构化答案入口校验与旧请求摘要兼容。

docs/plans/plan-ask-user-interactive-question.md §WP1：
- 入口校验修订（reply 合法条件、两组互斥、非 reply 禁带、旧 clarify 不变）；
- intent()/digest() 显式条件序列化——旧形状请求与改动前基线逐字节一致
  （基线为改动前 model_dump 输出的硬编码值，含 canonical 串与 sha256）。
受理层幂等（同键命中/改答案拒绝）归 WP2 数据库链路，不在此重复。
"""

import hashlib
import json

import pytest
from pydantic import ValidationError

from src.services.agent_runner.contracts import AttachmentRef, canonical_json
from src.services.agent_runner.control_contracts import AnswerItem, RunnerControl

pytestmark = pytest.mark.unit


def structured(**overrides):
    values = {"question_id": "q1", "selected_keys": ["q1o1"], "text": None}
    values.update(overrides)
    return AnswerItem(**values)


def reply(**overrides):
    values = {"client_request_id": "k", "action": "reply",
              "target_execution_id": "runner-1", "wait_id": "wait-1"}
    values.update(overrides)
    return RunnerControl(**values)


def expect_code(callable_, code):
    with pytest.raises(ValidationError) as error:
        callable_()
    assert code in str(error.value)


class TestAnswerItemShape:
    def test_question_id_allows_single_digit_only(self):
        assert structured().question_id == "q1"
        for bad in ("q0", "q10", "question-1", "", "w1"):
            with pytest.raises(ValidationError):
                structured(question_id=bad)

    def test_question_id_is_case_insensitive_at_entry(self):
        # 归一化为小写发生在 WP2 受理层，入口只验格式。
        assert structured(question_id="Q2").question_id == "Q2"

    def test_selected_keys_and_text_are_structural_only(self):
        # 选项范围/题型匹配归受理层；入口只要求 list[str] 与可空 text。
        item = structured(selected_keys=["q1o1", "q9o9"], text="自定义")
        assert item.selected_keys == ["q1o1", "q9o9"] and item.text == "自定义"
        assert structured(selected_keys=[], text=None).text is None

    def test_text_is_required_but_nullable_and_extra_forbidden(self):
        with pytest.raises(ValidationError):
            AnswerItem(question_id="q1", selected_keys=[])  # 缺 text 即缺题
        with pytest.raises(ValidationError):
            structured(extra_field="x")


class TestEntryValidation:
    def test_answers_only_reply_passes(self):
        control = reply(answers=[structured()])
        assert control.answers[0].question_id == "q1"

    def test_empty_answers_array_is_a_legitimate_submission(self):
        assert reply(answers=[]).answers == []

    def test_skip_alone_passes(self):
        assert reply(skip=True).skip is True

    def test_answers_conflict_with_nonempty_answer(self):
        expect_code(lambda: reply(answer="文字回答", answers=[structured()]),
                    "CONTROL_ANSWER_CONFLICT")
        # 空白 answer 视为空，与 answers 不冲突。
        assert reply(answer="  ", answers=[structured()]).answers

    def test_skip_conflicts_with_answers(self):
        expect_code(lambda: reply(answers=[], skip=True), "CONTROL_SKIP_CONFLICT")

    def test_non_reply_actions_cannot_carry_structured_fields(self):
        for action in ("pause", "resume", "browser_complete"):
            values = {"client_request_id": "k", "action": action,
                      "target_execution_id": "runner-1"}
            if action == "browser_complete":
                values.update(wait_id="wait-1", completion_ref="ref-1")
            expect_code(lambda values=values: RunnerControl(**values, answers=[structured()]),
                        "CONTROL_STRUCTURED_UNEXPECTED")
            expect_code(lambda values=values: RunnerControl(**values, skip=True),
                        "CONTROL_STRUCTURED_UNEXPECTED")
        # 缺省（answers=None、skip=False）照常合法，非 reply 行为不变。
        assert RunnerControl(client_request_id="k", action="pause",
                             target_execution_id="runner-1").skip is False

    def test_legacy_empty_clarify_reply_still_requires_answer(self):
        expect_code(reply, "CONTROL_REPLY_REQUIRED")
        expect_code(lambda: reply(answer="   "), "CONTROL_REPLY_REQUIRED")
        # attachments-only 与 whitespace-answer 既有行为不变。
        assert reply(answer="  ", attachments=[AttachmentRef(file_id="f")])

    def test_answers_items_are_capped_at_five(self):
        items = [structured(question_id=f"q{i}") for i in range(1, 6)]
        assert len(reply(answers=items).answers) == 5
        items.append(structured(question_id="q6"))
        with pytest.raises(ValidationError):
            reply(answers=items)


# 摘要兼容基线：改动前 model_dump(mode='json') 弹出 client_request_id 后的
# canonical_json 输出与 sha256。旧形状请求（无 answers/skip）必须逐字节一致。

REPLY_TEXT_BASELINE = json.dumps(
    {"action": "reply", "answer": "hello 你好", "attachments": [],
     "completion_ref": None, "target_execution_id": "runner-1", "wait_id": "wait-1"},
    ensure_ascii=False, sort_keys=True, separators=(",", ":"))
REPLY_TEXT_DIGEST = "b14850ae9c42a2c33072f805ed58291a78f09a80c560baca30d8c288bfa81b28"

REPLY_ATTACHMENT_BASELINE = json.dumps(
    {"action": "reply", "answer": "see file",
     "attachments": [{"content": None, "file_id": "file-1", "mime_type": "text/plain",
                      "name": "notes.txt", "size": 12, "type": "file"}],
     "completion_ref": None, "target_execution_id": "runner-1", "wait_id": "wait-1"},
    ensure_ascii=False, sort_keys=True, separators=(",", ":"))
REPLY_ATTACHMENT_DIGEST = "b1e15f13f8a3f66ac5de8ec9aefd034cb40cdf4bfe6f04ca69f94f80659d1b75"

PAUSE_BASELINE = json.dumps(
    {"action": "pause", "answer": "", "attachments": [], "completion_ref": None,
     "target_execution_id": "runner-1", "wait_id": None},
    ensure_ascii=False, sort_keys=True, separators=(",", ":"))
PAUSE_DIGEST = "2e53c8b1beebb5da5ba4f856f58448f185e1c12f4b022f0b105ef36ea73252d4"

RESUME_BASELINE = json.dumps(
    {"action": "resume", "answer": "", "attachments": [], "completion_ref": None,
     "target_execution_id": "runner-1", "wait_id": None},
    ensure_ascii=False, sort_keys=True, separators=(",", ":"))
RESUME_DIGEST = "cd3a3aeb8d088fb6b91da3b4de0167ae8f3f62003212fca0a72e58a2bd647da9"


class TestLegacyIntentCompatibility:
    def test_text_reply_keeps_byte_identical_serialization(self):
        control = reply(client_request_id="legacy-key-1", answer="hello 你好")
        assert canonical_json(control.intent()) == REPLY_TEXT_BASELINE
        assert control.digest() == REPLY_TEXT_DIGEST

    def test_attachment_reply_keeps_byte_identical_serialization(self):
        control = reply(client_request_id="legacy-key-2", answer="see file",
                        attachments=[AttachmentRef(file_id="file-1", name="notes.txt",
                                                   type="file", mime_type="text/plain",
                                                   size=12)])
        assert canonical_json(control.intent()) == REPLY_ATTACHMENT_BASELINE
        assert control.digest() == REPLY_ATTACHMENT_DIGEST

    def test_pause_keeps_byte_identical_serialization(self):
        control = RunnerControl(client_request_id="legacy-key-3", action="pause",
                                target_execution_id="runner-1")
        assert canonical_json(control.intent()) == PAUSE_BASELINE
        assert control.digest() == PAUSE_DIGEST

    def test_resume_keeps_byte_identical_serialization(self):
        control = RunnerControl(client_request_id="legacy-key-4", action="resume",
                                target_execution_id="runner-1")
        assert canonical_json(control.intent()) == RESUME_BASELINE
        assert control.digest() == RESUME_DIGEST

    def test_legacy_intent_has_no_structured_keys(self):
        for control in (reply(answer="x"),
                        RunnerControl(client_request_id="k", action="pause",
                                      target_execution_id="r")):
            assert "answers" not in control.intent()
            assert "skip" not in control.intent()

    def test_baselines_match_independent_sha256_of_canonical(self):
        # 基线自洽：digest 确为 canonical 串的 sha256，防硬编码抄写错误。
        assert REPLY_TEXT_DIGEST == hashlib.sha256(
            REPLY_TEXT_BASELINE.encode("utf-8")).hexdigest()


class TestStructuredIntentSerialization:
    def test_answers_key_appears_only_when_supplied(self):
        control = reply(answers=[structured(text="备注")])
        intent = control.intent()
        assert intent["answers"] == [
            {"question_id": "q1", "selected_keys": ["q1o1"], "text": "备注"}]
        assert "skip" not in intent
        # 其余字段仍是完整 model_dump 形状（无全局排除）。
        assert intent["completion_ref"] is None and intent["answer"] == ""

    def test_skip_key_appears_only_when_true(self):
        intent = reply(skip=True).intent()
        assert intent["skip"] is True
        assert "answers" not in intent

    def test_structured_digest_differs_from_plain_text_shape(self):
        assert reply(answers=[]).digest() != REPLY_TEXT_DIGEST
        assert reply(skip=True).digest() != REPLY_TEXT_DIGEST

    def test_same_structured_intent_is_stable_for_idempotent_retry(self):
        first = reply(client_request_id="retry", answers=[structured()])
        second = reply(client_request_id="other-key", answers=[structured()])
        assert first.digest() == second.digest()
