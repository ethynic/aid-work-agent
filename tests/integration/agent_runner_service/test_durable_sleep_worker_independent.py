"""Fresh API/worker acceptance with real PG and local scripted provider I/O."""
import json
import copy
import os
import re
from types import MethodType
import uuid

import pytest

from .conftest import Processes, wait_for
from .test_api import start_api_pair, require_status
from .test_worker import workers, prices, accept, runner, terminal, decoded
from .provider import Reply, tool_reply
from .test_worker_controls import child_profile

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def service_processes(tmp_path_factory, service_database):
    processes = Processes(tmp_path_factory.mktemp('independent-sleep-workers'), service_database)
    if os.name == 'nt':
        # Existing Unix fixture cleanup uses killpg. These child interpreters
        # spawn no providers; terminate only the exact owned Popen handles.
        def stop(self, child, *, force=False):
            assert child in self.children
            if child.poll() is None:
                (child.kill if force else child.terminate)()
                child.wait(timeout=10)
        processes.stop = MethodType(stop, processes)
    original_start = processes.start
    def start(self, arguments, **options):
        gate = (options.get('environment') or {}).get('AID_TEST_CHILD_ADMISSION_GATE')
        if arguments[:2] == ['-m', 'src.services.agent_runner.worker'] and (os.name == 'nt' or gate):
            # Adapt OS signals and add an optional deterministic admission race
            # gate; actual CLI/run_worker and production execution still run.
            script = '''
import asyncio, os, runpy, signal, sys, time
from pathlib import Path
if os.name=='nt':
    def register(self, signum, callback, *args):
        signal.signal(signum,lambda *_:self.call_soon_threadsafe(callback,*args))
    asyncio.ProactorEventLoop.add_signal_handler=register
if os.environ.get('AID_TEST_CHILD_ADMISSION_GATE'):
    from src.services.agent_runner.usage_repository import UsageRepository
    original_start=UsageRepository.start
    directory=Path(os.environ['AID_TEST_CHILD_ADMISSION_GATE'])
    def gated_start(self,attempt,**values):
        if values.get('execution_id')!=attempt.runner_id and not (directory/'arrived').exists():
            (directory/'arrived').write_text('ready')
            deadline=time.monotonic()+20
            while not (directory/'release').exists():
                if time.monotonic()>deadline: raise RuntimeError('INDEPENDENT_TEST_GATE_TIMEOUT')
                time.sleep(.01)
        return original_start(self,attempt,**values)
    UsageRepository.start=gated_start
sys.argv=['worker',*sys.argv[1:]]
runpy.run_module('src.services.agent_runner.worker',run_name='__main__')
'''
            arguments = ['-c', script, *arguments[2:]]
        return original_start(arguments, **options)
    processes.start = MethodType(start, processes)
    try:
        yield processes
    finally:
        for index, child in enumerate(processes.children):
            if child.poll() not in (0, None):
                log = processes.root / f'child-{index}.log'
                if log.exists():
                    text = log.read_text(errors='replace')
                    print('Independent child diagnosis:', {
                        'exit': child.returncode,
                        'classes': re.findall(r'^([A-Za-z_.]+(?:Error|Exception)):', text, re.MULTILINE),
                        'assertions': re.findall(r'^AssertionError: ([A-Za-z0-9 _.-]+)$', text, re.MULTILINE),
                        'missing_modules': re.findall(r"No module named '([A-Za-z0-9_.]+)'", text),
                        'locations': re.findall(r'File "([^"]+)", line ([0-9]+)', text)[-5:],
                    })
        processes.close()


@pytest.fixture(scope='module')
def api_pair(service_processes):
    return start_api_pair(service_processes, extra_environment={'AGENT_RUNNER_DURABLE_SLEEP_ENABLED': 'true'})


