"""Independent business invariants against a disposable PostgreSQL database."""
import copy
import json
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from src.config.settings import AgentRunnerConfig
from src.core.agent_engine.contracts import AgentMode, ExecutionState, ToolCall, ToolFact
from src.core.agent_events import _truncate_tool_content
from src.services.agent_runner.contracts import canonical_json
from src.services.agent_runner.continuous_context import prepare_compaction
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.continuous_history import archive_and_compact
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.usage_pricing import grouped_cost
from src.services.agent_runner.worker import RunnerWorker
from .test_storage import storage, attempt
from .test_usage_storage import ledger, prices, started
from .test_continuous_settlement import active
from .test_durable_sleep_boundaries import claimed_timer
from .test_durable_sleep_worker_independent import service_processes, api_pair
from .test_worker import workers, accept, runner, decoded
from .provider import Reply, tool_reply
from .test_api import require_status
from .conftest import wait_for
from .test_web_gateway import gateways

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('next_action', ['resume', 'cancel'])
def test_real_worker_postclaim_balance_race_preserves_then_continues_or_cancels_original_task(
        workers, actors, service_database, tmp_path, monkeypatch, next_action):
    """The gate changes timing only: real CLI, authorizer, Engine and ledger run."""
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    marker = workers.provider.register(tool_reply('agent_sleep', {'seconds': 1}, call_id='same-provider-id'),
        Reply(content='continued original consumed sleep exactly once'))
    accepted = accept(workers.api, actors['a'], marker)
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    sleeping = runner(service_database, accepted['runner_id'])
    assert sleeping['status'] == 'waiting'
    original = decoded(sleeping['checkpoint'])
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
        (accepted['runner_id'],))[0]['due'], timeout=10)
    original_start = workers.processes.start
    gate_script = '''
import asyncio, os, signal, sys, time
from pathlib import Path
if os.name=='nt':
    def register(self, signum, callback, *args):
        signal.signal(signum,lambda *_:self.call_soon_threadsafe(callback,*args))
    asyncio.ProactorEventLoop.add_signal_handler=register
from src.services.agent_runner.worker import RunnerWorker, main
original_execute=RunnerWorker.execute
async def gated_execute(self,row):
    if (row.get('checkpoint') or {}).get('timer_activation'):
        directory=Path(os.environ['AID_TEST_POSTCLAIM_GATE'])
        (directory/'arrived').write_text('owned')
        until=time.monotonic()+15
        while not (directory/'release').exists():
            if time.monotonic()>until: raise RuntimeError('INDEPENDENT_POSTCLAIM_GATE_TIMEOUT')
            await asyncio.sleep(.01)
    return await original_execute(self,row)
RunnerWorker.execute=gated_execute
sys.argv=['worker',*sys.argv[1:]]
main()
'''
    def start(arguments, **options):
        if arguments[:2] == ['-m', 'src.services.agent_runner.worker']:
            arguments = ['-c', gate_script, *arguments[2:]]
        return original_start(arguments, **options)
    monkeypatch.setattr(workers.processes, 'start', start)
    workers.environment['AID_TEST_POSTCLAIM_GATE'] = str(tmp_path)
    claimed, _ = workers.start()
    try:
        wait_for(lambda: (tmp_path / 'arrived').exists(), timeout=20)
        service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (actors['a'].tenant_id,))
    finally:
        (tmp_path / 'release').write_text('continue')
    workers.assert_clean_exit(claimed)
    paused = runner(service_database, accepted['runner_id'])
    preserved = decoded(paused['checkpoint'])
    assert paused['status'] == 'paused' and paused['wake_at'] is None
    assert preserved['durable_sleep']['phase'] == 'consumed'
    assert len(workers.provider.requests(marker)) == 1
    assert paused['record_id'] == sleeping['record_id'] and paused['input_digest'] == sleeping['input_digest']
    if next_action == 'resume':
        service_database.rows('UPDATE tenants SET credit_balance=1000 WHERE tenant_id=%s', (actors['a'].tenant_id,))
        require_status(workers.api.call('POST', '/v1/runners/' + accepted['runner_id'] + '/controls', actor=actors['a'],
            json={'client_request_id': 'independent-funded-resume', 'action': 'resume'}), 202)
    else:
        require_status(workers.api.call('POST', '/v1/runners/' + accepted['runner_id'] + '/cancel', actor=actors['a']), 200)
    following, _ = workers.start()
    workers.assert_clean_exit(following)
    final = runner(service_database, accepted['runner_id'])
    assert final['status'] == ('completed' if next_action == 'resume' else 'cancelled')
    assert len(workers.provider.requests(marker)) == (2 if next_action == 'resume' else 1)
    current = decoded(final['checkpoint'])
    sleep_id = original['durable_sleep']['tool_call_id']
    assert current['execution']['tools'][sleep_id]['result'] == preserved['execution']['tools'][sleep_id]['result']
    if next_action == 'resume':
        assert len([message for message in current['execution']['messages'] if message.get('tool_call_id') == sleep_id]) == 1
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (accepted['runner_id'],)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['CREDIT_BLOCKED', 'USER_UNAUTHORIZED', 'TENANT_UNAVAILABLE',
    'TENANT_EXPIRED', 'SUBSCRIPTION_REQUIRED', 'PROFILE_FORBIDDEN'])
