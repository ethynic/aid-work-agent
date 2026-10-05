"""Prepared native KF foundation contracts, never the Runner/delivery pipeline."""
import asyncio

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage

pytestmark = pytest.mark.integration


def test_actual_current_config_crypto_pull_page_original_sdk_and_pg_inbox_keep_legacy_sid(kf_scope):
    """Developer normal: original crypto/worker/SDK/DAL, localhost provider IO."""
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import settings

    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    before = scope.rows('SELECT count(*) AS n FROM agent_runners WHERE tenant_id=%s', (scope.tenant_id,))[0]['n']
    accepted = seed_intent(scope, repository)
    assert accepted['requested_generation'] == 1
    scope.peer.script(PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'page_one_done',
        'msg_list': [text_message(scope, 'provider_message_' + scope.marker)]}))
    # Actual config model/type is checked against final handoff before launch.
    config = type(settings.agent_runner.wecom_kf)(enabled=True, lease_seconds=30,
        heartbeat_seconds=10, page_limit=100, page_bytes=1048576)

    async def pull():
        worker = KfIngressWorker(config, repository, client_factory=scope.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()

    result = asyncio.run(pull())
    assert result == {'received': 1, 'has_more': False}
    account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert account['account_id'] == accepted['account_id']
    assert account['requested_generation'] == account['completed_generation'] == 1
    assert account['cursor'] == 'page_one_done'
    assert account['worker_id'] is None and account['lease_until'] is None
    assert account['raw_profile'] == '' and account['profile_id'] == 'main'
    inbox = scope.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))
    assert len(inbox) == 1
    fact = inbox[0]
    assert fact['namespace'] == 'sync' and fact['actor_id'] == scope.actor_id
    assert fact['message_type'] == 'text' and fact['config_version'] == account['config_version']
    assert fact['payload']['text']['content'] == 'Fictional incoming text'
    assert fact['capability_ciphertext'] is None
    route = scope.rows('SELECT * FROM channel_session_routes WHERE config_id=%s', (scope.config_id,))[0]
    assert route['route_id'] == fact['route_id']
    assert route['session_id'] == scope.legacy_sid and route['user_id'] is None
    assert route['legacy_shared'] is True and route['profile_id'] == 'main' and route['raw_profile'] == ''
    assert route['actor_id'] == scope.actor_id and route['chat_id'] == scope.open_kfid
    history = scope.rows('SELECT content FROM channel_messages WHERE session_id=%s', (scope.legacy_sid,))
    assert history == [{'content': 'Original fictional history'}]
    assert scope.rows('SELECT count(*) AS n FROM agent_runners WHERE tenant_id=%s', (scope.tenant_id,))[0]['n'] == before
    assert [call['path'] for call in scope.peer.calls] == [
        '/cgi-bin/gettoken', '/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg']
    assert all(call['credential_valid'] for call in scope.peer.calls)
    assert scope.peer.calls[-1]['cursor'] == '' and scope.peer.calls[-1]['account_matches'] is True
    assert 'token' not in scope.peer.calls[-1]['keys']  # Existing SDK mode; official requirement unresolved.
    assert not scope.peer.errors
    assert not any(key in fact['payload'] for key in ('token', 'secret', 'encoding_aes_key', 'welcome_code'))