@pytest.mark.parametrize('wake', ['timer', 'user_resume', 'due_with_resume_input'])
def test_actual_worker_parks_exits_and_fresh_worker_resumes_same_sleep_once(
        workers, actors, service_database, wake):
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    marker = workers.provider.register(tool_reply('agent_sleep', {'seconds': 60 if wake == 'user_resume' else 1},
                                                  call_id='independent-worker-sleep'),
                                       Reply(content='resumed-original-task-once'))
    accepted = accept(workers.api, actors['a'], marker)
    first, _ = workers.start()
    parked = wait_for(lambda: (row if (row := runner(service_database, accepted['runner_id']))['status'] == 'waiting'
                              else None), timeout=25)
    workers.assert_clean_exit(first)
    assert len(workers.provider.requests(marker)) == 1
    assert parked['wake_at'] is not None and parked['lease_until'] is None and parked['worker_id'] is None
    before = decoded(parked['checkpoint'])
    assert before['execution']['resources']['business_iterations'] == 0
    wait = before['execution']['waiting']
    public = require_status(workers.api.call('GET', '/v1/runners/'+accepted['runner_id'], actor=actors['a']), 200)['runner']
    public_wait = public['snapshot']['waiting']
    assert public_wait['kind'] == 'timer' and public_wait['wake_at']
    if wake == 'due_with_resume_input':
        wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                               (accepted['runner_id'],))[0]['due'], timeout=65)
    if wake in {'user_resume', 'due_with_resume_input'}:
        supplement = {'answer': 'new-original-task-instruction '+marker} if wake == 'due_with_resume_input' else {}
        require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/controls', actor=actors['a'],
                       json={'client_request_id': uuid.uuid4().hex, 'action': 'resume',
                             'wait_id': wait['wait_id'], 'target_execution_id': wait['target_execution_id'], **supplement}), 202)
    else:
        wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                               (accepted['runner_id'],))[0]['due'], timeout=10)
    second, _ = workers.start()
    completed = terminal(service_database, accepted['runner_id'])
    workers.assert_clean_exit(second)
    assert completed['status'] == 'completed' and completed['attempt'] == parked['attempt'] + 1
    assert completed['record_id'] == parked['record_id'] and completed['input_digest'] == parked['input_digest']
    checkpoint = decoded(completed['checkpoint'])
    fact = checkpoint['execution']['tools'][wait['tool_call_id']]
    assert fact['result']['trigger'] == ('timer' if wake == 'timer' else 'user_resume') and fact['result_recorded']
    paired = [message for message in checkpoint['execution']['messages']
              if message.get('tool_call_id') == wait['tool_call_id']]
    assert len(paired) == 1
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
    if wake == 'due_with_resume_input':
        assert any(message.get('role') == 'user' and 'new-original-task-instruction' in str(message.get('content'))
                   for message in workers.provider.requests(marker)[1]['messages'])
    receipts = service_database.rows('SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                     (accepted['runner_id'],))
    assert len(receipts) == 2 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
    record = service_database.rows('SELECT prompt_tokens,completion_tokens FROM chat_records WHERE record_id=%s',
                                   (completed['record_id'],))[0]
    assert record['prompt_tokens'] == 22 and record['completion_tokens'] == 14
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                 (accepted['runner_id'],)) == []


