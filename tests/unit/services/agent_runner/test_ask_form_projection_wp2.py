"""WP2 公共视图投影：wait.form 白名单、supplementalInputs 扩展与表单引用。

docs/plans/plan-ask-user-interactive-question.md §WP2：
- `_public_waiting` 白名单加 `form`（无 form 的 wait 输出与改动前完全一致）；
- supplementalInputs 扩展投影（form 快照 + 归一化答案）仅结构化受理携带；
- clarificationQuestions 问题文本沿用既有结构，附带 form_id 引用；
- 校验错误只出现在 HTTP 响应，任何投影不新增错误字段。
"""

import pytest
from types import SimpleNamespace

from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.public_view import public_runner

pytestmark = pytest.mark.unit


FORM = {
    "form_id": "frm_wp2-1", "wait_id": "wait_wp2-1", "title": "归档范围",
    "questions": [
        {"id": "q1", "tag": "归档范围", "prompt": "选哪些？", "type": "multi", "required": True,
         "allow_custom_text": True, "custom_placeholder": "输入自定义内容…", "max_select": 3,
         "options": [{"key": "q1o1", "label": "queue_order 序列自愈", "description": "已完成"},
                     {"key": "q1o2", "label": "wecom_kf 限频修复"}]},
        {"id": "q3", "tag": "补充说明", "prompt": "还要补充什么？", "type": "text",
         "placeholder": "输入你的回答…"},
    ],
    "allow_skip": True, "expires_at": None,
}


def row(snapshot):
    return {'runner_id': 'runner-1', 'session_kind': 'web', 'session_id': 'session-1',
            'source': 'chat', 'status': 'waiting', 'settlement_status': None,
            'client_request_id': 'key-1', 'profile_id': 'main', 'queue_order': 1,
            'revision': 2, 'cancel_requested': False, 'pause_requested': False,
            'view_revision': 3, 'control_revision': 1, 'accepted_at': None,
            'updated_at': None, 'finished_at': None, 'public_snapshot': snapshot,
            'result': None}


def projected(snapshot):
    return public_runner(row(snapshot))['snapshot']


class TestWaitingFormProjection:
    def test_form_whitelisted_with_known_fields_only(self):
        waiting = {'kind': 'clarification', 'question': '请确认', 'wait_id': 'wait_wp2-1',
                   'target_execution_id': 'runner-1', 'tool_call_id': 'call-1',
                   'form': {**FORM, 'secret_field': 'must-not-leak',
                            'questions': [{**FORM['questions'][0], 'private': 'x'}]}}
        form = projected({'waiting': waiting})['waiting']['form']
        assert form['form_id'] == 'frm_wp2-1' and form['allow_skip'] is True
        assert 'secret_field' not in form
        question = form['questions'][0]
        assert question['id'] == 'q1' and question['max_select'] == 3
        assert 'private' not in question
        assert question['options'] == [
            {'key': 'q1o1', 'label': 'queue_order 序列自愈', 'description': '已完成'},
            {'key': 'q1o2', 'label': 'wecom_kf 限频修复'}]  # 缺省 description 不产出 None 键

    def test_waiting_without_form_output_unchanged(self):
        waiting = {'kind': 'clarification', 'question': '哪种城市？',
                   'wait_id': 'wait-1', 'target_execution_id': 'runner-1',
                   'tool_call_id': 'call-1', 'missing_info': ['city']}
        result = projected({'waiting': waiting})['waiting']
        assert result == {'kind': 'clarification', 'question': '哪种城市？',
                          'tool_call_id': 'call-1', 'wait_id': 'wait-1',
                          'target_execution_id': 'runner-1', 'missing_info': ['city']}


class TestSupplementalInputsProjection:
    def structured_entry(self):
        # errors 等额外键是真实可能存在的输入（HTTP 错误通道与快照分离的前提），
        # 白名单投影必须不输出它们——泄露条件在本用例内构造（评审：无 errors 的
        # "未出现"断言检不出泄露）。
        return {'control_id': 'control-1', 'client_request_id': 'key-1',
                'message_id': 'control-1:user', 'text': 'Q1【归档范围】wecom_kf 限频修复',
                'wait_id': 'wait_wp2-1', 'target_execution_id': 'runner-1',
                'accepted_at': '2026-10-14T00:00:00', 'attachments': [],
                'form': FORM,
                'answers': [{'question_id': 'q1', 'selected_keys': ['q1o2'], 'text': None}],
                'errors': [{'question_id': 'q1', 'code': 'FORM_ANSWER_INVALID',
                            'message': '该题为必答'}]}

    def test_structured_acceptance_projects_form_and_answers(self):
        result = projected({'supplementalInputs': [self.structured_entry()]})
        inputs = result['supplementalInputs']
        assert inputs[0]['text'] == 'Q1【归档范围】wecom_kf 限频修复'
        assert inputs[0]['form']['form_id'] == 'frm_wp2-1'
        assert inputs[0]['answers'] == [{'question_id': 'q1', 'selected_keys': ['q1o2'],
                                         'text': None}]
        # errors 只属于 HTTP 响应，快照投影不输出（真实泄露条件已构造）。
        assert 'errors' not in result
        assert 'errors' not in inputs[0]

    def test_plain_text_history_projection_unchanged(self):
        entry = {'control_id': 'control-2', 'client_request_id': 'key-2',
                 'message_id': 'control-2:user', 'text': '直接文字回答',
                 'wait_id': 'wait-1', 'target_execution_id': 'runner-1',
                 'accepted_at': '2026-10-14T00:00:00', 'attachments': [],
                 'errors': [{'question_id': 'q1', 'code': 'X', 'message': 'x'}]}
        assert projected({'supplementalInputs': [entry]})['supplementalInputs'] == [{
            'control_id': 'control-2', 'client_request_id': 'key-2',
            'message_id': 'control-2:user', 'text': '直接文字回答', 'wait_id': 'wait-1',
            'target_execution_id': 'runner-1', 'accepted_at': '2026-10-14T00:00:00',
            'attachments': []}]


def decorate(waiting):
    state = SimpleNamespace(waiting=waiting, execution_id='runner-1',
                            children={}, followup_messages=[], messages=[])
    control = DurableControl(SimpleNamespace(), SimpleNamespace(runner_id='runner-1'),
                             {'revision': 1, 'public_snapshot': {},
                              'cancel_requested': False, 'status': 'running'})
    control.decorate_wait(state)
    return control.snapshot


class TestClarificationQuestionsFormReference:
    def test_form_wait_records_form_id_reference(self):
        waiting = {'kind': 'clarification', 'question': '请确认归档范围',
                   'wait_id': 'wait_wp2-1', 'tool_call_id': 'call-1', 'form': FORM}
        questions = decorate(waiting)['clarificationQuestions']
        assert questions[0]['wait_id'] == 'wait_wp2-1'
        assert questions[0]['text'] == '请确认归档范围'
        assert questions[0]['form_id'] == 'frm_wp2-1'

    def test_legacy_clarify_records_no_form_reference(self):
        waiting = {'kind': 'clarification', 'question': '哪种城市？',
                   'wait_id': 'wait-1', 'tool_call_id': 'call-1'}
        questions = decorate(waiting)['clarificationQuestions']
        assert questions[0]['text'] == '哪种城市？'
        assert 'form_id' not in questions[0]
