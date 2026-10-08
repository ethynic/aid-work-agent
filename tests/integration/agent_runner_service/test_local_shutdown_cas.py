"""Actual Worker/Runtime/Proxy bind commits after duration fresh read.

The only decoration introduces timing barriers and always executes original SQL.
This proves one causal shutdown path, not undecorated CLI coverage.
"""
from pathlib import Path
import signal
import uuid

import pytest

from .conftest import wait_for
from .provider import tool_reply
from .test_local_invocation_recovery import ordinary_local,committed_local,local_diagnosis
from .test_worker import workers,prices,api_pair,accept,runner

pytestmark=pytest.mark.integration


@pytest.mark.parametrize('replacement_epoch',[False,True])
def test_actual_shutdown_duration_cas_cannot_skip_original_attempt_interrupt(workers,ordinary_local,service_database,replacement_epoch):
    actor,profile,_,_=ordinary_local
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='shutdown-cas-call'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    gates=workers.root/('shutdown-cas-'+uuid.uuid4().hex);gates.mkdir()
    probe=workers.processes.start([str(Path(__file__).with_name('local_shutdown_cas_probe.py')),str(gates)],
        environment=workers.environment,private_working_directory=True)
    try:
        wait_for((gates/'bind-entered').exists,timeout=20)
        before=runner(service_database,accepted['runner_id'])
        assert before['status']=='running' and committed_local(before) is None
        # The real pending to_thread enqueue is deliberately still outstanding.
        probe.send_signal(signal.SIGTERM)
        wait_for((gates/'duration-read').exists,timeout=10)
        (gates/'release-bind').touch()
        wait_for((gates/'bind-committed').exists,timeout=10)
        after_bind=runner(service_database,accepted['runner_id'])
        assert after_bind['revision']>before['revision'] and committed_local(after_bind)
        if replacement_epoch:
            service_database.rows("UPDATE agent_runners SET worker_id='replacement-during-shutdown',attempt=attempt+1 WHERE runner_id=%s",(accepted['runner_id'],))
        (gates/'release-duration').touch()
        workers.assert_clean_exit(probe)
        after=runner(service_database,accepted['runner_id'])
        assert after['status']==('running' if replacement_epoch else 'interrupted'),local_diagnosis(workers,service_database,accepted['runner_id'])
        assert after['attempt']==before['attempt']+int(replacement_epoch)
        if replacement_epoch: assert after['worker_id']=='replacement-during-shutdown'
        assert committed_local(after)==committed_local(after_bind)
        assert service_database.rows('SELECT state FROM local_tool_invocations WHERE session_id=%s',(actor.session_id,))==[{'state':'queued'}]
        assert len(workers.provider.requests(marker))==1
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(actor.session_id,))==[]
    finally:
        (gates/'release-bind').touch();(gates/'release-duration').touch()
        workers.processes.stop(probe)
