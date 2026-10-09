"""Native child-tree owner contracts with genuine HTTP ACKs and real PG.

The owned CP shape and epoch replacement are declared contract fixtures, not
public fanout or Engine-produced descendants. No ACK/audit writer is faked.
"""
import asyncio
import copy
import uuid

import pytest
from psycopg2 import sql

from .domain_fixtures import recruiting_domain
from .domain_io import WebhookPeer, WebhookReply
from .test_worker import workers, api_pair, prices, accept, decoded, runner
from .provider import Reply

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def acknowledged_tree(workers, recruiting_domain, service_database):
    from src.config.settings import settings, AgentRunnerPeerConfig
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
    from src.services.agent_runner.repository import RunnerRepository
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.runtime.child import ChildControl
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from src.services.agent_runner.authorization import RunnerAuthorizer
    from src.services.agent_runner.ownership import Attempt
    from src.services.agent_runner.notify_facts import digest
    from src.services.recruiting_notify_service import upsert_settings, get_settings, format_done_content
    from src.local_tools.notify_flow import _post_phase
    actor, profile = recruiting_domain
    marker = workers.provider.register(Reply(content='not-an-engine-conversation-fixture'))
    accepted = accept(workers.api, actor, marker, profile_id=profile)
    repository = RunnerRepository(service_database.connect)
    execution = ExecutionRepository(service_database.connect)
    acquired = execution.acquire('fixture-native-notify-owner', 120)
    assert acquired['runner_id'] == accepted['runner_id']
    identity = Identity(*(acquired[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')))
    root = ExecutionState(identity, accepted['runner_id'], AgentMode.MASTER, 'Declared native owner fixture', [], profile_id=profile)
    children = []
    for ordinal in range(2):
        parent_call = 'native-delegate-' + str(ordinal)
        root.tools[parent_call] = ToolFact(ToolCall(parent_call, 'delegate', {'subagent_name': profile}), phase='dispatching')
        state = ExecutionState(identity, 'native-notify-child-' + uuid.uuid4().hex, AgentMode.SUBAGENT,
                               'Declared child owner fixture', [], profile_id=profile)
        arguments = {'kind': 'done', 'job_name': '虚构契约岗位',
                     'candidates': [{'name': '虚构子名单' + str(ordinal), 'time': '虚构时间'}]}
        call_id = 'native-notify-' + str(ordinal)
        state.tools[call_id] = ToolFact(ToolCall(call_id, 'boss_interview_notify', arguments), phase='dispatching')
        root.children[parent_call] = {'execution_id': state.execution_id, 'checkpoint': state.checkpoint()}
        children.append((parent_call, state, call_id, arguments))
    attempt = Attempt(acquired['runner_id'], acquired['worker_id'], acquired['attempt'])
    saved = execution.save_checkpoint(attempt, acquired['revision'], {'execution': root.checkpoint()})
    control = DurableControl(execution, attempt, saved)
    control.state = root
    # The CLI reads this fixture peer from its explicit environment. This
    # in-process owner composition must supply the same normal DI config;
    # ambient pytest settings contain no randomly generated API peer.
    config = settings.agent_runner.model_copy(update={'peers': {
        **settings.agent_runner.peers,
        workers.api.service_id: AgentRunnerPeerConfig(
            sources=['chat', 'wecom_kf', 'feishu', 'dingtalk']),
    }})
    async def authorize(profile_id=None):
        await asyncio.to_thread(RunnerAuthorizer(config, service_database.connect).authorize_persisted,
                               repository.get(attempt.runner_id), execute=True)
    control.authorization_check = authorize
    with WebhookPeer(WebhookReply(), WebhookReply()) as peer:
        upsert_settings(actor.tenant_id, enabled=True, webhook_url=peer.url, at_mobiles=[])
        configuration = get_settings(actor.tenant_id)
        trusted_config = {'tenant_id': actor.tenant_id, 'version': configuration['updated_at'].isoformat(),
                          'at_digest': digest([])}
        for parent_call, state, call_id, arguments in children:
            child = ChildControl(root, control, parent_call)
            owner = RunnerLocalLifecycle(child, state, call_id)
            original = {**arguments, 'note': None}
            content = format_done_content(arguments['job_name'], arguments['candidates'])
            await owner.complete_phase(branch='notify.binding', ordinal=0,
                intent={**original, 'notify_binding_version': 1}, result={'resume_id': None})
            await owner.complete_phase(branch='notify.policy', ordinal=0,
                intent={**original, 'resume_id': None, 'notify_policy_version': 1},
                result={'configuration': trusted_config, 'content': content, 'markdown_count': 1,
                        'mention_required': False, 'mention_payload_digest': digest({'unused': True})})
            payload = {'msgtype': 'markdown', 'markdown': {'content': content}}
            intent = {'configuration': trusted_config, 'payload_digest': digest(payload),
                      'chunk': 0, 'attempt': 0, 'notify_phase_version': 1}
            async def dispatch(payload=payload):
                return peer.url, payload
            fact = await _post_phase(owner, branch='notify.markdown', ordinal=0, intent=intent, dispatch=dispatch)
            assert fact == {'outcome': 'acknowledged', 'provider_code': 0}
        assert len(peer.requests) == 2 and not peer.errors
    assert workers.api.call('POST', f"/v1/runners/{accepted['runner_id']}/cancel", actor=actor).status_code == 200
    # Mutable live messages are deliberately newer than the original DB row.
    root.messages.append({'role': 'user', 'content': 'fictional-live-root-message'})
    for child in root.children.values():
        child['checkpoint']['messages'].append({'role': 'user', 'content': 'fictional-live-child-message'})
    return actor, accepted, root, control


async def test_live_cancel_does_not_audit_siblings_and_drained_projection_keeps_all_facts_once(
        acknowledged_tree, service_database):
    from src.services.agent_runner.local_recovery import cancel_owned_invocations
    from src.services.agent_runner.cancellation_projection import accept_cancellation_row
    actor, accepted, root, control = acknowledged_tree
    before = runner(service_database, accepted['runner_id'])
    live_messages = {key: copy.deepcopy(child['checkpoint']['messages']) for key, child in root.children.items()}
    default = cancel_owned_invocations(service_database.connect, control.attempt)
    assert default.row['revision'] == before['revision']
    assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    # The explicit drained/cancel-only owner is the only full-tree audit path.
    audited = cancel_owned_invocations(service_database.connect, control.attempt, complete_audit=True)
    accept_cancellation_row(control, audited.row, root)
    for key, child in root.children.items():
        assert child['checkpoint']['messages'] == live_messages[key]
        phases = child['checkpoint']['resources']['local_domain_phases']
        assert sum(phase['branch'] == 'notify.log' for phase in phases.values()) == 1
    assert root.messages == [{'role': 'user', 'content': 'fictional-live-root-message'}]
    await control.save(root, 'declared-drained-owned-tree')
    again = cancel_owned_invocations(service_database.connect, control.attempt, complete_audit=True)
    assert again.row['revision'] == control.revision
    assert service_database.rows('SELECT status FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == [{'status': 'sent'}, {'status': 'sent'}]
    assert decoded(again.row['checkpoint'])['execution']['children'] == root.children


async def test_notify_log_and_entire_child_tree_checkpoint_roll_back_together(acknowledged_tree, service_database):
    from src.services.agent_runner.local_recovery import cancel_owned_invocations
    from src.core.agent_engine.contracts import CheckpointFailure
    actor, accepted, root, control = acknowledged_tree
    before = runner(service_database, accepted['runner_id'])
    function = 'fixture_notify_cas_' + uuid.uuid4().hex
    trigger = function + '_trigger'
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL('''CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.runner_id = {} AND NEW.revision > OLD.revision THEN
              RAISE EXCEPTION 'FIXTURE_NOTIFY_CHECKPOINT_REFUSED'; END IF;
            RETURN NEW; END $$''').format(sql.Identifier(function), sql.Literal(accepted['runner_id'])))
        cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON agent_runners FOR EACH ROW EXECUTE FUNCTION {}()').format(sql.Identifier(trigger), sql.Identifier(function)))
    try:
        with pytest.raises(CheckpointFailure):
            cancel_owned_invocations(service_database.connect, control.attempt, complete_audit=True)
        assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
        after = runner(service_database, accepted['runner_id'])
        assert after['checkpoint'] == before['checkpoint'] and after['revision'] == before['revision']
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER {} ON agent_runners').format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(function)))
    cancel_owned_invocations(service_database.connect, control.attempt, complete_audit=True)
    assert service_database.rows('SELECT count(*) AS n FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == [{'n': 2}]


async def test_original_epoch_cannot_write_notification_audits_using_new_owner_row(acknowledged_tree, service_database):
    from src.services.agent_runner.local_recovery import cancel_owned_invocations
    from src.services.agent_runner.ownership import Attempt, LeaseLost
    actor, accepted, root, control = acknowledged_tree
    # Explicit native epoch replacement fixture, not a public recovery flow.
    rows = service_database.rows('''UPDATE agent_runners SET attempt=attempt+1,worker_id=%s,
        lease_until=clock_timestamp()+interval '120 seconds' WHERE runner_id=%s RETURNING *''',
        ('fixture-new-notify-owner', accepted['runner_id']))
    replacement = rows[0]
    with pytest.raises(LeaseLost):
        cancel_owned_invocations(service_database.connect, control.attempt, complete_audit=True)
    after = runner(service_database, accepted['runner_id'])
    assert after['checkpoint'] == replacement['checkpoint'] and after['attempt'] == replacement['attempt']
    assert service_database.rows('SELECT 1 FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    current = Attempt(after['runner_id'], after['worker_id'], after['attempt'])
    cancel_owned_invocations(service_database.connect, current, complete_audit=True)
    assert service_database.rows('SELECT count(*) AS n FROM bs_recruiting_notify_logs WHERE tenant_id=%s', (actor.tenant_id,)) == [{'n': 2}]
