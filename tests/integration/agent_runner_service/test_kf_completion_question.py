"""Original master clarification and one real child conversation, localhost IO.

Replaces the withdrawn, unrun ordinary-clarify WAIT/ACK/SQL-fault combination.
HEAD Agent.run returns master clarify to the model; genuine child clarification
may be answered by another actual customer message. No internal control API,
pause button, fake checkpoint or exact Attempt requirement is used.
"""
import asyncio
import json
import time

import pytest

from .conftest import wait_for
from .kf_completion_fixtures import (completion_scope, completion_profile_price,
    kf_scope, fresh_customer_text)
from .kf_completion_peer import CompletionWireReply
from .kf_completion_orchestration import (original_admission,
    accept_through_original_admission, deliver_through_original_admission)
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply, tool_reply
from .test_usage_storage import prices
from .test_worker import Workers, decoded, runner, terminal

pytestmark = pytest.mark.integration


def test_actual_master_clarify_returns_to_model_and_delivers_normal_customer_reply(
        completion_scope, service_processes, provider_peer, prices):
    c, s = completion_scope, completion_scope.scope
    question = 'Which fictional customer should be selected?'
    answer = 'Fictional original model continuation after clarify'
    marker = provider_peer.register(tool_reply('clarify', {'question': question,
        'missing_info': ['customer']}, call_id='ordinary_master_clarify'), Reply(content=answer))
    c.platform.script('/cgi-bin/kf/customer/batchget', *(CompletionWireReply(payload={
        'errcode': 0, 'customer_list': [{'external_userid': s.actor_id,
            'nickname': 'Fictional original customer'}]}) for _ in range(8)))
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(64)))
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply())
    api = fleet = admission = None
    try:
        api = KfSourceApi(service_processes, c.platform, provider_environment=provider_peer.environment)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)
        locators = c.receive(fresh_customer_text(s, 'master_clarify_' + s.marker, marker))
        accepted = asyncio.run(accept_through_original_admission(c, admission, locators))
        identifier = accepted['current_runner_id']
        child, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('ordinary_master_clarify_worker', [api.child, child])
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and decoded(finished['result'])['output'] == answer
        physical = provider_peer.requests(marker)
        assert len(physical) == 2
        paired = [m for m in physical[1]['messages'] if m.get('role') == 'tool'
            and m.get('tool_call_id') == 'ordinary_master_clarify']
        assert len(paired) == 1 and question in paired[0]['content']
        asyncio.run(deliver_through_original_admission(c, admission, locators[0], identifier))
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        assert s.rows('SELECT COUNT(*) AS n FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == [{'n': 1}]
        assert not provider_peer.errors and not c.platform.errors
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('ordinary_master_clarify_finally')


def test_actual_one_child_clarification_accepts_customer_chat_answer_and_finishes_original_conversation(
        completion_scope, service_processes, provider_peer, prices):
    c, s = completion_scope, completion_scope.scope
    profile = 'customer-followup'
    subscription = 'completion_chat_child_' + s.marker
    s.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) "
        "VALUES(%s,%s,%s,'active','paid')", (subscription, s.tenant_id, profile))
    delegate = tool_reply('delegate_to_subagent', {'subagent_name': profile,
        'task_description': 'pending_original_marker'}, call_id='ordinary_chat_delegate')
    child_answer = 'Fictional child answered'
    final = 'Fictional parent response after the customer answered the child'
    question = 'Which fictional customer?'
    marker = provider_peer.register(delegate, tool_reply('clarify', {
        'question': question, 'missing_info': ['customer']},
        call_id='ordinary_child_clarify'), Reply(content=child_answer), Reply(content=final))
    delegate.tool_calls[0]['function']['arguments'] = json.dumps({
        'subagent_name': profile, 'task_description': marker})
    c.platform.script('/cgi-bin/kf/customer/batchget', *(CompletionWireReply(payload={
        'errcode': 0, 'customer_list': [{'external_userid': s.actor_id,
            'nickname': 'Fictional original customer'}]}) for _ in range(8)))
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(96)))
    c.platform.script('/cgi-bin/kf/send_msg', *(CompletionWireReply() for _ in range(4)))
    # Observe the real HTTP bytes without replacing the SDK/response or
    # granting reply permission. Only the matching boolean is retained.
    import threading
    question_sent = threading.Event()
    original_handler = c.platform._server.RequestHandlerClass

    class ObserveLeafQuestion(original_handler):
        def do_POST(self):
            original_stream = self.rfile

            class ObserveRead:
                def read(_, *args, **kwargs):
                    raw = original_stream.read(*args, **kwargs)
                    if self.path.split('?', 1)[0] == '/cgi-bin/kf/send_msg':
                        try:
                            body = json.loads(raw)
                        except (ValueError, UnicodeError):
                            body = {}
                        if (body.get('touser') == s.actor_id
                                and body.get('open_kfid') == s.open_kfid
                                and body.get('msgtype') == 'text'
                                and question in (body.get('text') or {}).get('content', '')):
                            question_sent.set()
                    return raw

                def __getattr__(_, name):
                    return getattr(original_stream, name)

            self.rfile = ObserveRead()
            try:
                return super().do_POST()
            finally:
                self.rfile = original_stream

    c.platform._server.RequestHandlerClass = ObserveLeafQuestion
    api = fleet = admission = None
    try:
        api = KfSourceApi(service_processes, c.platform, provider_environment=provider_peer.environment)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)
        balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance']
        initial = c.receive(fresh_customer_text(s, 'child_question_' + s.marker, marker))
        accepted = asyncio.run(accept_through_original_admission(c, admission, initial))
        first_id = accepted['current_runner_id']
        first, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('ordinary_child_question_worker', [api.child, first])
        wait_for(lambda: row if (row := runner(s.database, first_id))['status'] == 'waiting'
            else None, timeout=30)
        fleet.assert_clean_exit(first)
        assert len(provider_peer.requests(marker)) == 2

        async def deliver_question():
            for _ in range(16):
                assert await admission.run_once() is None
                if question_sent.is_set():
                    return
                await asyncio.sleep(c.config.wecom_kf.poll_seconds)
            raise AssertionError('ORIGINAL_CHILD_QUESTION_NOT_SENT')
        asyncio.run(deliver_question())
        assert question_sent.is_set(), 'ORIGINAL_LEAF_QUESTION_NOT_VISIBLE_ON_HTTP'
        customer_answer = 'Fictional customer Alice supplied through chat'
        replies = c.receive(fresh_customer_text(s, 'child_chat_answer_' + s.marker, customer_answer))
        continued = asyncio.run(accept_through_original_admission(c, admission, replies))
        current_id = continued['current_runner_id']  # Same Runner identity is not a product requirement.
        second, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('ordinary_child_answer_worker', [api.child, first, second])
        finished = terminal(s.database, current_id, timeout=40)
        fleet.assert_clean_exit(second)
        assert finished['status'] == 'completed'
        output = decoded(finished['result'])['output']
        assert child_answer in output or final in output
        physical = provider_peer.requests(marker)
        assert any(customer_answer in str(m.get('content')) for m in physical[2]['messages']
            if m.get('role') == 'user')
        asyncio.run(deliver_through_original_admission(c, admission, replies[0], current_id))
        history = s.rows('SELECT content FROM channel_messages WHERE tenant_id=%s AND session_id=%s '
            "AND role='user'", (s.tenant_id, s.legacy_sid))
        assert sum(row['content'] == customer_answer for row in history) == 1
        receipts = s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=ANY(%s)',
            (list({first_id, current_id}),))
        assert len(receipts) == len(physical) == len({row['receipt_id'] for row in receipts})
        # Compare the actual original price/usage groups, not a mandated
        # number of parent calls or a forced no-redelegation implementation.
        from src.services.agent_runner.usage_pricing import grouped_cost
        ids = list({first_id, current_id})
        cost = sum(grouped_cost([r for r in receipts if r['runner_id'] == identifier])[0]
            for identifier in ids)
        records = s.rows('SELECT record_id,total_token_count,credit_cost FROM chat_records '
            'WHERE session_id=%s', (s.legacy_sid,))
        assert len(records) == len({r['record_id'] for r in records})
        assert sum(r['credit_cost'] for r in records) == cost
        assert sum(r['total_token_count'] for r in records) == sum(
            r['usage']['prompt_tokens'] + r['usage']['completion_tokens'] for r in receipts)
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,))[0]['credit_balance'] == balance - cost
        assert not provider_peer.errors and not c.platform.errors
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        s.rows('DELETE FROM subscriptions WHERE subscription_id=%s', (subscription,))
        c.record_resources('ordinary_child_chat_finally')
