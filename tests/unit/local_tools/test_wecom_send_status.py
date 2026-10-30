"""Only a trusted original send can authorize this separate read; no physical client or DB."""
import copy
from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.local_tools import repository, wecom_send_status as scope, pricing
from src.local_tools.proxy_tool import WecomSendStatusTool, WecomMessageSendTool, LocalToolProxyTool
from src.local_tools.overlay_flow import poll_operation, _known
from src.local_tools.durable_flow import LocalContinuationRequired
from src.services.agent_runner.local_invocations import LocalPhase
from src.services.agent_runner.local_recovery import LocalRecoveryProof, owned_invocations
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.contracts import RunnerError
from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact

pytestmark = pytest.mark.unit
SEND, STATUS, DEVICE = str(uuid4()), str(uuid4()), str(uuid4())
RECEIPT = 'send-2026-10-26T01-02-03-004Z'


def original(**changes):
    return dict(id=SEND, tenant_id='tenant', user_id='user', session_id='session', device_id=DEVICE,
        tool_name='wecom_message_send', provider_key='wecom', state='unknown', effect='unknown',
        claimed_at=datetime.now(timezone.utc),
        result_json={'success': False, 'code': 'EXECUTION_UNKNOWN', 'data': {'receipt_id': RECEIPT}}, **changes)


