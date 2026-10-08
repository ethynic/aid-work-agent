"""Actual overlay original phases across process loss; prepare, await A freeze."""
from decimal import Decimal
from pathlib import Path
import json
import uuid

import pytest

from .conftest import wait_for
from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_local_overlay_flow import overlay_steps
from .test_local_invocation_recovery import interrupted
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('boundary,hard', [
    ('inspect-original-inflight',False),
    ('choice-response-committed',True),
    ('dismiss-known-retry-inflight',True),
    ('heal-fee-committed',True),
])
def test_overlay_resume_reuses_all_original_phases_receipts_and_fees(
        workers, recruiting_domain, service_database, boundary, hard):
    actor, profile = recruiting_domain
    workers.environment['LITE_MODEL_CODE'] = 'qwen/' + workers.models[0]
    choice_marker = 'fixture-overlay-' + uuid.uuid4().hex[:12]
    workers.provider.register(Reply(content=json.dumps({'found':True,'text':'关闭'})), marker=choice_marker)
    marker = workers.provider.register(tool_reply('boss_select_job',
        {'job_name':'Fictional exact job'}, call_id='recovery-overlay-call'),
        Reply(content='overlay-after-real-process-loss'))
    accepted = accept(workers.api, actor, marker, profile_id=profile)
    probe = boundary in {'choice-response-committed','heal-fee-committed'}
    with ScriptedDesktop(workers, actor, overlay_steps(choice_marker)) as desktop:
        for ordinal in range(4):
            desktop.allow_claim(ordinal)
        if probe:
            branch = 'heal.choice' if boundary=='choice-response-committed' else 'heal.fee'
            gate = workers.root / ('domain-commit-' + uuid.uuid4().hex)
            gate.mkdir()
            worker_id = 'fixture-domain-commit-' + uuid.uuid4().hex
            first = workers.processes.start([str(Path(__file__).with_name('domain_commit_probe.py')),
                worker_id,branch,'0',str(gate)], environment=workers.environment,
                private_working_directory=True)
            workers.children.append((first,worker_id))
        else:
            first, _ = workers.start()
        last_completed = {'inspect-original-inflight':0,'choice-response-committed':1,
            'dismiss-known-retry-inflight':2,'heal-fee-committed':3}[boundary]
        original_ids = []
        for ordinal in range(last_completed+1):
            original_ids.append(desktop.claimed(ordinal)['invocation_id'])
            desktop.allow_result(ordinal)
            desktop.completed(ordinal)
        if probe:
            wait_for((gate / 'committed.json').exists,timeout=15)
            commit = json.loads((gate / 'committed.json').read_text())
            assert commit['runner_id']==accepted['runner_id'] and commit['branch']==branch
        else:
            original_ids.append(desktop.claimed(last_completed+1)['invocation_id'])
        stopped = interrupted(workers, service_database, accepted, first, hard=hard)
        prior = decoded(stopped['checkpoint'])['execution']
        refs_before = prior['resources']['local_invocations']
        if boundary=='heal-fee-committed':
            assert service_database.rows("SELECT count(*) AS n FROM client_usage_logs WHERE tenant_id=%s AND model='boss_overlay_heal'",
                                          (actor.tenant_id,)) == [{'n':1}]
        submitted, _ = control(workers.api, actor, accepted['runner_id'], 'resume')
        # No post-commit decoration on recovery: normal operator CLI rehydrates
        # real checkpoints and runs the actual registered proxy/domain owners.
        second, _ = workers.start()
        for ordinal in range(last_completed+1,4):
            claimed = desktop.claimed(ordinal)['invocation_id']
            if ordinal < len(original_ids):
                assert claimed == original_ids[ordinal]
            else:
                original_ids.append(claimed)
            desktop.allow_result(ordinal)
            desktop.completed(ordinal)
        workers.assert_clean_exit(desktop.child)
        workers.assert_clean_exit(second)
        finished = terminal(service_database, accepted['runner_id'])
    assert finished['status']=='completed'
    assert decoded(finished['result'])['output']=='overlay-after-real-process-loss'
    assert finished['attempt']==stopped['attempt']+1
    after = decoded(finished['checkpoint'])['execution']
    for key, reference in refs_before.items():
        assert after['resources']['local_invocations'][key] == reference
    actual_ids = service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s ORDER BY created_at,id',
                                        (actor.session_id,))
    assert [str(row['id']) for row in actual_ids] == original_ids and len(set(original_ids))==4
    assert service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s ORDER BY model',
                                  (actor.tenant_id,)) == [
        {'model':'boss_overlay_heal','credit_cost':Decimal('2.00')},
        {'model':'boss_select_job','credit_cost':Decimal('0.50')}]
    receipts = service_database.rows('SELECT receipt_id,phase,applied,purpose FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                      (accepted['runner_id'],))
    assert len(receipts)==3 and all(row['phase']=='observed' and row['applied'] for row in receipts)
    choice = [fact for fact in after['resources']['local_domain_phases'].values() if fact['branch']=='heal.choice']
    assert len(choice)==1 and choice[0]['phase']=='completed'
    assert [row['receipt_id'] for row in receipts if row['purpose'].startswith('domain:')] == [choice[0]['result']['_runner_receipt_id']]
    assert len(after['model_calls'])==2
    assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                                  (actor.session_id,)) == [{'total_token_count':54,'credit_cost':Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
                                  (actor.tenant_id,))[0]['credit_balance']==Decimal('997.49')
    assert len(workers.provider.requests(marker))==2 and len(workers.provider.requests(choice_marker))==1
    assert not workers.provider.errors
    observed = workers.api.call('GET',
        f"/v1/runners/{accepted['runner_id']}/controls/{submitted['control']['control_id']}",actor=actor)
    assert observed.status_code==200 and observed.json()['control']['status']=='consumed'
