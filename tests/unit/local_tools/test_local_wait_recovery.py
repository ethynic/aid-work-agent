"""Lease loss must not discard receipts or turn user dialogue into GUI replay."""

import pytest

from src.local_tools import repository
from src.services.agent_runner.local_recovery import cancel_completion_status
from src.services.agent_runner.public_view import local_recovery_applicable
from test_write_result_billing import _run_write_result, HASH, INVOCATION_ID, TENANT, DEVICE


def lost_row(**overrides):
    return {'id': INVOCATION_ID, 'tenant_id': TENANT, 'device_id': DEVICE,
        'user_id': 'user', 'session_id': 'session', 'tool_name': 'boss_greet',
        'arguments_json': {}, 'state': 'unknown', 'effect': 'unknown',
        'result_json': None, 'error_code': 'LEASE_LOST', 'error_message': 'Original lease lost',
        'claim_token_hash': HASH, 'credit_cost': None, **overrides}


def test_late_known_completion_preserves_unknown_and_bills_original_once():
    original = lost_row()
    _, conn, ledger, _ = _run_write_result(original, original)
    ledger.assert_called_once()
    sql, values = conn.cursor_obj.updates()[-1]
    assert values[0:2] == ('unknown', 'unknown')
    assert values[3:5] == ('LEASE_LOST', 'Original lease lost')
    late = values[2].adapted['_late_result']
    assert late['success'] is True and late['effect'] == 'applied' and late['stopped'] is True
    persisted = {**original, 'result_json': {'_late_result': late}, 'credit_cost': 1}
    _, duplicate, ledger, _ = _run_write_result(persisted, persisted)
    ledger.assert_not_called()
    assert duplicate.cursor_obj.updates() == []
    observed = repository.observed_invocation(persisted)
    assert observed['state'] == 'succeeded' and persisted['state'] == 'unknown'


@pytest.mark.parametrize('success,code,effect,known', [
    (True, None, 'none', True), (True, None, 'applied', True),
    (True, None, 'unknown', False), (False, 'NOT_LOGGED_IN', 'none', True),
    (False, 'CANCELLED', 'none', False), (False, 'INTERNAL_ERROR', 'none', False),
    (False, 'EXECUTION_UNKNOWN', 'unknown', False), (False, 'NOT_LOGGED_IN', 'unknown', False),
])
def test_only_completed_or_preexecution_result_releases_desktop(success, code, effect, known):
    assert repository.result_proves_completion(success, code, effect) is known


@pytest.mark.parametrize('late', [False, True])
def test_next_login_failure_with_no_business_payload_can_retry_only_known_none(late):
    receipt = {'success':False, 'code':'NOT_LOGGED_IN', 'effect':'none', 'stopped':True}
    invocation = {'state':'failed', 'effect':'none', 'result_json':receipt}
    if late:
        invocation.update(state='unknown', effect='unknown', result_json={'_late_result':receipt})
    assert repository.continuation_retry_safe(invocation)
    receipt['data'] = {'resumes':[{'name':'already_read'}]}
    assert not repository.continuation_retry_safe(invocation)
    receipt.pop('data')
    if late:
        receipt['code'] = 'CANCELLED'
    else:
        invocation['effect'] = 'unknown'
    assert not repository.continuation_retry_safe(invocation)


@pytest.mark.parametrize('code,legacy_stopped,completed', [
    ('WRONG_PAGE',False,True), ('INVALID_ARGUMENT',False,True),
    ('CANCELLED',True,False), ('INTERNAL_ERROR',True,False),
])
def test_stored_late_boolean_cannot_override_current_structured_completion_evidence(code,legacy_stopped,completed):
    raw = lost_row(result_json={'_late_result':{'success':False,'code':code,'effect':'none','stopped':legacy_stopped}})
    observed = repository.observed_invocation(raw)
    assert (observed['state'] == 'failed') is completed
    assert repository.operation_completion_known(raw) is completed
    assert raw['state'] == raw['effect'] == 'unknown'


def test_unknown_device_receipt_never_overwrites_a_prior_authoritative_result():
    explicit = lost_row(result_json={'success': False, 'code': 'EXECUTION_UNKNOWN'})
    _, conn, ledger, _ = _run_write_result(explicit, explicit)
    assert conn.cursor_obj.updates() == []
    ledger.assert_not_called()
    still_unknown = lost_row(result_json={'_late_result': {'success': False,
        'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown', 'stopped': False}})
    assert repository.observed_invocation(still_unknown) is still_unknown


def test_detach_changes_cloud_wait_only_and_does_not_release_browser_or_domain_unknown():
    local = {'kind': 'local', 'state': 'unknown', 'effect': 'unknown'}
    assert cancel_completion_status([local])[0] is False
    assert cancel_completion_status([local], detached_local_wait=True)[0] is True
    assert cancel_completion_status([local, {'kind': 'domain', 'known': False}], detached_local_wait=True)[0] is False
    assert cancel_completion_status([{'state':'unknown','effect':'unknown'}], detached_local_wait=True)[0] is False


def test_only_pending_local_operation_offers_recovery_not_historical_local_or_ask_user():
    node = {'tools': {'call': {'phase':'waiting'}}, 'resources': {
        'local_invocations': {'key': {'tool_call_id':'call'}}}}
    row = {'session_kind':'web', 'source':'chat', 'status':'interrupted',
        'worker_id': None, 'lease_until': None, 'checkpoint': {'execution':node}}
    assert local_recovery_applicable(row)
    row['cancel_requested'] = True
    assert local_recovery_applicable(row)
    row['checkpoint']['detached_local_wait'] = True
    assert not local_recovery_applicable(row)
    row['checkpoint'].pop('detached_local_wait')
    node['tools']['call']['phase'] = 'completed'
    assert not local_recovery_applicable(row)
    node['tools']['call']['phase'] = 'waiting'
    node['waiting'] = {'kind':'clarification'}
    assert not local_recovery_applicable(row)


@pytest.mark.parametrize('receipt,still_held', [
    ({'_late_result': {'success': True, 'effect': 'none'}}, False),
    ({'_late_result': {'success': False, 'code': 'WRONG_PAGE', 'effect': 'none'}}, False),
    ({'_late_result': {'success': False, 'code': 'CANCELLED', 'effect': 'none'}}, True),
    (None, True),
])
def test_public_verification_projection_uses_actual_receipts_without_modifying_frozen_history(receipt, still_held):
    from unittest.mock import Mock
    import copy
    from src.services.agent_runner.public_view import with_local_operation_facts
    row = {'tenant_id': TENANT, 'user_id': 'user', 'session_id': 'session',
        'checkpoint': {'detached_local_wait': True, 'detached_local_invocations': [INVOCATION_ID]}}
    original = copy.deepcopy(row)
    cursor = Mock()
    cursor.fetchall.return_value = [lost_row(result_json=receipt)]
    projected = with_local_operation_facts(cursor, row)
    assert bool(projected['_unresolved_local_invocations']) is still_held
    assert row == original, 'a GET projection cannot erase frozen IDs or change unknown audit history'
    assert cursor.execute.call_count == 1
    assert cursor.execute.call_args.args[0].startswith('SELECT ')
    assert cursor.execute.call_args.args[1][:3] == (TENANT, 'user', 'session')
