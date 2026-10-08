"""Prepared original SDK HTTP limits/private errors, with a real loopback peer."""
import asyncio
import threading

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage

pytestmark = pytest.mark.integration


def test_original_native_http_oversize_and_private_error_do_not_save_page_and_close_actual_clients(kf_scope):
    from loguru import logger
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    # Empty message page would be valid after JSON parsing. The real streamed
    # HTTP body must be rejected by the native 64KiB budget before JSON decode.
    release = threading.Event()
    changed_config_page = PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'stale_cfg_not_saved',
        'msg_list': [text_message(scope, 'after_cfg_closed_' + scope.marker)]}, release=release)
    scope.peer.script(PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'oversize_not_saved',
        'msg_list': [], 'provider_extra': 'X' * 70000}),
        PullPage({'errcode': 40099, 'errmsg': scope.material.pull_token,
            'debug_url': scope.peer.base_url + '?credential=' + scope.peer.secret}), changed_config_page)
    clients, logs = [], []
    def original_client(corp, secret):
        client = scope.original_client(corp, secret)
        clients.append(client)
        return client
    sink = logger.add(lambda record: logs.append(str(record)), level='DEBUG')

    async def original_pull():
        config = type(settings.agent_runner.wecom_kf)(enabled=True, page_bytes=65536)
        worker = KfIngressWorker(config, repository, original_client)
        try:
            assert await worker.run_once() is None
            seed_intent(scope, repository)
            assert await worker.run_once() is None  # Keyset end then wrap.
            assert await worker.run_once() is None
            seed_intent(scope, repository)
            assert await worker.run_once() is None
            pending = asyncio.create_task(worker.run_once())
            try:
                assert await asyncio.to_thread(changed_config_page.arrived.wait, 4)
                # Real authorization changes while the already-dispatched
                # original read is held; current PG config must be rechecked.
                scope.rows('UPDATE tenant_channel_configs SET verified=0,updated_at=clock_timestamp() WHERE config_id=%s', (scope.config_id,))
            finally:
                release.set()
            assert await asyncio.wait_for(pending, 6) is None
            assert not worker._active
            # All three pages failed, so no entry survived for reuse.
            assert not worker.client_pool._entries
        finally:
            release.set()
            await worker.close()

    try:
        asyncio.run(original_pull())
        # Pool semantics (was: one brand-new client per page, closed at each
        # page end — original ingress_worker.py:74/:88-91 before M7 pooling).
        # Every scripted page here fails (oversize -> errcode=-1, errcode 40099,
        # config changed mid-flight), and a failed page discards its pooled
        # entry and closes the client, so each of the three rejected sync pages
        # still owns exactly one distinct, now closed client; nothing is reused
        # across failures and the worker keeps no entry afterwards.
        assert len(clients) == 3
        assert all(client._http_client is not None and client._http_client.is_closed for client in clients)
        account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
        assert account['cursor'] == '' and account['completed_generation'] == 0 and account['requested_generation'] == 3
        assert account['worker_id'] is None and account['lease_until'] is None
        assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
        assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []
        leaked = any(private in '\n'.join(logs) for private in (
            scope.peer.secret, scope.material.pull_token, scope.material.token, scope.material.encoding_aes_key))
        assert leaked is False
        assert len([call for call in scope.peer.calls if call['path'] == '/cgi-bin/kf/sync_msg']) == 3
        assert not scope.peer.errors
    finally:
        logger.remove(sink)
