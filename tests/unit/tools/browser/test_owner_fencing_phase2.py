"""owner fencing 必须在任何生产命令写 IPC 前生效。"""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.tools.browser.executor.local as local_module
from src.tools.browser.executor.fenced import FencedBrowserExecutor
from src.tools.browser.executor.local import LocalPlaywrightExecutor
from src.tools.browser.executor.models import (
    BrowserRunSpec, ClickCommand, ContentCommand, FillCommand, KeyboardCommand,
    NavigateCommand, PointerCommand, ResultStatus, SelectCommand, SnapshotCommand,
    StartCommand, StartResult,
)
import src.tools.browser.run_store as run_store_module
from src.tools.browser.run_store import InMemoryRunStore, RedisRunStore, RunRecord
from src.tools.browser.reaper import BrowserRunReaper
from src.tools.browser.run_manager import BrowserRunManager, RunState


class _Process:
    stdin = object()
    stdout = object()
    returncode = None


def _fields(run_id: str, seq: int = 2):
    return dict(
        run_id=run_id, seq=seq, command_id="bc_" + uuid.uuid4().hex,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
    )


def _commands(run_id: str):
    fields = _fields(run_id)
    return [
        ("navigate", NavigateCommand(**fields, url="https://example.test/")),
        ("snapshot", SnapshotCommand(**fields)),
        ("click", ClickCommand(**fields, ref="e1")),
        ("fill", FillCommand(**fields, ref="e1", value="secret")),
        ("select", SelectCommand(**fields, ref="e1", option="one")),
        ("keyboard", KeyboardCommand(**fields, key="Enter")),
        ("pointer", PointerCommand(**fields, action="move", x=1, y=1)),
        ("content", ContentCommand(**fields, format="text")),
    ]


async def _owned_fixture():
    run_id = "br_" + uuid.uuid4().hex
    store = InMemoryRunStore()
    await store.create(RunRecord(
        tenant_id="tenant", user_id="user", run_id=run_id,
        session_id="session", state="RUNNING_AGENT",
    ))
    token = await store.acquire_owner("tenant", run_id, "same-manager", 30)
    return store, run_id, token


@pytest.mark.asyncio
async def test_owner_acquire_is_unique_epoch_and_active_owner_must_renew():
    store, run_id, first = await _owned_fixture()
    assert await store.acquire_owner("tenant", run_id, "same-manager", 30) is False
    key = ("tenant", run_id)
    async with store._lock:
        record = store._records[key]
        store._records[key] = record.model_copy(update={"owner_expires_at": time.time() - 1})
    second = await store.acquire_owner("tenant", run_id, "same-manager", 30)
    assert second
    assert second != first
    assert not await store.renew_owner("tenant", run_id, str(first), 30)
    assert await store.renew_owner("tenant", run_id, str(second), 30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["expired", "reaper", "aba", "cancel", "finalizing", "terminal", "missing"]
)
@pytest.mark.parametrize("method_name", [
    "navigate", "snapshot", "click", "fill", "select", "keyboard", "pointer", "content",
])
async def test_every_command_is_rejected_before_write_frame(
    monkeypatch, failure, method_name
):
    store, run_id, old_token = await _owned_fixture()
    key = ("tenant", run_id)
    if failure in {"expired", "reaper", "aba"}:
        async with store._lock:
            record = store._records[key]
            store._records[key] = record.model_copy(update={"owner_expires_at": time.time() - 1})
    if failure == "reaper":
        assert await store.acquire_owner("tenant", run_id, "reaper", 30)
    elif failure == "aba":
        new_token = await store.acquire_owner("tenant", run_id, "same-manager", 30)
        assert new_token != old_token
    elif failure == "cancel":
        assert await store.request_cancel("tenant", run_id)
    elif failure in {"finalizing", "terminal"}:
        async with store._lock:
            record = store._records[key]
            store._records[key] = record.model_copy(update={
                "state": "FINALIZING" if failure == "finalizing" else "SUCCEEDED"
            })
    elif failure == "missing":
        async with store._lock:
            store._records.pop(key)

    local = LocalPlaywrightExecutor()
    local._run = BrowserRunSpec(
        run_id=run_id, tenant_id="tenant", user_id="user", session_id="session"
    )
    local._process = _Process()
    local._force_reap = AsyncMock()
    executor = FencedBrowserExecutor(local, store, "tenant", run_id, str(old_token))
    write = AsyncMock()
    monkeypatch.setattr(local_module, "write_frame", write)

    command = dict(_commands(run_id))[method_name]
    result = await getattr(executor, method_name)(command)
    assert result.error_code == ("CANCELLED" if failure == "cancel" else "OWNER_LEASE_LOST")
    write.assert_not_awaited()
    local._force_reap.assert_awaited_once_with("owner_fence_failed")