def test_actual_child_sleep_restores_original_subtree_in_fresh_worker(
        workers, actors, service_database, child_profile):
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    first = Reply(content='')
    marker = workers.provider.register(first,
        tool_reply('agent_sleep', {'seconds': 1}, call_id='independent-child-sleep'),
        Reply(content='original-child-finished'), Reply(content='original-parent-finished'))
    first.tool_calls = tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker},
        call_id='independent-parent-delegate').tool_calls
    accepted = accept(workers.api, actors['a'], marker)
    before, _ = workers.start()
    parked = wait_for(lambda: (row if (row := runner(service_database, accepted['runner_id']))['status'] == 'waiting'
                              else None), timeout=25)
    workers.assert_clean_exit(before)
    assert len(workers.provider.requests(marker)) == 2
    checkpoint = decoded(parked['checkpoint'])
    delegate_id = next(iter(checkpoint['execution']['children']))
    original_child = checkpoint['execution']['children'][delegate_id]
    assert original_child['checkpoint']['waiting']['kind'] == 'timer'
    assert checkpoint['execution']['waiting']['kind'] == 'child_wait'
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                           (accepted['runner_id'],))[0]['due'], timeout=10)
    after, _ = workers.start()
    completed = terminal(service_database, accepted['runner_id'])
    workers.assert_clean_exit(after)
    restored = decoded(completed['checkpoint'])['execution']['children'][delegate_id]
    assert completed['status'] == 'completed' and restored['execution_id'] == original_child['execution_id']
    assert restored['checkpoint']['outcome'] == 'completed'
    fact = restored['checkpoint']['tools'][original_child['checkpoint']['waiting']['tool_call_id']]
    assert fact['phase'] == 'completed' and fact['result_recorded'] and fact['result']['trigger'] == 'timer'
    assert len(workers.provider.requests(marker)) == 4 and not workers.provider.errors
    receipts = service_database.rows('SELECT execution_id,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                     (accepted['runner_id'],))
    assert len(receipts) == 4 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
    assert {item['execution_id'] for item in receipts} == {accepted['runner_id'], original_child['execution_id']}


@pytest.mark.parametrize('decision', ['positive', 'negative', 'cancel_before_consume'])
def test_real_bound_clarification_consumption_grants_only_the_confirmed_current_task(
        workers, actors, service_database, decision):
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    marker = workers.provider.register(tool_reply('ask_user', {'purpose': 'confirm_continuous'},
                                                   call_id='independent-confirm-continuous'),
                                       tool_reply('agent_sleep', {'seconds': 60}, call_id='confirmed-task-sleep'))
    accepted = accept(workers.api, actors['a'], marker)
    process, _ = workers.start()
    workers.assert_clean_exit(process)
    original = runner(service_database, accepted['runner_id'])
    assert original['status'] == 'waiting'
    wait = decoded(original['checkpoint'])['execution']['waiting']
    form = wait['form']
    option = form['questions'][0]['options'][1 if decision == 'negative' else 0]['key']
    request = {'client_request_id': uuid.uuid4().hex, 'action': 'reply',
               'wait_id': wait['wait_id'], 'target_execution_id': wait['target_execution_id'],
               'answers': [{'question_id': form['questions'][0]['id'], 'selected_keys': [option], 'text': None}]}
    path = '/v1/runners/'+accepted['runner_id']+'/controls'
    first = require_status(workers.api.call('POST', path, actor=actors['a'], json=request), 202)
    second = require_status(workers.api.call('POST', path, actor=actors['a'], json=request), 202)
    assert first['control']['control_id'] == second['control']['control_id']
    if decision == 'cancel_before_consume':
        require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actors['a']), 200)
    resumed, _ = workers.start()
    workers.assert_clean_exit(resumed)
    current = runner(service_database, accepted['runner_id'])
    checkpoint = decoded(current['checkpoint'])
    assert current['input_digest'] == original['input_digest'] and decoded(current['input']) == decoded(original['input'])
    assert 'execution_mode' not in decoded(current['input'])
    if decision == 'positive':
        assert checkpoint['continuous']['enabled'] is True
        assert checkpoint['continuous']['control_id'] == first['control']['control_id']
        assert checkpoint['continuous']['policy_revision'] == 1
    else:
        assert not checkpoint.get('continuous', {}).get('enabled')
    assert current['status'] == ('cancelled' if decision == 'cancel_before_consume' else 'waiting')
    assert len(workers.provider.requests(marker)) == (1 if decision == 'cancel_before_consume' else 2)
    assert not workers.provider.errors
    if decision != 'cancel_before_consume':
        require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actors['a']), 200)
        cancelled, _ = workers.start()
        workers.assert_clean_exit(cancelled)
        assert runner(service_database, accepted['runner_id'])['status'] == 'cancelled'