async def test_postclaim_admission_denial_retains_consumed_leaf_and_original_session_claim(
        storage, actors, service_database, monkeypatch, tmp_path, code):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner, 'durable_sleep_enabled', True)
    row, principal, coordinator = claimed_timer(storage, actors['a'], service_database, child=True)
    before = copy.deepcopy(row['checkpoint'])
    factory = SimpleNamespace(resource_directory=tmp_path, create=AsyncMock())
    def deny(*args, **kwargs):
        raise RunnerError(code, 402 if code == 'CREDIT_BLOCKED' else 403)
    worker = RunnerWorker(AgentRunnerConfig(enabled=True), worker_id=row['worker_id'],
        execution_repository=storage[1], repository=storage[0], usage_repository=SimpleNamespace(),
        runtime_factory=factory, authorizer=SimpleNamespace(authorize_persisted=deny),
        finalizer=RunnerFinalizer(service_database.connect))
    monkeypatch.setattr(worker, '_persist_trace', lambda *args: None)
    await worker.execute(row)
    paused = storage[0].get(row['runner_id'])
    assert paused['status'] == 'paused' and paused['wake_at'] is None
    assert paused['checkpoint']['durable_sleep'] == before['durable_sleep']
    assert paused['checkpoint']['execution']['children'] == before['execution']['children']
    assert paused['checkpoint']['execution']['resources']['admission_blocked'] == code
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s',
        (row['session_id'],)) == [{'owner_runner_id': row['runner_id']}]
    assert service_database.rows('SELECT record_id FROM chat_records WHERE record_id=%s', (row['record_id'],)) == []
    factory.create.assert_not_called()


def test_integer_upgrade_and_real_receipt_settlement_survive_large_counters_and_late_fact_retries(
        active, service_database):
    ledger, finalizer, row = active
    columns = ['total_token_count', 'prompt_tokens', 'completion_tokens', 'cached_input_tokens',
        'agent_iterations', 'duration_ms', 'embedding_tokens', 'asr_calls']
    # A real schema transition, inside an exclusively owned disposable database.
    with service_database.connect() as connection:
        cursor = connection.cursor()
        cursor.execute('ALTER TABLE chat_records ' + ','.join(f'ALTER COLUMN {name} TYPE INTEGER' for name in columns))
        migration = next(item['statements'] for item in yaml.safe_load(
            (Path(__file__).resolve().parents[3] / 'deploy/db_update.yaml').read_text(encoding='utf8'))
            if item['datetime'] == '2026-10-22 23:10:00')
        cursor.execute(migration)
        cursor.execute(migration)
    size = 2**31 + 41
    checkpoint = copy.deepcopy(row['checkpoint'])
    checkpoint.update(execution={'iteration': size}, execution_duration_ms=size)
    current = ledger[1].save_checkpoint(attempt(row), row['revision'], checkpoint)
    first = started(ledger, call='independent-huge-first')
    late = started(ledger, call='independent-late-original')
    initial_balance = Decimal(str(service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
        (row['tenant_id'],))[0]['credit_balance']))
    first_usage = {'prompt_tokens': size, 'completion_tokens': size + 7, 'cached_tokens': size - 2}
    ledger[2].observe(attempt(row), first['receipt_id'], first_usage)
    active_row = finalizer.settle_active(attempt(row))
    finalizer.settle_active(attempt(row))
    staged = ledger[1].stage_finalization(attempt(row), active_row['revision'], active_row['checkpoint'],
        {'status': 'completed', 'output': 'same original task', 'messages': [], 'images': [], 'duration_ms': size},
        active_row['public_snapshot'])
    finalizer.finalize(attempt(staged))
    ledger[2].observe(attempt(row), late['receipt_id'], {'prompt_tokens': 99, 'completion_tokens': 1})
    finalizer.settle_pending_usage(row['runner_id'])
    finalizer.settle_pending_usage(row['runner_id'])
    ledger[2].observe(attempt(row), late['receipt_id'], {'prompt_tokens': 99, 'completion_tokens': 1})
    finalizer.settle_pending_usage(row['runner_id'])
    receipts = service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (row['runner_id'],))
    expected, totals, _ = grouped_cost(receipts)
    record = service_database.rows('SELECT * FROM chat_records WHERE record_id=%s', (row['record_id'],))[0]
    assert record['agent_iterations'] == size and record['duration_ms'] == size
    assert all(record[key] == value for key, value in totals.items())
    assert record['status'] == 'completed' and Decimal(str(record['credit_cost'])) == expected
    balance = Decimal(str(service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
        (row['tenant_id'],))[0]['credit_balance']))
    assert balance == initial_balance - expected
    assert all(item['applied'] for item in receipts)


