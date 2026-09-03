"""Phase 2 协议、进程、fallback 与事务的独立安全回归。"""

from __future__ import annotations

import pytest

# psutil 为可选依赖（容器环境未必安装），缺失时跳过整个模块而非让收集报错
pytest.importorskip("psutil")

import asyncio
import io
import struct
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import psutil
import pytest
from pydantic import ValidationError

import src.tools.browser.executor.local as local_module
import src.tools.browser.run_db as run_db_module
import src.tools.browser.run_store as run_store_module
from src.tools.browser.executor.local import LocalPlaywrightExecutor
from src.tools.browser.executor.models import (
    BrowserRunSpec,
    ClickCommand,
    ContentCommand,
    NavigateCommand,
    ResultStatus,
    SnapshotElement,
    SnapshotResult,
)
from src.tools.browser.run_db import BrowserRunDB
from src.tools.browser.run_store import RedisRunStore, RunRecord
from src.tools.browser.worker_main import BrowserWorker
from src.tools.browser.worker_protocol import (
    MAX_FRAME_BYTES,
    FrameTooLarge,
    InvalidFrame,
    ProtocolError,
    TruncatedFrame,
    decode_payload,
    encode_frame,
    read_frame,
    read_frame_sync,
)


def _run_spec() -> BrowserRunSpec:
    return BrowserRunSpec(
        run_id="br_" + uuid.uuid4().hex,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="audit-session",
    )


def _navigate(run_id: str, *, seq: int = 2, deadline_delta: float = 5) -> NavigateCommand:
    return NavigateCommand(
        run_id=run_id,
        seq=seq,
        command_id=f"bc_{uuid.uuid4().hex}",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=deadline_delta),
        url="data:text/html,<p>safe</p>",
    )


def test_protocol_exact_boundary_zero_negative_invalid_utf8_json_and_pollution():
    payload = b'{"x":"' + (b"a" * (MAX_FRAME_BYTES - 8)) + b'"}'
    assert len(payload) == MAX_FRAME_BYTES
    assert decode_payload(payload)["x"].startswith("a")
    with pytest.raises(FrameTooLarge):
        decode_payload(payload + b" ")
    for bad in (b"", b"\xff", b"{", b"[]"):
        with pytest.raises(InvalidFrame):
            decode_payload(bad)
    with pytest.raises(InvalidFrame):
        read_frame_sync(io.BytesIO(struct.pack(">I", 0)))
    with pytest.raises(FrameTooLarge):
        read_frame_sync(io.BytesIO(struct.pack(">I", 0xFFFFFFFF)))
    with pytest.raises(ProtocolError):
        read_frame_sync(io.BytesIO(b"NOISE_ON_STDOUT"))


@pytest.mark.asyncio
async def test_async_protocol_truncated_header_and_body():
    for data in (b"\x00\x00", struct.pack(">I", 4) + b"ab"):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        with pytest.raises(TruncatedFrame):
            await read_frame(reader)


@pytest.mark.asyncio
async def test_worker_rejects_expired_deadline_and_caches_idempotent_result():
    run = _run_spec()
    worker = BrowserWorker()
    worker.run = run
    worker.last_seq = 1
    command = _navigate(run.run_id, deadline_delta=-1)
    first = await worker.handle(command)
    second = await worker.handle(command)
    assert first == second
    assert first["error_code"] == "COMMAND_TIMEOUT"
    assert worker.last_seq == 2


class _ProtocolProcess:
    stdin = object()
    stdout = object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected_error",
    [
        ({"run_id": "wrong", "command_id": "same", "seq": 2}, ProtocolError),
        ({"run_id": "same", "command_id": "same", "seq": 2, "status": "not-valid"}, ValidationError),
    ],
)
async def test_local_executor_reaps_on_mismatched_or_invalid_response(
    monkeypatch, response, expected_error
):
    run = _run_spec()
    command = _navigate(run.run_id)
    response = dict(response)
    response["command_id"] = command.command_id
    if response["run_id"] == "same":
        response["run_id"] = run.run_id
    executor = LocalPlaywrightExecutor()
    executor._run = run
    executor._process = _ProtocolProcess()
    reaped = AsyncMock()
    monkeypatch.setattr(executor, "_force_reap", reaped)
    monkeypatch.setattr(local_module, "write_frame", AsyncMock())
    monkeypatch.setattr(local_module, "read_frame", AsyncMock(return_value=response))
    with pytest.raises(expected_error):
        await executor._request(command, SnapshotResult)
    reaped.assert_awaited_once_with("protocol_or_timeout")


