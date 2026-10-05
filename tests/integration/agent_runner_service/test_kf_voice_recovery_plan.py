"""First Engine recovery retains a prior PG business plan.

The prior completed root is an explicit PG history-state fixture constructed
with the original ExecutionPlan model. It has no delivery claim and is not
claimed to have been dispatched, delivered or acknowledged. Current Voice,
Factory/BusinessPlan/Read/Engine/history/fees are the real original chain.
"""
from decimal import Decimal
import json
import secrets

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price
from .kf_voice_peer import AsrReply
from .kf_admission_service import verify_original_resources
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded, tool_reply
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


def test_actual_first_engine_voice_recovery_inherits_original_pg_plan_and_read_advances_only_one_task(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    from src.models.plan import ExecutionPlan, Task
    from src.channels.wecom_kf.voice_repository import VoiceRepository
    v, s = voice_scope, voice_scope.scope
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    previous_id = 'voice_plan_previous_' + secrets.token_hex(12)
    fixture = fleet.root / 'voice-inherited-plan.txt'
    fixture.write_text('original-voice-plan-read')
    plan = ExecutionPlan(plan_id='plan_' + secrets.token_hex(12), intent='Original previous PG plan', tasks=[
        Task(task_id='first', tool_name='read', description='Read once', parameters={'file_path': str(fixture)}),
        Task(task_id='second', tool_name='read', description='Read later', parameters={'file_path': str(fixture)})])
    plan_data = plan.model_dump(mode='json')
    marker = provider_peer.register(tool_reply('read', {'file_path': str(fixture)}, call_id='voice-plan-read'),
        Reply(content='Original inherited first task completed'))
    transcript = marker + ' exact original first Engine recovery transcript'
    v.peer.replies.append(AsrReply(raw_body=b'{'))
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    try:
        verify_original_resources(fleet, v.api)
        current = runner(s.database, identifier)
        # A free immediately preceding BIGINT order is a legal history-state
        # fixture. No prior claim is removed and no delivery is invented.
        previous_order = current['queue_order'] - 1
        assert s.rows('SELECT 1 FROM agent_runners WHERE queue_order=%s', (previous_order,)) == []
        with s.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('''INSERT INTO agent_runners
                (runner_id,queue_order,tenant_id,scope_key,session_kind,session_id,actor_kind,actor_id,
                 user_id,service_id,source,client_request_id,input_digest,input,profile_id,profile_fingerprint,
                 checkpoint,status,settlement_status,record_id,result,finished_at)
                SELECT %s,%s,tenant_id,scope_key,session_kind,session_id,actor_kind,actor_id,
                 user_id,service_id,source,%s,input_digest,input,profile_id,profile_fingerprint,
                 %s::jsonb,'completed','settled',%s,%s::jsonb,clock_timestamp()
                FROM agent_runners WHERE runner_id=%s''',
                (previous_id, previous_order, 'prior_plan_' + secrets.token_hex(12), json.dumps({'business_plan': plan_data}),
                 'record_fixture_' + secrets.token_hex(12), json.dumps({'status': 'completed', 'output': 'PG fixture'}), identifier))
        before = runner(s.database, previous_id)
        assert s.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (previous_id,)) == []
        first = start_voice_worker(fleet, v)
        interrupted = wait_for(lambda: r if (r := runner(s.database, identifier))['status'] == 'interrupted' else None, timeout=25)
        fleet.assert_clean_exit(first)
        assert decoded(interrupted['checkpoint']).get('unstarted') is True
        assert not decoded(interrupted['checkpoint']).get('execution')
        prep = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert prep['phase'] == 'unknown' and prep['authorized_attempt'] == 1
        assert post_count(v) == 1 and not provider_peer.requests(marker)
        # Known-result re-projection is explicit original typed-observer PG DI;
        # it does not assert natural reconstruction of a lost HTTP response.
        VoiceRepository(None, s.database.connect).result(input_ref, success=True, text=transcript, status=20000000)
        response = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=input_ref),
            json={'action': 'resume', 'client_request_id': 'voice_plan_resume_' + secrets.token_hex(10)})
        assert response.status_code == 202
        second = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(second)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled' and finished['attempt'] == 2
        completed_plan = decoded(finished['checkpoint'])['business_plan']
        assert completed_plan['plan_id'] == plan.plan_id
        assert [task['status'] for task in completed_plan['tasks']] == ['completed', 'pending']
        assert runner(s.database, previous_id) == before
        assert len(provider_peer.requests(marker)) == 2 and post_count(v) == 1
        assert s.rows('SELECT asr_calls,total_token_count,credit_cost,user_message FROM chat_records WHERE record_id=%s',
            (finished['record_id'],)) == [{'asr_calls': 1, 'total_token_count': 36,
                'credit_cost': Decimal('0.11'), 'user_message': '[ASR识别结果] ' + transcript}]
        receipts = s.rows('SELECT owner,authorized_attempt,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 3 and all(r['phase'] == 'observed' and r['applied'] for r in receipts)
        assert [r['authorized_attempt'] for r in receipts if r['owner'] == 'asr'] == [1]
        assert all(r['authorized_attempt'] == 2 for r in receipts if r['owner'] == 'llm')
        assert s.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s', (input_ref,)) == [{'phase': 'applied'}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s',
            (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'first_engine_plan', error)
        raise
    finally:
        fleet.close()
        s.rows('DELETE FROM agent_runner_controls WHERE runner_id=%s', (previous_id,))
        s.rows('DELETE FROM agent_runners WHERE runner_id=%s', (previous_id,))
