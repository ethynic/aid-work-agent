"""Bounded in-process SDK client reuse: pool mechanics and actual worker reuse.

Pool-mechanics cases use hand-written fake clients with real bool/int fields
(never MagicMock: its always-truthy attributes would lie about reuse/close).
Actual-worker cases reuse the original loopback peer, SDK client and PG facts.
"""
import asyncio
from datetime import datetime

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage

pytestmark = pytest.mark.integration

V1, V2, V3 = datetime(2026, 10, 1), datetime(2026, 10, 2), datetime(2026, 10, 3)


class FakeClient:
    """Original client seam double with real field values."""

    def __init__(self, corp_id, secret):
        self.corp_id, self.secret = corp_id, secret
        self.closed, self.close_count, self.ingress_bytes = False, 0, None

    def enable_ingress_mode(self, max_bytes):
        self.ingress_bytes = max_bytes

    async def close(self):
        self.close_count += 1
        self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def pool_account(marker, version, *, secret='fictional_secret', corp_id='fictional_corp'):
    from src.channels.wecom_kf.ingress_auth import AccountProof, CurrentAccount
    proof = AccountProof('tenant_' + marker, 'config_' + marker, corp_id,
                         'open_' + marker, 'main', '', version)
    return CurrentAccount(proof, secret, 'fictional_token', 'fictional_aes_key')


def fresh_pool(**overrides):
    from src.channels.wecom_kf.client_pool import KfClientPool
    settings = {'client_factory': FakeClient, 'max_entries': 4,
                'idle_ttl_seconds': 300, 'clock': FakeClock()}
    settings.update(overrides)
    return KfClientPool(**settings)


def test_pool_same_key_reuses_client_and_repeats_account_check_only_for_new_entries():
    pool = fresh_pool()
    first = asyncio.run(pool.acquire(pool_account('same', V1)))
    assert first.needs_verify is True
    asyncio.run(asyncio.wait_for(pool.release(first, failed=False), 5))
    pool.mark_verified(first)

    second = asyncio.run(pool.acquire(pool_account('same', V1)))
    assert second.client is first.client
    assert second.needs_verify is False
    assert first.client.closed is False and first.client.close_count == 0
    asyncio.run(asyncio.wait_for(pool.release(second, failed=False), 5))
    assert len(pool._entries) == 1


def test_pool_page_failure_discards_entry_so_next_page_cold_restarts():
    pool = fresh_pool()
    first = asyncio.run(pool.acquire(pool_account('fail', V1)))
    pool.mark_verified(first)
    asyncio.run(asyncio.wait_for(pool.release(first, failed=True), 5))
    assert first.client.closed is True
    assert len(pool._entries) == 0

    again = asyncio.run(pool.acquire(pool_account('fail', V1)))
    assert again.client is not first.client and again.needs_verify is True


def test_pool_hard_credential_check_never_reuses_entry_across_secret_rotation():
    # The claim comparison does not require config_version (ingress_repository
    # _account_matches require_version=False), so a secret changed without an
    # updated_at bump still reaches acquire with the same key. The pool must
    # verify corp_id+secret itself before reuse.
    pool = fresh_pool()
    first = asyncio.run(pool.acquire(pool_account('rotate', V1, secret='secret_one')))
    asyncio.run(asyncio.wait_for(pool.release(first, failed=False), 5))

    rotated = asyncio.run(pool.acquire(pool_account('rotate', V1, secret='secret_two')))
    assert rotated.client is not first.client
    assert rotated.client.secret == 'secret_two'
    assert rotated.needs_verify is True
    assert first.client.closed is True

    # A config_version bump is a different key: a new entry verifies fully and
    # the still-matching rotated entry stays pooled and open.
    bumped = asyncio.run(pool.acquire(pool_account('rotate', V2, secret='secret_two')))
    assert bumped.client is not rotated.client and bumped.needs_verify is True
    assert rotated.client.closed is False
    assert len(pool._entries) == 2


