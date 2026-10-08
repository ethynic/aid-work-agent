"""实际 HTTP/ASGI 契约；仅替换数据库、worker 状态与平台 I/O 边界。"""

import asyncio
import base64
import copy
import importlib.util
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import httpx
import pytest

from src.channels.runner_agent import ChannelRunnerAgent
from src.config.settings import AgentRunnerConfig
from src.core.agent_events import extract_downloadable_file, make_verbose_event
from src.core.verbose_feedback import VerboseFeedbackState
from src.services.agent_runner.api import create_app
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import RunnerError, RunnerSubmit, canonical_json
from src.services.agent_runner.control_repository import ControlRepository as StoredControls
from src.services.agent_runner.manager import RunnerManager
from src.services.agent_runner.web_client import RunnerServiceClient


class ChannelStore:
    def __init__(self):
        self.sessions = {'session': {'session_id': 'session', 'tenant_id': 'tenant',
            'channel_type': 'wecom_kf', 'channel_user_id': 'actor', 'channel_chat_id': 'kf',
            'subagent_id': 'main', 'user_id': None, 'metadata': {}}}
        self.configs = {('tenant', 'config'): {'channel_type': 'wecom_kf',
            'config': {'corp_id': 'corp', 'secret': 'test-secret', 'enabled': True,
                'kf_account': [{'open_kfid': 'kf', 'subagent_type': 'main'}]}}}
        self.statements = []

    @contextmanager
    def connection(self):
        store = self

        class Cursor:
            def execute(self, sql, params=()):
                store.statements.append((sql, params))
                if 'FROM channel_sessions' in sql:
                    self.row = store.sessions.get(params[0])
                    if self.row and len(params) > 1 and self.row['tenant_id'] != params[1]:
                        self.row = None
                elif 'FROM tenant_channel_configs' in sql:
                    self.row = store.configs.get(tuple(params))
                elif 'credit_balance FROM tenants' in sql:
                    self.row = {'credit_balance': 100}
                elif 'FROM tenants' in sql:
                    self.row = {'status': 'active', 'expire_at': None}
                elif 'FROM subscriptions' in sql:
                    self.row = {'exists': 1}
                else:
                    raise AssertionError('Unexpected database I/O: ' + sql)

            def fetchone(self):
                return copy.deepcopy(self.row)

        yield SimpleNamespace(cursor=lambda: Cursor())


class MemoryRunners:
    def __init__(self, store):
        self.connection_factory = store.connection
        self.rows, self.submitted, self.cancelled = {}, [], []
        self.status, self.snapshot = 'completed', {}
        self.polls = {}

    def find_request(self, principal, request):
        return next((row for row in self.rows.values() if row['client_request_id'] == request.client_request_id), None)

    def submit(self, principal, request, fingerprint, **kwargs):
        self.submitted.append(request)
        identifier = 'runner-' + str(len(self.submitted))
        identity = principal.identity
        row = {'runner_id': identifier, 'session_kind': identity.session_kind, 'session_id': identity.session_id,
            'tenant_id': identity.tenant_id, 'scope_key': principal.scope_key, 'source': identity.source,
            'actor_kind': principal.actor_kind, 'actor_id': principal.actor_id, 'user_id': identity.user_id,
            'service_id': principal.service_id, 'client_request_id': request.client_request_id,
            'input': request.intent(), 'profile_id': request.profile_id, 'status': self.status,
            'queue_order': len(self.submitted), 'revision': 1, 'view_revision': 1, 'control_revision': 0,
            'cancel_requested': False, 'settlement_status': 'settled', 'accepted_at': '2026-10-08',
            'updated_at': '2026-10-08', 'public_snapshot': copy.deepcopy(self.snapshot),
            'checkpoint': {'pending_finalization': {'messages': [
                {'role': 'user', 'content': request.text},
                {'role': 'assistant', 'tool_calls': [{'id': 'tool', 'function': {'name': 'probe', 'arguments': {'token': 'test-hidden'}}}]},
                {'role': 'tool', 'tool_call_id': 'tool', 'content': 'tool result'},
                {'role': 'assistant', 'content': 'answer'},
            ]}}, 'result': {'status': 'completed', 'output': 'answer', 'images': [
                {'file_id': 'image', 'download_url': '/api/files/image/download'}]}}
        self.rows[identifier] = row
        return copy.deepcopy(row), True

    def get(self, identifier):
        row = self.rows[identifier]
        if identifier in self.polls:
            self.polls[identifier] -= 1
            if self.polls[identifier] <= 0:
                row['status'] = 'completed'
        return copy.deepcopy(row)

    def list_session(self, principal, limit, before):
        rows = [row for row in self.rows.values() if row['session_id'] == principal.identity.session_id
            and row['actor_id'] == principal.actor_id and row['source'] == principal.identity.source]
        return {'rows': rows, 'active': [row for row in rows if row['status'] not in {'completed', 'failed', 'cancelled'}],
            'has_more': False, 'next_cursor': None}

    def cancel(self, principal, identifier):
        self.cancelled.append(identifier)
        self.rows[identifier]['status'] = 'cancelled'
        return self.get(identifier)


