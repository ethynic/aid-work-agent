"""Original send ACK and real comm-log return-window interruption/cancellation."""
from decimal import Decimal
import json
from pathlib import Path
import uuid

import pytest

from .conftest import wait_for
from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_local_invocation_recovery import interrupted
from .test_worker import workers, api_pair, prices, accept, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('boundary', ['comm-log-committed-restart', 'cancel-after-ack-before-comm-log'])
def test_actual_original_send_is_not_reissued_when_postprocessing_restarts_or_cancels(
        workers, recruiting_domain, service_database, boundary):
    from src.services.recruiting_resume_service import create_resume_record
    actor, profile = recruiting_domain
    candidate = '虚构发送' + uuid.uuid4().hex[:12]
    resume = create_resume_record(actor.tenant_id, actor.user_id, candidate_name=candidate)
    message = 'fictional message with original physical ACK'
    marker = workers.provider.register(tool_reply('boss_send_to', {'to': candidate, 'message': message},
                                                call_id='original-send-recovery-call'),
                                       Reply(content='fictional-original-send-recovered'))
    branch = 'send.comm_log' if boundary == 'comm-log-committed-restart' else 'send.comm_binding'
    steps = [{'tool_name': 'boss_send_to', 'result': {'success': True, 'effect': 'applied',
              'data': {'to': candidate, 'sent': True, 'dry_run': False}}}]
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        gate = workers.root / ('send-commit-' + uuid.uuid4().hex)
        gate.mkdir()
        worker_id = 'fixture-send-commit-' + uuid.uuid4().hex
        first = workers.processes.start([str(Path(__file__).with_name('domain_commit_probe.py')),
            worker_id, branch, '0', str(gate)], environment=workers.environment,
            private_working_directory=True)
        workers.children.append((first, worker_id))
        invocation_id = desktop.claimed(0)['invocation_id']
        desktop.allow_result(0)
        desktop.completed(0)
        wait_for((gate / 'committed.json').exists, timeout=20)
        assert json.loads((gate / 'committed.json').read_text())['branch'] == branch
        before_logs = service_database.rows('SELECT id FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s', (actor.tenant_id,))
        assert len(before_logs) == (1 if branch == 'send.comm_log' else 0)
        if branch == 'send.comm_log':
            stopped = interrupted(workers, service_database, accepted, first, hard=True)
            original_refs = decoded(stopped['checkpoint'])['execution']['resources']['local_invocations']
            control(workers.api, actor, accepted['runner_id'], 'resume')
            second, _ = workers.start()
            workers.assert_clean_exit(second)
        else:
            cancelled = workers.api.call('POST', f"/v1/runners/{accepted['runner_id']}/cancel", actor=actor)
            assert cancelled.status_code == 200
            (gate / 'release').touch()
            workers.assert_clean_exit(first)
        finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == ('completed' if branch == 'send.comm_log' else 'cancelled')
    invocations = service_database.rows('SELECT id,state,credit_cost FROM local_tool_invocations WHERE session_id=%s', (actor.session_id,))
    assert len(invocations) == 1 and str(invocations[0]['id']) == invocation_id
    assert invocations[0]['state'] == 'succeeded' and Decimal(str(invocations[0]['credit_cost'])) == Decimal('1.00')
    fees = service_database.rows('SELECT model,credit_cost FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,))
    assert fees == [{'model': 'boss_send_to', 'credit_cost': Decimal('1.00')}]
    logs = service_database.rows('SELECT id,resume_id,content FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s', (actor.tenant_id,))
    if branch == 'send.comm_log':
        assert logs == [{'id': before_logs[0]['id'], 'resume_id': resume['id'], 'content': message}]
        after = decoded(finished['checkpoint'])['execution']
        assert after['resources']['local_invocations'] == original_refs
        assert len(workers.provider.requests(marker)) == 2
    else:
        # The original sent ACK is already trusted. Cancellation forbids new
        # sends but must retain this delivery's original domain audit exactly
        # once under the still-valid attempt, rather than hide the send.
        assert len(logs) == 1 and logs[0]['resume_id'] == resume['id'] and logs[0]['content'] == message
        assert len(workers.provider.requests(marker)) == 1
    assert not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (accepted['runner_id'],)) == []