def test_pool_capacity_and_idle_ttl_keep_entry_count_bounded():
    pool = fresh_pool(max_entries=2)
    clock = pool._clock
    k1 = asyncio.run(pool.acquire(pool_account('k1', V1)))
    asyncio.run(asyncio.wait_for(pool.release(k1, failed=False), 5))
    clock.now += 1
    k2 = asyncio.run(pool.acquire(pool_account('k2', V1)))
    asyncio.run(asyncio.wait_for(pool.release(k2, failed=False), 5))
    clock.now += 1
    # At capacity, the oldest idle entry is evicted and closed for a new key.
    k3 = asyncio.run(pool.acquire(pool_account('k3', V1)))
    assert k1.client.closed is True and k2.client.closed is False
    assert len(pool._entries) == 2
    asyncio.run(asyncio.wait_for(pool.release(k3, failed=False), 5))

    # Idle entries older than the TTL are swept closed on the next acquire.
    clock.now += 301
    k4 = asyncio.run(pool.acquire(pool_account('k4', V1)))
    assert k2.client.closed is True and k3.client.closed is True
    assert list(pool._entries) == [k4.entry.key]


def test_pool_capacity_overflow_with_all_entries_in_use_detaches_not_closes():
    pool = fresh_pool(max_entries=2)
    h1 = asyncio.run(pool.acquire(pool_account('busy1', V1)))
    pool._clock.now += 1
    h2 = asyncio.run(pool.acquire(pool_account('busy2', V1)))
    pool._clock.now += 1
    # No idle entry exists; the least-recently-used entry is detached from the
    # pool dict (bound kept) but its in-flight page keeps the client open.
    h3 = asyncio.run(pool.acquire(pool_account('busy3', V1)))
    assert len(pool._entries) == 2
    assert {h2.entry.key, h3.entry.key} == set(pool._entries)
    assert h1.client.closed is False
    # Releasing a detached entry closes it even after a successful page.
    asyncio.run(asyncio.wait_for(pool.release(h1, failed=False), 5))
    assert h1.client.closed is True
    asyncio.run(asyncio.wait_for(pool.release(h2, failed=False), 5))
    asyncio.run(asyncio.wait_for(pool.release(h3, failed=False), 5))


def test_pool_close_discards_all_entries_keeps_secrets_private_and_stays_closed():
    pool = fresh_pool()
    held = asyncio.run(pool.acquire(pool_account('close', V1, secret='fictional_secret_xyz')))
    pool.mark_verified(held)
    asyncio.run(asyncio.wait_for(pool.release(held, failed=False), 5))
    assert 'fictional_secret_xyz' not in repr(list(pool._entries.values()))
    assert 'fictional_secret_xyz' not in repr(held)

    idle = asyncio.run(pool.acquire(pool_account('close2', V1, secret='fictional_secret_abc')))
    asyncio.run(asyncio.wait_for(pool.release(idle, failed=False), 5))
    asyncio.run(asyncio.wait_for(pool.close(), 5))
    assert held.client.closed is True and idle.client.closed is True
    assert len(pool._entries) == 0
    asyncio.run(asyncio.wait_for(pool.close(), 5))  # Idempotent worker shutdown.

    from src.channels.wecom_kf.ingress_auth import KfIngressError
    with pytest.raises(KfIngressError) as rejected:
        asyncio.run(pool.acquire(pool_account('close', V1)))
    assert rejected.value.code == 'KF_INGRESS_POOL_CLOSED'


def test_pool_config_defaults_declare_bounded_process_cache_only():
    from pydantic import ValidationError
    from src.config.settings import AgentRunnerWeComKfConfig
    config = AgentRunnerWeComKfConfig()
    assert config.client_pool_capacity == 16
    assert config.client_pool_idle_seconds == 300.0
    with pytest.raises(ValidationError):
        AgentRunnerWeComKfConfig(client_pool_capacity=0)
    with pytest.raises(ValidationError):
        AgentRunnerWeComKfConfig(client_pool_idle_seconds=0)


