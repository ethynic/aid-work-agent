"""Actual KF transfer flows; only external platform/model HTTP is fictional.

Original Crypto, pull, Admission, Source API, resident Worker, SDK and billing.
No checkpoint fault, forced balance, internal resume or late-input combination.
"""
import asyncio
from decimal import Decimal
import json

import pytest

from .kf_completion_fixtures import (completion_scope, kf_scope,
    completion_profile_price, fresh_customer_text)
from .kf_completion_peer import CompletionWireReply
from .kf_completion_orchestration import (original_admission,
    accept_through_original_admission, deliver_through_original_admission)
from .kf_admission_service import (KfSourceApi, start_original_worker,
    verify_original_resources, safe_source_execution_diagnostic)
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply, tool_reply
from .test_usage_storage import prices
from .test_worker import Workers, decoded, terminal

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('completion_scope', ['pre-sales'], indirect=True)
def test_actual_admission_transfer_ack_stops_round_and_next_customer_message_is_human_history(
        completion_scope, completion_profile_price, service_processes, provider_peer):
    _actual_channel_transfer(completion_scope, completion_profile_price,
        service_processes, provider_peer, succeeds=True)


@pytest.mark.parametrize('completion_scope', ['pre-sales'], indirect=True)
def test_actual_admission_transfer_rejected_keeps_ai_and_delivers_original_normal_reply(
        completion_scope, completion_profile_price, service_processes, provider_peer):
    _actual_channel_transfer(completion_scope, completion_profile_price,
        service_processes, provider_peer, succeeds=False)


