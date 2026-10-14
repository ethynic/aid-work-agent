"""Empty filtered results do not recognize/bill/store fallback recommendations."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.local_tools import resume_continuation as continuation
from src.local_tools.proxy_tool import BossResumeBatchTool, LocalToolProxyTool

pytestmark = pytest.mark.unit


def receipt(reason, **data):
    return {'success': True, 'effect': 'none', 'invocation_id': str(uuid4()),
        'data': {'resumes': [], 'failures': [], 'attempted': 0,
            'processed_names': [], 'job_name': '测试职位', 'stop_reason': reason, **data}}


@pytest.mark.parametrize('reason', continuation.FILTER_TERMINAL_REASONS)
def test_stop_reason_survives_empty_history_and_has_no_next_action(reason):
    metadata = continuation.batch_metadata(receipt(reason))
    assert metadata['stop_reason'] == reason
    assert metadata['continuation']['reason'] == reason
    assert metadata['continuation']['available'] is False
    assert metadata['continuation']['arguments'] is None


@pytest.mark.parametrize('path', ['legacy', 'durable'])
@pytest.mark.parametrize('reason', continuation.FILTER_TERMINAL_REASONS)
@pytest.mark.asyncio
async def test_empty_matches_are_success_without_recognition_fee_store(monkeypatch, path, reason):
    from src.local_tools import proxy_tool, domain_flow, overlay_flow, resume_flow
    import src.tools.context as context
    raw = receipt(reason)
    vl, bill, store, process = AsyncMock(), AsyncMock(), MagicMock(), AsyncMock()
    monkeypatch.setattr(proxy_tool, '_evaluate_resume_via_vl', vl)
    monkeypatch.setattr(proxy_tool, '_bill_resume_recognition', bill)
    monkeypatch.setattr(proxy_tool.recruiting_resume_service, 'create_resume_record_from_tool_result', store)
    monkeypatch.setattr(resume_flow, 'process_item', process)
    tool = BossResumeBatchTool()
    if path == 'legacy':
        monkeypatch.setattr(LocalToolProxyTool, 'execute', AsyncMock(return_value=raw))
        result = await tool._execute_legacy(_trusted_tenant_id='tenant', _trusted_user_id='user')
    else:
        monkeypatch.setattr(overlay_flow, 'run_base', AsyncMock(return_value=raw))
        monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(tenant_id='tenant'))
        operation = SimpleNamespace(branch='device', ordinal=0, owner=SimpleNamespace(saved_phase=AsyncMock()))
        result = await domain_flow.resume_result(tool, operation, {}, batch=True)
    assert result['success'] is True
    assert result['data']['resumes'] == []
    assert result['data']['attempted'] == 0
    assert result['data']['continuation']['reason'] == reason
    assert '推荐' in result['message'] and '匹配候选人' in result['message']
    vl.assert_not_called()
    bill.assert_not_called()
    store.assert_not_called()
    process.assert_not_called()


@pytest.mark.parametrize('data', [
    {'resumes': [{'candidate_name': '推荐甲', 'images': [{'base64': 'private-image'}]}]},
    {'attempted': 1}, {'attempted': False}, {'failures': [{'name': '推荐甲', 'error': 'error'}]},
    {'resumes': 7}, {'failures': 7},
])
def test_contradictory_no_match_receipt_fails_closed_without_images(data):
    raw = receipt('no_matching_candidates', **data)
    result = continuation.filter_terminal_result(raw, continuation.batch_metadata(raw))
    assert result['success'] is False and result['code'] == 'RESUME_PAYLOAD_INVALID'
    assert result['data']['resumes'] == []
    assert 'private-image' not in str(result)


def test_partial_matched_batch_at_boundary_still_processes_real_resumes():
    raw = receipt('recommendations_boundary', resumes=[{'candidate_name': '匹配甲'}],
        processed_names=['匹配甲'], attempted=1)
    metadata = continuation.batch_metadata(raw)
    assert continuation.filter_terminal_result(raw, metadata) is None
    assert metadata['continuation']['available'] is False
    assert '没有更多匹配候选人' in continuation.filter_stop_message(raw['data']['stop_reason'])


@pytest.mark.parametrize('effect', ['unknown', None, 'applied'])
def test_uncertain_effect_does_not_claim_confirmed_empty_results(effect):
    raw = {**receipt('no_matching_candidates'), 'effect': effect}
    result = continuation.filter_terminal_result(raw, continuation.batch_metadata(raw))
    assert result['success'] is False and result['code'] == 'EXECUTION_UNKNOWN'
    assert '未知' in result['message']
    assert result['data']['continuation']['available'] is False


@pytest.mark.asyncio
async def test_empty_matches_cannot_be_continued_even_with_old_excluded_names(monkeypatch):
    from src.local_tools import repository
    raw = receipt('no_matching_candidates')
    previous = {'tenant_id': 'tenant', 'user_id': 'user', 'session_id': 'session',
        'device_id': 'device', 'tool_name': 'boss_resume_batch', 'state': 'succeeded',
        'effect': 'none', 'arguments_json': {'exclude_names': ['匹配甲']}, 'result_json': raw}
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: previous)
    canonical, failure = await continuation.prepare_next({'continue_from': raw['invocation_id'],
        '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user', '_session_id': 'session'})
    assert canonical is None and failure['code'] == 'CONTINUATION_STOPPED'
    assert 'no_matching_candidates' in failure['message']