@pytest.mark.asyncio
async def test_normal_owner_fence_allows_write(monkeypatch):
    store, run_id, token = await _owned_fixture()
    local = LocalPlaywrightExecutor()
    local._run = BrowserRunSpec(
        run_id=run_id, tenant_id="tenant", user_id="user", session_id="session"
    )
    local._process = _Process()
    executor = FencedBrowserExecutor(local, store, "tenant", run_id, str(token))
    command = NavigateCommand(**_fields(run_id), url="https://example.test/")
    write = AsyncMock()
    monkeypatch.setattr(local_module, "write_frame", write)
    monkeypatch.setattr(local_module, "read_frame", AsyncMock(return_value={
        "run_id": run_id, "command_id": command.command_id, "seq": command.seq,
        "status": "ok", "current_origin_path": "https://example.test/",
    }))
    result = await executor.navigate(command)
    assert result.status.value == "ok"
    write.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_command_is_also_fenced_before_write(monkeypatch):
    store, run_id, token = await _owned_fixture()
    local = LocalPlaywrightExecutor()
    run = BrowserRunSpec(
        run_id=run_id, tenant_id="tenant", user_id="user", session_id="session"
    )
    local._run = run
    local._process = _Process()
    local._force_reap = AsyncMock()
    FencedBrowserExecutor(local, store, "tenant", run_id, str(token))
    async with store._lock:
        record = store._records[("tenant", run_id)]
        store._records[("tenant", run_id)] = record.model_copy(
            update={"owner_expires_at": time.time() - 1}
        )
    write = AsyncMock()
    monkeypatch.setattr(local_module, "write_frame", write)
    command = StartCommand(**_fields(run_id, seq=1), run=run)
    result = await local._request(command, StartResult)
    assert result.status == ResultStatus.ERROR
    assert result.error_code == "OWNER_LEASE_LOST"
    write.assert_not_awaited()
    local._force_reap.assert_awaited_once_with("owner_fence_failed")


@pytest.mark.asyncio
async def test_old_manager_finalize_cannot_mutate_new_owner_epoch():
    store, run_id, old_token = await _owned_fixture()
    manager = BrowserRunManager(store=store, run_db=_NoopDB())
    key = ("tenant", run_id)
    old_executor = AsyncMock()
    manager._owner_tokens[key] = str(old_token)
    manager._executors[key] = old_executor
    async with store._lock:
        record = store._records[key]
        store._records[key] = record.model_copy(
            update={"owner_expires_at": time.time() - 1}
        )
    new_token = await store.acquire_owner("tenant", run_id, "new-owner", 30)
    assert new_token and new_token != old_token

    with pytest.raises(RuntimeError, match="OWNER_LEASE_LOST"):
        await manager.finalize("tenant", run_id, RunState.FAILED, "old_owner_failed")

    current = await store.get("tenant", run_id)
    assert current.state == "RUNNING_AGENT"
    assert current.owner_token == new_token
    old_executor.close.assert_awaited_once_with("owner_lost")


def test_owner_epoch_token_is_not_exposed_in_run_record_repr():
    record = RunRecord(
        tenant_id="tenant", user_id="user", run_id="br_" + "9" * 32,
        session_id="session", state="RUNNING_AGENT", owner_token="bo_secret_epoch",
    )
    assert "bo_secret_epoch" not in repr(record)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lua_result", "expected"),
    [(1, None), (2, "CANCELLED"), (3, "OWNER_LEASE_LOST")],
)
async def test_redis_fence_uses_one_atomic_lua_without_renewal(
    monkeypatch, lua_result, expected
):
    eval_mock = MagicMock(return_value=lua_result)
    monkeypatch.setattr(run_store_module.redis_client, "is_available", lambda: True)
    monkeypatch.setattr(
        run_store_module.redis_client, "_client", SimpleNamespace(eval=eval_mock)
    )
    renew = MagicMock(side_effect=AssertionError("命令 fence 不得续租"))
    monkeypatch.setattr(run_store_module.redis_client, "renew_lock", renew)
    store = object.__new__(RedisRunStore)
    store.run_ttl = 600
    assert await store.fence_owner("tenant", "br_" + "1" * 32, "bo_token", 10.0) == expected
    eval_mock.assert_called_once()
    args = eval_mock.call_args.args
    assert args[1] == 2
    assert args[2].endswith("browser_owner:tenant:br_" + "1" * 32)
    assert args[3].endswith("browser_run:tenant:br_" + "1" * 32)
    assert args[4:] == ("bo_token", 10.0)
    renew.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", [
    "navigate", "snapshot", "click", "fill", "select", "keyboard", "pointer", "content",
])
async def test_redis_unavailable_rejects_every_command_without_ipc(
    monkeypatch, method_name
):
    run_id = "br_" + uuid.uuid4().hex
    store = object.__new__(RedisRunStore)
    store.run_ttl = 600
    monkeypatch.setattr(run_store_module.redis_client, "is_available", lambda: False)
    local = LocalPlaywrightExecutor()
    local._run = BrowserRunSpec(
        run_id=run_id, tenant_id="tenant", user_id="user", session_id="session"
    )
    local._process = _Process()
    local._force_reap = AsyncMock()
    executor = FencedBrowserExecutor(local, store, "tenant", run_id, "bo_old")
    write = AsyncMock()
    monkeypatch.setattr(local_module, "write_frame", write)
    result = await getattr(executor, method_name)(dict(_commands(run_id))[method_name])
    assert result.error_code == "OWNER_LEASE_LOST"
    write.assert_not_awaited()
    local._force_reap.assert_awaited_once_with("owner_fence_failed")


