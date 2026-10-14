"""Continuation through real Runner, model authorization, fees and recovery.

Only desktop result IO and the model HTTP peer are fictional. In particular,
none of the four ownership proofs or canonical argument preparation is mocked.
"""
from decimal import Decimal
import json
import re
from pathlib import Path
import uuid

import pytest

from .conftest import wait_for
from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_local_resume_flow import resume_payload, evaluation_reply
from .test_local_invocation_recovery import interrupted, local_diagnosis
from .test_worker import workers, api_pair, prices, accept, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


def diagnosed_terminal(workers, database, identifier):
    try:
        return terminal(database, identifier)
    except AssertionError:
        row = database.rows('SELECT checkpoint FROM agent_runners WHERE runner_id=%s', (identifier,))[0]
        state = decoded(row['checkpoint']).get('execution') or {}
        diagnosis = local_diagnosis(workers, database, identifier)
        diagnosis['domain_phases'] = [{key: phase.get(key) for key in ('branch', 'phase', 'ordinal')}
            for phase in state.get('resources', {}).get('local_domain_phases', {}).values()]
        diagnosis['tool_codes'] = [{key: (fact.get('result') or {}).get(key) for key in ('code', 'error_code')}
            for fact in state.get('tools', {}).values()]
        diagnosis['worker_stop_kinds'] = []
        for child, _ in workers.children:
            log_path = workers.processes.root / f'child-{workers.processes.children.index(child)}.log'
            if log_path.exists():
                diagnosis['worker_stop_kinds'].extend(re.findall(r'AgentRunner attempt stopped runner=\S+ kind=(\w+)', log_path.read_text()))
        raise AssertionError('Runner did not become terminal: ' + json.dumps(diagnosis)) from None


@pytest.mark.parametrize('boundary', [None, 'resume.evaluate', 'resume.fee'])
def test_next_scores_and_restores_original_images_without_duplicate_reads_or_fees(
        workers, recruiting_domain, service_database, boundary):
    actor, profile = recruiting_domain
    candidates = ['虚构续筛' + uuid.uuid4().hex[:10] for _ in range(2)]
    for candidate in candidates:
        workers.provider.register(evaluation_reply(candidate), marker=candidate)
    steps = [{'tool_name': tool, 'result': {'success': True, 'effect': 'none', 'data': {
        'job_name': 'Fixture PHP', 'stop_reason': 'limit_reached', 'processed_names': [candidate],
        'resumes': [resume_payload(candidate)], 'failures': [], 'attempted': 1}}}
        for tool, candidate in zip(['boss_resume_batch', 'boss_resume_next'], candidates)]
    # Per-item duplicate/job gates consume the same job context as the batch.
    for step in steps:
        step['result']['data']['resumes'][0]['job_name'] = 'Fixture PHP'
    with ScriptedDesktop(workers, actor, steps) as desktop:
        marker = workers.provider.register(tool_reply('boss_resume_batch', {'limit': 1}, call_id='parent-batch'),
                                           Reply(content='parent-finished'))
        desktop.allow_claim(0)
        parent = accept(workers.api, actor, marker, profile_id=profile)
        first, _ = workers.start()
        try:
            parent_id = desktop.claimed(0)['invocation_id']
        except AssertionError:
            row = service_database.rows('SELECT checkpoint FROM agent_runners WHERE runner_id=%s', (parent['runner_id'],))[0]
            result = decoded(row['checkpoint'])['execution']['tools']['parent-batch'].get('result') or {}
            raise AssertionError('Initial batch did not dispatch: ' + json.dumps(
                {**local_diagnosis(workers, service_database, parent['runner_id']),
                 'tool_failure': {key: result.get(key) for key in ('code', 'error', 'message')}})) from None
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(first)
        assert diagnosed_terminal(workers, service_database, parent['runner_id'])['status'] == 'completed'

        # Omitted limit exercises the canonical default added by prepare_next.
        marker = workers.provider.register(tool_reply('boss_resume_next', {'continue_from': parent_id}, call_id='next-call'),
                                           Reply(content='next-finished'))
        desktop.allow_claim(1)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        if boundary:
            gate = workers.root / ('next-commit-' + uuid.uuid4().hex)
            gate.mkdir()
            worker_id = 'fixture-next-' + uuid.uuid4().hex
            child = workers.processes.start([str(Path(__file__).with_name('domain_commit_probe.py')),
                worker_id, boundary, '0', str(gate)], environment=workers.environment, private_working_directory=True)
            workers.children.append((child, worker_id))
        else:
            child, _ = workers.start()
        original_id = desktop.claimed(1)['invocation_id']
        desktop.allow_result(1)
        desktop.completed(1)
        if boundary:
            wait_for((gate / 'committed.json').exists, timeout=30)
            stopped = interrupted(workers, service_database, accepted, child, hard=True)
            assert stopped['status'] == 'interrupted'
            control(workers.api, actor, accepted['runner_id'], 'resume')
            child, _ = workers.start()
        workers.assert_clean_exit(child)
    finished = diagnosed_terminal(workers, service_database, accepted['runner_id'])
    assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
    assert finished['attempt'] == (2 if boundary else 1)
    state = decoded(finished['checkpoint'])['execution']
    assert state['tools']['next-call']['result']['success']
    invocations = service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,))
    assert {str(row['id']) for row in invocations} == {parent_id, original_id}
    stored = service_database.rows('SELECT candidate_name FROM bs_recruiting_operator_resumes WHERE tenant_id=%s', (actor.tenant_id,))
    assert {row['candidate_name'] for row in stored} == set(candidates)
    fees = service_database.rows("SELECT credit_cost FROM client_usage_logs WHERE tenant_id=%s AND model='boss_resume_recognition'", (actor.tenant_id,))
    assert len(fees) == 2 and all(row['credit_cost'] == Decimal('1.00') for row in fees)
    receipts = service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (accepted['runner_id'],))
    covered = [row for row in receipts if decoded(row['price_snapshot']).get('covered_cost') == 'resume_recognition']
    assert len(covered) == 1 and covered[0]['tool_call_id'] == 'next-call'
    assert all(len(workers.provider.requests(name)) == 1 for name in candidates)
    assert not workers.provider.errors
