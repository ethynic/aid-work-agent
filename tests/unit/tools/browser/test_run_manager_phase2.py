import asyncio
import time

import pytest

from src.tools.browser.run_manager import (
    BrowserRunManager,
    InvalidRunTransition,
    RunState,
    close_all_active_browser_managers,
)
from src.tools.browser.run_store import InMemoryRunStore
from src.tools.browser.reaper import BrowserRunReaper
from src.config.settings import settings
from tests.unit.tools.browser.fake_executor import FakeRemoteExecutor


class FakeRouter:
    def __init__(self): self.executors = []
    def create_executor(self, target):
        assert target == "server"
        executor = FakeRemoteExecutor(); self.executors.append(executor); return executor


class BrokenRouter:
    def create_executor(self, target):
        raise RuntimeError("router unavailable")


class NoopDB:
    async def create_run(self, data): pass
    async def update_run_state(self, *args, **kwargs): return True


class SimulatedDistributedStore(InMemoryRunStore):
    distributed = True


@pytest.mark.asyncio
async def test_two_tenants_with_same_session_are_isolated_and_context_required():
    store = InMemoryRunStore()
    manager = BrowserRunManager(store=store, router=FakeRouter(), run_db=NoopDB())
    a = await manager.create("tenant-a", "user", "same")
    b = await manager.create("tenant-b", "user", "same")
    assert a.run_id != b.run_id
    assert await store.get("tenant-a", b.run_id) is None
    with pytest.raises(ValueError):
        await manager.create("", "user", "same")


@pytest.mark.asyncio
async def test_legal_illegal_transitions_and_concurrent_finalize_are_idempotent():
    manager = BrowserRunManager(store=InMemoryRunStore(), router=FakeRouter(), run_db=NoopDB())
    record = await manager.create("tenant", "user", "session")
    await manager.start(record)
    with pytest.raises(InvalidRunTransition):
        await manager.transition("tenant", record.run_id, RunState.CREATED)
    results = await asyncio.gather(*[
        manager.finalize("tenant", record.run_id, RunState.SUCCEEDED, "success") for _ in range(5)
    ])
    assert {item.state for item in results} == {RunState.SUCCEEDED.value}
    assert manager.router.executors[0].closed is True


@pytest.mark.asyncio
async def test_owner_lease_cancel_and_reaper_claim_are_single_winner():
    store = InMemoryRunStore()
    m1 = BrowserRunManager(store=store, router=FakeRouter(), owner_id="worker-1", run_db=NoopDB())
    record = await m1.create("tenant", "user", "session")
    assert await store.acquire_owner("tenant", record.run_id, "worker-1", 30)
    assert not await store.acquire_owner("tenant", record.run_id, "worker-2", 30)
    assert await store.request_cancel("tenant", record.run_id)
    assert (await store.get("tenant", record.run_id)).cancel_requested is True
    async with store._lock:
        current = store._records[("tenant", record.run_id)]
        store._records[("tenant", record.run_id)] = current.model_copy(update={"owner_expires_at": time.time() - 1})
    reaper1 = BrowserRunReaper(BrowserRunManager(store=store, router=FakeRouter(), run_db=NoopDB()))
    reaper2 = BrowserRunReaper(BrowserRunManager(store=store, router=FakeRouter(), run_db=NoopDB()))
    claimed = await asyncio.gather(reaper1.reap_once(), reaper2.reap_once())
    assert sum(claimed) == 1
    assert (await store.get("tenant", record.run_id)).state == RunState.EXPIRED.value


@pytest.mark.asyncio
async def test_redis_unavailable_store_is_explicit_single_request_degradation():
    manager = BrowserRunManager(store=InMemoryRunStore(), router=FakeRouter(), run_db=NoopDB())
    assert manager.degraded is True
    record = await manager.create("tenant", "user", "session", "server")
    assert record.degraded_single_request is True
    with pytest.raises(RuntimeError, match="REDIS_REQUIRED"):
        await manager.create("tenant", "user", "session", "client")


@pytest.mark.asyncio
async def test_cancel_from_second_worker_is_observed_by_owner(monkeypatch):
    monkeypatch.setattr(settings.tools.browser, "owner_renew_interval", 0.01)
    store = SimulatedDistributedStore()
    router = FakeRouter()
    owner = BrowserRunManager(store=store, router=router, owner_id="worker-1", run_db=NoopDB())
    other = BrowserRunManager(store=store, router=FakeRouter(), owner_id="worker-2", run_db=NoopDB())
    record = await owner.create("tenant", "user", "session")
    await owner.start(record)
    assert await other.request_cancel("tenant", "user", record.run_id)
    for _ in range(50):
        current = await store.get("tenant", record.run_id)
        if current.state == RunState.CANCELLED.value:
            break
        await asyncio.sleep(0.01)
    assert current.state == RunState.CANCELLED.value
    assert router.executors[0].closed is True


@pytest.mark.asyncio
async def test_router_start_failure_is_finalized_instead_of_stuck_routing():
    store = InMemoryRunStore()
    manager = BrowserRunManager(store=store, router=BrokenRouter(), run_db=NoopDB())
    record = await manager.create("tenant", "user", "session")
    with pytest.raises(RuntimeError, match="router unavailable"):
        await manager.start(record)
    assert (await store.get("tenant", record.run_id)).state == RunState.FAILED.value


@pytest.mark.asyncio
async def test_lost_distributed_state_closes_local_executor_and_shutdown_registry():
    store = SimulatedDistributedStore()
    router = FakeRouter()
    manager = BrowserRunManager(store=store, router=router, run_db=NoopDB())
    record = await manager.create("tenant", "user", "session")
    await manager.start(record)
    async with store._lock:
        del store._records[("tenant", record.run_id)]
    with pytest.raises(KeyError, match="RUN_NOT_FOUND"):
        await manager.finalize("tenant", record.run_id, RunState.FAILED, "redis_lost")
    assert router.executors[0].closed is True

    second_router = FakeRouter()
    second_manager = BrowserRunManager(
        store=InMemoryRunStore(), router=second_router, run_db=NoopDB()
    )
    second = await second_manager.create("tenant", "user", "session")
    await second_manager.start(second)
    await close_all_active_browser_managers("shutdown")
    assert second_router.executors[0].closed is True