def test_explicit_continuous_survives_a_no_tool_answer_and_charges_every_physical_call(
        workers, actors, service_database):
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    marker = workers.provider.register(tool_reply('agent_sleep', {'seconds': 1}, call_id='continuous-first-sleep'),
                                       Reply(content='no fictional update this round'),
                                       tool_reply('agent_sleep', {'seconds': 60}, call_id='continuous-next-sleep'))
    accepted = accept(workers.api, actors['a'], marker, execution_mode='continuous')
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    parked = runner(service_database, accepted['runner_id'])
    assert parked['status'] == 'waiting'
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                           (accepted['runner_id'],))[0]['due'], timeout=10)
    resumed, _ = workers.start()
    workers.assert_clean_exit(resumed)
    current = runner(service_database, accepted['runner_id'])
    assert current['status'] == 'waiting' and current['finished_at'] is None
    checkpoint = decoded(current['checkpoint'])
    assert checkpoint['continuous']['enabled'] is True and checkpoint['execution']['iteration'] == 3
    assert checkpoint['execution']['resources']['business_iterations'] == 1
    record = service_database.rows('SELECT prompt_tokens,completion_tokens FROM chat_records WHERE record_id=%s',
                                   (current['record_id'],))[0]
    assert record['prompt_tokens'] == 33 and record['completion_tokens'] == 21
    assert len(workers.provider.requests(marker)) == 3 and not workers.provider.errors
    require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actors['a']), 200)
    cancelled, _ = workers.start()
    workers.assert_clean_exit(cancelled)
    assert runner(service_database, accepted['runner_id'])['status'] == 'cancelled'
    assert len(workers.provider.requests(marker)) == 3


def test_child_fiftieth_round_guard_resumes_original_leaf_and_rejects_old_wait_ids(
        workers, actors, service_database, child_profile):
    """Accelerate prior work counters; actual guard, control and worker are real."""
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    first = Reply(content='')
    marker = workers.provider.register(first,
        tool_reply('agent_sleep', {'seconds': 1}, call_id='guard-child-bootstrap-sleep'),
        tool_reply('skill_complete', {}, call_id='child-fiftieth-business-tool'),
        tool_reply('skill_complete', {}, call_id='child-next-segment-business-tool'),
        tool_reply('agent_sleep', {'seconds': 60}, call_id='child-post-guard-sleep'))
    first.tool_calls = tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker}, call_id='guard-parent-delegate').tool_calls
    accepted = accept(workers.api, actors['a'], marker, execution_mode='continuous')
    initial, _ = workers.start()
    workers.assert_clean_exit(initial)
    parked = runner(service_database, accepted['runner_id'])
    assert parked['status'] == 'waiting'
    checkpoint = decoded(parked['checkpoint'])
    delegate_id = next(iter(checkpoint['execution']['children']))
    leaf = checkpoint['execution']['children'][delegate_id]['checkpoint']
    assert leaf['max_iterations'] == 50
    original_child_id = leaf['execution_id']
    leaf['iteration'] = 49
    leaf['resources'].update(business_iterations=49, continuous_segment_start=0)
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',
                          (json.dumps(checkpoint), accepted['runner_id']))
    wait_for(lambda: service_database.rows('SELECT wake_at<=clock_timestamp() AS due FROM agent_runners WHERE runner_id=%s',
                                           (accepted['runner_id'],))[0]['due'], timeout=10)
    # Sleep consumption starts a new segment. Accelerate the *consumed*
    # activation and simulate its safe handoff crash, so consumption cannot
    # correctly reset our fixture counters before the guard is exercised.
    from src.core.agent_engine.contracts import ExecutionState, Outcome
    from src.services.agent_runner.durable_sleep import complete_sleep
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.timer_repository import TimerRepository
    expected = copy.deepcopy({**parked, 'checkpoint': checkpoint})
    root_state = ExecutionState.restore(checkpoint['execution'])
    child_state = ExecutionState.restore(leaf)
    complete_sleep(expected, {root_state.execution_id: root_state, child_state.execution_id: child_state},
                   checkpoint, trigger='timer')
    child_state.resources['continuous_segment_start'] = 0
    child_state.outcome = root_state.outcome = Outcome.RUNNING
    root_state.children[delegate_id]['checkpoint'] = child_state.checkpoint()
    checkpoint['execution'] = root_state.checkpoint()
    TimerRepository(service_database.connect).claim(expected, 'guard-fixture-preaction-owner', 30, checkpoint)
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                          (accepted['runner_id'],))
    ExecutionRepository(service_database.connect).reap_expired()
    to_guard, _ = workers.start()
    workers.assert_clean_exit(to_guard)
    guarded = runner(service_database, accepted['runner_id'])
    saved = decoded(guarded['checkpoint'])
    child = saved['execution']['children'][delegate_id]['checkpoint']
    assert child['execution_id'] == original_child_id and child['iteration'] == 50
    assert child['waiting']['kind'] == 'continuous_guard'
    wait = child['waiting']
    request = {'client_request_id': uuid.uuid4().hex, 'action': 'resume',
               'wait_id': wait['wait_id'], 'target_execution_id': wait['target_execution_id']}
    path = '/v1/runners/'+accepted['runner_id']+'/controls'
    require_status(workers.api.call('POST', path, actor=actors['a'], json=request), 202)
    resume_guard, _ = workers.start()
    workers.assert_clean_exit(resume_guard)
    continuing = runner(service_database, accepted['runner_id'])
    child = decoded(continuing['checkpoint'])['execution']['children'][delegate_id]['checkpoint']
    assert continuing['status'] == 'waiting' and child['waiting']['kind'] == 'timer'
    assert child['execution_id'] == original_child_id and child['iteration'] == 52
    assert child['resources']['business_iterations'] == 51
    stale = {**request, 'client_request_id': uuid.uuid4().hex}
    rejected = workers.api.call('POST', path, actor=actors['a'], json=stale)
    assert rejected.status_code == 409
    assert runner(service_database, accepted['runner_id'])['resume_control_id'] is None
    assert len(workers.provider.requests(marker)) == 5 and not workers.provider.errors
    require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actors['a']), 200)
    cancelled, _ = workers.start()
    workers.assert_clean_exit(cancelled)
    assert runner(service_database, accepted['runner_id'])['status'] == 'cancelled'


