from datetime import datetime, timedelta, timezone
import uuid
from pathlib import Path
import shutil
import tempfile

import pytest

from src.tools.browser.executor.local import LocalPlaywrightExecutor
from src.tools.browser.executor.models import (
    BrowserRunSpec, ContentCommand, NavigateCommand, ResultStatus, SnapshotCommand,
)
from tests.unit.tools.browser.fake_executor import FakeRemoteExecutor


def _fields(run_id, seq, command_id):
    return dict(run_id=run_id, seq=seq, command_id=command_id, deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15))


@pytest.fixture
def worker_temporary_directory(monkeypatch):
    directory = Path(tempfile.mkdtemp(prefix='browser-protocol-fixture-', dir='/tmp'))
    monkeypatch.setenv('TMPDIR', str(directory))
    monkeypatch.setattr(tempfile, 'tempdir', str(directory))
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
        assert not directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [FakeRemoteExecutor,
    pytest.param(LocalPlaywrightExecutor, marks=pytest.mark.real_browser)], ids=["fake_remote", "local_worker"])
async def test_executor_contract_start_command_idempotence_seq_and_close(factory, worker_temporary_directory):
    executor = factory()
    run = BrowserRunSpec(run_id="br_" + uuid.uuid4().hex, tenant_id="tenant-a", user_id="user-a", session_id="same-session")
    try:
        started = await executor.start(run)
    except ImportError:
        pytest.skip("本机未安装 Playwright")
    assert started.status == ResultStatus.OK
    command = NavigateCommand(**_fields(run.run_id, 2, "bc_nav_0001"), url="data:text/html,<button>Go</button>")
    first = await executor.navigate(command)
    second = await executor.navigate(command)
    assert first.model_dump() == second.model_dump()
    snapshot = await executor.snapshot(SnapshotCommand(**_fields(run.run_id, 3, "bc_snap_001")))
    assert snapshot.status == ResultStatus.OK
    old = await executor.snapshot(SnapshotCommand(**_fields(run.run_id, 2, "bc_old_0001")))
    assert old.status == ResultStatus.ERROR
    assert old.error_code == "SEQ_REJECTED"
    content = await executor.content(ContentCommand(**_fields(run.run_id, 4, "bc_content1"), format="text"))
    assert content.status == ResultStatus.OK
    closed = await executor.close("completed")
    assert closed.closed is True


def test_sensitive_command_fields_are_not_in_repr():
    run_id = "br_" + uuid.uuid4().hex
    command = NavigateCommand(**_fields(run_id, 1, "bc_repr_001"), url="https://example.test/?token=secret")
    assert "token=secret" not in repr(command)