def lookup(source=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = original() if source is None else source
    return cursor


def status_row(**changes):
    row = dict(id=STATUS, tenant_id='tenant', user_id='user', session_id='session', device_id=DEVICE,
        tool_name=scope.TOOL, provider_key='wecom', state='queued', arguments_json={'receipt_id': RECEIPT},
        business_kind=scope.BUSINESS_KIND, business_ref={'version': 1, 'send_invocation_id': SEND})
    row.update(changes)
    return row


def test_scope_uses_owned_original_and_only_its_receipt_not_model_input():
    cursor = lookup()
    assert scope.invocation_scope(cursor, status_row()) == {
        'send_invocation_id': SEND, 'device_id': DEVICE, 'receipt_id': RECEIPT}
    assert cursor.execute.call_args.args[1] == (SEND, 'tenant', 'user', 'session')
    assert 'tenant_id=%s' in cursor.execute.call_args.args[0]


@pytest.mark.parametrize('field,value', [
    ('tenant_id', 'another'), ('user_id', 'another'), ('session_id', 'another'), ('device_id', str(uuid4())),
    ('tool_name', 'wecom_send_image'), ('provider_key', 'boss-recruiting'), ('state', 'running'),
    ('effect', 'none'), ('effect', None), ('result_json', {}),
    ('result_json', {'data': {'receipt_id': '../arbitrary'}}),
    ('result_json', {'data': {'receipt_id': RECEIPT, 'receipt_invalidation_failed': True}}),
])
def test_cross_scope_non_send_preexecution_or_missing_receipt_never_grants_read(field, value):
    source = original()
    source[field] = value
    with pytest.raises(scope.SendStatusScopeError):
        scope.invocation_scope(lookup(source), status_row())


@pytest.mark.parametrize('change', [
    {'tool_name': 'wecom_message_send'}, {'provider_key': 'boss-recruiting'}, {'business_kind': None},
    {'business_ref': {'version': True, 'send_invocation_id': SEND}},
    {'arguments_json': {'receipt_id': RECEIPT, 'text': 'never send'}},
    {'arguments_json': {'receipt_id': RECEIPT + '-2'}}, {'device_id': None},
])
def test_tool_name_or_model_supplied_binding_is_not_a_hold_bypass(change):
    with pytest.raises(scope.SendStatusScopeError):
        scope.invocation_scope(lookup(), status_row(**change))


def test_late_unknown_receipt_is_readable_without_rewriting_original_unknown():
    source = original()
    source['result_json'] = {'_late_result': {'success': False, 'code': 'EXECUTION_UNKNOWN',
        'effect': 'unknown', 'data': {'receipt_id': RECEIPT}}}
    before = copy.deepcopy(source)
    assert scope.invocation_scope(lookup(source), status_row())['receipt_id'] == RECEIPT
    assert source == before and not repository.operation_completion_known(source)
    source['result_json']['_late_result']['effect'] = 'none'
    with pytest.raises(scope.SendStatusScopeError):
        scope.invocation_scope(lookup(source), status_row())


@pytest.mark.parametrize('state', ['failed', 'cancelled'])
def test_cancelled_after_possible_input_can_be_read_without_resuming_or_releasing_it(state):
    source = original()
    source.update(state=state, effect='partial', error_code='CANCELLED')
    before = copy.deepcopy(source)
    assert scope.invocation_scope(lookup(source), status_row())['receipt_id'] == RECEIPT
    assert source == before and not repository.operation_completion_known(source)
    source['effect'] = 'none'
    with pytest.raises(scope.SendStatusScopeError):
        scope.invocation_scope(lookup(source), status_row())


def test_only_original_send_hold_is_bypassed_other_unknown_holds_still_block():
    hold = {**original(), 'checkpoint': {'detached_local_invocations': [SEND]}}
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = [hold]
    before = copy.deepcopy(hold)
    proof = scope.invocation_scope(lookup(), status_row())
    repository.assert_device_operations_settled(cursor, 'tenant', DEVICE, send_status=proof)
    assert hold == before
    with pytest.raises(repository.DeviceOperationUnresolved):
        repository.assert_device_operations_settled(cursor, 'tenant', DEVICE)
    other = str(uuid4())
    cursor.fetchall.return_value = [hold, {**hold, 'id': other, 'checkpoint': {'detached_local_invocations': [other]}}]
    with pytest.raises(repository.DeviceOperationUnresolved):
        repository.assert_device_operations_settled(cursor, 'tenant', DEVICE, send_status=proof)


def test_zero_price_is_enforced_at_write_result_price_source_even_with_paid_defaults(monkeypatch):
    monkeypatch.setattr(pricing.settings, 'boss_tool_billing', SimpleNamespace(enabled=True,
        default_credit_price=99, tool_credit_prices={'wecom_send_status': 10}))
    assert pricing.tool_credit_price(scope.TOOL) == 0
    assert WecomSendStatusTool()._tool_credit_price() == 0


async def test_proxy_dispatch_translates_cloud_id_but_never_calls_the_sender():
    tool = WecomSendStatusTool()
    proof = {'send_invocation_id': SEND, 'device_id': DEVICE, 'receipt_id': RECEIPT}
    result = {'success': True, 'effect': 'none', 'data': {'send_status': 'unknown'}}
    with patch.object(scope, 'resolve_original', return_value=proof), \
         patch('src.local_tools.service.LocalInvocationService.enqueue', return_value=status_row()) as enqueue, \
         patch.object(tool, '_poll_invocation', AsyncMock(return_value=result)):
        actual = await tool._dispatch_and_wait('tenant', 'user', 'session', {'id': DEVICE},
            {'send_invocation_id': SEND}, None)
    assert actual is result and actual['data']['send_status'] == 'unknown'
    assert enqueue.call_args.kwargs['tool_name'] == scope.TOOL
    assert enqueue.call_args.kwargs['arguments'] == {'receipt_id': RECEIPT}
    assert enqueue.call_args.kwargs['business_ref']['send_invocation_id'] == SEND
    assert WecomSendStatusTool.execute is LocalToolProxyTool.execute
    with pytest.raises(LocalContinuationRequired):
        _known({'success': False, 'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown'}, WecomMessageSendTool())


def recovery_fixture():
    row = dict(runner_id=str(uuid4()), tenant_id='tenant', user_id='user', session_id='session',
        source='chat', session_kind='web', attempt=1)
    state = ExecutionState(Identity('tenant', 'user', 'session'), row['runner_id'], AgentMode.MASTER, '', [])
    call = ToolCall('status-call', scope.TOOL, {'send_invocation_id': SEND})
    state.tools[call.id] = ToolFact(call, phase='waiting', invocation_id=STATUS)
    phase = LocalPhase(state.execution_id, call.id, 'main', 0)
    reference = dict(runner_id=row['runner_id'], execution_id=state.execution_id,
        tool_call_id=call.id, branch='main', ordinal=0)
    request = dict(tool_name=scope.TOOL, arguments={'receipt_id': RECEIPT}, device_id=DEVICE,
        provider_key='wecom', deadline_at=None, authorization_epoch=None, execution_lane='standard')
    ref = dict(reference, invocation_id=STATUS, request=request)
    state.resources['local_invocations'] = {phase.key(row['runner_id']): ref}
    invocation = status_row(state='succeeded', effect='none', result_json={'success': True,
        'data': {'send_status': 'unknown'}}, business_kind='agent_runner', business_ref=reference,
        dedupe_key=phase.key(row['runner_id']), deadline_at=None, authorization_epoch=None, execution_lane='standard')
    row['checkpoint'] = {'execution': state.checkpoint()}
    return row, state, call, phase, ref, invocation


@contextmanager
def source_connection(source):
    connection = MagicMock()
    connection.cursor.return_value = lookup(source)
    yield connection


def test_restart_proof_and_cancel_ownership_keep_original_status_invocation():
    row, state, call, phase, ref, invocation = recovery_fixture()
    with patch.object(repository, 'get_invocation', return_value=invocation) as load:
        proof = LocalRecoveryProof(lambda: source_connection(original()))
        assert proof.can_restore(row, state, state.tools[call.id])
    assert load.call_args.args == (STATUS, 'tenant')
    RunnerLocalLifecycle.validate_invocation(state.identity, row['runner_id'], phase, ref, invocation)
    assert [saved['invocation_id'] for _, _, saved in owned_invocations(state.checkpoint(), row['runner_id'])] == [STATUS]


@pytest.mark.parametrize('tamper', ['cloud', 'source', 'device', 'native', 'phase', 'owner'])
def test_restart_proof_rejects_tampered_cloud_source_device_native_and_phase(tamper):
    row, state, call, phase, ref, invocation = recovery_fixture()
    source = original()
    if tamper == 'cloud':
        state.tools[call.id].call = ToolCall(call.id, scope.TOOL, {'send_invocation_id': str(uuid4())})
    elif tamper == 'source': source['user_id'] = 'another'
    elif tamper == 'device': ref['request']['device_id'] = str(uuid4())
    elif tamper == 'native': invocation['arguments_json'] = {'receipt_id': RECEIPT + '-2'}
    elif tamper == 'phase': ref['branch'] = 'heal.retry'
    elif tamper == 'owner': invocation['user_id'] = 'another'
    with patch.object(repository, 'get_invocation', return_value=invocation):
        with pytest.raises(RunnerError):
            LocalRecoveryProof(lambda: source_connection(source)).can_restore(row, state, state.tools[call.id])


async def test_recovered_status_polls_saved_row_without_enqueuing_any_send_or_status():
    row, state, call, phase, ref, invocation = recovery_fixture()
    owner = SimpleNamespace(saved_phase=AsyncMock(return_value={'request': ref['request'], 'invocation': invocation}))
    operation = SimpleNamespace(owner=owner, branch='main', ordinal=0)
    tool = WecomSendStatusTool()
    terminal = {'success': True, 'effect': 'none', 'data': {'send_status': 'unknown'}}
    with patch.object(scope, 'resolve_original', return_value={'receipt_id': RECEIPT}), \
         patch.object(tool, '_poll_invocation', AsyncMock(return_value=terminal)) as poll, \
         patch.object(tool, '_dispatch_and_wait', AsyncMock(side_effect=AssertionError('must not enqueue'))):
        result = await poll_operation(tool, operation, call.arguments, tenant_id='tenant', user_id='user',
            session_id='session', progress_queue=None)
    assert result is terminal
    assert poll.call_args.args[0]['id'] == STATUS


def test_durable_status_scope_reads_exact_saved_cloud_call_and_rejects_wrong_branch():
    row, state, call, phase, ref, invocation = recovery_fixture()
    cursor = MagicMock()
    cursor.fetchone.side_effect = [dict(checkpoint=row['checkpoint'], user_id='user', session_id='session'), original()]
    assert scope.invocation_scope(cursor, invocation, dispatch=True)['receipt_id'] == RECEIPT
    invocation['business_ref']['branch'] = 'heal.retry'
    with pytest.raises(scope.SendStatusScopeError):
        scope.invocation_scope(cursor, invocation)


def test_claim_cannot_borrow_a_saved_cloud_call_without_its_exact_status_phase_binding():
    row, state, call, phase, ref, invocation = recovery_fixture()
    invocation['id'] = str(uuid4())
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(checkpoint=row['checkpoint'], user_id='user', session_id='session')
    with pytest.raises(scope.SendStatusScopeError):
        scope.invocation_scope(cursor, invocation, dispatch=True)


class AdmissionDatabase:
    """Record only SQL boundaries; execute actual source/hold/enqueue/claim/start policies."""
    def __init__(self):
        self.source = original()
        self.status = status_row()
        self.holds = [{**original(), 'checkpoint': {'detached_local_invocations': [SEND]}}]
        self.statements, self.selected = [], None
        self.connection = MagicMock()
        self.connection.cursor.return_value = self

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if 'SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s' in sql and 'user_id=%s' in sql:
            self.selected = self.source
        elif 'SELECT i.id FROM local_tool_invocations' in sql:
            self.selected = None
        elif 'SELECT i.id,i.state' in sql:
            self.selected = self.holds
        elif 'INSERT INTO local_tool_invocations' in sql:
            self.status.update(tenant_id=params[0], user_id=params[1], device_id=params[2],
                tool_name=params[3], arguments_json=params[4].adapted, session_id=params[5],
                provider_key=params[6], business_kind=params[7], business_ref=params[8].adapted)
            self.selected = {'id': STATUS}
        elif "SET state='failed',effect='none'" in sql:
            self.status.update(state='failed', effect='none', error_code='WECOM_SEND_SCOPE_INVALID',
                result_json=params[1].adapted)
            self.selected = {'id': STATUS}
        elif 'INSERT INTO local_tool_events' in sql:
            self.selected = None
        elif 'SELECT * FROM local_tool_invocations' in sql:
            self.selected = self.status
        elif "SET state = 'claimed'" in sql:
            self.status['state'] = 'claimed'
            self.selected = self.status
        elif "SET state = 'running'" in sql:
            self.status['state'] = 'running'
            self.selected = self.status
        else:
            raise AssertionError('unexpected SQL boundary')

    def fetchone(self):
        return self.selected

    def fetchall(self):
        return self.selected

    @contextmanager
    def connect(self):
        yield self.connection


def enqueue(database):
    return repository.create_invocation_in_tx(database, 'tenant', 'user', DEVICE, scope.TOOL,
        {'receipt_id': RECEIPT}, 'session', provider_key='wecom', business_kind=scope.BUSINESS_KIND,
        business_ref={'version': 1, 'send_invocation_id': SEND})


def test_actual_admission_chain_rechecks_scope_at_enqueue_claim_and_start_without_releasing_hold():
    database = AdmissionDatabase()
    before = copy.deepcopy(database.holds)
    with patch.object(repository, 'get_db_connection', database.connect), \
         patch.object(repository, 'lock_operation_admission'), patch.object(repository, 'lock_active_device', return_value=True):
        assert enqueue(database) == STATUS
        assert repository.claim_next(DEVICE, 'tenant', 'claim', 60, ['wecom'])['id'] == STATUS
        assert repository.mark_started(STATUS, 'tenant', 'claim')['state'] == 'running'
    source_reads = [sql for sql, _ in database.statements if 'user_id=%s AND session_id=%s' in sql]
    assert len(source_reads) == 3
    assert database.holds == before
    writes = [sql for sql, _ in database.statements if sql.lstrip().startswith(('INSERT', 'UPDATE'))]
    assert len(writes) == 3 and all('agent_runners' not in sql for sql in writes)


@pytest.mark.parametrize('stage', ['enqueue', 'claim', 'start'])
def test_source_invalidated_before_any_admission_stage_blocks_without_native_dispatch(stage):
    database = AdmissionDatabase()
    with patch.object(repository, 'get_db_connection', database.connect), \
         patch.object(repository, 'lock_operation_admission'), patch.object(repository, 'lock_active_device', return_value=True):
        if stage != 'enqueue': enqueue(database)
        if stage == 'start': repository.claim_next(DEVICE, 'tenant', 'claim', 60, ['wecom'])
        database.source['result_json']['data']['receipt_invalidation_failed'] = True
        count = len(database.statements)
        if stage == 'enqueue':
            with pytest.raises(repository.DeviceOperationUnresolved):
                enqueue(database)
            assert not any(sql.lstrip().startswith(('INSERT', 'UPDATE')) for sql, _ in database.statements[count:])
        else:
            actual = repository.claim_next(DEVICE, 'tenant', 'claim', 60, ['wecom']) if stage == 'claim' else repository.mark_started(STATUS, 'tenant', 'claim')
            assert actual is None
            assert database.status['state'] == 'failed' and database.status['effect'] == 'none'
            assert database.status['result_json']['code'] == 'WECOM_SEND_SCOPE_INVALID'
            assert any('INSERT INTO local_tool_events' in sql for sql, _ in database.statements[count:])


async def test_restart_source_changed_after_preflight_returns_verification_without_poll_or_dispatch():
    row, state, call, phase, ref, invocation = recovery_fixture()
    operation = SimpleNamespace(owner=SimpleNamespace(saved_phase=AsyncMock(return_value={
        'request': ref['request'], 'invocation': invocation})), branch='main', ordinal=0)
    tool = WecomSendStatusTool()
    with patch.object(scope, 'resolve_original', side_effect=scope.SendStatusScopeError()), \
         patch.object(tool, '_poll_invocation', AsyncMock(side_effect=AssertionError('no poll'))):
        with pytest.raises(LocalContinuationRequired):
            await poll_operation(tool, operation, call.arguments, tenant_id='tenant', user_id='user',
                session_id='session', progress_queue=None)


@pytest.mark.parametrize('state', ['running', 'succeeded', 'unknown'])
def test_late_started_request_cannot_reclassify_an_already_dispatched_status_as_none(state):
    database = AdmissionDatabase()
    database.status.update(state=state, effect='unknown', started_at=datetime.now(timezone.utc))
    database.source['effect'] = 'none'
    before = copy.deepcopy(database.status)
    with patch.object(repository, 'get_db_connection', database.connect), \
         patch.object(repository, 'lock_operation_admission'), patch.object(repository, 'lock_active_device', return_value=True):
        assert repository.mark_started(STATUS, 'tenant', 'claim') is None
    assert database.status == before
    assert not any(sql.lstrip().startswith(('INSERT', 'UPDATE')) for sql, _ in database.statements)


def test_an_unrelated_hold_during_claim_is_not_misclassified_as_an_invalid_status_source():
    database = AdmissionDatabase()
    with patch.object(repository, 'get_db_connection', database.connect), \
         patch.object(repository, 'lock_operation_admission'), patch.object(repository, 'lock_active_device', return_value=True):
        enqueue(database)
        other = str(uuid4())
        database.holds.append({**original(), 'id': other, 'checkpoint': {'detached_local_invocations': [other]}})
        before = copy.deepcopy(database.status)
        with pytest.raises(repository.DeviceOperationUnresolved):
            repository.claim_next(DEVICE, 'tenant', 'claim', 60, ['wecom'])
    assert database.status == before


def rejected_fixture():
    row, state, call, phase, ref, invocation = recovery_fixture()
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(checkpoint=row['checkpoint'], user_id='user', session_id='session')
    marker = scope.no_dispatch_marker(cursor, invocation)
    invocation.update(state='failed', effect='none', started_at=None, error_code='WECOM_SEND_SCOPE_INVALID',
        result_json={'success': False, 'code': 'WECOM_SEND_SCOPE_INVALID', 'retryable': False,
                     'data': None, scope.NO_DISPATCH: marker})
    return row, state, call, phase, ref, invocation


async def test_server_no_dispatch_failure_is_consumed_after_restart_without_rechecking_invalid_source():
    row, state, call, phase, ref, invocation = rejected_fixture()
    with patch.object(repository, 'get_invocation', return_value=invocation), \
         patch.object(scope, 'original_scope', side_effect=AssertionError('must not re-query invalid source')):
        assert LocalRecoveryProof(lambda: source_connection({})).can_restore(row, state, state.tools[call.id])
    tool = WecomSendStatusTool()
    operation = SimpleNamespace(owner=SimpleNamespace(saved_phase=AsyncMock(return_value={
        'request': ref['request'], 'invocation': invocation})), branch='main', ordinal=0)
    with patch.object(tool, 'native_arguments', AsyncMock(side_effect=AssertionError('no source query'))), \
         patch.object(tool, '_dispatch_and_wait', AsyncMock(side_effect=AssertionError('no new dispatch'))), \
         patch.object(tool, '_poll_invocation', AsyncMock(return_value=tool._map_terminal(invocation))):
        result = await poll_operation(tool, operation, call.arguments, tenant_id='tenant', user_id='user',
            session_id='session', progress_queue=None)
    assert result['code'] == 'WECOM_SEND_SCOPE_INVALID' and result['effect'] == 'none'


@pytest.mark.parametrize('tamper', ['borrowed', 'id', 'native', 'version', 'nested', 'missing', 'cloud', 'business_ref'])
def test_only_exact_server_no_dispatch_marker_can_replace_a_source_read(tamper):
    row, state, call, phase, ref, invocation = rejected_fixture()
    arguments = call.arguments
    if tamper == 'borrowed': invocation['result_json'][scope.NO_DISPATCH]['invocation_id'] = str(uuid4())
    elif tamper == 'id': invocation['id'] = str(uuid4())
    elif tamper == 'native': invocation['result_json'][scope.NO_DISPATCH]['native_request']['arguments']['receipt_id'] += '-2'
    elif tamper == 'version': invocation['result_json'][scope.NO_DISPATCH]['version'] = True
    elif tamper == 'nested': invocation['result_json']['data'] = {scope.NO_DISPATCH: invocation['result_json'].pop(scope.NO_DISPATCH)}
    elif tamper == 'missing': invocation['result_json'].pop(scope.NO_DISPATCH)
    elif tamper == 'cloud': arguments = {'send_invocation_id': str(uuid4())}
    else: invocation['business_ref'] = {**invocation['business_ref'], 'tool_call_id': 'another-call'}
    assert not scope.rejected_before_dispatch(invocation, arguments, ref['request'])


def test_a_private_failure_marker_never_skips_existing_phase_and_subject_ownership_checks():
    row, state, call, phase, ref, invocation = rejected_fixture()
    invocation['user_id'] = 'another-user'
    with patch.object(repository, 'get_invocation', return_value=invocation):
        with pytest.raises(RunnerError):
            LocalRecoveryProof(lambda: source_connection({})).can_restore(row, state, state.tools[call.id])


def test_only_the_complete_private_rejection_proves_the_physical_read_finished():
    row, state, call, phase, ref, invocation = rejected_fixture()
    before = copy.deepcopy(invocation)
    assert repository.operation_completion_known(invocation)
    assert invocation == before
    assert not repository.operation_completion_known(original())
    assert 'WECOM_SEND_SCOPE_INVALID' not in repository.COMPLETED_NONE_RESULT_CODES


@pytest.mark.parametrize('tamper', ['borrowed', 'native', 'business_ref', 'version', 'nested', 'missing',
    'started', 'succeeded', 'unknown', 'original_send', 'missing_started_column'])
def test_completion_proof_cannot_be_borrowed_or_faked_by_client_error_data(tamper):
    _, _, _, _, _, invocation = rejected_fixture()
    if tamper == 'borrowed': invocation['id'] = str(uuid4())
    elif tamper == 'native': invocation['arguments_json'] = {'receipt_id': RECEIPT + '-2'}
    elif tamper == 'business_ref': invocation['business_ref'] = {**invocation['business_ref'], 'tool_call_id': 'different'}
    elif tamper == 'version': invocation['result_json'][scope.NO_DISPATCH]['version'] = True
    elif tamper == 'nested': invocation['result_json']['data'] = {scope.NO_DISPATCH: invocation['result_json'].pop(scope.NO_DISPATCH)}
    elif tamper == 'missing': invocation['result_json'].pop(scope.NO_DISPATCH)
    elif tamper == 'started': invocation['started_at'] = datetime.now(timezone.utc)
    elif tamper == 'missing_started_column': invocation.pop('started_at')
    elif tamper == 'original_send': invocation['tool_name'] = 'wecom_message_send'
    else: invocation['state'] = tamper
    assert not repository.operation_completion_known(invocation)