@pytest.fixture
def bridge(monkeypatch):
    from src.services.agent_runner.application_sources import build_source_capabilities
    store = ChannelStore()
    repository = MemoryRunners(store)
    token = 'test-service-token'
    token_hash = bcrypt.hashpw(token.encode(), bcrypt.gensalt(rounds=4)).decode()
    config = AgentRunnerConfig(enabled=True, web_service_id='bridge', api_url='http://runner.test',
        peers={'bridge': {'sources': ['wecom_kf', 'chat'], 'token_hash': token_hash}})
    authorizer = RunnerAuthorizer(config, store.connection,
        source_port=build_source_capabilities(config, store.connection))
    manager = RunnerManager(repository, authorizer, SimpleNamespace(resolve=lambda profile: (None, 'fingerprint')))
    controls = {}

    class Controls:
        def __init__(self, connection):
            pass

        def find_request(self, *args):
            return None

        def submit(self, principal, identifier, request, **kwargs):
            value = {'control_id': 'control', 'runner_id': identifier, 'client_request_id': request.client_request_id,
                'status': 'accepted', 'action': request.action}
            controls['request'] = request
            controls['value'] = value
            repository.polls[identifier] = 3
            return value, True

        def get(self, *args):
            return {**controls['value'], 'status': 'consumed'}

    monkeypatch.setattr('src.services.agent_runner.control_application.ControlRepository', Controls)
    app = create_app(config=config, manager=manager)
    original_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=app)
    monkeypatch.setattr('src.services.agent_runner.web_client.httpx.AsyncClient',
        lambda **kwargs: original_client(**kwargs, transport=transport))
    client = RunnerServiceClient(config, token=token)
    agent = ChannelRunnerAgent(source='wecom_kf', session_id='session', channel_user_id='actor',
        channel_chat_id='kf', config_id='config', client=client)
    return SimpleNamespace(store=store, repository=repository, manager=manager, authorizer=authorizer,
        agent=agent, client=client, config=config, controls=controls, app=app,
        original_client=original_client, transport=transport)


@pytest.mark.asyncio
async def test_actual_service_submit_preserves_merged_input_attachments_and_returns_files_images_and_tool_history(bridge):
    file_event = {'type': 'tool_result', 'success': True, 'toolName': 'export', 'result': {
        'file_id': 'document', 'file_name': 'report.pdf', 'download_url': '/api/files/document/download', 'file_size': 4}}
    bridge.repository.snapshot = {'progressMessages': [file_event]}
    events = []
    with patch('src.core.agent_engine.AgentEngine.run', side_effect=AssertionError('No local model execution')):
        result = await bridge.agent.process_message_sync(user_input='first\n[用户追加消息] second',
            session_id='session', attachments=[{'name': 'input.pdf', 'type': 'file', 'content': 'dGVzdA==',
                'mime_type': 'application/pdf', 'local_path': 'must-not-cross-service'}], progress_callback=events.append)
    submitted = bridge.repository.submitted[0]
    assert submitted.text == 'first\n[用户追加消息] second'
    assert submitted.attachments[0].content == 'dGVzdA=='
    assert 'local_path' not in submitted.model_dump()['attachments'][0]
    assert submitted.request_data['channel_config_id'] == 'config'
    assert str(result) == 'answer' and result.images[0]['file_id'] == 'image'
    assert extract_downloadable_file(events[0])['file_name'] == 'report.pdf'
    history = next(event for event in events if event['type'] == 'tool_messages')['messages']
    assert [message['role'] for message in history] == ['assistant', 'tool']
    assert history[1]['tool_call_id'] == history[0]['tool_calls'][0]['id']
    assert history[0]['tool_calls'][0]['function']['arguments']['token'] != 'test-hidden'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,code', [
    ('service', 'SERVICE_UNAUTHORIZED'), ('actor', 'CHANNEL_ACTOR_FORBIDDEN'),
    ('profile', 'CHANNEL_PROFILE_MISMATCH'), ('config', 'CHANNEL_CONFIG_FORBIDDEN'),
    ('account', 'CHANNEL_ACCOUNT_FORBIDDEN'), ('disabled', 'CHANNEL_CONFIG_DISABLED'),
    ('source', 'SESSION_NOT_FOUND'),
])
async def test_actual_http_authorization_rejects_untrusted_route_before_any_runner_acceptance(bridge, fault, code):
    if fault == 'service':
        bridge.client.token = 'wrong-test-token'
    elif fault == 'actor':
        bridge.agent.channel_user_id = 'other-actor'
    elif fault == 'profile':
        bridge.agent.profile_id = 'other-profile'
    elif fault == 'config':
        bridge.agent.config_id = 'other-config'
    elif fault == 'account':
        bridge.store.configs[('tenant', 'config')]['config']['kf_account'][0]['open_kfid'] = 'other-kf'
    elif fault == 'disabled':
        bridge.store.configs[('tenant', 'config')]['config']['enabled'] = False
    else:
        bridge.store.sessions['session']['channel_type'] = 'feishu'
    with pytest.raises(RunnerError, match=code):
        await bridge.agent.process_message_sync(user_input='request', session_id='session')
    assert not bridge.repository.submitted


