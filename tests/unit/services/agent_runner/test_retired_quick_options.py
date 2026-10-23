"""旧快捷选项已停用：任意工具与历史投影都不能恢复旧按钮。"""

import copy

import pytest

from src.services.agent_runner.presentation import progress_display, project_user_event
from src.services.agent_runner.public_view import _public_snapshot
from src.api.message_projection import public_conversation_messages


pytestmark = pytest.mark.unit

OPTIONS = [{'key': 'job-a', 'label': '岗位甲'}, {'key': 'job-b', 'label': '岗位乙'}]


@pytest.mark.parametrize('tool_name', ['boss_jobs_list', 'present_options', 'future_tool', None])
def test_tool_results_preserve_facts_without_creating_shortcuts(tool_name):
    event = {'type': 'tool_result', 'toolName': tool_name, 'toolCallId': 'call-1',
             'success': True, 'result': {'success': True, 'data': {'options': OPTIONS}}}
    original = copy.deepcopy(event)
    snapshot = {}

    project_user_event(snapshot, event)

    assert 'quickOptions' not in snapshot
    assert progress_display(event)['result']['data']['options'] == OPTIONS
    assert event == original


def test_old_snapshot_options_are_ignored_without_rewriting_stored_facts():
    snapshot = {'output': '历史正文', 'quickOptions': OPTIONS}
    original = copy.deepcopy(snapshot)
    assert _public_snapshot(snapshot) == {'output': '历史正文'}
    assert snapshot == original


@pytest.mark.parametrize('runner_metadata', [{}, {'runner_id': 'runner-1'}])
def test_history_never_exposes_retired_options_or_mutates_source(runner_metadata):
    rows = [{'message_id': 'assistant-1', 'role': 'assistant', 'content': '历史正文',
             'metadata': {**runner_metadata, 'quickOptions': OPTIONS}}]
    original = copy.deepcopy(rows)
    history = public_conversation_messages(rows)
    assert 'quickOptions' not in history[0]['metadata']
    assert history[0]['content'] == '历史正文'
    assert rows == original
