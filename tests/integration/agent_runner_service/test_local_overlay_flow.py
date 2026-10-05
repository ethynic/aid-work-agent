"""Actual registered Boss proxy overlay continuation; fictional outer IO only.

Prepared against the handoff contract. Formal acceptance must wait for the A
source freeze. The implementation agent may run only the happy path as its
isolated developmental check before freezing the slice.
"""
from decimal import Decimal
import json
import uuid

import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


def overlay_steps(marker, *, original_effect='none', original_code='UI_CHANGED'):
    return [
        {'tool_name':'boss_select_job', 'result':{'success':False,
            'code':original_code, 'effect':original_effect}},
        {'tool_name':'boss_overlay_inspect', 'result':{'effect':'none', 'data':{
            'candidates':[
                {'text':'关闭','cls':'btn-default','x':10,'y':10,'w':30,'h':20},
                {'text':marker,'cls':'label','x':20,'y':50,'w':100,'h':20}],
            'icon_candidates':[], 'viewport':{'width':1000,'height':800}}}},
        {'tool_name':'boss_overlay_dismiss', 'result':{'effect':'applied','data':{'dismissed':True}}},
        {'tool_name':'boss_select_job', 'result':{'effect':'applied','data':{'selected':True}}},
    ]


def test_actual_overlay_none_effect_choice_retry_and_two_billing_owners(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    # Both the ordinary model and the real lite/choice producer resolve a real
    # isolated price. No price resolver/Gateway/producer replacement.
    workers.environment['LITE_MODEL_CODE'] = 'qwen/' + workers.models[0]
    choice_marker = 'fixture-overlay-' + uuid.uuid4().hex[:12]
    workers.provider.register(Reply(content=json.dumps({'found':True,'text':'关闭'})), marker=choice_marker)
    marker = workers.provider.register(
        tool_reply('boss_select_job', {'job_name':'Fictional exact job'}, call_id='overlay-call'),
        Reply(content='actual-overlay-restored-output'))
    with ScriptedDesktop(workers, actor, overlay_steps(choice_marker)) as desktop:
        for ordinal in range(4):
            desktop.allow_claim(ordinal)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        child, _ = workers.start()
        identifiers = []
        for ordinal in range(4):
            identifiers.append(desktop.claimed(ordinal)['invocation_id'])
            desktop.allow_result(ordinal)
            assert desktop.completed(ordinal)['invocation_id'] == identifiers[-1]
        workers.assert_clean_exit(desktop.child)
        workers.assert_clean_exit(child)
        finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed'
    assert decoded(finished['result'])['output'] == 'actual-overlay-restored-output'
    assert len(set(identifiers)) == 4
    invocations = service_database.rows(
        'SELECT id,tool_name,state,effect,credit_cost FROM local_tool_invocations WHERE session_id=%s ORDER BY created_at,id',
        (actor.session_id,))
    assert [str(row['id']) for row in invocations] == identifiers
    assert [row['tool_name'] for row in invocations] == [step['tool_name'] for step in overlay_steps(choice_marker)]
    assert [(row['state'],row['effect']) for row in invocations] == [
        ('failed','none'),('succeeded','none'),('succeeded','applied'),('succeeded','applied')]
    usage = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s ORDER BY model',
                                  (actor.tenant_id,))
    assert usage == [{'model':'boss_overlay_heal','credit_cost':Decimal('2.00')},
                     {'model':'boss_select_job','credit_cost':Decimal('0.50')}]
    receipts = service_database.rows(
        'SELECT receipt_id,execution_id,tool_call_id,purpose,phase,usage,applied FROM agent_runner_usage_receipts WHERE runner_id=%s',
        (accepted['runner_id'],))
    assert len(receipts) == 3 and all(row['phase']=='observed' and row['applied'] for row in receipts)
    domain_receipts = [row for row in receipts if row['purpose'].startswith('domain:')]
    assert len(domain_receipts) == 1 and domain_receipts[0]['tool_call_id'] == 'overlay-call'
    state = decoded(finished['checkpoint'])['execution']
    assert len(state['model_calls']) == 2 # Domain response is not an Engine model step.
    domain_facts = list(state['resources']['local_domain_phases'].values())
    choice = [fact for fact in domain_facts if fact['branch']=='heal.choice']
    assert len(choice) == 1 and choice[0]['phase']=='completed'
    assert choice[0]['result']['_runner_receipt_id'] == domain_receipts[0]['receipt_id']
    assert len([fact for fact in domain_facts if fact['branch']=='heal.fee' and fact['phase']=='completed']) == 1
    record = service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                                   (actor.session_id,))
    assert record == [{'total_token_count':54,'credit_cost':Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
                                  (actor.tenant_id,))[0]['credit_balance'] == Decimal('997.49')
    assert len(workers.provider.requests(marker)) == 2
    assert len(workers.provider.requests(choice_marker)) == 1 and not workers.provider.errors


@pytest.mark.parametrize('effect', ['partial', 'applied', None])
@pytest.mark.parametrize('code', ['UI_CHANGED', 'BUSY'])
def test_failed_original_with_possible_effect_never_starts_healing_or_consumes_resume(
        workers, recruiting_domain, service_database, effect, code):
    actor, profile = recruiting_domain
    marker = workers.provider.register(tool_reply('boss_select_job',
        {'job_name':'Fictional unsafe-effect job'}, call_id='unsafe-heal-call'))
    # The real client DAL preserves these protocol-legal v1 effects. The fixture
    # must not normalize the result/CP to none to make it look safely retryable.
    steps = overlay_steps('unused-choice-marker', original_effect=effect, original_code=code)[:1]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        child, _ = workers.start()
        original = desktop.claimed(0)['invocation_id']
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(desktop.child)
        workers.assert_clean_exit(child)
    before = runner(service_database, accepted['runner_id'])
    assert before['status'] == 'waiting'
    waiting = decoded(before['public_snapshot'])['waiting']
    assert waiting['kind'] == 'verification'
    assert waiting['error_code'] == 'LOCAL_HEAL_EFFECT_VERIFICATION_REQUIRED'
    assert service_database.rows('SELECT state,effect,error_code FROM local_tool_invocations WHERE id=%s',
                                  (original,)) == [{'state':'failed','effect':effect,'error_code':code}]
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE session_id=%s',
                                  (actor.session_id,)) == [{'n':1}]
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    claim_before = service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                          (accepted['runner_id'],))
    receipts_before = service_database.rows('SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                             (accepted['runner_id'],))
    assert len(receipts_before) == 1
    submitted, _ = control(workers.api, actor, accepted['runner_id'], 'resume')
    retry, _ = workers.start()
    workers.assert_clean_exit(retry)
    receipt = workers.api.call('GET',
        f"/v1/runners/{accepted['runner_id']}/controls/{submitted['control']['control_id']}", actor=actor)
    assert receipt.status_code == 200
    assert receipt.json()['control']['status'] == 'rejected'
    assert receipt.json()['control']['error_code'] == 'RECOVERY_TOOL_VERIFICATION_REQUIRED'
    assert service_database.rows('SELECT consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
        (submitted['control']['control_id'],)) == [{'consumed_attempt':None}]
    after = runner(service_database, accepted['runner_id'])
    assert after['attempt'] == before['attempt'] and after['input'] == before['input']
    assert decoded(after['checkpoint']) == decoded(before['checkpoint'])
    assert service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                  (accepted['runner_id'],)) == claim_before
    assert service_database.rows('SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                  (accepted['runner_id'],)) == receipts_before
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE session_id=%s',
                                  (actor.session_id,)) == [{'n':1}]
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
