"""Actual domain commits interrupted at a return window, then default CLI.

The initial process wraps only the real commit return for timing; the next
process is the undecorated production operator. All fees/SQL/model IO are real
owners and the existing fictional outer IO peers. Await B source freeze.
"""
from decimal import Decimal
import json
from pathlib import Path
import uuid

from psycopg2 import sql
import pytest

from .conftest import wait_for
from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_local_resume_flow import resume_payload, evaluation_reply
from .test_local_invocation_recovery import interrupted
from .test_worker import workers, api_pair, prices, accept, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('boundary', ['detail-model-saved', 'detail-fee-before-insert-failure', 'batch-first-item-completed'])
def test_actual_resume_domain_recovery_keeps_original_model_fee_item_and_invocation(
        workers, recruiting_domain, service_database, boundary):
    actor, profile = recruiting_domain
    batch = boundary == 'batch-first-item-completed'
    candidates = ['虚构恢复' + uuid.uuid4().hex[:12] for _ in range(2 if batch else 1)]
    payloads = [resume_payload(name) for name in candidates]
    for name in candidates:
        workers.provider.register(evaluation_reply(name), marker=name)
    tool = 'boss_resume_batch' if batch else 'boss_resume_detail'
    arguments = {'limit': 2} if batch else {'candidate_name': candidates[0]}
    data = {'resumes': payloads, 'failures': [], 'attempted': 2} if batch else payloads[0]
    marker = workers.provider.register(tool_reply(tool, arguments, call_id='original-resume-recovery-call'),
                                       Reply(content='fictional-resume-recovered-output'))
    branch = {'detail-model-saved': 'resume.evaluate',
              'detail-fee-before-insert-failure': 'resume.fee',
              'batch-first-item-completed': 'resume.match'}[boundary]
    steps = [{'tool_name': tool, 'result': {'success': True, 'effect': 'none', 'data': data}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        gate = workers.root / ('resume-commit-' + uuid.uuid4().hex)
        gate.mkdir()
        worker_id = 'fixture-resume-commit-' + uuid.uuid4().hex
        first = workers.processes.start([str(Path(__file__).with_name('domain_commit_probe.py')),
            worker_id, branch, '0', str(gate)], environment=workers.environment,
            private_working_directory=True)
        workers.children.append((first, worker_id))
        invocation_id = desktop.claimed(0)['invocation_id']
        desktop.allow_result(0)
        desktop.completed(0)
        wait_for((gate / 'committed.json').exists, timeout=20)
        committed = json.loads((gate / 'committed.json').read_text())
        assert committed['runner_id'] == accepted['runner_id'] and committed['branch'] == branch
        stopped = interrupted(workers, service_database, accepted, first, hard=True)
        original = decoded(stopped['checkpoint'])['execution']
        refs = original['resources']['local_invocations']
        original_domain = original['resources']['local_domain_phases']
        saved_facts = {key: value for key, value in original_domain.items() if value['phase'] == 'completed'}
        fee_before = service_database.rows("SELECT count(*) AS n FROM client_usage_logs WHERE tenant_id=%s AND model='boss_resume_recognition'",
                                           (actor.tenant_id,))[0]['n']
        assert fee_before == (0 if boundary == 'detail-model-saved' else 1)
        constraint = None
        try:
            if boundary == 'detail-fee-before-insert-failure':
                # Real SQL constraint failure AFTER a legitimately committed
                # recognition fee; no fake DAL result or separate commit writer.
                constraint = 'fixture_resume_reject_' + uuid.uuid4().hex
                with service_database.connect() as connection, connection.cursor() as cursor:
                    cursor.execute(sql.SQL('ALTER TABLE bs_recruiting_operator_resumes ADD CONSTRAINT {} CHECK (tenant_id <> %s OR candidate_name <> %s)').format(sql.Identifier(constraint)),
                                   (actor.tenant_id, candidates[0]))
            submitted, _ = control(workers.api, actor, accepted['runner_id'], 'resume')
            second, _ = workers.start()
            workers.assert_clean_exit(second)
            finished = terminal(service_database, accepted['runner_id'])
        finally:
            if constraint:
                with service_database.connect() as connection, connection.cursor() as cursor:
                    cursor.execute(sql.SQL('ALTER TABLE bs_recruiting_operator_resumes DROP CONSTRAINT {}').format(sql.Identifier(constraint)))
    assert finished['status'] == 'completed' and finished['attempt'] == stopped['attempt'] + 1
    restored = decoded(finished['checkpoint'])['execution']
    assert restored['resources']['local_invocations'] == refs
    for key, value in saved_facts.items():
        assert restored['resources']['local_domain_phases'][key] == value
    invocations = service_database.rows('SELECT id,credit_cost FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,))
    assert len(invocations) == 1 and str(invocations[0]['id']) == invocation_id and not invocations[0]['credit_cost']
    fees = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert len(fees) == len(candidates) and all(row == {'model': 'boss_resume_recognition', 'credit_cost': Decimal('1.00')} for row in fees)
    stored = service_database.rows('SELECT id,candidate_name,images FROM bs_recruiting_operator_resumes WHERE tenant_id=%s', (actor.tenant_id,))
    if constraint:
        assert stored == []
        result = restored['tools']['original-resume-recovery-call']['result']
        assert not result['success'] and result['code'] == 'RESUME_STORE_FAILED'
        insert = [row for row in restored['resources']['local_domain_phases'].values() if row['branch'] == 'resume.insert']
        assert len(insert) == 1 and not insert[0]['result']['applied']
    else:
        assert len(stored) == len(candidates) and {row['candidate_name'] for row in stored} == set(candidates)
        assert len({decoded(row['images'])[0]['file_id'] for row in stored}) == len(candidates)
    assert all(len(workers.provider.requests(name)) == 1 for name in candidates)
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
    receipts = service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (accepted['runner_id'],))
    assert len(receipts) == 2 + len(candidates) and all(row['phase'] == 'observed' and row['applied'] for row in receipts)
    assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                                 (actor.session_id,)) == [{'total_token_count': 18 * (2 + len(candidates)), 'credit_cost': Decimal('0.01')}]
    observed = workers.api.call('GET', f"/v1/runners/{accepted['runner_id']}/controls/{submitted['control']['control_id']}", actor=actor)
    assert observed.status_code == 200 and observed.json()['control']['status'] == 'consumed'
