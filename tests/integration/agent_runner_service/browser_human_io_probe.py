"""Actual Local IO lock queue and original fully-written command ACK timing DI."""
import asyncio
from pathlib import Path
import sys
import time


def install_io_gates(entered, released, written, acknowledged):
    import src.tools.browser.executor.local as local
    from src.tools.browser.owner_port import current_human_action_guard

    original_request = local.LocalPlaywrightExecutor._request
    original_write = local.write_frame
    queued = False
    held_written = False

    async def wait_file(path):
        until = time.monotonic() + 12
        while not Path(path).exists():
            if time.monotonic() >= until:
                raise TimeoutError('Fixture original IO gate not released')
            await asyncio.sleep(.02)

    async def request(self, command, result_type, **kwargs):
        nonlocal queued
        holder = None
        if kwargs.get('builder') is not None and current_human_action_guard() is not None and not queued:
            queued = True
            locked = asyncio.Event()
            async def hold_original_lock():
                async with self._io_lock:
                    locked.set()
                    Path(entered).touch()
                    await wait_file(released)
            holder = asyncio.create_task(hold_original_lock())
            await locked.wait()
        try:
            # Original _request really waits on its actual same IO lock;
            # authorization, DTO construction and protocol result are intact.
            return await original_request(self, command, result_type, **kwargs)
        finally:
            if holder is not None:
                Path(released).touch()
                await holder

    async def write(writer, message, *args, **kwargs):
        nonlocal held_written
        await original_write(writer, message, *args, **kwargs)
        if message.get('type') == 'keyboard' and message.get('key') == 'F' and not held_written:
            held_written = True
            # Actual frame was written; real Browser receives and performs it.
            # Only delay returning to the original ACK reader.
            Path(written).touch()
            await wait_file(acknowledged)

    local.LocalPlaywrightExecutor._request = request
    local.write_frame = write


if __name__ == '__main__':
    report, entered, released, written, acknowledged, *arguments = sys.argv[1:]
    from tests.integration.agent_runner_service.browser_human_service_probe import install
    install(report)
    install_io_gates(entered, released, written, acknowledged)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
