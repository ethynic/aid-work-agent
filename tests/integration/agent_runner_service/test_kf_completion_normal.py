"""Prepared complete normal chain; no mutable production imported at load.

Actual Crypto/pull, SDK, internal socket API, PG, resident Engine and adapter
handoff. External platform/model responses are localhost fixtures. Registration
is business context, never an upgraded Runner principal. Await final recap
producer/config handoff before declaring this node executable-ready.
"""
import asyncio
from decimal import Decimal
import threading

import pytest

from .kf_completion_fixtures import (completion_scope, completion_profile_price,
    kf_scope, fresh_enter_session, fresh_customer_text)
from .kf_completion_peer import CompletionWireReply
from .kf_completion_business_assertions import run_original_enter_business
from .kf_completion_batch_assertions import run_original_batch_to_terminal
from .kf_completion_delivery_assertions import (
    read_original_presentation,
    finish_original_delivery_and_assert_no_fee_or_history_replay)
from .kf_completion_orchestration import (original_admission,
    accept_through_original_admission, deliver_through_original_admission)
from .kf_admission_service import KfSourceApi, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('completion_scope', ['pre-sales'], indirect=True)
def test_actual_crypto_business_batch_resident_runner_sdk_delivery_next_round_and_background_handoff(
        completion_scope, completion_profile_price, service_processes, provider_peer, monkeypatch):
    c, s = completion_scope, completion_scope.scope
    customer_reply = {
        'errcode': 0, 'customer_list': [{'external_userid': s.actor_id,
            'nickname': 'Fictional completion customer'}]}
    c.platform.script('/cgi-bin/kf/customer/batchget',
        *(CompletionWireReply(payload=customer_reply) for _ in range(5)))
    c.platform.script('/cgi-bin/kf/send_msg_on_event', CompletionWireReply())
    c.platform.script('/cgi-bin/kf/service_state/get',
        CompletionWireReply(payload={'errcode': 0, 'service_state': 0}),
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(64)))
    c.platform.script('/cgi-bin/kf/service_state/trans', CompletionWireReply())
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(), CompletionWireReply())
    api, fleet, admission, replies = None, None, None, []
    try:
        enter = c.receive(fresh_enter_session(s, 'enter_' + s.marker,
            c.welcome_code, scene=c.scene))[0]
        api = KfSourceApi(service_processes, c.platform, provider_environment=provider_peer.environment)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        customer = asyncio.run(run_original_enter_business(c, enter, admission=admission))
        fleet = Workers(service_processes, api, provider_peer, completion_profile_price)
        verify_original_resources(fleet, api)
        initial_balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance']
        identifiers, delivery_ids = [], []
        for ordinal in (1, 2):
            reply = Reply(content='Fictional bounded completion answer ' + str(ordinal),
                release=threading.Event())
            replies.append(reply)
            marker = provider_peer.register(reply)
            texts = [marker + ' first segment', 'Fictional second segment ' + str(ordinal)]
            locators = c.receive(*(fresh_customer_text(s,
                'round_' + str(ordinal) + '_' + str(index) + '_' + s.marker, text)
                for index, text in enumerate(texts)))
            before_accept = s.rows('SELECT message_id,payload,payload_digest,accepted_input_ref '
                'FROM wecom_kf_inbox WHERE account_id=%s AND message_id=ANY(%s) ORDER BY receive_seq',
                (c.account['account_id'], [locator.message_id for locator in locators]))
            accepted_response = asyncio.run(accept_through_original_admission(c, admission, locators))
            if ordinal == 1:
                # Actual first SDK0 -> original transition POST/ACK -> fresh
                # SDK1 allows this receipt to be selected, without rewriting
                # its fixed classification to AI or manufacturing a permit.
                classified = s.rows('SELECT classification,observed_state,scope,payload_digest '
                    'FROM wecom_kf_receipt_classifications WHERE account_id=%s AND namespace=%s AND message_id=%s',
                    (locators[0].account_id, locators[0].namespace, locators[0].message_id))
                assert len(classified) == 1 and classified[0]['classification'] == 'unknown'
                assert classified[0]['observed_state'] == 0
                transition = s.rows("SELECT phase,response_origin,errcode,scope,payload_digest,proof "
                    "FROM wecom_kf_wire_operations WHERE locator->>'account_id'=%s "
                    "AND locator->>'message_id'=%s AND purpose='transition_to_ai'",
                    (locators[0].account_id, locators[0].message_id))
                assert len(transition) == 1
                assert {key: transition[0][key] for key in ('phase', 'response_origin', 'errcode')} == {
                    'phase': 'ack', 'response_origin': 'platform', 'errcode': 0}
                assert transition[0]['scope'] == classified[0]['scope']
                assert transition[0]['payload_digest'] == classified[0]['payload_digest'] == before_accept[0]['payload_digest']
                assert transition[0]['proof']['source_state'] == 0 and transition[0]['proof']['service_state'] == 1
                assert c.platform.count('/cgi-bin/kf/service_state/trans') == 1
            accepted, finished, _ = run_original_batch_to_terminal(c, api, fleet,
                provider_peer, locators, texts, reply, marker,
                accepted_response=accepted_response, receipt_snapshots=before_accept)
            identifier = accepted['current_runner_id']
            identifiers.append(identifier)
            assert identifier not in identifiers[:-1]
            assert finished['user_id'] is None
            assert finished['user_id'] != customer
            asyncio.run(read_original_presentation(c, api, locators[0], identifier, reply.content))
            delivery_id = asyncio.run(deliver_through_original_admission(c, admission, locators[0], identifier))
            delivery_ids.append(delivery_id)
            finish_original_delivery_and_assert_no_fee_or_history_replay(c, api, locators[0],
                delivery_id, identifier, expected_outcome='closed_accepted_known')
            assert len(provider_peer.requests(marker)) == 1
        assert len(set(delivery_ids)) == 2
        assert s.rows('SELECT gate FROM agent_runner_session_claims WHERE session_id=%s',
            (s.legacy_sid,)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance'] == initial_balance - Decimal('0.02')
        # Production AI ACK producer must supply these real stable intents.
        # No test INSERT and no substitute human/employee task may satisfy it.
        intents = s.rows("SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents "
            "WHERE account_id=%s AND task_name='lead_refresh' ORDER BY task_id", (c.account['account_id'],))
        assert len(intents) == 2 and all(row['state'] == 'pending_adapter' for row in intents)
        from src.channels.wecom_kf.recap_handoff import KfRecapHandoff
        from src.services.recap.tasks import RECAP_TASK_ADAPTERS
        from src.services.recap.tasks.lead_refresh import LeadRefreshAdapter
        assert 'lead_refresh' in RECAP_TASK_ADAPTERS
        assert not (s.rows('SELECT metadata FROM channel_sessions WHERE session_id=%s',
            (s.legacy_sid,))[0]['metadata'] or {}).get('lead_capture')
        original_execute = LeadRefreshAdapter.execute
        actual_adapter_calls = []

        async def observe_original_adapter(payload):
            actual_adapter_calls.append(('entered', payload.round_message_id))
            result = await original_execute(payload)
            actual_adapter_calls.append(('returned', payload.round_message_id))
            assert result is None  # Original no-lead behavior, not effectdone.
            return result
        monkeypatch.setattr(LeadRefreshAdapter, 'execute', staticmethod(observe_original_adapter))

        async def handoff_original_tasks():
            worker = KfRecapHandoff(s.database.connect)
            returned = []
            try:
                for _ in range(12):
                    value = await worker.run_once()
                    if value and value['task_id'] in {row['task_id'] for row in intents}:
                        returned.append(value)
                    if len(returned) == 2:
                        break
                assert len(returned) == 2
                assert {row['task_id'] for row in returned} == {row['task_id'] for row in intents}
                assert all(row['disposition'] == 'dispatch_returned' for row in returned)
                assert not worker.tasks
            finally:
                await worker.close()
        asyncio.run(handoff_original_tasks())
        for intent in intents:
            assert actual_adapter_calls.count(('entered', intent['history_id'])) == 1
            assert actual_adapter_calls.count(('returned', intent['history_id'])) == 1
        facts = s.rows("SELECT phase,value FROM wecom_kf_business_facts "
            "WHERE account_id=%s AND business_kind='recap:lead_refresh'", (c.account['account_id'],))
        assert len(facts) == 2 and all(row['phase'] == 'known'
            and row['value']['disposition'] == 'dispatch_returned' for row in facts)
        assert all('effectdone' not in row['value'] for row in facts)
        final_intents = s.rows("SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents "
            "WHERE account_id=%s AND task_name='lead_refresh' ORDER BY task_id", (c.account['account_id'],))
        assert final_intents == [{**row, 'state': 'dispatch_returned'} for row in intents]
        assert c.platform.count('/cgi-bin/kf/send_msg') == 2
        assert c.platform.count('/cgi-bin/kf/send_msg_on_event') == 1
        assert c.platform.count('/cgi-bin/kf/service_state/trans') == 1
        assert not c.platform.errors and not provider_peer.errors
    finally:
        for reply in replies:
            reply.release.set()
        if admission is not None:
            asyncio.run(admission.close())
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('normal_body_finally_before_fixture_teardown')
