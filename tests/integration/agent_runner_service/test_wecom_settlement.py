"""WeCom history/settlement ownership on disposable PostgreSQL, no model/platform IO."""
from decimal import Decimal
import uuid

import pytest

from src.channels.session import ChannelSessionManager
from src.config.settings import AgentRunnerConfig
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import RunnerSubmit
from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.repository import RunnerRepository
from src.services.agent_runner.transaction_repository import write_history
from src.services.agent_runner.usage_repository import UsageRepository
from src.services.session_record import SessionRecordManager

from .test_storage import attempt
from .test_usage_storage import prices  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def wecom_session(actors):
    actor = actors['a']
    manager = ChannelSessionManager()
    session = manager.get_or_create_session('wecom', 'wecom-fixture-' + uuid.uuid4().hex,
        actor.tenant_id, user_info={'user_id': actor.user_id, 'name': '测试用户'})
    return actor, manager, session


def test_real_wecom_receipts_settle_once_while_channel_is_only_history_writer(
    wecom_session, service_database, prices, monkeypatch
):
    actor, channel, session = wecom_session
    session_id = session['session_id']
    # Actual channel entry record lifecycle: skip_save creates no extra record.
    entry_record = SessionRecordManager.start_record(session_id=session_id,
        user_id=actor.user_id, tenant_id=actor.tenant_id, source_type='wecom', user_message='request')
    entry_record.skip_save = True
    entry_record.complete('answer')
    assert SessionRecordManager.end_record() is None
    assert SessionRecordManager.get_current_record() is None
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (session_id,)) == []

    config = AgentRunnerConfig(web_service_token='fixture-token',
        peers={'wecom-test': {'sources': ['wecom']}})
    authorizer = RunnerAuthorizer(config, service_database.connect)
    request = RunnerSubmit(source='wecom', session={'kind': 'channel', 'session_id': session_id},
        channel_user_id=session['channel_user_id'], client_request_id=uuid.uuid4().hex, text='request')
    principal = authorizer.authorize(request, 'wecom-test', 'fixture-token')
    assert principal.identity.tenant_id == actor.tenant_id and principal.identity.user_id == actor.user_id
    repository = RunnerRepository(service_database.connect)
    execution = ExecutionRepository(service_database.connect)
    row, created = repository.submit(principal, request, 'fixture-profile')
    assert created
    owned = execution.acquire('wecom-test-worker', 30)
    assert owned['runner_id'] == row['runner_id']
    usage = UsageRepository(service_database.connect)
    for call, purpose, boundary in [('main', 'llm', None), ('skill', 'skill', 'skill-call:skill')]:
        kwargs = {'boundary': boundary} if boundary else {}
        receipt = usage.start(attempt(owned), call_id=call, execution_id='root', purpose=purpose,
            provider='qwen', model=prices[0], **kwargs)
        usage.observe(attempt(owned), receipt['receipt_id'], {'prompt_tokens': 10, 'completion_tokens': 0})
    result = {'status': 'completed', 'output': 'answer', 'messages': [
        {'role': 'assistant', 'tool_calls': [{'id': 'tool-1', 'function': {'name': 'probe', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'tool-1', 'content': 'tool result'}]}
    execution.stage_finalization(attempt(owned), owned['revision'], {}, result, {})
    finalizer = RunnerFinalizer(service_database.connect)
    # Exclude unrelated cache/trace databases; finalize/_settle/receipts/debit/claim are real.
    monkeypatch.setattr(finalizer, '_after_commit', lambda *args: None)
    final = finalizer.finalize(attempt(owned))
    assert final['status'] == 'completed' and final['settlement_status'] == 'settled'
    assert service_database.rows('SELECT 1 FROM channel_messages WHERE session_id=%s', (session_id,)) == []
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE session_id=%s', (session_id,)) == []
    records = service_database.rows('SELECT record_id,source_type,credit_cost,prompt_tokens FROM chat_records WHERE session_id=%s', (session_id,))
    assert len(records) == 1 and records[0]['record_id'] == final['record_id']
    assert records[0]['source_type'] == 'wecom' and records[0]['prompt_tokens'] == 20
    assert records[0]['credit_cost'] == Decimal('0.02')
    balance = service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (actor.tenant_id,))[0]['credit_balance']
    assert balance == Decimal('999.98')
    receipts = service_database.rows('SELECT applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (row['runner_id'],))
    assert len(receipts) == 2 and all(receipt['applied'] for receipt in receipts)
    finalizer.settle_pending_usage(row['runner_id'])
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (actor.tenant_id,))[0]['credit_balance'] == balance
    messages = [{'role': 'user', 'content': 'request'},
        {'role': 'assistant', 'content': '', 'metadata': {'tool_calls': result['messages'][0]['tool_calls']}},
        {'role': 'tool', 'content': 'tool result', 'metadata': {'tool_call_id': 'tool-1'}},
        {'role': 'assistant', 'content': 'answer'}]
    assert len(channel.add_messages_batch_transactional(session_id, actor.tenant_id, messages)) == 4
    stored = service_database.rows('SELECT role FROM channel_messages WHERE session_id=%s', (session_id,))
    assert len(stored) == 4
    assert sorted(item['role'] for item in stored) == ['assistant', 'assistant', 'tool', 'user']


def test_write_history_stamps_user_message_with_submission_time(
    wecom_session, service_database
):
    """write_history 的主 user 消息时间必须是任务提交时刻（用户发送时间）。

    为什么重要：Runner 整轮消息在回答完成后落库，若 user 消息用落库时刻，
    前端问/答显示同一时间（都是回答时刻），与真实对话节奏不符。
    tool/assistant 仍用落库时刻（接近实际产生时间）。
    """
    actor, _channel, session = wecom_session
    session_id = session['session_id']
    config = AgentRunnerConfig(web_service_token='fixture-token',
        peers={'wecom-test': {'sources': ['wecom']}})
    authorizer = RunnerAuthorizer(config, service_database.connect)
    request = RunnerSubmit(source='wecom', session={'kind': 'channel', 'session_id': session_id},
        channel_user_id=session['channel_user_id'], client_request_id=uuid.uuid4().hex, text='request')
    principal = authorizer.authorize(request, 'wecom-test', 'fixture-token')
    row, created = RunnerRepository(service_database.connect).submit(principal, request, 'fixture-profile')
    assert created

    with service_database.connect() as connection, connection.cursor() as cursor:
        write_history(cursor, row, {'status': 'completed', 'output': 'answer', 'messages': []})

    stored = service_database.rows(
        'SELECT role, content, created_at FROM channel_messages WHERE session_id=%s ORDER BY id',
        (session_id,))
    assert [item['role'] for item in stored] == ['user', 'assistant']
    accepted = row['accepted_at'].replace(tzinfo=None)
    assert stored[0]['created_at'] == accepted, 'user 消息 created_at 必须等于 agent_runners.accepted_at'
    assert stored[1]['created_at'] >= stored[0]['created_at'], 'assistant 时间不得早于 user'


@pytest.mark.parametrize('fault,code', [
    ('actor', 'CHANNEL_ACTOR_FORBIDDEN'), ('chat', 'CHANNEL_ACTOR_FORBIDDEN'),
    ('source', 'SESSION_NOT_FOUND'), ('profile', 'CHANNEL_PROFILE_MISMATCH'),
    ('user_tenant', 'CHANNEL_USER_FORBIDDEN'), ('user_disabled', 'CHANNEL_USER_FORBIDDEN'),
    ('tenant_disabled', 'TENANT_UNAVAILABLE'), ('tenant_expired', 'TENANT_EXPIRED'),
    ('subscription', 'PROFILE_FORBIDDEN'), ('peer', 'SERVICE_SOURCE_FORBIDDEN')])
def test_wecom_authorization_rejects_invalid_persisted_subject_before_acceptance(
    wecom_session, actors, service_database, fault, code
):
    from src.services.agent_runner.contracts import RunnerError
    actor, _, session = wecom_session
    config = AgentRunnerConfig(web_service_token='fixture-token',
        peers={'wecom-test': {'sources': ['wecom', 'feishu']}})
    request = RunnerSubmit(source='wecom', session={'kind': 'channel', 'session_id': session['session_id']},
        channel_user_id=session['channel_user_id'], client_request_id=uuid.uuid4().hex, text='request')
    if fault == 'actor':
        request.channel_user_id = 'foreign-actor'
    elif fault == 'chat':
        request.channel_chat_id = 'foreign-chat'
    elif fault == 'source':
        request.source = 'feishu'
    elif fault == 'profile':
        request.profile_id = 'foreign-profile'
    elif fault == 'user_tenant':
        service_database.rows('UPDATE channel_sessions SET user_id=%s WHERE session_id=%s', (actors['b'].user_id, session['session_id']))
    elif fault == 'user_disabled':
        service_database.rows("UPDATE users SET status='disabled' WHERE user_id=%s", (actor.user_id,))
    elif fault == 'tenant_disabled':
        service_database.rows("UPDATE tenants SET status='suspended' WHERE tenant_id=%s", (actor.tenant_id,))
    elif fault == 'tenant_expired':
        service_database.rows("UPDATE tenants SET expire_at=NOW()-INTERVAL '1 day' WHERE tenant_id=%s", (actor.tenant_id,))
    elif fault == 'subscription':
        request.profile_id = 'unsubscribed'
        service_database.rows('UPDATE channel_sessions SET subagent_id=%s WHERE session_id=%s', (request.profile_id, session['session_id']))
    else:
        config.peers['wecom-test'].sources = ['chat']
    with pytest.raises(RunnerError, match=code):
        RunnerAuthorizer(config, service_database.connect).authorize(request, 'wecom-test', 'fixture-token')
    assert service_database.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (session['session_id'],)) == []


def test_wecom_session_identity_preserves_shared_application_scope_and_other_isolation(wecom_session, actors):
    actor, manager, session = wecom_session
    # Applications are deliberately absent from session identity, as in the original entry.
    same = manager.get_or_create_session('wecom', session['channel_user_id'], actor.tenant_id,
        user_info={'user_id': actor.user_id}, subagent_id='')
    assert same['session_id'] == session['session_id']
    variants = [manager.get_or_create_session('wecom', session['channel_user_id'], actor.tenant_id,
        user_info={'user_id': actor.user_id}, subagent_id='sales'),
        manager.get_or_create_session('wecom', session['channel_user_id'] + '-other', actor.tenant_id,
        user_info={'user_id': actor.user_id}),
        manager.get_or_create_session('wecom', session['channel_user_id'], actors['b'].tenant_id,
        user_info={'user_id': actors['b'].user_id})]
    assert len({session['session_id'], *(item['session_id'] for item in variants)}) == 4