@pytest.mark.asyncio
async def test_cancelled_merge_attempt_is_cancelled_remotely_before_replacement_is_accepted(bridge):
    bridge.repository.status = 'running'
    request = RunnerSubmit(source='wecom_kf', session={'kind': 'channel', 'session_id': 'session'},
        channel_user_id='actor', channel_chat_id='kf', client_request_id='old', text='old',
        request_data={'channel_config_id': 'config'})
    credentials = {'service_id': 'bridge', 'service_token': bridge.client.token,
        'actor_source': 'wecom_kf', 'actor_user': 'actor', 'actor_chat': 'kf'}
    old = bridge.manager.submit(request, credentials)[0]
    bridge.repository.status = 'completed'
    assert str(await bridge.agent.process_message_sync(user_input='merged', session_id='session')) == 'answer'
    assert bridge.repository.cancelled == [old['runner_id']]
    assert [request.text for request in bridge.repository.submitted] == ['old', 'merged']


@pytest.mark.asyncio
async def test_local_cancel_check_cancels_service_task_instead_of_abandoning_it(bridge):
    bridge.repository.status = 'running'
    checks = iter([False, True])
    result = await bridge.agent.process_message_sync(user_input='request', session_id='session', cancel_check=lambda: next(checks))
    assert str(result) == '' and bridge.repository.cancelled == ['runner-1']


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapper', [None, 'child_wait', 'child_clarification'])
async def test_clarification_reply_uses_control_and_waits_for_applied_reply_without_resubmitting(bridge, wrapper):
    bridge.repository.status = 'waiting'
    bridge.repository.snapshot = {'waiting': {'kind': 'clarification', 'question': 'Which city?',
        'wait_id': 'wait', 'target_execution_id': 'execution'}}
    if wrapper:
        bridge.repository.snapshot['waiting'] = {'kind': wrapper, 'child_wait': bridge.repository.snapshot['waiting']}
    assert str(await bridge.agent.process_message_sync(user_input='book', session_id='session')) == 'Which city?'
    assert str(await bridge.agent.process_message_sync(user_input='Shanghai', session_id='session')) == 'answer'
    assert len(bridge.repository.submitted) == 1 and not bridge.repository.cancelled
    assert bridge.controls['request'].answer == 'Shanghai' and bridge.controls['request'].wait_id == 'wait'


@pytest.mark.asyncio
async def test_accepted_http_response_loss_reuses_same_submit_key_without_second_execution(bridge, monkeypatch):
    class LoseFirstAcceptance(httpx.AsyncBaseTransport):
        lost = False

        async def handle_async_request(self, request):
            response = await bridge.transport.handle_async_request(request)
            if request.method == 'POST' and request.url.path == '/v1/runners' and not self.lost:
                self.lost = True
                await response.aclose()
                raise httpx.ReadError('test response lost', request=request)
            return response

    transport = LoseFirstAcceptance()
    monkeypatch.setattr('src.services.agent_runner.web_client.httpx.AsyncClient',
        lambda **kwargs: bridge.original_client(**kwargs, transport=transport))
    assert str(await bridge.agent.process_message_sync(user_input='request', session_id='session')) == 'answer'
    assert transport.lost and len(bridge.repository.submitted) == 1


