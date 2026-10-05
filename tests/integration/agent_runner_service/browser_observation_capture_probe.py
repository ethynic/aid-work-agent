"""Fresh ordinary worker with only original frame capture scheduling gated.

This creates a real no-frame subscription window. It does not return a fake
frame or alter auth/lease/Hub/sidecar/core operations. Once the task-owned gate
opens, the original Local capture performs its genuine IPC/JPEG publication.
"""
import asyncio
import json
from pathlib import Path
import runpy
import sys


def install(gate, report):
    from src.tools.browser.executor.local import LocalPlaywrightExecutor
    release, destination = Path(gate), Path(report)
    original = LocalPlaywrightExecutor._capture_frame

    async def held_capture(self):
        if not release.exists():
            pending = destination.with_suffix('.pending')
            pending.write_text(json.dumps({'original_capture_waiting': True}))
            pending.replace(destination)
        while not release.exists():
            await asyncio.sleep(.05)
        return await original(self)

    LocalPlaywrightExecutor._capture_frame = held_capture


if __name__ == '__main__':
    gate, report, *arguments = sys.argv[1:]
    install(gate, report)
    sys.argv = ['src.services.agent_runner.worker', *arguments]
    runpy.run_module('src.services.agent_runner.worker', run_name='__main__')