def test_actual_worker_reuses_pooled_client_across_pages_and_cold_restarts_after_rejection(kf_scope):
    """Loopback real SDK: same-key pages drop to one sync call; failures discard."""
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    clients = []

    def original_client(corp, secret):
        client = scope.original_client(corp, secret)
        clients.append(client)
        return client

    scope.peer.script(
        PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'pool_one',
            'msg_list': [text_message(scope, 'pool_msg_one_' + scope.marker)]}),
        PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'pool_two',
            'msg_list': [text_message(scope, 'pool_msg_two_' + scope.marker, send_time=101)]}),
        PullPage({'errcode': 40058, 'errmsg': 'fictional rejected page'}),
        PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'pool_three',
            'msg_list': [text_message(scope, 'pool_msg_three_' + scope.marker, send_time=102)]}))
    config = type(settings.agent_runner.wecom_kf)(enabled=True)

    async def pull_pages():
        worker = KfIngressWorker(config, repository, original_client)
        try:
            seed_intent(scope, repository)
            assert await worker.run_once() == {'received': 1, 'has_more': False}
            seed_intent(scope, repository)
            assert await worker.run_once() is None  # Keyset wrap only.
            assert await worker.run_once() == {'received': 1, 'has_more': False}
            # Reuse kept one actual HTTP pool and token cache open across pages.
            assert len(clients) == 1
            assert clients[0]._http_client is not None and not clients[0]._http_client.is_closed
            seed_intent(scope, repository)
            assert await worker.run_once() is None  # Keyset wrap only.
            assert await worker.run_once() is None  # errcode page rejected.
            # A failed page discards its pooled client; the rejected page kept
            # the account cursor unchanged.
            assert clients[0]._http_client.is_closed
            seed_intent(scope, repository)
            assert await worker.run_once() is None  # Keyset wrap only.
            assert await worker.run_once() == {'received': 1, 'has_more': False}
            assert len(clients) == 2
        finally:
            await worker.close()

    asyncio.run(pull_pages())
    assert clients[1]._http_client is not None and clients[1]._http_client.is_closed
    account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert account['cursor'] == 'pool_three'
    assert account['requested_generation'] == account['completed_generation'] == 4
    assert scope.rows('SELECT count(*) AS n FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))[0]['n'] == 3
    # External calls: two cold starts only. Pages two and three reused the
    # entry (one sync call each, no repeated gettoken/account check).
    assert [call['path'] for call in scope.peer.calls] == [
        '/cgi-bin/gettoken', '/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg',
        '/cgi-bin/kf/sync_msg',
        '/cgi-bin/kf/sync_msg',
        '/cgi-bin/gettoken', '/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg']
    sync = [call for call in scope.peer.calls if call['path'] == '/cgi-bin/kf/sync_msg']
    assert [call['cursor'] for call in sync] == ['', 'pool_one', 'pool_two', 'pool_two']
    assert all(call['credential_valid'] and call['account_matches'] for call in sync)
    assert not scope.peer.errors


def test_actual_worker_platform_side_account_delete_moves_first_error_to_sync_then_reverifies(kf_scope):
    """The risk the pool adds: a deleted account first fails at sync_msg, and
    the discarded entry re-runs the full account check on the next cold start."""
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    scope.peer.script(
        PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'deleted_one',
            'msg_list': [text_message(scope, 'deleted_msg_' + scope.marker)]}),
        PullPage({'errcode': 40058, 'errmsg': 'fictional deleted account sync rejection'}))
    config = type(settings.agent_runner.wecom_kf)(enabled=True)

    async def pull_pages():
        worker = KfIngressWorker(config, repository, scope.original_client)
        try:
            seed_intent(scope, repository)
            assert await worker.run_once() == {'received': 1, 'has_more': False}
            # The account disappears platform-side inside the same config
            # version; the reused entry skips account/list, so sync_msg is the
            # first failing call and the page is still rejected.
            scope.peer.account_response = {'errcode': 0, 'account_list': []}
            seed_intent(scope, repository)
            assert await worker.run_once() is None
            assert await worker.run_once() is None  # Rejected page, no save.
            seed_intent(scope, repository)
            assert await worker.run_once() is None
            # Cold restart repeats the full platform account check, which now
            # fails before any sync call; nothing is saved or pooled.
            assert await worker.run_once() is None
        finally:
            await worker.close()

    asyncio.run(pull_pages())
    account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert account['cursor'] == 'deleted_one'
    assert account['requested_generation'] == 3 and account['completed_generation'] == 1
    assert scope.rows('SELECT count(*) AS n FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))[0]['n'] == 1
    assert [call['path'] for call in scope.peer.calls] == [
        '/cgi-bin/gettoken', '/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg',
        '/cgi-bin/kf/sync_msg',
        '/cgi-bin/gettoken', '/cgi-bin/kf/account/list']
    assert not scope.peer.errors