@pytest.mark.asyncio
async def test_coroutine_cancellation_waits_for_service_cancel_confirmation(bridge):
    bridge.repository.status = 'running'
    task = asyncio.create_task(bridge.agent.process_message_sync(user_input='request', session_id='session'))
    async def wait_for_acceptance():
        while not bridge.repository.submitted:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_for_acceptance(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert bridge.repository.cancelled == ['runner-1']
    assert bridge.repository.rows['runner-1']['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_unsupported_browser_wait_does_not_leave_session_claim_parked(bridge):
    bridge.repository.status = 'waiting'
    bridge.repository.snapshot = {'waiting': {'kind': 'browser', 'wait_id': 'wait'}}
    with pytest.raises(RunnerError, match='CHANNEL_EXECUTION_WAIT_UNSUPPORTED'):
        await bridge.agent.process_message_sync(user_input='request', session_id='session')
    assert bridge.repository.cancelled == ['runner-1']


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['paused', 'interrupted'])
async def test_unsupported_parked_execution_is_cancelled_and_reported(bridge, status):
    bridge.repository.status = status
    with pytest.raises(RunnerError, match='CHANNEL_EXECUTION_INTERRUPTED'):
        await bridge.agent.process_message_sync(user_input='request', session_id='session')
    assert bridge.repository.cancelled == ['runner-1']


@pytest.mark.asyncio
async def test_original_owner_feedback_state_suppresses_duplicate_service_feedback_across_reprocessing(bridge):
    verbose = make_verbose_event('verbose-1', '正在查询相关信息', 'policy')
    bridge.repository.snapshot = {'verboseMessages': [verbose, verbose]}
    state, events = VerboseFeedbackState(), []
    for text in ('first', 'merged'):
        await bridge.agent.process_message_sync(user_input=text, session_id='session',
            feedback_state=state, progress_callback=events.append)
    observed = [event for event in events if event['type'] == 'verbose']
    assert len(observed) == 1 and state.event is observed[0]


@pytest.mark.asyncio
async def test_channel_result_authorizes_current_actor_and_excludes_main_user_answer_history(bridge):
    await bridge.agent.process_message_sync(user_input='request', session_id='session')
    value = await bridge.client.request('GET', '/v1/runners/runner-1/channel-result', authorization=None,
        channel_source='wecom_kf', channel_user='actor', channel_chat='kf')
    assert [message['role'] for message in value['messages']] == ['assistant', 'tool']
    with pytest.raises(RunnerError, match='CHANNEL_ACTOR_FORBIDDEN'):
        await bridge.client.request('GET', '/v1/runners/runner-1/channel-result', authorization=None,
            channel_source='wecom_kf', channel_user='another-actor', channel_chat='kf')


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', ['{"token":"test-hidden","city":"Shanghai"}', '{invalid-test-secret'])
async def test_channel_result_masks_real_json_string_tool_arguments_and_invalid_json(bridge, arguments):
    await bridge.agent.process_message_sync(user_input='request', session_id='session')
    private = bridge.repository.rows['runner-1']['checkpoint']['pending_finalization']['messages'][1]
    private['tool_calls'][0]['function']['arguments'] = arguments
    value = await bridge.client.request('GET', '/v1/runners/runner-1/channel-result', authorization=None,
        channel_source='wecom_kf', channel_user='actor', channel_chat='kf')
    safe = value['messages'][0]['tool_calls'][0]['function']['arguments']
    assert 'test-hidden' not in str(safe) and 'invalid-test-secret' not in str(safe)
    assert private['tool_calls'][0]['function']['arguments'] == arguments


@pytest.mark.asyncio
@pytest.mark.parametrize('follower', [False, True])
async def test_original_channel_processor_keeps_one_history_and_delivery_owner_for_merged_batch(bridge, follower):
    from src.channels.session import ChannelSessionManager
    from src.core.session_queue import EnqueueResult
    manager = ChannelSessionManager()
    batch_write = MagicMock(return_value=['user', 'tool-call', 'tool', 'assistant'])
    sender = AsyncMock(return_value=True)

    async def queued(**kwargs):
        if follower:
            return EnqueueResult(status='merged', was_merged=True, merged_input='first second')
        response = await kwargs['processor'](lambda: False, 'first second', [])
        return EnqueueResult(status='success', response_text=response, was_merged=True,
            merged_input='first second', lease_token='lease')

    queue = SimpleNamespace(enqueue_and_process=queued, mark_responding=MagicMock(),
        mark_idle=MagicMock(), finish_processing=MagicMock())
    with patch('src.core.session_queue.session_queue', queue), \
            patch.object(manager, 'add_messages_batch_transactional', batch_write), \
            patch('src.saas.db.tenant_db.TenantDB.get_by_id', return_value={'credit_balance': 100}), \
            patch('src.services.recap.trigger_recap'):
        result = await manager.process_and_persist(session_id='session', tenant_id='tenant',
            agent=bridge.agent, user_content='first', send_response=sender)
    if follower:
        assert result['status'] == 'merged' and not bridge.repository.submitted
        batch_write.assert_not_called()
        sender.assert_not_awaited()
    else:
        assert result['status'] == 'success' and len(bridge.repository.submitted) == 1
        assert bridge.repository.submitted[0].text == 'first second'
        batch_write.assert_called_once()
        messages = batch_write.call_args.args[2]
        assert [message['role'] for message in messages] == ['user', 'assistant', 'tool', 'assistant']
        assert messages[0]['content'] == 'first second'
        assert messages[-1]['metadata']['images'][0]['file_id'] == 'image'
        sender.assert_awaited_once()
        queue.finish_processing.assert_called_once_with('session', 'lease')


@pytest.mark.asyncio
async def test_worker_channel_context_isolated_by_tenant_session_and_always_closes_adapter(bridge):
    from src.channels.wecom_kf.context import _kf_context, get_kf_context
    from src.services.agent_runner.channel_context import channel_runtime_scope
    bridge.store.sessions['second'] = {**bridge.store.sessions['session'], 'session_id': 'second',
        'tenant_id': 'other-tenant', 'channel_user_id': 'other-actor', 'channel_chat_id': 'other-kf'}
    bridge.store.configs[('other-tenant', 'other-config')] = {'channel_type': 'wecom_kf', 'config': {
        'corp_id': 'other-corp', 'secret': 'test-secret', 'kf_account': [{'open_kfid': 'other-kf'}]}}
    adapters, entered = [], []
    ready = asyncio.Event()

    def factory(**config):
        adapter = SimpleNamespace(corp_id=config['corp_id'], set_tenant_id=AsyncMock(), close=AsyncMock())
        adapters.append(adapter)
        return adapter

    async def task(session, tenant, config, fail=False):
        request = RunnerSubmit(source='wecom_kf', session={'kind': 'channel', 'session_id': session},
            channel_user_id=bridge.store.sessions[session]['channel_user_id'],
            channel_chat_id=bridge.store.sessions[session]['channel_chat_id'], client_request_id=session,
            text='request', request_data={'channel_config_id': config, 'channel_user_info': {'nickname': tenant}})
        row = {'source': 'wecom_kf', 'session_id': session, 'tenant_id': tenant,
            'client_request_id': session, 'input': request.intent()}
        try:
            async with channel_runtime_scope(row, bridge.store.connection):
                entered.append(session)
                if len(entered) == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 1)
                context = get_kf_context()
                assert context['tenant_id'] == tenant and context['session_id'] == session
                assert context['channel_user_info']['nickname'] == tenant
                if fail:
                    raise RuntimeError('test body failed')
        except RuntimeError:
            assert fail
        assert get_kf_context() == {'outer': True}

    token = _kf_context.set({'outer': True})
    try:
        with patch('src.channels.wecom_kf.adapter.WeComKfAdapter', factory):
            await asyncio.gather(task('session', 'tenant', 'config'), task('second', 'other-tenant', 'other-config', True))
        assert len(adapters) == 2
        for adapter in adapters:
            adapter.close.assert_awaited_once()
        assert get_kf_context() == {'outer': True}
    finally:
        _kf_context.reset(token)


@pytest.mark.parametrize('source,history_count', [('wecom_kf', 0), ('chat', 1)])
def test_finalizer_settles_execution_once_releases_claim_and_leaves_kf_history_to_channel(source, history_count):
    from src.services.agent_runner.finalizer import RunnerFinalizer
    from src.services.agent_runner.ownership import Attempt
    cursor, connection = MagicMock(), MagicMock()
    connection.__enter__.return_value.cursor.return_value = cursor
    row = {'source': source, 'runner_id': 'runner', 'record_id': 'record', 'status': 'finalizing',
        'scope_key': 'tenant:tenant', 'session_kind': 'channel' if source == 'wecom_kf' else 'web',
        'session_id': 'session', 'checkpoint': {'pending_finalization': {'status': 'completed', 'output': 'answer'}}}
    finished = {**row, 'status': 'completed'}
    cursor.fetchone.side_effect = [{'owner_runner_id': 'runner'}, finished]
    finalizer = RunnerFinalizer(lambda: connection)
    with patch('src.services.agent_runner.finalizer.lock_runner', return_value=row), \
            patch.object(finalizer, '_settle', return_value=('settled', 5)) as settle, \
            patch('src.services.agent_runner.finalizer.project_terminal_public_state', return_value=({}, row['checkpoint']['pending_finalization'])), \
            patch('src.services.agent_runner.finalizer.write_history') as history, \
            patch('src.services.agent_runner.control_repository.close_pending_controls'), \
            patch('src.services.agent_runner.finalizer.EventRepository.notify_in_tx', return_value=finished), \
            patch.object(finalizer, '_after_commit'):
        assert finalizer.finalize(Attempt('runner', 'worker', 1))['status'] == 'completed'
    assert settle.call_count == 1 and history.call_count == history_count
    assert any('DELETE FROM agent_runner_session_claims' in call.args[0] for call in cursor.execute.call_args_list)
    connection.__enter__.return_value.commit.assert_called_once()


def test_fresh_runner_schema_and_original_channel_do_not_require_retired_native_pipeline():
    from src.services.agent_runner.repository import RunnerRepository
    from src.channels.wecom_kf.api_client import WeComKfApiClient
    cursor, connection = MagicMock(), MagicMock()
    connection.__enter__.return_value.cursor.return_value = cursor
    cursor.fetchone.side_effect = lambda: {'name': cursor.execute.call_args.args[1][0]}
    RunnerRepository(lambda: connection).assert_schema()
    queried = [call.args[1][0] for call in cursor.execute.call_args_list if 'to_regclass' in call.args[0]]
    assert queried and all(name.startswith('agent_runner') for name in queried)
    assert not hasattr(WeComKfApiClient, 'enable_native_writes')
    for module in ('ingress_worker', 'admission_worker', 'delivery', 'context_worker', 'voice_media'):
        assert importlib.util.find_spec('src.channels.wecom_kf.' + module) is None
    root = Path(__file__).resolve().parents[3]
    schema = (root / 'deploy' / 'init-postgres.sql').read_text(encoding='utf-8')
    # 历史 DDL 必须累积保留；运行时依赖不能靠删建表记录来掩盖。
    for table in ('wecom_kf_inbox', 'wecom_kf_deliveries', 'wecom_kf_wire_operations'):
        assert table in schema
    assert schema.rstrip().endswith('ALTER TABLE IF EXISTS agent_runner_session_claims DROP COLUMN IF EXISTS gate;')


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [24, 2 * 1024 * 1024])
@pytest.mark.parametrize('redis_index', [False, True])
async def test_failed_asr_voice_original_attachment_build_crosses_http_by_id_and_worker_resolves_only_owner(
        bridge, tmp_path, size, redis_index):
    from src.saas.api.channel_routes import _build_attachments_for_agent, _transcribe_voice_with_asr
    from src.services.agent_runner.worker import RuntimeFactory
    root = tmp_path / 'storage'
    voice = root / 'tenants' / 'tenant' / 'conversation' / 'file_voice.wav'
    voice.parent.mkdir(parents=True)
    payload = b'v' * size
    voice.write_bytes(payload)
    encoded = base64.b64encode(payload).decode()
    with patch('src.tools.asr.speech_to_text_tool.SpeechToTextTool.execute',
            new=AsyncMock(return_value={'success': False, 'error': 'test ASR unavailable'})):
        text = await _transcribe_voice_with_asr(encoded, 'wav')
    assert text == '[语音消息]'
    attachments = _build_attachments_for_agent([{'type': 'voice', 'file_name': 'voice.wav',
        'content': encoded, 'mime_type': 'audio/wav', 'local_path': str(voice)}])
    await bridge.agent.process_message_sync(user_input=text, session_id='session', attachments=attachments)
    accepted = bridge.repository.submitted[0]
    assert accepted.text == text and accepted.attachments[0].type == 'file'
    assert accepted.attachments[0].file_id == voice.name and accepted.attachments[0].content is None
    assert len(canonical_json(accepted.intent())) < 4096
    metadata = {'path': str(voice), 'type': 'file', 'mime_type': 'audio/wav'} if redis_index else {}
    with patch('src.core.storage.configured_storage_root', return_value=root), \
            patch('src.core.redis_client.redis_client.hgetall', return_value=metadata):
        result = RuntimeFactory.attachments(bridge.repository.rows['runner-1'])
        assert Path(result[0]['url']).read_bytes() == payload
        assert result[0]['type'] == 'file' and result[0]['mime_type'] == 'audio/wav'
        foreign = {**bridge.repository.rows['runner-1'], 'tenant_id': 'foreign-tenant'}
        with pytest.raises(RunnerError, match='ATTACHMENT_NOT_FOUND'):
            RuntimeFactory.attachments(foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_path', ['/v1/runners/runner-1', '/v1/runners/runner-1/channel-result'])
async def test_transient_poll_or_result_response_loss_does_not_restart_accepted_execution(bridge, monkeypatch, failed_path):
    bridge.repository.status = 'running'
    events = []

    class LoseReadOnce(httpx.AsyncBaseTransport):
        lost = False

        async def handle_async_request(self, request):
            response = await bridge.transport.handle_async_request(request)
            if request.method == 'POST' and request.url.path == '/v1/runners':
                bridge.repository.polls['runner-1'] = 1
            if request.method == 'GET' and request.url.path == failed_path and not self.lost:
                self.lost = True
                await response.aclose()
                raise httpx.ReadError('test transient read loss', request=request)
            return response

    transport = LoseReadOnce()
    monkeypatch.setattr('src.services.agent_runner.web_client.httpx.AsyncClient',
        lambda **kwargs: bridge.original_client(**kwargs, transport=transport))
    response = await bridge.agent.process_message_sync(user_input='request', session_id='session', progress_callback=events.append)
    assert str(response) == 'answer' and transport.lost
    assert len(bridge.repository.submitted) == 1 and not bridge.repository.cancelled
    assert len([event for event in events if event['type'] == 'tool_messages']) == 1


@pytest.mark.asyncio
async def test_reply_acceptance_window_cancellation_confirms_original_runner_cancellation(bridge, monkeypatch):
    bridge.repository.status = 'waiting'
    bridge.repository.snapshot = {'waiting': {'kind': 'clarification', 'question': 'Which city?',
        'wait_id': 'wait', 'target_execution_id': 'execution'}}
    await bridge.agent.process_message_sync(user_input='book', session_id='session')
    accepted, release = asyncio.Event(), asyncio.Event()

    class DelayedReply(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await bridge.transport.handle_async_request(request)
            if request.method == 'POST' and request.url.path.endswith('/controls'):
                accepted.set()
                await release.wait()
            return response

    monkeypatch.setattr('src.services.agent_runner.web_client.httpx.AsyncClient',
        lambda **kwargs: bridge.original_client(**kwargs, transport=DelayedReply()))
    task = asyncio.create_task(bridge.agent.process_message_sync(user_input='Shanghai', session_id='session'))
    await asyncio.wait_for(accepted.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), 'Cancellation must wait for accepted reply and remote cancel confirmation'
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert len(bridge.repository.submitted) == 1 and bridge.repository.cancelled == ['runner-1']


def test_recovery_cannot_resume_after_session_claim_ownership_was_lost():
    from src.services.agent_runner.recovery_repository import lock_original_claim
    cursor = MagicMock()
    row = {'runner_id': 'original', 'scope_key': 'tenant:tenant', 'session_kind': 'channel', 'session_id': 'session'}
    for claim in (None, {'owner_runner_id': 'replacement'}):
        cursor.fetchone.return_value = claim
        with pytest.raises(RunnerError, match='RECOVERY_CLAIM_LOST'):
            lock_original_claim(cursor, row)


@pytest.mark.asyncio
async def test_runner_enabled_callback_keeps_original_factory_decryption_and_background_message_dispatch(bridge, monkeypatch):
    from src.saas.api import channel_routes
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner, 'enabled', True)
    crypto = SimpleNamespace(verify_signature=MagicMock(return_value=True),
        decrypt=MagicMock(return_value='<xml><Event>kf_msg_or_event</Event><OpenKfId>kf</OpenKfId></xml>'))
    adapter = SimpleNamespace(crypto=crypto)
    factory = AsyncMock(return_value=(adapter, None, None))
    original_background = AsyncMock()
    monkeypatch.setattr(channel_routes.ChannelFactory, 'create_from_tenant_config', factory)
    monkeypatch.setattr(channel_routes, '_process_tenant_wecom_kf_messages', original_background)
    monkeypatch.setattr(channel_routes, '_kf_tlog', MagicMock())
    request = SimpleNamespace(body=AsyncMock(return_value=b'<xml><Encrypt>ciphertext</Encrypt></xml>'),
        query_params={'msg_signature': 'signature', 'timestamp': '1', 'nonce': 'nonce'})
    response = await channel_routes.tenant_wecom_kf_callback_post('tenant', 'config', request)
    await asyncio.sleep(0)
    assert response.status_code == 200 and response.body == b'success'
    factory.assert_awaited_once_with('tenant', 'wecom_kf', config_id='config')
    crypto.verify_signature.assert_called_once_with('signature', '1', 'nonce', 'ciphertext')
    crypto.decrypt.assert_called_once_with('ciphertext')
    original_background.assert_awaited_once_with('tenant', 'config', 'kf', adapter)
    assert not bridge.repository.submitted, 'Callback acceptance remains owned by original channel'


@pytest.mark.asyncio
async def test_worker_rejects_foreign_session_before_adapter_construction(bridge):
    from src.services.agent_runner.channel_context import channel_runtime_scope
    request = RunnerSubmit(source='wecom_kf', session={'kind': 'channel', 'session_id': 'session'},
        channel_user_id='actor', channel_chat_id='kf', client_request_id='request', text='request',
        request_data={'channel_config_id': 'config'})
    row = {'source': 'wecom_kf', 'session_id': 'session', 'tenant_id': 'foreign-tenant',
        'client_request_id': 'request', 'input': request.intent()}
    with patch('src.channels.wecom_kf.adapter.WeComKfAdapter') as adapter:
        with pytest.raises(RunnerError, match='SESSION_NOT_FOUND'):
            async with channel_runtime_scope(row, bridge.store.connection):
                pytest.fail('Foreign tenant must never enter tool execution context')
        adapter.assert_not_called()


@pytest.mark.asyncio
async def test_real_recovery_reply_on_fresh_schema_uses_generic_control_without_retired_source_table(bridge):
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
    from src.services.agent_runner.recovery import RecoveryCoordinator
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    await bridge.agent.process_message_sync(user_input='request', session_id='session')
    row = bridge.repository.rows['runner-1']
    state = ExecutionState(Identity('tenant', None, 'session', 'wecom_kf', 'channel'),
        'runner-1', AgentMode.MASTER, 'test', [], profile_id='main', outcome=Outcome.WAITING)
    state.resources['configuration_fingerprint'] = configuration_fingerprint('fingerprint')
    call = ToolCall('clarify', 'ask_user', {'question': 'Which city?'})
    state.tools[call.id] = ToolFact(call, phase='waiting')
    state.pending = [call]
    state.waiting = {'kind': 'clarification', 'question': 'Which city?', 'wait_id': 'wait',
        'tool_call_id': call.id, 'target_execution_id': state.execution_id}
    row.update(profile_fingerprint='fingerprint', checkpoint={'execution': state.checkpoint()})
    control = {'control_id': 'reply-control', 'action': 'reply', 'accepted_at': '2026-10-08',
        'payload': {'action': 'reply', 'answer': 'Shanghai', 'attachments': [], 'wait_id': 'wait', 'target_execution_id': 'runner-1'}}
    coordinator = RecoveryCoordinator(bridge.authorizer, SimpleNamespace(resolve=lambda profile: (None, 'fingerprint')))
    recovered = coordinator.prepare(row, control)
    root = recovered.states['runner-1']
    assert root.outcome == Outcome.RUNNING and root.waiting is None
    assert root.tools['clarify'].phase == 'completed' and root.tools['clarify'].result['answer'] == 'Shanghai'
    assert root.followup_messages[0]['content'] == 'Shanghai'
    assert recovered.checkpoint['applied_control_id'] == 'reply-control'
    assert recovered.checkpoint['supplemental_inputs'][0]['text'] == 'Shanghai'
    assert not any('agent_runner_inputs' in sql for sql, params in bridge.store.statements)
    from src.services.agent_runner.recovery_repository import RecoveryRepository
    row.update(status='waiting', attempt=0, lease_valid=False, resume_control_id=control['control_id'])
    control.update(status='accepted', client_request_id='reply-request')
    resumed = {**row, 'status': 'running', 'attempt': 1, 'checkpoint': recovered.checkpoint}
    cursor, connection = MagicMock(), MagicMock()
    connection.__enter__.return_value.cursor.return_value = cursor
    cursor.fetchone.side_effect = [{'owner_runner_id': row['runner_id']}, control, resumed]
    with patch('src.services.agent_runner.recovery_repository.lock_runner', return_value=row) as fence, \
            patch('src.services.agent_runner.recovery_repository.ControlRepository', StoredControls), \
            patch('src.services.agent_runner.recovery_repository.sync_public_display', side_effect=lambda cursor, row, **kwargs: row), \
            patch('src.services.agent_runner.recovery_repository.EventRepository.notify_in_tx', return_value=resumed):
        value = RecoveryRepository(lambda: connection).claim_resume('runner-1', 'worker', 30,
            revision=1, control_id=control['control_id'], checkpoint=recovered.checkpoint,
            source_port=bridge.authorizer.source_port)
    assert value['attempt'] == 1 and value['checkpoint']['applied_control_id'] == control['control_id']
    assert fence.call_count == 2 and fence.call_args.kwargs == {'dispatch': True}
    connection.__enter__.return_value.commit.assert_called_once()
    sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("UPDATE agent_runner_controls SET status='consumed'" in statement for statement in sql)
    assert not any('agent_runner_inputs' in statement for statement in sql)


@pytest.mark.asyncio
@pytest.mark.parametrize('continuation', [False, True])
async def test_context_assembler_keeps_generic_incoming_and_browser_continuation_after_source_projection_removal(continuation):
    from src.core.agent_engine.contracts import AgentMode, Identity
    from src.services.agent_runner.runtime.context_assembler import ContextAssembler
    prior = [{'role': 'user', 'content': 'old request'}, {'role': 'assistant', 'content': 'old answer'}]
    if continuation:
        prior.append({'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'browser', 'type': 'function', 'function': {'name': 'browser', 'arguments': '{}'}}]})
    compression = SimpleNamespace(_run_compression_phase=AsyncMock(), event=None)
    remember = SimpleNamespace(_handle_remember_intent=AsyncMock())
    attachment_port = SimpleNamespace(prepare=MagicMock(side_effect=lambda text, *args: (text, 'file instructions', None)),
        build_image_reference_content=MagicMock(return_value=(None, None)),
        _build_multimodal_user_content=MagicMock(return_value=None))
    history = SimpleNamespace(reader=SimpleNamespace(assert_authorized=MagicMock()),
        _reload_memory_from_db=MagicMock(), memory=SimpleNamespace(get_context=MagicMock(return_value=prior)),
        active_summary=MagicMock(return_value=None))
    assembler = ContextAssembler(identity=Identity('tenant', None, 'session', 'wecom_kf', 'channel'),
        role=AgentMode.MASTER, profile_config=None, history=history, compression=compression,
        prompt_sources=SimpleNamespace(_build_system_prompt=MagicMock(return_value='system')),
        skills=SimpleNamespace(_ensure_tenant_skills_loaded=MagicMock(), skill_registry={}),
        remember=remember, attachments=attachment_port, visibility=SimpleNamespace(prime=AsyncMock()))
    state, events = await assembler.prepare('new request', execution_id='runner',
        continuation={'tool_call_id': 'browser', 'content': {'success': True}} if continuation else None)
    assert state.identity.session_kind == 'channel' and state.initial_len == len(state.messages)
    assert any(message.get('content') == 'old answer' for message in state.messages)
    if continuation:
        attachment_port.prepare.assert_not_called()
        compression._run_compression_phase.assert_not_awaited()
        assert state.messages[-1]['role'] == 'tool' and state.messages[-1]['tool_call_id'] == 'browser'
        assert events[0]['type'] == 'tool_messages' and events[0]['messages'][0]['tool_call_id'] == 'browser'
    else:
        remember._handle_remember_intent.assert_awaited_once_with('new request', None)
        compression._run_compression_phase.assert_awaited_once_with('session')
        assert any('new request' in message.get('content', '') for message in state.messages)
        assert state.messages[-1]['content'] == 'file instructions'
