"""Real Source HTTP/PG guard against a declared Browser-wait checkpoint.

The wait is a kernel/storage fixture, not a Chrome owner, Browser completion,
or physical Browser execution. Receipt classification may legitimately persist;
the original execution/control/input/claim/fee facts must remain unchanged.
"""
import asyncio
from copy import deepcopy

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope, fresh_customer_text
from .kf_completion_peer import CompletionWireReply
from .kf_admission_service import KfSourceApi
from .kf_admission_fixtures import cleanup_text_runners
from .test_worker import runner, decoded

pytestmark = pytest.mark.integration


def test_actual_source_text_cannot_reply_to_stored_browser_wait_or_change_original_claim(completion_scope):
    from src.core.agent_engine.contracts import (ExecutionState, Identity, AgentMode,
        ToolCall, ToolFact, Outcome)
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.input_repository import InputRepository, input_message
    from src.services.agent_runner.application_sources import project_input_in_tx
    from src.services.agent_runner.ownership import Attempt
    c, s = completion_scope, completion_scope.scope
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(8)))
    api = None
    try:
        initial = c.receive(fresh_customer_text(s, 'browser_guard_initial_' + s.marker,
            'Fictional already accepted source request'))[0]
        api = KfSourceApi(c.processes, c.platform)
        c.config.api_url = api.url
        c.record_resources('browser_guard_api_started', [api.child])

        async def accept_initial():
            client = SourceClient(c.config, token=api._credential)
            try:
                return await client.accept(initial)
            finally:
                await client.close()
        accepted = asyncio.run(accept_initial())
        execution = ExecutionRepository(s.database.connect)
        execution.input_repository = InputRepository(s.database.connect)
        owned = execution.acquire('completion_browser_storage_' + s.marker, 120)
        assert owned['runner_id'] == accepted['current_runner_id']
        authority = Attempt(owned['runner_id'], owned['worker_id'], owned['attempt'])
        # Only the legal durable waiting shape is supplied. There is no browser
        # run binding, assistance completion, boot proof or fake Owner adapter.
        with s.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s', (initial.stable_key,))
            fact = cursor.fetchone()
            incoming = input_message(fact, project_input_in_tx(cursor, fact))
        state = ExecutionState(Identity(s.tenant_id, None, s.legacy_sid, 'wecom_kf', 'channel'),
            owned['runner_id'], AgentMode.MASTER, 'Declared Browser wait storage fixture',
            [incoming], initial_len=1)
        call = ToolCall('completion_browser_wait', 'browser_automation', {'steps': [{'action': 'snapshot'}]})
        state.pending = [call]
        state.tools[call.id] = ToolFact(call, phase='waiting')
        state.messages.append({'role': 'assistant', 'content': '', 'tool_calls': [call.message_call()]})
        state.outcome = Outcome.WAITING
        state.waiting = {'kind': 'human_assistance', 'tool_call_id': call.id,
            'target_execution_id': state.execution_id, 'wait_id': 'storage_browser_wait_' + s.marker,
            'assistance_id': 'storage_assistance_' + s.marker}
        checkpoint = {**deepcopy(decoded(owned['checkpoint'])), 'execution': state.checkpoint()}
        saved = execution.save_checkpoint(authority, owned['revision'], checkpoint)
        assert s.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s',
            (initial.stable_key,)) == [{'phase': 'appended'}]
        parked = execution.park(authority, saved['revision'], 'waiting', saved['checkpoint'],
            {'waiting': deepcopy(state.waiting)})
        identifier = parked['runner_id']
        candidate = c.receive(fresh_customer_text(s, 'browser_guard_reply_' + s.marker,
            'Fictional text must not complete Browser assistance'))[0]

        def protected_facts():
            return {'runner': runner(s.database, identifier),
                'claim': s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)),
                'controls': s.rows('SELECT * FROM agent_runner_controls WHERE runner_id=%s ORDER BY control_id', (identifier,)),
                'inputs': s.rows('SELECT * FROM agent_runner_inputs WHERE current_runner_id=%s ORDER BY ordinal', (identifier,)),
                'receipts': s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)),
                'records': s.rows('SELECT * FROM chat_records WHERE tenant_id=%s AND session_id=%s', (s.tenant_id, s.legacy_sid)),
                'balance': s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))}
        before = protected_facts()
        assert before['claim'] and before['controls'] == [] and before['receipts'] == []
        response = api.call('POST', '/v1/source-inputs', json={**candidate.value(),
            'client_request_id': candidate.stable_key})
        assert response.status_code == 409
        assert response.json()['error'] == 'SOURCE_QUESTION_REPLY_NOT_AVAILABLE'
        assert protected_facts() == before
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
            (candidate.account_id, candidate.message_id)) == [{'accepted_input_ref': None}]
        assert s.rows('SELECT 1 FROM agent_runner_inputs WHERE input_ref=%s', (candidate.stable_key,)) == []
        assert s.rows('SELECT 1 FROM bs_browser_runs WHERE runner_id=%s', (identifier,)) == []
        assert s.rows('SELECT 1 FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,)) == []
        assert c.platform.count('/cgi-bin/kf/send_msg') == 0
        assert not c.platform.errors and not s.peer.errors
    finally:
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('browser_guard_body_finally_before_fixture_teardown')