@pytest.mark.asyncio
async def test_local_executor_deadline_includes_time_waiting_for_io_lock(monkeypatch):
    run = _run_spec()
    command = _navigate(run.run_id, deadline_delta=0.03)
    executor = LocalPlaywrightExecutor()
    executor._run = run
    executor._process = _ProtocolProcess()
    reaped = AsyncMock()
    monkeypatch.setattr(executor, "_force_reap", reaped)
    await executor._io_lock.acquire()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await executor._request(command, SnapshotResult)
    finally:
        executor._io_lock.release()
    reaped.assert_awaited_once()


def _same_process(pid: int, create_time: float) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - create_time) < 0.01
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


@pytest.mark.asyncio
async def test_real_local_worker_contract_leaves_no_owned_processes():
    executor = LocalPlaywrightExecutor()
    run = _run_spec()
    owned: list[tuple[int, float]] = []
    try:
        started = await executor.start(run)
        assert started.status == ResultStatus.OK
        process = psutil.Process(executor._process.pid)
        owned.append((process.pid, process.create_time()))
        navigated = await executor.navigate(_navigate(run.run_id))
        assert navigated.status == ResultStatus.OK
        await asyncio.sleep(0.1)
        for child in process.children(recursive=True):
            try:
                owned.append((child.pid, child.create_time()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    finally:
        await executor.close("completed")
    for _ in range(50):
        if not any(_same_process(pid, created) for pid, created in owned):
            break
        await asyncio.sleep(0.05)
    assert not [item for item in owned if _same_process(*item)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), ProtocolError("damaged")])
async def test_local_close_timeout_or_protocol_damage_reaps_only_owned_worker(
    monkeypatch, failure
):
    executor = LocalPlaywrightExecutor()
    run = _run_spec()
    await executor.start(run)
    process = psutil.Process(executor._process.pid)
    identity = (process.pid, process.create_time())
    monkeypatch.setattr(executor, "_request", AsyncMock(side_effect=failure))
    result = await executor.close("error")
    assert result.closed is True
    assert result.forced is True
    assert result.error_code == "CLOSE_UNCONFIRMED"
    assert not _same_process(*identity)
    assert executor._stderr_task is None


