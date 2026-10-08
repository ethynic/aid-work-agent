"""Original AI ACK producer and registered recap adapter; void is not effectdone."""
import asyncio

import pytest

from .kf_completion_terminal import completion_terminal, completion_scope, kf_scope, completion_profile_price
from .kf_completion_peer import CompletionWireReply
from .kf_completion_delivery_assertions import deliver_original_terminal

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('completion_scope', ['pre-sales'], indirect=True)
def test_actual_registered_adapter_return_then_sql_loss_keeps_started_job_and_fresh_consumer_does_not_replay(
        completion_terminal, monkeypatch):
    from psycopg2 import DatabaseError, sql
    from src.channels.wecom_kf.recap_handoff import KfRecapHandoff
    from src.services.recap.tasks.lead_refresh import LeadRefreshAdapter
    t, c = completion_terminal, completion_terminal.completion
    s = c.scope
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply())
    asyncio.run(deliver_original_terminal(c, t.api, t.locators[0], t.identifier))
    tasks = s.rows("SELECT task_id,history_id,state FROM wecom_kf_context_task_intents "
        "WHERE account_id=%s AND task_name='lead_refresh'", (c.account['account_id'],))
    assert len(tasks) == 1 and tasks[0]['state'] == 'pending_adapter'
    before = t.immutable_execution_facts()
    assert not (s.rows('SELECT metadata FROM channel_sessions WHERE session_id=%s',
        (s.legacy_sid,))[0]['metadata'] or {}).get('lead_capture')
    original_execute = LeadRefreshAdapter.execute
    entered, returned = [], []

    async def original_adapter_observation(payload):
        entered.append(payload.round_message_id)
        result = await original_execute(payload)
        returned.append(payload.round_message_id)
        assert result is None
        return result
    monkeypatch.setattr(LeadRefreshAdapter, 'execute', staticmethod(original_adapter_observation))
    name = 'completion_recap_fault_' + s.marker
    s.rows(sql.SQL('CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN '
        "IF NEW.tenant_id={} AND NEW.business_kind='recap:lead_refresh' "
        "AND NEW.value->>'disposition'='dispatch_returned' THEN RAISE EXCEPTION 'OWN_RECAP_RETURN_STORAGE_FAILURE'; END IF; "
        'RETURN NEW; END $$').format(sql.Identifier(name), sql.Literal(s.tenant_id)))
    s.rows(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON wecom_kf_business_facts '
        'FOR EACH ROW EXECUTE FUNCTION {}()').format(sql.Identifier(name), sql.Identifier(name)))

    async def actual_dispatch_then_fresh_scan():
        first = KfRecapHandoff(s.database.connect)
        try:
            with pytest.raises(DatabaseError) as rejected:
                for _ in range(8):
                    await first.run_once()
            assert rejected.value.pgcode == 'P0001'
            assert entered == returned == [tasks[0]['history_id']]
        finally:
            await first.close()
        fresh = KfRecapHandoff(s.database.connect)
        try:
            for _ in range(4):
                assert await fresh.run_once() is None
            assert not fresh.tasks
        finally:
            await fresh.close()
    try:
        asyncio.run(actual_dispatch_then_fresh_scan())
        assert s.rows('SELECT state FROM wecom_kf_context_task_intents WHERE task_id=%s',
            (tasks[0]['task_id'],)) == [{'state': 'started'}]
        facts = s.rows("SELECT phase,value FROM wecom_kf_business_facts "
            "WHERE account_id=%s AND business_kind='recap:lead_refresh'", (c.account['account_id'],))
        assert len(facts) == 1 and facts[0]['phase'] == 'unknown'
        assert facts[0]['value']['disposition'] == 'started' and 'effectdone' not in facts[0]['value']
        assert entered == returned == [tasks[0]['history_id']]
        assert t.immutable_execution_facts() == before
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
    finally:
        s.rows(sql.SQL('DROP TRIGGER IF EXISTS {} ON wecom_kf_business_facts').format(sql.Identifier(name)))
        s.rows(sql.SQL('DROP FUNCTION IF EXISTS {}()').format(sql.Identifier(name)))
