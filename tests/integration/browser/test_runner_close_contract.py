"""Finite real close contracts; no page renderer or provider result substitutes.

Native close-method faults are explicitly IO DI on actual Playwright resources.
The two PID-reuse negatives simulate /proc observations and forbid any signal;
the dead-leader positive uses actual original descendants and real OS signals.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import uuid

import psutil
import pytest

from src.tools.browser.executor.local import LocalPlaywrightExecutor
from src.tools.browser.executor.models import BrowserRunSpec, CloseCommand, ResultStatus, StartCommand
from src.tools.browser.owner_port import BrowserOwnerFailure
from src.tools.browser.worker_main import BrowserWorker

pytestmark = [pytest.mark.integration, pytest.mark.browser, pytest.mark.real_browser,
              pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux owned PID/birth contract')]


@pytest.fixture
def linux_browser_tmp(monkeypatch):
    directory = Path(tempfile.mkdtemp(prefix='browser-close-fixture-', dir='/tmp'))
    monkeypatch.setenv('TMPDIR', str(directory))
    monkeypatch.setattr(tempfile, 'tempdir', str(directory))
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
        assert not directory.exists()


def run_spec():
    return BrowserRunSpec(run_id='br_' + uuid.uuid4().hex, tenant_id='fixture-tenant',
                          user_id='fixture-user', session_id='fixture-session')


def command_fields(run_id, seq):
    return dict(run_id=run_id, seq=seq, command_id='bc_' + uuid.uuid4().hex,
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15))


def identities(pid):
    parent = psutil.Process(pid)
    return {p.pid: p.create_time() for p in [parent, *parent.children(recursive=True)]}


def same_alive(pid, birth):
    try:
        p = psutil.Process(pid)
        return p.create_time() == birth and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def stopped(owned):
    deadline = asyncio.get_running_loop().time() + 5
    while any(same_alive(pid, birth) for pid, birth in owned.items()):
        assert asyncio.get_running_loop().time() < deadline, 'Owned native descendant still alive'
        await asyncio.sleep(.05)


async def cleanup(owned):
    # PID + birth checks protect unrelated processes, including numeric reuse.
    for pid, birth in reversed(tuple(owned.items())):
        if same_alive(pid, birth):
            psutil.Process(pid).kill()
    await stopped(owned)


@asynccontextmanager
async def unrelated_process():
    p = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(60)',
                                            start_new_session=True)
    try:
        yield p
    finally:
        if p.returncode is None:
            p.kill()
        await p.wait()


@pytest.mark.asyncio
async def test_real_worker_close_ack_closes_original_resources_only(linux_browser_tmp):
    executor = LocalPlaywrightExecutor()
    executor.require_resource_close_proof()
    owned = {}
    async with unrelated_process() as unrelated:
        try:
            result = await executor.start(run_spec())
            assert result.status == ResultStatus.OK
            owned = identities(executor._process.pid)
            assert len(owned) >= 3
            closed = await executor.close('fixture_completed')
            assert closed.closed and not closed.forced
            assert executor.resource_close_confirmed and executor._process is None
            await stopped(owned)
            assert unrelated.returncode is None
            assert await executor.close('fixture_duplicate') == closed
        finally:
            await cleanup(owned)


@pytest.mark.asyncio
async def test_dead_leader_reaps_real_owned_descendants_but_retains_unknown_close(linux_browser_tmp):
    executor = LocalPlaywrightExecutor()
    executor.require_resource_close_proof()
    owned = {}
    async with unrelated_process() as unrelated:
        try:
            assert (await executor.start(run_spec())).status == ResultStatus.OK
            process = executor._process
            owned = identities(process.pid)
            assert len(owned) >= 3
            group = {pid: birth for pid, birth in owned.items()
                     if same_alive(pid, birth) and os.getpgid(pid) == executor._owned_pgid}
            assert len(group) >= 2
            # STOP only actual original-group children. Chromium may create a
            # separate group, which is outside this native group-signal proof.
            for pid, birth in group.items():
                if pid != process.pid and same_alive(pid, birth):
                    os.kill(pid, signal.SIGSTOP)
            process.kill()  # Only the original worker leader.
            deadline = asyncio.get_running_loop().time() + 3
            while process.returncode is None:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(.02)
            assert any(same_alive(pid, birth) for pid, birth in group.items() if pid != process.pid)
            with pytest.raises(BrowserOwnerFailure, match='BROWSER_CLOSE_VERIFICATION_REQUIRED'):
                await executor.close('fixture_dead_leader')
            await stopped(group)
            print({'original_group_members': len(group),
                   'other_descendants_still_live': sum(same_alive(pid, birth)
                       for pid, birth in owned.items() if pid not in group)})
            assert executor._process is process and not executor.resource_close_confirmed
            assert executor._close_result is None and unrelated.returncode is None
            with pytest.raises(BrowserOwnerFailure, match='BROWSER_CLOSE_VERIFICATION_REQUIRED'):
                await executor.close('fixture_duplicate_unknown')
            assert unrelated.returncode is None
        finally:
            await cleanup(owned)


def test_observed_empty_group_permanently_revokes_numeric_signal(monkeypatch):
    executor = LocalPlaywrightExecutor()
    executor._owned_pgid = 900001  # /proc DI only; never create or signal this group.
    observed = [{}]
    monkeypatch.setattr(executor, '_current_group_members', lambda: observed[-1])
    signals = []
    monkeypatch.setattr(os, 'killpg', lambda *args: signals.append(args))
    assert not executor._owned_group_alive()
    observed.append({900002: 101})  # Simulated later numeric group reuse.
    executor._signal_owned_group(signal.SIGKILL)
    assert not signals and executor._group_signal_revoked


def test_changed_birth_does_not_grant_group_signal(monkeypatch):
    executor = LocalPlaywrightExecutor()
    executor._owned_pgid = 900003
    executor._group_members = {900004: 100}
    monkeypatch.setattr(executor, '_current_group_members', lambda: {900004: 200})
    signals = []
    monkeypatch.setattr(os, 'killpg', lambda *args: signals.append(args))
    with pytest.raises(BrowserOwnerFailure, match='BROWSER_PROCESS_OWNER_VERIFICATION_REQUIRED'):
        executor._signal_owned_group(signal.SIGKILL)
    assert signals == []


@pytest.mark.asyncio
@pytest.mark.parametrize('all_resources_fail', [False, True], ids=['ancestor_closes', 'false_ack_retains_refs'])
async def test_real_worker_resource_close_failure_ack_and_ancestor_proof(linux_browser_tmp, monkeypatch,
                                                                       all_resources_fail):
    worker = BrowserWorker()
    run = run_spec()
    try:
        start = await worker.handle(StartCommand(**command_fields(run.run_id, 1), run=run))
        assert start['status'] == 'ok'
        page, context, browser, playwright = worker.page, worker.context, worker.browser, worker.playwright
        async def fail_native_close():
            raise OSError('fixture_native_close_failure')
        with monkeypatch.context() as faults:
            faults.setattr(page, 'close', fail_native_close)
            if all_resources_fail:
                for resource, method in ((context, 'close'), (browser, 'close'), (playwright, 'stop')):
                    faults.setattr(resource, method, fail_native_close)
            response = await worker.handle(CloseCommand(**command_fields(run.run_id, 2), reason='fixture'))
            if all_resources_fail:
                assert response['closed'] is False and response['status'] == 'error'
                assert response['error_code'] == 'CLOSE_UNCONFIRMED'
                assert worker.page is page and worker.context is context
                assert worker.browser is browser and worker.playwright is playwright
                assert browser.is_connected()
            else:
                assert response['closed'] is True and response['status'] == 'ok'
                assert page.is_closed() and not browser.is_connected()
                assert worker.page is worker.context is worker.browser is worker.playwright is None
    finally:
        await worker.close()
        assert worker.closed