@pytest.mark.asyncio
async def test_local_close_cancellation_reaps_owned_worker(monkeypatch):
    executor = LocalPlaywrightExecutor()
    run = _run_spec()
    await executor.start(run)
    process = psutil.Process(executor._process.pid)
    identity = (process.pid, process.create_time())
    entered = asyncio.Event()

    async def blocked_request(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(executor, "_request", blocked_request)
    task = asyncio.create_task(executor.close("cancelled"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not _same_process(*identity)
    assert executor._stderr_task is None


@pytest.mark.asyncio
async def test_local_close_reports_worker_crash_as_unconfirmed():
    executor = LocalPlaywrightExecutor()
    run = _run_spec()
    await executor.start(run)
    process = executor._process
    identity = (process.pid, psutil.Process(process.pid).create_time())
    psutil.Process(process.pid).terminate()
    await asyncio.wait_for(process.wait(), timeout=5)
    result = await executor.close("worker_crash")
    assert result.closed is True
    assert result.forced is True
    assert result.error_code == "CLOSE_UNCONFIRMED"
    assert not _same_process(*identity)


@pytest.mark.asyncio
async def test_redis_store_never_uses_fallback_when_real_redis_is_unavailable(monkeypatch):
    store = object.__new__(RedisRunStore)
    store.run_ttl = 600
    fallback_forbidden = AsyncMock(side_effect=AssertionError("不应调用 fallback API"))
    monkeypatch.setattr(run_store_module.redis_client, "is_available", lambda: False)
    monkeypatch.setattr(run_store_module.redis_client, "acquire_lock", fallback_forbidden)
    record = RunRecord(
        tenant_id="tenant", user_id="user", run_id="br_" + uuid.uuid4().hex,
        session_id="same", state="CREATED",
    )
    assert await store.create(record) is False
    assert await store.get("tenant", record.run_id) is None
    assert await store.compare_state("tenant", record.run_id, {"CREATED"}, "ROUTING") is None
    assert await store.acquire_owner("tenant", record.run_id, "owner", 30) is False
    assert await store.renew_owner("tenant", record.run_id, "owner", 30) is False
    assert await store.release_owner("tenant", record.run_id, "owner") is False
    assert await store.request_cancel("tenant", record.run_id) is False
    assert await store.list_expired(time.time()) == []
    fallback_forbidden.assert_not_awaited()


class _FakeDBConnection:
    def __init__(self):
        self.calls = []
        self.rowcount = 1
        self.commits = 0
        self.row = {"tenant_id": "tenant", "run_id": "run"}

    def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_run_db_all_crud_is_parameterized_tenant_scoped_and_committed(monkeypatch):
    connection = _FakeDBConnection()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(run_db_module, "get_db_connection", fake_connection)
    db = BrowserRunDB()
    await db.create_run({
        "tenant_id": "tenant", "user_id": "user", "run_id": "run",
        "session_id": "session", "state": "CREATED",
    })
    assert (await db.get_run("tenant", "run"))["tenant_id"] == "tenant"
    assert await db.update_run_state("tenant", "run", "SUCCEEDED")
    await db.create_assistance({
        "tenant_id": "tenant", "user_id": "user", "assistance_id": "assist",
        "run_id": "run", "agent_execution_id": "agent", "tool_call_id": "tool",
        "reason_code": "AUTH_REQUIRED", "instruction_code": "LOGIN",
        "completion_mode": "confirm_only", "expires_at": datetime.now(timezone.utc),
    })
    assert (await db.get_assistance("tenant", "assist"))["tenant_id"] == "tenant"
    assert await db.update_assistance_state("tenant", "assist", "pending", "completed")
    selects_updates = [call for call in connection.calls if call[0].startswith(("SELECT", "UPDATE"))]
    assert selects_updates
    assert all("tenant_id=%s" in sql for sql, _ in selects_updates)
    assert all("tenant" in params for _, params in selects_updates)
    assert connection.commits == 4


def test_sensitive_snapshot_fields_are_hidden_from_repr():
    secret = "credential=must-not-appear"
    result = SnapshotResult(
        run_id="br_" + uuid.uuid4().hex,
        command_id="bc_snapshot_safe",
        seq=2,
        status=ResultStatus.OK,
        current_origin_path=f"https://example.test/{secret}",
        title=secret,
        interactive_elements=(SnapshotElement(ref="e1", label=secret),),
        page_text=secret,
    )
    assert secret not in repr(result)


def test_selector_is_hidden_and_ref_must_be_snapshot_generated():
    fields = {
        "run_id": "br_" + uuid.uuid4().hex,
        "seq": 2,
        "command_id": "bc_sensitive_selector",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=5),
    }
    selector = '[data-secret="credential=must-not-appear"]'
    command = ContentCommand(**fields, selector=selector)
    assert selector not in repr(command)
    with pytest.raises(ValidationError):
        ClickCommand(**fields, ref='e1"] body')


def _browser_ddl_distributed() -> bool:
    """bs_browser 表 DDL 是否仍随部署脚本分发（61e9d7e7 简化时曾从脚本移除）"""
    for path in ("deploy/init-postgres.sql", "deploy/db_update.sql"):
        sql = open(path, encoding="utf-8").read().lower()
        if "create table if not exists bs_browser_runs" in sql:
            return True
    return False


def test_assistance_foreign_key_is_tenant_scoped_in_both_deploy_scripts():
    if not _browser_ddl_distributed():
        pytest.skip(
            "bs_browser 表 DDL 未随部署脚本分发（存量库已有表，新库依赖初始化流程）；"
            "恢复 DDL 后本守卫自动恢复断言"
        )
    expected = "foreign key (tenant_id, run_id) references bs_browser_runs(tenant_id, run_id)"
    for path in ("deploy/init-postgres.sql", "deploy/db_update.sql"):
        sql = " ".join(open(path, encoding="utf-8").read().lower().split())
        assert "unique (tenant_id, run_id)" in sql
        assert expected in sql