def _actual_channel_transfer(c, prices, processes, provider, *, succeeds):
    s = c.scope
    payload = decoded(s.rows('SELECT config FROM tenant_channel_configs WHERE config_id=%s',
        (s.config_id,))[0]['config'])
    payload['kf_account'][0]['servicer_userid_list'] = ['fictional_owned_servicer']
    s.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=clock_timestamp() WHERE config_id=%s',
        (json.dumps(payload), s.config_id))
    c.platform.script('/cgi-bin/kf/customer/batchget', *(CompletionWireReply(payload={
        'errcode': 0, 'customer_list': [{'external_userid': s.actor_id,
            'nickname': 'Fictional transfer customer'}]}) for _ in range(4)))
    # External platform state served by the actual SDK HTTP. Only after a real
    # transfer ACK does the peer change to 3; stored classifications stay intact.
    state = CompletionWireReply(payload={'errcode': 0, 'service_state': 1})
    c.platform.script('/cgi-bin/kf/service_state/get', *([state] * 64))
    transfer_reply = CompletionWireReply(payload={'errcode': 0 if succeeds else 45015})
    c.platform.script('/cgi-bin/kf/service_state/trans', transfer_reply)
    ordinary = 'Fictional ordinary reply after transfer was rejected'
    if not succeeds:
        c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply())
    call_id = 'actual_customer_transfer'
    replies = [tool_reply('transfer_to_human', {'reason': 'user_request'}, call_id=call_id)]
    if not succeeds:
        replies.append(Reply(content=ordinary))
    marker = provider.register(*replies)
    api = fleet = admission = identifier = None
    try:
        api = KfSourceApi(processes, c.platform, provider_environment=provider.environment)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        fleet = Workers(processes, api, provider, prices)
        verify_original_resources(fleet, api)
        balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance']
        locators = c.receive(fresh_customer_text(s, 'transfer_first_' + s.marker,
            marker + ' Please transfer me to a human'), fresh_customer_text(s,
            'transfer_second_' + s.marker, 'Fictional second customer segment'))
        accepted = asyncio.run(accept_through_original_admission(c, admission, locators))
        identifier = accepted['current_runner_id']
        child, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('actual_transfer_worker_started', [api.child, child])
        assert transfer_reply.finished.wait(20)
        if succeeds:
            state.payload = {'errcode': 0, 'service_state': 3}
        finished = terminal(s.database, identifier, timeout=35)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert finished['user_id'] is None
        execution = decoded(finished['checkpoint'])['execution']
        fact = execution['tools'][call_id]
        assert fact['phase'] == 'completed' and fact['result_recorded'] is True
        assert fact['result']['success'] is succeeds
        assert execution.get('terminal_directive') == ('stop_execution' if succeeds else None)
        assert sum(message.get('tool_call_id') == call_id for message in execution['messages']) == 1
        assert len(provider.requests(marker)) == (1 if succeeds else 2)
        wires = s.rows("SELECT phase,response_origin,errcode FROM wecom_kf_wire_operations "
            "WHERE purpose LIKE 'tool_transfer_%%' AND proof->>'source_runner_id'=%s", (identifier,))
        assert wires == [{'phase': 'ack' if succeeds else 'reject', 'response_origin': 'platform',
            'errcode': 0 if succeeds else 45015}]
        assert c.platform.count('/cgi-bin/kf/service_state/trans') == 1
        assert s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE record_id=%s',
            (finished['record_id'],)) == [{'total_token_count': 18 if succeeds else 36,
                'credit_cost': Decimal('0.01')}]
        receipts = s.rows('SELECT phase,applied,record_id FROM agent_runner_usage_receipts '
            'WHERE runner_id=%s', (identifier,))
        assert len(receipts) == (1 if succeeds else 2)
        assert all(row == {'phase': 'observed', 'applied': True,
            'record_id': finished['record_id']} for row in receipts)
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance'] == balance - Decimal('0.01')
        metadata = decoded(s.rows('SELECT metadata FROM channel_sessions WHERE session_id=%s',
            (s.legacy_sid,))[0]['metadata']) or {}
        human_markers = s.rows("SELECT role FROM channel_messages WHERE tenant_id=%s AND session_id=%s "
            "AND metadata->>'kind'='transfer_to_human_marker'", (s.tenant_id, s.legacy_sid))
        if succeeds:
            assert metadata['service_state'] == 3 and metadata['transfer_source'] == 'agent'
            assert human_markers == [{'role': 'system'}]

            async def drain_transfer_delivery():
                for _ in range(16):
                    assert await admission.run_once() is None
                    rows = await asyncio.to_thread(s.rows,
                        'SELECT phase,closed_outcome FROM wecom_kf_deliveries WHERE runner_id=%s', (identifier,))
                    if rows == [{'phase': 'closed', 'closed_outcome': 'closed_suppressed_known'}]:
                        return
                    await asyncio.sleep(c.config.wecom_kf.poll_seconds)
                raise AssertionError('ACTUAL_TRANSFER_DELIVERY_NOT_CLOSED')
            asyncio.run(drain_transfer_delivery())
            assert c.platform.count('/cgi-bin/kf/send_msg') == 0
            assert s.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
            followup_text = 'Fictional customer message for the human after transfer'
            followup = c.receive(fresh_customer_text(s, 'human_after_transfer_' + s.marker,
                followup_text))[0]

            async def consume_human_message():
                for _ in range(12):
                    assert await admission.run_once() is None
                    row = admission.last_context
                    if row and row.get('disposition') == 'persisted':
                        return row['history_id']
                    await asyncio.sleep(c.config.wecom_kf.poll_seconds)
                raise AssertionError('ACTUAL_HUMAN_MESSAGE_NOT_PERSISTED')
            history_id = asyncio.run(consume_human_message())
            history = s.rows('SELECT role,content,metadata FROM channel_messages WHERE message_id=%s', (history_id,))
            assert len(history) == 1 and history[0]['role'] == 'user' and history[0]['content'] == followup_text
            assert history[0]['metadata']['source'] == 'customer_human'
            assert history[0]['metadata']['msgid'] == followup.message_id
            assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s '
                'AND namespace=%s AND message_id=%s', (followup.account_id, followup.namespace,
                followup.message_id)) == [{'accepted_input_ref': None}]
            assert s.rows('SELECT 1 FROM agent_runner_inputs WHERE input_ref=%s', (followup.stable_key,)) == []
            assert len(provider.requests(marker)) == 1
            assert c.platform.count('/cgi-bin/kf/send_msg') == 0
            assert c.platform.count('/cgi-bin/kf/service_state/trans') == 1
        else:
            assert metadata.get('service_state') != 3 and human_markers == []
            assert decoded(finished['result'])['output'] == ordinary
            second_messages = provider.requests(marker)[1]['messages']
            assert any(message.get('role') == 'tool' and message.get('tool_call_id') == call_id
                and json.loads(message['content'])['success'] is False for message in second_messages)
            asyncio.run(deliver_through_original_admission(c, admission, locators[0], identifier))
            assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance'] == balance - Decimal('0.01')
        assert s.rows('SELECT COUNT(*) AS n FROM chat_records WHERE session_id=%s',
            (s.legacy_sid,)) == [{'n': 1}]
        assert not provider.errors and not c.platform.errors and not s.peer.errors
    except BaseException:
        if identifier is not None:
            print(json.dumps(safe_source_execution_diagnostic(s, identifier, processes), sort_keys=True))
        raise
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('transfer_body_finally_before_fixture_teardown')
