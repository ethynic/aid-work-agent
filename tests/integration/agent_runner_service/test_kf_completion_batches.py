"""Prepared bounded batch/receipt ordering contract; no mutable imports at load.

This is a real original Crypto/page/PG two-collector contract. It proves manifest
membership and employee barriers, not later Context history or model dispatch.
The complete normal/running ordering case covers those actual owners separately.
"""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import time

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope, fresh_customer_text
from .kf_context_fixtures import human_text
from .kf_completion_peer import CompletionWireReply
from .kf_admission_service import KfSourceApi
from .kf_admission_fixtures import cleanup_text_runners

pytestmark = pytest.mark.integration


def test_actual_same_page_stable_order_and_two_collectors_keep_employee_barrier_and_member_facts(completion_scope):
    from src.channels.wecom_kf.admission_repository import KfBatchRepository
    c, s = completion_scope, completion_scope.scope
    now = int(time.time())
    first_id, second_id, employee_id, later_id = [kind + s.marker for kind in
        ('completion_c1_', 'completion_c2_', 'completion_s1_', 'completion_c3_')]
    first = fresh_customer_text(s, first_id, 'Fictional first continuous segment', send_time=now - 3)
    second = fresh_customer_text(s, second_id, 'Fictional second continuous segment', send_time=now - 2)
    employee = human_text(s, employee_id, 'Fictional employee barrier', employee=True, send_time=now - 1)
    later = fresh_customer_text(s, later_id, 'Fictional later separate segment', send_time=now)
    # Real page bytes are deliberately reverse ordered. Original ingress may
    # sort only this new sync page, preserving each original payload/digest.
    locators = c.receive(later, employee, second, first)
    by_id = {locator.message_id: locator for locator in locators}
    before = s.rows('SELECT message_id,payload,payload_digest,receive_seq,receipt_order,accepted_input_ref '
        'FROM wecom_kf_inbox WHERE account_id=%s ORDER BY receive_seq', (c.account['account_id'],))
    assert [row['message_id'] for row in before] == [first_id, second_id, employee_id, later_id]
    assert all(row['accepted_input_ref'] is None for row in before)
    repository = KfBatchRepository(s.database.connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(repository.collect, by_id[first_id]) for _ in range(2)]
        winners = [future.result(timeout=8) for future in futures]
    assert all(winner is not None for winner in winners)
    assert winners[0] == winners[1]
    assert [member.message_id for member in winners[0].members] == [first_id, second_id]
    assert s.rows('SELECT count(*) AS n FROM wecom_kf_input_batches WHERE account_id=%s',
        (c.account['account_id'],)) == [{'n': 1}]
    manifest = s.rows('SELECT b.phase,m.ordinal,m.message_id,m.payload_digest,m.receipt_seq '
        'FROM wecom_kf_input_batches b JOIN wecom_kf_input_batch_members m USING(batch_ref) '
        'WHERE b.account_id=%s ORDER BY m.ordinal', (c.account['account_id'],))
    assert [row['message_id'] for row in manifest] == [first_id, second_id]
    assert [row['ordinal'] for row in manifest] == [0, 1]
    assert all(row['phase'] == 'sealed' for row in manifest)
    for row, original in zip(manifest, before[:2]):
        assert row['payload_digest'] == original['payload_digest']
        assert row['receipt_seq'] == original['receive_seq']
    # The original pull retries identical facts, not a rewritten aggregate.
    c.receive(later, employee, second, first, expected_received=0)
    after = s.rows('SELECT message_id,payload,payload_digest,receive_seq,receipt_order,accepted_input_ref '
        'FROM wecom_kf_inbox WHERE account_id=%s ORDER BY receive_seq', (c.account['account_id'],))
    assert after == before
    again = repository.collect(by_id[first_id])
    assert again == winners[0]
    assert s.rows('SELECT 1 FROM agent_runner_inputs WHERE locator->>\'account_id\'=%s',
        (c.account['account_id'],)) == []
    assert s.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s AND session_id=%s',
        (s.tenant_id, s.legacy_sid)) == []
    assert s.rows('SELECT 1 FROM channel_messages WHERE tenant_id=%s AND session_id=%s '
        'AND message_id<>%s', (s.tenant_id, s.legacy_sid, 'kf_history_' + s.marker)) == []
    assert not c.platform.errors and not s.peer.errors

    # The very same sealed manifest now crosses the real internal HTTP/SDK
    # boundary. Failure while linking the second member must roll back the
    # first member, root, claim and group membership together. Reliable SDK
    # classification is a separate original stage, not part of this rollback.
    from psycopg2 import sql
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.contracts import RunnerError
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(4)))
    name = 'completion_batch_link_fault_' + s.marker
    api = None
    try:
        api = KfSourceApi(c.processes, c.platform)
        c.config.api_url = api.url
        c.record_resources('batch_atomic_api_started', [api.child])
        s.rows(sql.SQL('CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN '
            'IF NEW.account_id={} AND NEW.message_id={} AND NEW.accepted_input_ref IS NOT NULL '
            "THEN RAISE EXCEPTION 'OWN_SECOND_BATCH_MEMBER_STORAGE_FAILURE'; END IF; RETURN NEW; END $$"
            ).format(sql.Identifier(name), sql.Literal(c.account['account_id']), sql.Literal(second_id)))
        s.rows(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON wecom_kf_inbox FOR EACH ROW '
            'EXECUTE FUNCTION {}()').format(sql.Identifier(name), sql.Identifier(name)))

        async def actual_failed_accept():
            client = SourceClient(c.config, token=api._credential)
            try:
                with pytest.raises(RunnerError) as failure:
                    await client.accept_batch(winners[0])
                assert failure.value.code == 'SOURCE_INPUT_NOT_ACCEPTED'
                assert failure.value.status == 500
            finally:
                await client.close()
        asyncio.run(actual_failed_accept())
        assert s.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT 1 FROM agent_runner_inputs WHERE locator->>\'account_id\'=%s',
            (c.account['account_id'],)) == []
        assert s.rows('SELECT 1 FROM agent_runner_session_claims WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s '
            'AND message_id=ANY(%s) ORDER BY receive_seq',
            (c.account['account_id'], [first_id, second_id])) == [
                {'accepted_input_ref': None}, {'accepted_input_ref': None}]
        assert s.rows('SELECT phase,accepted_runner_id FROM wecom_kf_input_batches WHERE account_id=%s',
            (c.account['account_id'],)) == [{'phase': 'sealed', 'accepted_runner_id': None}]
        s.rows(sql.SQL('DROP TRIGGER {} ON wecom_kf_inbox').format(sql.Identifier(name)))
        s.rows(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(name)))

        async def actual_accept_and_duplicate():
            client = SourceClient(c.config, token=api._credential)
            try:
                accepted = await client.accept_batch(winners[0])
                repeat = await client.accept_batch(winners[0])
                assert accepted['created'] is True and repeat['created'] is False
                assert accepted['current_runner_id'] == repeat['current_runner_id']
                assert [row['input_ref'] for row in accepted['members']] == [
                    by_id[first_id].stable_key, by_id[second_id].stable_key]
                assert all(row['created'] is False for row in repeat['members'])
                return accepted
            finally:
                await client.close()
        accepted = asyncio.run(actual_accept_and_duplicate())
        assert s.rows('SELECT status,attempt FROM agent_runners WHERE runner_id=%s',
            (accepted['current_runner_id'],)) == [{'status': 'queued', 'attempt': 0}]
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s',
            (accepted['current_runner_id'],)) == []
        assert not c.platform.errors and not s.peer.errors
    finally:
        s.rows(sql.SQL('DROP TRIGGER IF EXISTS {} ON wecom_kf_inbox').format(sql.Identifier(name)))
        s.rows(sql.SQL('DROP FUNCTION IF EXISTS {}()').format(sql.Identifier(name)))
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('batch_atomic_body_finally_before_fixture_teardown')