def test_two_hundred_artifact_rounds_keep_bounded_projection_and_all_archived_references_after_retry(
        storage, actors, service_database):
    repository, execution, submit = storage
    _, principal = submit(actors['a'])
    row = execution.acquire('independent-artifact-capacity', 300)
    root = ExecutionState(principal.identity, row['runner_id'], AgentMode.MASTER, '', [])
    port = DurableControl(execution, attempt(row), row)
    port.envelope['presentation'] = {'downloadableFiles': []}
    for turn in range(200):
        for index in range(50):
            identity = turn * 50 + index
            root.images.append({'file_id': f'image-{identity}', 'download_url': f'/api/files/image-{identity}/download'})
            port.envelope['presentation']['downloadableFiles'].append({'file_id': f'file-{identity}',
                'file_name': f'output-{identity}.txt', 'download_url': f'/api/files/file-{identity}/download'})
        if turn == 100:
            # Archive commits, then lose the updated checkpoint and repeat it.
            old_root, old_envelope = copy.deepcopy(root), copy.deepcopy(port.envelope)
            archive_and_compact(port, root)
            port.envelope = old_envelope
            archive_and_compact(port, old_root)
            root = old_root
        else:
            archive_and_compact(port, root)
        port._capture(root)
        assert len(root.images) <= 48
        assert len(port.snapshot['downloadableFiles']) <= 48
        assert len(json.dumps(port.snapshot).encode()) < 100_000
    records = service_database.rows('SELECT message_id,metadata FROM chat_messages WHERE session_id=%s', (row['session_id'],))
    image_ids, file_ids = set(), set()
    for record in records:
        metadata = json.loads(record['metadata']) if isinstance(record['metadata'], str) else record['metadata']
        image_ids.update(item['file_id'] for item in metadata.get('images') or [])
        file_ids.update(item['file_id'] for item in metadata.get('downloadableFiles') or [])
    image_ids.update(item['file_id'] for item in root.images)
    file_ids.update(item['file_id'] for item in port.snapshot['downloadableFiles'])
    assert image_ids == {f'image-{index}' for index in range(10_000)}
    assert file_ids == {f'file-{index}' for index in range(10_000)}
    assert len(records) == len({record['message_id'] for record in records}) == 201


def test_runner_history_pages_bypass_warm_cache_and_old_pages_exclude_new_archives(
        gateways, storage, actors, service_database):
    accepted, _ = storage[2](actors['a'])
    gateway = gateways.start()
    path = '/api/sessions/' + actors['a'].session_id + '/messages'
    def insert(name, *, declaration=False, control=False):
        metadata = {'runner_id': accepted['runner_id'], 'runner_queue_order': accepted['queue_order']}
        if declaration:
            metadata['tool_calls'] = [{'id': name, 'function': {'name': 'read', 'arguments': '{}'}}]
        if control:
            metadata['control_id'] = 'control_kept'
        message_id = 'control_kept:user' if control else accepted['runner_id'] + ':history:' + name
        service_database.rows('INSERT INTO chat_messages(message_id,session_id,role,content,metadata) VALUES(%s,%s,%s,%s,%s)',
            (message_id, actors['a'].session_id, 'user' if control else 'assistant', name, json.dumps(metadata)))
        return message_id
    warm = insert('warm-cache-original')
    assert [item['message_id'] for item in require_status(gateway.call('GET', path, actor=actors['a']), 200)['messages']] == [warm]
    expected = {warm}
    for index in range(207):
        item = insert(str(index), declaration=index < 3, control=index == 7)
        if index >= 3:
            expected.add(item)
    insert('latest-hidden-declaration', declaration=True)
    first = require_status(gateway.call('GET', path, actor=actors['a'],
        params={'runner_id': accepted['runner_id'], 'before_id': '0', 'limit': 100}), 200)
    assert len(first['messages']) == 100 and first['has_more']
    cursor = first['next_cursor']
    actual = {item['message_id'] for item in first['messages']}
    late = insert('arrived-after-latest-window')
    more, pages = first['has_more'], 1
    while more:
        page = require_status(gateway.call('GET', path, actor=actors['a'], params={'runner_id': accepted['runner_id'],
            'before_id': cursor, 'limit': 100}), 200)
        assert int(page['next_cursor']) < int(cursor)
        ids = {item['message_id'] for item in page['messages']}
        assert not actual.intersection(ids)
        actual.update(ids)
        cursor, more = page['next_cursor'], page['has_more']
        pages += 1
        assert pages < 5
    assert pages == 3 and actual == expected and late not in actual
    latest = require_status(gateway.call('GET', path, actor=actors['a'], params={
        'runner_id': accepted['runner_id'], 'before_id': '0', 'limit': 100}), 200)
    assert len(latest['messages']) == 100
    assert late in {item['message_id'] for item in latest['messages']}


