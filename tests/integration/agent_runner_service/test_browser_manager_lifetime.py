"""Native owner-contract integration: GC cannot discard unfinished cleanup.

The starting ToolFact is the existing declared PG storage-contract fixture,
not a fabricated Engine outcome. Manager/owner/control/Redis/PG/Chromium and
Factory cleanup are actual implementations. Close-transport and PG trigger
faults are explicit IO DI; neither supplies a close ACK or changes a proof.
"""
import gc
import weakref

import pytest

from src.core.agent_engine.contracts import CheckpointFailure, ExecutionState, ToolCall, ToolFact
from src.config.settings import AgentRunnerBrowserOwnerConfig
from src.services.agent_runner.browser_owner import RunnerBrowserOwner
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.worker import RuntimeFactory
from src.tools.browser.executor.local import LocalPlaywrightExecutor
from src.tools.browser.executor.models import CloseCommand
from src.tools.browser.owner_port import BrowserOwnerFailure, browser_execution_owner_scope
from src.tools.browser.run_manager import BrowserRunManager, RunState
from src.tools.browser.run_store import RedisRunStore
from src.tools.browser.worker_protocol import ProtocolError
from tests.integration.browser.test_runner_close_contract import linux_browser_tmp, identities, cleanup

from .browser_io import browser_redis
from .test_browser_binding_storage import browser_owner, storage, attempt

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['native_unconfirmed', 'pg_close_failed'])
async def test_original_boot_retains_manager_across_gc_until_confirmed_pg_close(
        browser_owner, browser_redis, linux_browser_tmp, service_database, monkeypatch, boundary):
    values = browser_owner
    row, original = values['row'], values['call_id']
    state = ExecutionState.restore(row['checkpoint']['execution'])
    control = DurableControl(values['execution'], values['attempt'], row)
    process_resources = {'boot': values['boot'], 'config': AgentRunnerBrowserOwnerConfig(enabled=True)}
    control.browser_process = process_resources
    factory = RuntimeFactory(browser_process=process_resources)
    client, prefix, _ = browser_redis
    # Connection DI only: original RedisClient methods perform actual ping,
    # Lua/CAS/TTL operations against the tracked helper's unique namespace.
    from src.tools.browser.run_store import redis_client
    monkeypatch.setattr(redis_client, '_client', client)
    monkeypatch.setattr(redis_client, '_connected', True)
    monkeypatch.setattr(redis_client, '_key_prefix', prefix)
    assert redis_client.is_available()

    def owner_manager(call_id):
        owner = RunnerBrowserOwner(control, state, call_id)
        with browser_execution_owner_scope(owner):
            manager = BrowserRunManager(store=RedisRunStore())
        return owner, manager

    owned = {}
    manager_ref = None
    try:
        owner, manager = owner_manager(original)
        record = await manager.create(row['tenant_id'], row['user_id'], row['session_id'])
        await manager.start(record)
        native = manager._executors[(row['tenant_id'], record.run_id)]._executor
        owned = identities(native._process.pid)
        manager_ref = weakref.ref(manager)
        await manager._stop_renew_task((row['tenant_id'], record.run_id))
        second = 'native-lifetime-second-call'
        state.tools[second] = ToolFact(ToolCall(second, 'browser_automation', {}), phase='dispatching')
        second_owner, second_manager = owner_manager(second)
        second_record = await second_manager.create(row['tenant_id'], row['user_id'], row['session_id'])
        # A distinct unstarted manager provides actual zero-IO close proof.
        if boundary == 'native_unconfirmed':
            original_request = LocalPlaywrightExecutor._request
            async def failed_close_transport(self, command, result_type):
                if isinstance(command, CloseCommand):
                    raise ProtocolError('fixture_close_ack_transport_failure')
                return await original_request(self, command, result_type)
            with monkeypatch.context() as fault:
                fault.setattr(LocalPlaywrightExecutor, '_request', failed_close_transport)
                with pytest.raises(BrowserOwnerFailure, match='BROWSER_CLOSE_VERIFICATION_REQUIRED'):
                    await manager.finalize(row['tenant_id'], record.run_id, RunState.SUCCEEDED, 'fixture')
        else:
            # Only the original run's closed UPDATE fails; other state writes
            # and the actual native resource closure execute normally.
            from psycopg2 import sql
            import uuid
            name = 'browser_close_fixture_' + uuid.uuid4().hex
            with service_database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.run_id={} AND NEW.runtime_state='closed' THEN RAISE EXCEPTION 'BROWSER_FIXTURE_CLOSE_FAILED'; END IF; RETURN NEW; END $$")
                    .format(sql.Identifier(name), sql.Literal(record.run_id)))
                cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON bs_browser_runs FOR EACH ROW EXECUTE FUNCTION {}()')
                    .format(sql.Identifier(name), sql.Identifier(name)))
            try:
                with pytest.raises(CheckpointFailure, match='BROWSER_OWNER_STORAGE_FAILED'):
                    await manager.finalize(row['tenant_id'], record.run_id, RunState.SUCCEEDED, 'fixture')
            finally:
                with service_database.connect() as connection, connection.cursor() as cursor:
                    cursor.execute(sql.SQL('DROP TRIGGER {} ON bs_browser_runs').format(sql.Identifier(name)))
                    cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(name)))
        assert manager in process_resources['retained_managers']
        assert second_manager in process_resources['retained_managers']
        native = owner = manager = None
        gc.collect()
        assert manager_ref() is not None
        if boundary == 'native_unconfirmed':
            with pytest.raises(BrowserOwnerFailure, match='BROWSER_CLOSE_VERIFICATION_REQUIRED'):
                await factory.close()
            assert manager_ref() in process_resources['retained_managers']
            assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',
                                          (record.run_id,)) != [{'runtime_state': 'closed'}]
        else:
            # Retry only this original manager first; successful release must
            # not clear the second manager's native ownership capability.
            await manager_ref().finalize(row['tenant_id'], record.run_id, RunState.SUCCEEDED, 'fixture_retry')
            assert manager_ref() not in process_resources['retained_managers']
            assert second_manager in process_resources['retained_managers']
            await factory.close()
            assert service_database.rows('SELECT runtime_state,owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                          (record.run_id,)) == [{'runtime_state': 'closed', 'owner_lease_until': None}]
        assert second_manager not in process_resources['retained_managers']
        assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',
                                      (second_record.run_id,)) == [{'runtime_state': 'closed'}]
    finally:
        for retained in tuple(process_resources.get('retained_managers', set())):
            for key in tuple(retained._renew_tasks):
                await retained._stop_renew_task(key)
        await cleanup(owned)
        # The failed-close case intentionally retains business-unknown handles.
        # After exact PID/birth fixture cleanup, clear only this test's private
        # process resource container, never a production registry or PG proof.
        process_resources.get('retained_managers', set()).clear()
