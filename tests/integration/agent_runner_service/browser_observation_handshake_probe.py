"""Timing-only fault: hold original sidecar auth across observation stop.

The real Worker/sidecar/auth/Hub are unchanged. The stop calls the original
sidecar method; the authorization calls its original method after release.
"""
import asyncio
from pathlib import Path
import runpy
import sys

stop, stopped, entered, release = map(Path, sys.argv[1:5])
arguments = sys.argv[5:]
from src.services.agent_runner.browser_sidecar import BrowserViewSidecar
original_open = BrowserViewSidecar.open
original_auth = BrowserViewSidecar._authorize


async def authorize(self, assertion):
    if not entered.exists():
        entered.touch()
        while not release.exists():
            await asyncio.sleep(.01)
    return await original_auth(self, assertion)


async def opened(self):
    await original_open(self)

    async def stop_original():
        while not stop.exists():
            await asyncio.sleep(.01)
        await self.stop_observations()
        stopped.touch()

    self._fixture_stop_task = asyncio.create_task(stop_original())

BrowserViewSidecar._authorize = authorize
BrowserViewSidecar.open = opened
sys.argv = ['worker', *arguments]
runpy.run_module('src.services.agent_runner.worker', run_name='__main__')
