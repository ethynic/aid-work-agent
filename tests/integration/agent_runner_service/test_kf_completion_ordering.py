"""Actual C1/batch -> servicer barrier -> C2/batch, not a fabricated tree."""
import asyncio
from decimal import Decimal
import threading
import time

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope, fresh_customer_text
from .kf_completion_peer import KfCompletionPeer, CompletionWireReply
from .kf_completion_batch_assertions import accept_original_sealed_batch
from .kf_completion_delivery_assertions import deliver_original_terminal
from .kf_context_fixtures import human_text
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, terminal

pytestmark = pytest.mark.integration


def test_actual_employee_barrier_waits_for_public_terminal_then_is_assistant_before_next_batch_model(
        completion_scope, service_processes, provider_peer, prices):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.services.agent_runner.source_client import SourceClient
    c, s = completion_scope, completion_scope.scope
    first = Reply(content='Fictional first group completion', release=threading.Event())
    second = Reply(content='Fictional second group completion', release=threading.Event())
    markers = [provider_peer.register(reply) for reply in (first, second)]
    now = int(time.time())
    employee_text = 'Fictional employee exact intermediate context'
    a, b, employee, d, e = c.receive(
        fresh_customer_text(s, 'ordered_a_' + s.marker, markers[0], send_time=now - 4),
        fresh_customer_text(s, 'ordered_b_' + s.marker, 'Fictional first adjacent segment', send_time=now - 3),
        human_text(s, 'ordered_s_' + s.marker, employee_text, employee=True, send_time=now - 2),
        fresh_customer_text(s, 'ordered_d_' + s.marker, markers[1], send_time=now - 1),
        fresh_customer_text(s, 'ordered_e_' + s.marker, 'Fictional second adjacent segment', send_time=now))
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(64)))
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(), CompletionWireReply())
    # Separate external response stream for the genuine employee SDK request;
    # model-owner/source HTTP uses actual current1. No authority method changes.
    employee_peer = KfCompletionPeer(s.peer, s.actor_id)
    employee_peer.script('/cgi-bin/kf/service_state/get', CompletionWireReply(payload={
        'errcode': 0, 'service_state': 3}))

    def original_employee_client(corp, secret):
        client = c.original_client(corp, secret)
        client.BASE_URL = employee_peer.base_url
        return client

    api, fleet = None, None
    try:
        api = KfSourceApi(service_processes, c.platform, provider_environment=provider_peer.environment)
        c.config.api_url = api.url
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)
        before_balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance']
        accepted = asyncio.run(accept_original_sealed_batch(c, api, (a, b)))
        identifier = accepted['current_runner_id']
        after = s.rows('SELECT receipt_order FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
            (b.account_id, b.message_id))[0]['receipt_order']

        async def original_context_poll():
            client = SourceClient(c.config, token=api._credential)
            context = ContextWorker(c.config.wecom_kf,
                repository=ContextRepository(s.database.connect),
                client_factory=original_employee_client, source_client=client)
            context.after = after  # Keyset scheduling DI; original candidate still selected.
            try:
                return await context.run_once()
            finally:
                await context.close()
                await client.close()
        pending = asyncio.run(original_context_poll())
        assert pending['disposition'] == 'pending_history' and pending['history_id'] is None
        assert s.rows("SELECT 1 FROM channel_messages WHERE tenant_id=%s AND session_id=%s "
            "AND metadata->>'msgid'=%s", (s.tenant_id, s.legacy_sid, employee.message_id)) == []
        child, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('ordered_first_worker_started', [api.child, child])
        assert first.arrived.wait(15)
        requests = provider_peer.requests(markers[0])
        assert len(requests) == 1
        assert all(employee_text not in str(message.get('content')) for message in requests[0]['messages'])
        first.release.set()
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed'
        asyncio.run(deliver_original_terminal(c, api, a, identifier))
        persisted = asyncio.run(original_context_poll())
        assert persisted['disposition'] == 'persisted'
        rows = s.rows('SELECT message_id,role,content,metadata FROM channel_messages WHERE message_id=%s',
            (persisted['history_id'],))
        assert len(rows) == 1 and rows[0]['content'] == '[人工客服] ' + employee_text
        assert rows[0]['role'] == 'user' and rows[0]['metadata']['source'] == 'servicer'
        assert rows[0]['metadata']['actor_id'] == s.actor_id
        assert employee_peer.count('/cgi-bin/kf/service_state/get') == 1
        later = asyncio.run(accept_original_sealed_batch(c, api, (d, e)))
        later_id = later['current_runner_id']
        assert later_id != identifier
        child2, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('ordered_second_worker_started', [api.child, child, child2])
        assert second.arrived.wait(15)
        physical = provider_peer.requests(markers[1])
        assert len(physical) == 1
        messages = physical[0]['messages']
        assert sum(message.get('role') == 'assistant' and message.get('content') == '[人工客服] ' + employee_text
            for message in messages) == 1
        assert not any(message.get('role') == 'user' and employee_text in str(message.get('content'))
            for message in messages)
        second.release.set()
        assert terminal(s.database, later_id)['status'] == 'completed'
        fleet.assert_clean_exit(child2)
        asyncio.run(deliver_original_terminal(c, api, d, later_id))
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance'] == before_balance - Decimal('0.02')
        assert s.rows('SELECT 1 FROM agent_runner_inputs WHERE locator->>\'message_id\'=%s',
            (employee.message_id,)) == []
        assert s.rows('SELECT 1 FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == []
        assert len(provider_peer.requests(markers[0])) == len(provider_peer.requests(markers[1])) == 1
        assert not provider_peer.errors and not c.platform.errors and not employee_peer.errors
    finally:
        first.release.set(); second.release.set()
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        employee_peer.close()
        cleanup_text_runners(s)
        c.record_resources('ordering_body_finally_before_fixture_teardown')