def test_runner_history_page_rejects_foreign_subject_and_invalid_cursor_without_broadening_old_route(
        gateways, storage, actors):
    accepted, _ = storage[2](actors['a'])
    gateway = gateways.start()
    path = '/api/sessions/' + actors['a'].session_id + '/messages'
    require_status(gateway.call('GET', path, actor=actors['a'], params={'runner_id': accepted['runner_id']}), 200)
    require_status(gateway.call('GET', path, actor=actors['b'], params={'runner_id': accepted['runner_id']}), 404)
    for parameters in ({'before_id': '-1'}, {'before_id': '1.5'}, {'before_id': 'not-a-cursor'},
                       {'before_id': '9223372036854775808'}, {'limit': 0}, {'limit': 101}):
        require_status(gateway.call('GET', path, actor=actors['a'], params={'runner_id': accepted['runner_id'], **parameters}), 422)
    require_status(gateway.call('GET', path, actor=actors['a']), 200)


@pytest.mark.asyncio
async def test_legacy_completed_cursor_survives_database_restore_summary_archive_and_retained_old_local_proof(
        storage, actors, service_database):
    repository, execution, submit = storage
    _, principal = submit(actors['a'])
    row = execution.acquire('legacy-cursor-archive-owner', 300)
    root = ExecutionState(principal.identity, row['runner_id'], AgentMode.MASTER, '',
        [{'role': 'user', 'content': 'continue monitoring this group'}], initial_len=1)
    target = canonical_json(['read_messages', {'group': 'a'}])
    root.resources.update(history_offset=100, continuous_summary='previous summary through cursor 9',
        continuous_summary_through=100, continuous_cursors={target: {'/next_cursor': 'cursor-9'}})
    old = ToolCall('orphan-local-read', 'read_messages', {'group': 'a'})
    recent = ToolCall('legacy-read-completed-before-upgrade', 'read_messages', {'group': 'a'})
    root.tools[old.id] = ToolFact(old, phase='completed', result={'next_cursor': 'cursor-1'}, result_recorded=True)
    root.tools[recent.id] = ToolFact(recent, phase='completed',
        result={'messages': 'x' * 24000, 'next_cursor': 'cursor-10'}, result_recorded=True)
    root.resources['local_invocations'] = {'old-proof': {'tool_call_id': old.id, 'invocation_id': str(uuid.uuid4())}}
    root.messages.extend({'role': 'assistant', 'content': f'previous observation {index}'} for index in range(96))
    root.messages.extend([{'role': 'assistant', 'tool_calls': [recent.message_call()]},
        {'role': 'tool', 'tool_call_id': recent.id, 'content': _truncate_tool_content(json.dumps(root.tools[recent.id].result))}])
    saved = execution.save_checkpoint(attempt(row), row['revision'], {'execution': root.checkpoint()})
    root = ExecutionState.restore(saved['checkpoint']['execution'])
    port = DurableControl(execution, attempt(saved), saved)
    async def summarize(*args, **kwargs):
        return 'continue original monitoring task; exact opaque cursor retained separately', {}, False
    await prepare_compaction(root, summarize)
    archive_and_compact(port, root)
    assert root.resources['continuous_cursors'][target] == {'/next_cursor': 'cursor-10'}
    for index in range(40):
        item = ToolCall(f'later-observation-{index}', 'irrelevant_read', {})
        root.tools[item.id] = ToolFact(item, phase='completed', result={}, result_recorded=True)
    root.messages.extend({'role': 'assistant', 'content': f'later context {index}'} for index in range(96))
    await prepare_compaction(root, summarize)
    archive_and_compact(port, root)
    assert old.id in root.tools and recent.id not in root.tools
    assert not any(message.get('tool_call_id') == recent.id for message in root.messages)
    await port.save(root, 'independent_legacy_cursor_archived')
    restored = ExecutionState.restore(repository.get(row['runner_id'])['checkpoint']['execution'])
    archive_and_compact(port, restored)
    assert restored.resources['continuous_cursors'][target] == {'/next_cursor': 'cursor-10'}
    assert service_database.rows('SELECT message_id FROM chat_messages WHERE session_id=%s AND role=%s',
        (row['session_id'], 'tool'))
