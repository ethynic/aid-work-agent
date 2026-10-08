"""Original dispatch port + actual CFG lock/DBclock/SDK PreparedSource.

Narrow PG contract, not a running Engine model. Only connection entry is
observed to identify the real blocked backend; no clock/authorization result DI.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import queue

import pytest

from .conftest import wait_for
from .provider import Reply
from .kf_admission_fixtures import text_scope, kf_scope
from .test_worker import runner

pytestmark = pytest.mark.integration


def test_original_dispatch_waiting_on_current_config_lock_rechecks_actual_attempt_after_real_lease_expiry(text_scope, provider_peer):
    from src.channels.wecom_kf.api_client import WeComKfApiClient
    from src.config.settings import AgentRunnerPeerConfig
    from src.services.agent_runner.application_sources import ApplicationSources
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.ownership import Attempt, LeaseLost
    t, s = text_scope, text_scope.scope
    marker = provider_peer.register(Reply(status=409))
    locator = t.text('dispatch_fence_', marker)
    accepted = t.accept(locator)
    identifier = accepted['current_runner_id']
    # The local PG port receives the same actual native peer configuration as
    # this fixture's original socket API; authorization itself stays original.
    config = t.config.model_copy(deep=True)
    config.wecom_kf.service_id = t.api.service_id
    config.peers[t.api.service_id] = AgentRunnerPeerConfig(
        token_hash=t.api.environment['AGENT_RUNNER_SERVICE_TOKEN_HASH'], sources=['wecom_kf'])
    assert config.wecom_kf.enabled is True
    sources = ApplicationSources(config, s.database.connect)

    def original_external_client(corp_id, secret):
        client = WeComKfApiClient(corp_id, secret)
        client.BASE_URL = t.platform.base_url
        return client
    sources.provider.client_factory = original_external_client
    # Real original SDK current-state response before the core creates its short
    # lease; close/drain does not synthesize or refresh the PreparedSource clock.
    prepared = asyncio.run(sources.prepare_execution(runner(s.database, identifier)))
    assert prepared.locator == locator
    execution = ExecutionRepository(s.database.connect)
    execution.source_port, execution.input_repository = sources, sources.inputs
    owned = execution.acquire('source_dispatch_short_lease_' + s.marker, 2)
    assert owned['runner_id'] == identifier
    authority = Attempt(identifier, owned['worker_id'], owned['attempt'])
    facts = t.facts()
    claim = s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,))
    backend = queue.Queue()

    @contextmanager
    def observed_owned_connection():
        with s.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid() AS pid')
                backend.put(cursor.fetchone()['pid'])
            yield connection
    execution.connection_factory = observed_owned_connection
    with s.database.connect() as blocking, blocking.cursor() as cursor:
        cursor.execute('SELECT config_id FROM tenant_channel_configs WHERE config_id=%s FOR UPDATE', (s.config_id,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(execution.assert_dispatch, authority, source_prepared=prepared)
            try:
                pid = backend.get(timeout=2)
                wait_for(lambda: s.rows("SELECT wait_event_type='Lock' AS blocked FROM pg_stat_activity WHERE pid=%s", (pid,))[0]['blocked'], timeout=2)
                still_valid = s.rows('SELECT lease_until>clock_timestamp() AS valid FROM agent_runners WHERE runner_id=%s', (identifier,))[0]['valid']
                assert still_valid is True, 'The actual CFG lock must be reached before lease expiry'
                wait_for(lambda: s.rows('SELECT lease_until<=clock_timestamp() AS expired FROM agent_runners WHERE runner_id=%s', (identifier,))[0]['expired'], timeout=3)
                blocking.commit()  # Real lock release after real DBclock crosses the lease, below original 3s lock timeout.
                with pytest.raises(LeaseLost, match='RUNNER_ATTEMPT_EXPIRED'):
                    pending.result(timeout=3)
            finally:
                blocking.rollback()
                if not pending.done():
                    pending.result(timeout=5)  # Do not abandon the owned SQL thread/connection.
    after = runner(s.database, identifier)
    assert after['checkpoint'] == owned['checkpoint'] and after['revision'] == owned['revision']
    assert after['attempt'] == owned['attempt'] and after['worker_id'] == owned['worker_id']
    assert t.facts() == facts
    assert s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == claim
    assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
    assert not provider_peer.requests(marker) and not provider_peer.errors
    assert not t.platform.errors
