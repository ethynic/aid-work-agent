"""Known item failure versus actual receipt-storage authority failure."""
import uuid

from psycopg2 import sql
import pytest

from .domain_fixtures import recruiting_domain
from .domain_io import ScriptedDesktop
from .provider import Reply, tool_reply
from .test_local_resume_flow import resume_payload, evaluation_reply
from .test_worker import workers, api_pair, prices, accept, terminal, runner, decoded

pytestmark = pytest.mark.integration


def batch_inputs(workers):
    names = ['虚构首项' + uuid.uuid4().hex[:10], '虚构次项' + uuid.uuid4().hex[:10]]
    marker = workers.provider.register(tool_reply('boss_resume_batch', {'limit': 2}, call_id='batch-boundary-call'),
                                       Reply(content='fictional-batch-boundary-explained'))
    steps = [{'tool_name': 'boss_resume_batch', 'result': {'success': True, 'effect': 'none',
              'data': {'resumes': [resume_payload(name) for name in names], 'failures': [], 'attempted': 2}}}]
    return names, marker, steps


def test_known_name_failure_does_not_charge_or_insert_item_but_next_item_still_runs(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    names, marker, steps = batch_inputs(workers)
    workers.provider.register(evaluation_reply('完全不同的虚构姓名'), marker=names[0])
    workers.provider.register(evaluation_reply(names[1]), marker=names[1])
    with ScriptedDesktop(workers, actor, steps) as desktop:
        desktop.allow_claim(0)
        accepted = accept(workers.api, actor, marker, profile_id=profile)
        process, _ = workers.start()
        desktop.claimed(0)
        desktop.allow_result(0)
        desktop.completed(0)
        workers.assert_clean_exit(process)
    finished = terminal(service_database, accepted['runner_id'])
    assert finished['status'] == 'completed'
    result = decoded(finished['checkpoint'])['execution']['tools']['batch-boundary-call']['result']
    assert result['success']
    assert service_database.rows('SELECT candidate_name FROM bs_recruiting_operator_resumes WHERE tenant_id=%s',
                                 (actor.tenant_id,)) == [{'candidate_name': names[1]}]
    assert service_database.rows("SELECT count(*) AS n FROM client_usage_logs WHERE tenant_id=%s AND model='boss_resume_recognition'",
                                 (actor.tenant_id,)) == [{'n': 1}]
    assert all(len(workers.provider.requests(name)) == 1 for name in names)
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors


def test_actual_receipt_write_failure_stops_batch_before_next_item_or_recognition_fee(
        workers, recruiting_domain, service_database):
    actor, profile = recruiting_domain
    names, marker, steps = batch_inputs(workers)
    for name in names:
        workers.provider.register(evaluation_reply(name), marker=name)
    accepted = accept(workers.api, actor, marker, profile_id=profile)
    function = 'fixture_receipt_refusal_' + uuid.uuid4().hex
    trigger = function + '_trigger'
    with service_database.connect() as connection, connection.cursor() as cursor:
        # Real isolated PostgreSQL fault only for the original claimed runner's
        # covered observation. Actual external response succeeds; its mandatory
        # receipt write fails. No fake store exception or completed fact.
        cursor.execute(sql.SQL('''CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.runner_id = {} AND NEW.purpose LIKE 'domain:%:resume_recognition_covered'
                 AND NEW.phase = 'observed' THEN
                RAISE EXCEPTION 'FIXTURE_DOMAIN_RECEIPT_WRITE_REFUSED';
              END IF;
              RETURN NEW;
            END $$''').format(sql.Identifier(function), sql.Literal(accepted['runner_id'])))
        cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON agent_runner_usage_receipts FOR EACH ROW EXECUTE FUNCTION {}()').format(
            sql.Identifier(trigger), sql.Identifier(function)))
    try:
        with ScriptedDesktop(workers, actor, steps) as desktop:
            desktop.allow_claim(0)
            process, _ = workers.start()
            desktop.claimed(0)
            desktop.allow_result(0)
            desktop.completed(0)
            workers.assert_clean_exit(process)
        stopped = runner(service_database, accepted['runner_id'])
        assert stopped['status'] == 'interrupted'
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (accepted['runner_id'],)) == [{'owner_runner_id': accepted['runner_id']}]
        assert service_database.rows('SELECT 1 FROM bs_recruiting_operator_resumes WHERE tenant_id=%s', (actor.tenant_id,)) == []
        assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s', (actor.tenant_id,)) == []
        assert len(workers.provider.requests(names[0])) == 1 and workers.provider.requests(names[1]) == []
        assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
        receipts = service_database.rows('SELECT phase,price_snapshot FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                        (accepted['runner_id'],))
        assert len(receipts) == 2
        covered = [row for row in receipts if decoded(row['price_snapshot']).get('covered_cost') == 'resume_recognition']
        assert len(covered) == 1 and covered[0]['phase'] in {'started', 'unknown'}
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER {} ON agent_runner_usage_receipts').format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(function)))
