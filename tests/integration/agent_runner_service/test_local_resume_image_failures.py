"""Real owned filesystem error is an item failure, not an authority failure."""
from decimal import Decimal
import threading

import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .test_local_resume_boundaries import batch_inputs
from .test_local_resume_flow import evaluation_reply
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded

pytestmark = pytest.mark.integration


def test_actual_image_directory_error_keeps_item_fee_and_allows_next_batch_item(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    names, marker, steps = batch_inputs(workers)
    arrived, release = threading.Event(), threading.Event()
    workers.provider.register(evaluation_reply(names[0]), marker=names[0])
    next_reply = evaluation_reply(names[1])
    next_reply.arrived, next_reply.release = arrived, release
    workers.provider.register(next_reply, marker=names[1])
    # An actual regular file at this fixture's authorized artifact directory
    # makes mkdir raise FileExistsError. No source patches or synthetic results.
    parent = workers.storage / 'tenants' / actor.tenant_id
    parent.mkdir(parents=True, exist_ok=True)
    blocker = parent / 'recruiting'
    blocker.write_text('fictional-owned-mkdir-blocker')
    try:
        with ScriptedDesktop(workers, actor, steps) as desktop:
            desktop.allow_claim(0)
            accepted = accept(workers.api, actor, marker, profile_id=profile)
            process, _ = workers.start()
            desktop.claimed(0)
            desktop.allow_result(0)
            desktop.completed(0)
            if not arrived.wait(timeout=18):
                row = runner(service_database, accepted['runner_id'])
                phases = decoded(row['checkpoint']).get('execution', {}).get('resources', {}).get('local_domain_phases', {})
                # Capture safe real facts before fixtures remove their rows.
                diagnosis = {'status': row['status'], 'attempt': row['attempt'],
                    'revision': row['revision'], 'domain_phases': [
                        {'branch': fact['branch'], 'ordinal': fact['ordinal'],
                         'phase': fact['phase']} for fact in phases.values()],
                    'fee_count': service_database.rows(
                        "SELECT count(*) AS n FROM client_usage_logs WHERE tenant_id=%s AND model='boss_resume_recognition'",
                        (actor.tenant_id,))[0]['n'],
                    'physical_vl_counts': [len(workers.provider.requests(name)) for name in names]}
                raise AssertionError(f'Actual Batch did not continue after item filesystem error: {diagnosis}')
            assert service_database.rows(
                "SELECT count(*) AS n FROM client_usage_logs WHERE tenant_id=%s AND model='boss_resume_recognition'",
                (actor.tenant_id,)) == [{'n': 1}]
            assert service_database.rows('SELECT 1 FROM bs_recruiting_operator_resumes WHERE tenant_id=%s',
                                         (actor.tenant_id,)) == []
            blocker.unlink()
            release.set()
            workers.assert_clean_exit(process)
            finished = terminal(service_database, accepted['runner_id'])
    finally:
        release.set()
        # Never remove the directory created by a successful second item.
        if blocker.is_file():
            blocker.unlink()
    assert finished['status'] == 'completed'
    assert service_database.rows('SELECT candidate_name FROM bs_recruiting_operator_resumes WHERE tenant_id=%s',
                                 (actor.tenant_id,)) == [{'candidate_name': names[1]}]
    fees = service_database.rows(
        "SELECT credit_cost FROM client_usage_logs WHERE tenant_id=%s AND model='boss_resume_recognition'",
        (actor.tenant_id,))
    assert fees == [{'credit_cost': Decimal('1.00')}, {'credit_cost': Decimal('1.00')}]
    assert all(len(workers.provider.requests(name)) == 1 for name in names)
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE session_id=%s',
                                 (actor.session_id,)) == [{'n': 1}]