class _DelayedFenceStore(InMemoryRunStore):
    def __init__(self):
        super().__init__()
        self.fence_entered = asyncio.Event()
        self.fence_continue = asyncio.Event()

    async def fence_owner(self, tenant_id, run_id, owner_token, now):
        self.fence_entered.set()
        await self.fence_continue.wait()
        return await super().fence_owner(tenant_id, run_id, owner_token, now)


class _NoopDB:
    async def update_run_state(self, *args, **kwargs):
        return True


def _fenced_local(store, run_id, token):
    local = LocalPlaywrightExecutor()
    local._run = BrowserRunSpec(
        run_id=run_id, tenant_id="tenant", user_id="user", session_id="session"
    )
    local._process = _Process()
    local._force_reap = AsyncMock()
    return local, FencedBrowserExecutor(local, store, "tenant", run_id, str(token))


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["cancel", "reaper"])
async def test_concurrent_cancel_or_reaper_cannot_cross_delayed_fence(monkeypatch, race):
    store = _DelayedFenceStore()
    run_id = "br_" + uuid.uuid4().hex
    await store.create(RunRecord(
        tenant_id="tenant", user_id="user", run_id=run_id,
        session_id="session", state="RUNNING_AGENT",
    ))
    token = await store.acquire_owner("tenant", run_id, "owner", 30)
    local, executor = _fenced_local(store, run_id, token)
    write = AsyncMock()
    monkeypatch.setattr(local_module, "write_frame", write)
    task = asyncio.create_task(executor.navigate(
        NavigateCommand(**_fields(run_id), url="https://example.test/")
    ))
    await store.fence_entered.wait()
    if race == "cancel":
        assert await store.request_cancel("tenant", run_id)
    else:
        async with store._lock:
            record = store._records[("tenant", run_id)]
            store._records[("tenant", run_id)] = record.model_copy(
                update={"owner_expires_at": time.time() - 1}
            )
        reaper = BrowserRunReaper(BrowserRunManager(store=store, run_db=_NoopDB()))
        assert await reaper.reap_once() == 1
    store.fence_continue.set()
    result = await task
    assert result.error_code in {"CANCELLED", "OWNER_LEASE_LOST"}
    write.assert_not_awaited()
    local._force_reap.assert_awaited_once_with("owner_fence_failed")


@pytest.mark.asyncio
async def test_concurrent_renew_keeps_current_epoch_valid(monkeypatch):
    store = _DelayedFenceStore()
    run_id = "br_" + uuid.uuid4().hex
    await store.create(RunRecord(
        tenant_id="tenant", user_id="user", run_id=run_id,
        session_id="session", state="RUNNING_AGENT",
    ))
    token = await store.acquire_owner("tenant", run_id, "owner", 30)
    local, executor = _fenced_local(store, run_id, token)
    command = NavigateCommand(**_fields(run_id), url="https://example.test/")
    write = AsyncMock()
    monkeypatch.setattr(local_module, "write_frame", write)
    monkeypatch.setattr(local_module, "read_frame", AsyncMock(return_value={
        "run_id": run_id, "command_id": command.command_id, "seq": command.seq,
        "status": "ok", "current_origin_path": "https://example.test/",
    }))
    task = asyncio.create_task(executor.navigate(command))
    await store.fence_entered.wait()
    assert await store.renew_owner("tenant", run_id, str(token), 30)
    store.fence_continue.set()
    result = await task
    assert result.status.value == "ok"
    write.assert_awaited_once()
