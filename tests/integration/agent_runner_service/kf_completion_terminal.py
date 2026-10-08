"""One genuine terminal owner for finite send risks; no fake Runner/proof."""
import asyncio
import threading
from dataclasses import dataclass, field

import pytest

from .kf_completion_fixtures import (completion_scope, kf_scope,
    completion_profile_price, fresh_customer_text)
from .kf_completion_peer import CompletionWireReply
from .kf_completion_batch_assertions import run_original_batch_to_terminal
from .kf_admission_service import KfSourceApi, verify_original_resources
from .kf_admission_service import start_original_worker
from .kf_completion_batch_assertions import accept_original_sealed_batch
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply, tool_reply
from .test_worker import Workers, runner, decoded, terminal


@dataclass(repr=False)
class TerminalDeliveryScope:
    completion: object = field(repr=False)
    api: object = field(repr=False)
    fleet: object = field(repr=False)
    provider: object = field(repr=False)
    locators: tuple = field(repr=False)
    accepted: dict = field(repr=False)
    finished: dict = field(repr=False)
    marker: str = field(repr=False)

    @property
    def identifier(self):
        return self.accepted['current_runner_id']

    def immutable_execution_facts(self):
        """Actual fee/history/source facts before and after customer sending."""
        s = self.completion.scope
        return {
            'runner': s.rows('SELECT status,attempt,record_id,result,checkpoint FROM agent_runners '
                'WHERE runner_id=%s', (self.identifier,)),
            'receipts': s.rows('SELECT receipt_id,phase,applied,record_id,usage FROM agent_runner_usage_receipts '
                'WHERE runner_id=%s ORDER BY receipt_id', (self.identifier,)),
            'record': s.rows('SELECT record_id,total_token_count,credit_cost FROM chat_records WHERE record_id=%s',
                (self.finished['record_id'],)),
            'balance': s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)),
            'history': s.rows('SELECT message_id,role,content,metadata FROM channel_messages '
                'WHERE tenant_id=%s AND session_id=%s ORDER BY id', (s.tenant_id, s.legacy_sid)),
            'inputs': s.rows('SELECT input_ref,phase,current_runner_id FROM agent_runner_inputs '
                'WHERE current_runner_id=%s ORDER BY ordinal', (self.identifier,))}


@pytest.fixture
def completion_terminal(completion_scope, completion_profile_price, service_processes, provider_peer, request):
    c, s = completion_scope, completion_scope.scope
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(32)))
    api, fleet = None, None
    reply = Reply(content='Fictional bounded terminal for send risk', release=threading.Event())
    try:
        asset_mode = getattr(request, 'param', None) == 'file_asset'
        initial = None
        if asset_mode:
            # Original cp registers and publishes this real owned source file;
            # no test inserts an asset into CP/public presentation.
            initial = tool_reply('cp', {'source_file_path': 'pending_owned_source'},
                call_id='completion_actual_file_delivery')
            marker = provider_peer.register(initial, reply)
        else:
            marker = provider_peer.register(reply)
        locators = c.receive(fresh_customer_text(s, 'wire_a_' + s.marker, marker),
            fresh_customer_text(s, 'wire_b_' + s.marker, 'Fictional adjacent bounded text'))
        api = KfSourceApi(service_processes, c.platform, provider_environment=provider_peer.environment)
        c.config.api_url = api.url
        fleet = Workers(service_processes, api, provider_peer, completion_profile_price)
        verify_original_resources(fleet, api)
        if asset_mode:
            import json
            from decimal import Decimal
            source = fleet.root / 'actual-file-delivery-source.txt'
            source.write_text('Fictional original downloadable artifact')
            initial.tool_calls[0]['function']['arguments'] = json.dumps({'source_file_path': str(source)})
            accepted = asyncio.run(accept_original_sealed_batch(c, api, locators))
            child, _ = start_original_worker(fleet, api, maximum=1)
            c.record_resources('actual_asset_model_worker_started', [api.child, child])
            assert reply.arrived.wait(20), 'OWN_ACTUAL_CP_TOOL_DID_NOT_REACH_FINAL_MODEL'
            cp = decoded(runner(s.database, accepted['current_runner_id'])['checkpoint'])
            fact = cp['execution']['tools']['completion_actual_file_delivery']
            assert fact['phase'] == 'completed' and fact['success'] is True
            assert fact['result']['file_id'] and fact['result']['file_path']
            from pathlib import Path
            artifact = Path(fact['result']['file_path'])
            assert artifact.is_file() and artifact.read_text() == source.read_text()
            assert artifact.is_relative_to(fleet.storage)
            presentation = cp['presentation']['downloadableFiles']
            assert len(presentation) == 1 and presentation[0]['file_id'] == fact['result']['file_id']
            reply.release.set()
            finished = terminal(s.database, accepted['current_runner_id'])
            fleet.assert_clean_exit(child)
            assert finished['status'] == 'completed' and finished['user_id'] is None
            assert finished['settlement_status'] == 'settled'
            assert len(provider_peer.requests(marker)) == 2 and not provider_peer.errors
            assert s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE record_id=%s',
                (finished['record_id'],)) == [{'total_token_count': 36, 'credit_cost': Decimal('0.01')}]
            assert s.rows('SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s',
                (accepted['current_runner_id'],)) == [{'phase': 'observed', 'applied': True}] * 2
        else:
            accepted, finished, _ = run_original_batch_to_terminal(c, api, fleet, provider_peer,
                locators, [marker, 'Fictional adjacent bounded text'], reply, marker)
        yield TerminalDeliveryScope(c, api, fleet, provider_peer, tuple(locators), accepted, finished, marker)
    finally:
        reply.release.set()
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        c.record_resources('terminal_risk_body_finally_before_fixture_teardown')