def test_child_physical_admission_denied_after_preflight_safely_pauses_then_resumes_original_child(
        workers, actors, service_database, child_profile, tmp_path):
    workers.environment.update(AGENT_RUNNER_DURABLE_SLEEP_ENABLED='true',
                               AID_TEST_CHILD_ADMISSION_GATE=str(tmp_path))
    first = Reply(content='')
    marker = workers.provider.register(first, Reply(content='child-continued-after-balance-restored'),
                                       tool_reply('agent_sleep', {'seconds': 60}, call_id='funded-parent-sleep'))
    first.tool_calls = tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker}, call_id='admission-parent-delegate').tool_calls
    accepted = accept(workers.api, actors['a'], marker, execution_mode='continuous')
    process, _ = workers.start()
    try:
        wait_for(lambda: (tmp_path/'arrived').exists(), timeout=20)
        service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (actors['a'].tenant_id,))
    finally:
        (tmp_path/'release').write_text('go')
    workers.assert_clean_exit(process)
    paused = runner(service_database, accepted['runner_id'])
    assert paused['status'] == 'paused'
    saved = decoded(paused['checkpoint'])
    delegate_id = next(iter(saved['execution']['children']))
    child = saved['execution']['children'][delegate_id]['checkpoint']
    original_child = child['execution_id']
    assert child['outcome'] == 'paused' and child['model_calls'][-1]['phase'] == 'not_started'
    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
    receipts = service_database.rows('SELECT execution_id FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                     (accepted['runner_id'],))
    assert len(receipts) == 1 and receipts[0]['execution_id'] == accepted['runner_id']
    service_database.rows('UPDATE tenants SET credit_balance=1000 WHERE tenant_id=%s', (actors['a'].tenant_id,))
    require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/controls', actor=actors['a'],
                                   json={'client_request_id': uuid.uuid4().hex, 'action': 'resume'}), 202)
    resumed, _ = workers.start()
    workers.assert_clean_exit(resumed)
    current = runner(service_database, accepted['runner_id'])
    assert current['status'] == 'waiting'
    child = decoded(current['checkpoint'])['execution']['children'][delegate_id]['checkpoint']
    assert child['execution_id'] == original_child and child['outcome'] == 'completed'
    assert len(workers.provider.requests(marker)) == 3 and not workers.provider.errors
    receipts = service_database.rows('SELECT execution_id FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                     (accepted['runner_id'],))
    assert len(receipts) == 3 and sum(item['execution_id'] == original_child for item in receipts) == 1
    require_status(workers.api.call('POST', '/v1/runners/'+accepted['runner_id']+'/cancel', actor=actors['a']), 200)
    cancelled, _ = workers.start()
    workers.assert_clean_exit(cancelled)
    assert runner(service_database, accepted['runner_id'])['status'] == 'cancelled'
