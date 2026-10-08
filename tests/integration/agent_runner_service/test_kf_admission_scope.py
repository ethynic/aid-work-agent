"""Prepared same-SID/full-source risk in the existing seventh finite group.

Receipt setup is the real original callback/PG page contract. Source state is
original SDK localhost IO; acceptance, Runtime/Engine and both worker attempts
are real. This does not claim an external WeCom account certification or send.
"""
import asyncio
from decimal import Decimal
import threading

import pytest

from .kf_ingress_fixtures import kf_scope, text_message
from .kf_admission_peer import KfAdmissionPeer, StateReply
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded

pytestmark = pytest.mark.integration


def test_actual_distinct_config_full_route_sharing_legacy_sid_cannot_append_to_running_original_source(kf_scope, service_processes, provider_peer, prices):
    from src.channels.wecom_kf.ingress_auth import bounded_page
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.config.settings import settings
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.source_receipts import SourceLocator

    scope = kf_scope
    other_config = 'kf_same_sid_config_' + scope.marker
    release = threading.Event()
    first_reply = Reply(content='First full source completed', release=release)
    first_marker = provider_peer.register(first_reply)
    second_marker = provider_peer.register(Reply(content='Must not execute before original delivery'))
    repository = KfIngressRepository(scope.database.connect)

    def actual_received(config_id, message_id, marker):
        query, body = scope.material.encrypted_callback(scope.open_kfid)
        account = repository.accept_callback(scope.tenant_id, config_id, query, body)
        lease, _ = repository.claim('kf_source_setup_' + message_id)
        assert lease is not None and lease.proof.config_id == config_id
        try:
            result = repository.commit_page(lease, bounded_page({'errcode': 0,
                'has_more': 0, 'next_cursor': 'received_' + message_id,
                'msg_list': [text_message(scope, message_id, content=marker)]}, lease.proof))
            assert result == {'received': 1, 'has_more': False}
        finally:
            repository.release(lease)
        return SourceLocator('wecom_kf', account['account_id'], 'sync', message_id)

    platform = KfAdmissionPeer(scope.peer, scope.actor_id)
    platform.states.extend(StateReply() for _ in range(20))
    api, fleet = None, None
    try:
        first_locator = actual_received(scope.config_id, 'shared_sid_first_' + scope.marker, first_marker)
        with scope.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO tenant_channel_configs(config_id,tenant_id,channel_type,name,config,
                verified,subagent_type,updated_at) SELECT %s,tenant_id,channel_type,name,config,
                verified,subagent_type,clock_timestamp() FROM tenant_channel_configs WHERE config_id=%s""",
                (other_config, scope.config_id))
        second_locator = actual_received(other_config, 'shared_sid_second_' + scope.marker, second_marker)
        routes = scope.rows('SELECT route_id,session_id,user_id,legacy_shared,profile_id,actor_id,config_id FROM channel_session_routes WHERE config_id=ANY(%s) ORDER BY config_id', ([scope.config_id, other_config],))
        assert len(routes) == 2 and len({row['route_id'] for row in routes}) == 2
        assert {row['session_id'] for row in routes} == {scope.legacy_sid}
        assert all(row['user_id'] is None and row['legacy_shared'] is True and row['profile_id'] == 'main' and row['actor_id'] == scope.actor_id for row in routes)
        api = KfSourceApi(service_processes, platform, provider_environment=provider_peer.environment)
        config = settings.agent_runner.model_copy(deep=True)
        config.enabled = True
        config.wecom_kf.enabled = True
        config.api_url = api.url
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)

        async def accept(locator):
            client = SourceClient(config, token=api._credential)
            try:
                return await client.accept(locator)
            finally:
                await client.close()
        first = asyncio.run(accept(first_locator))
        assert first['success'] is True and first['created'] is True
        first_id = first['current_runner_id']
        first_child, _ = start_original_worker(fleet, api, maximum=1)
        assert first_reply.arrived.wait(15)
        original = runner(scope.database, first_id)
        assert original['status'] == 'running' and original['attempt'] == 1
        assert len(provider_peer.requests(first_marker)) == 1 and not provider_peer.requests(second_marker)
        second = asyncio.run(accept(second_locator))
        assert second['success'] is True and second['created'] is True
        second_id = second['current_runner_id']
        assert second_id == second['accepted_runner_id'] and second_id != first_id
        assert second['input_ref'] != first['input_ref']
        unchanged = runner(scope.database, first_id)
        assert unchanged['checkpoint'] == original['checkpoint']
        assert unchanged['input'] == original['input'] and unchanged['record_id'] == original['record_id']
        queued = runner(scope.database, second_id)
        assert queued['status'] == 'queued' and queued['attempt'] == 0 and queued['worker_id'] is None
        assert queued['session_id'] == original['session_id'] == scope.legacy_sid
        assert decoded(queued['input'])['text'] == second_marker
        claim = scope.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (scope.legacy_sid,))
        assert claim == [{'owner_runner_id': first_id, 'gate': 'execution'}]
        # Actual contender attempts original acquisition while first physical
        # model remains in flight; it cannot start the queued second source.
        contender, _ = start_original_worker(fleet, api, once=True)
        fleet.assert_clean_exit(contender)
        assert runner(scope.database, second_id)['attempt'] == 0 and not provider_peer.requests(second_marker)
        release.set()
        finished = terminal(scope.database, first_id)
        assert finished['status'] == 'completed' and finished['record_id'] == original['record_id']
        fleet.assert_clean_exit(first_child)
        delivery = scope.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (scope.legacy_sid,))
        assert delivery == [{'owner_runner_id': first_id, 'gate': 'delivery'}]
        after_delivery, _ = start_original_worker(fleet, api, once=True)
        fleet.assert_clean_exit(after_delivery)
        pending = runner(scope.database, second_id)
        assert pending['status'] == 'queued' and pending['attempt'] == 0 and pending['worker_id'] is None
        assert not provider_peer.requests(second_marker) and len(provider_peer.requests(first_marker)) == 1
        assert scope.rows('SELECT count(*) AS n FROM chat_records WHERE session_id=%s', (scope.legacy_sid,))[0]['n'] == 1
        assert scope.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (scope.tenant_id,))[0]['credit_balance'] == Decimal('999.99')
        history = scope.rows('SELECT message_id,content,metadata FROM channel_messages WHERE session_id=%s ORDER BY created_at', (scope.legacy_sid,))
        assert len(history) == 3 and history[1]['message_id'] == first['input_ref'] + ':user'
        assert decoded(history[1]['metadata'])['input_ref'] == first['input_ref']
        assert history[1]['content'] == first_marker and history[2]['content'] == first_reply.content
        assert all(row['content'] != second_marker for row in history)
        assert scope.rows('SELECT route_id,session_id,user_id,legacy_shared,profile_id,actor_id,config_id FROM channel_session_routes WHERE config_id=ANY(%s) ORDER BY config_id', ([scope.config_id, other_config],)) == routes
        assert not platform.errors and not scope.peer.errors and not provider_peer.errors
    finally:
        release.set()
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        platform.close()
        cleanup_text_runners(scope)
        scope.rows('DELETE FROM wecom_kf_inbox WHERE config_id=%s', (other_config,))
        scope.rows('DELETE FROM channel_session_routes WHERE config_id=%s', (other_config,))
        scope.rows('DELETE FROM wecom_kf_account_sync WHERE config_id=%s', (other_config,))
        scope.rows('DELETE FROM tenant_channel_configs WHERE config_id=%s', (other_config,))
