"""legacy SSE 桥：clarification 帧的 form 透传（Phase 3，设计 §7.1）。

旧页面无卡片渲染能力，桥仅随帧携带结构化表单数据；无 form 的旧 clarify
帧保持向后兼容（无 form 键）。
"""
from src.services.agent_runner.legacy_stream_bridge import _LegacyFrameState


def _row(waiting):
    return {'snapshot': {'waiting': waiting}, 'status': 'waiting',
            'settlement_status': None}


def _form():
    return {'form_id': 'frm_1', 'wait_id': 'wait-1', 'title': '归档范围',
            'questions': [{'id': 'q1', 'prompt': '选哪些？', 'type': 'multi'}]}


def test_clarification_frame_carries_form_when_present():
    state = _LegacyFrameState()
    frames, stop = state.project(_row({
        'kind': 'clarification', 'question': '选哪些？', 'wait_id': 'wait-1',
        'form': _form()}))
    frame = next(f for f in frames if f['type'] == 'clarification')
    assert frame['form'] == _form()
    assert frame['question'] == '选哪些？'
    assert stop is True  # legacy 语义：提问帧即本轮结束，作答走下一条消息


def test_clarification_frame_without_form_stays_backward_compatible():
    state = _LegacyFrameState()
    frames, _ = state.project(_row({
        'kind': 'clarification', 'question': '哪种城市？', 'wait_id': 'wait-2'}))
    frame = next(f for f in frames if f['type'] == 'clarification')
    assert 'form' not in frame
