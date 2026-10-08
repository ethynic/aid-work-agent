"""Declared short-lease argument DI + observation of original recovery TX.

Only this fresh owned Runner's claim_resume lease argument becomes one second.
Original SQL, Attempt allocation, source proof, event append and Worker handling
are unchanged. A separate real PG connection holds the event table lock.
"""
import json
import os
from pathlib import Path
import re
import runpy

from src.services.agent_runner.event_repository import EventRepository
from src.services.agent_runner.recovery_repository import RecoveryRepository

original_claim = RecoveryRepository.claim_resume
original_notify = EventRepository.notify_in_tx
report = Path(os.environ['KF_RECOVERY_LOCK_REPORT'])
runner_id = os.environ['KF_RECOVERY_LOCK_RUNNER']
facts = {}


def publish():
    temporary = report.with_suffix('.pending')
    temporary.write_text(json.dumps(facts, sort_keys=True))
    temporary.replace(report)


def observe_original_notify(cursor, before, **kwargs):
    after = kwargs.get('after')
    if (before is not None and before['runner_id'] == runner_id
            and before['status'] == 'paused' and isinstance(after, dict)
            and after['status'] == 'running'):
        assert after['attempt'] == before['attempt'] + 1
        cursor.execute('SELECT clock_timestamp() AS now,pg_backend_pid() AS pid')
        actual = cursor.fetchone()
        facts.update(notify_entered=True, new_attempt=after['attempt'],
            previous_attempt=before['attempt'], backend_pid=actual['pid'],
            database_now=actual['now'].isoformat(),
            actual_lease_until=after['lease_until'].isoformat())
        assert after['lease_until'] > actual['now']
        publish()
    return original_notify(cursor, before, **kwargs)


def short_original_claim(self, target, worker_id, lease_seconds, **kwargs):
    if target != runner_id:
        return original_claim(self, target, worker_id, lease_seconds, **kwargs)
    assert lease_seconds >= 5
    prepared = kwargs['prepared_source']
    assert prepared is not None and kwargs['source_port'] is not None
    facts.update(lease_argument_seconds=1,
                 actual_prepared_observed_at=prepared.observed_at.isoformat())
    try:
        return original_claim(self, target, worker_id, 1, **kwargs)
    except Exception as error:
        code = error.args[0] if error.args else None
        facts.update(claim_exception_class=type(error).__name__)
        if isinstance(code, str) and re.fullmatch(r'(?:SOURCE|RUNNER|RECOVERY)_[A-Z0-9_]+', code):
            facts['claim_error_code'] = code
        publish()
        raise


EventRepository.notify_in_tx = staticmethod(observe_original_notify)
RecoveryRepository.claim_resume = short_original_claim
runpy.run_path(str(Path(__file__).with_name('kf_admission_process.py')), run_name='__main__')
