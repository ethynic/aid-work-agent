"""External WS transport scheduling/redirect faults, original Worker retained."""
import asyncio
from pathlib import Path
import runpy
import sys

mode, report_name, release_name, target, *arguments = sys.argv[1:]
report, release = Path(report_name), Path(release_name)
from src.services.agent_runner.browser_sidecar import BrowserViewSidecar
from fastapi import WebSocket
original_observe = BrowserViewSidecar.observe


async def delayed_or_redirected(self, websocket: WebSocket):
    report.touch()
    if mode == 'redirect':
        from starlette.responses import Response
        await websocket.send_denial_response(Response(status_code=302, headers={'Location': target}))
        return
    while not release.exists():
        await asyncio.sleep(.01)
    return await original_observe(self, websocket)


if mode in {'redirect', 'slow_handshake'}:
    BrowserViewSidecar.observe = delayed_or_redirected
elif mode == 'slow_send':
    from starlette.websockets import WebSocket
    original_bytes = WebSocket.send_bytes

    async def held_bytes(self, data):
        if self.url.path == '/internal/runner-browser/view':
            report.touch()
            while not release.exists():
                await asyncio.sleep(.01)
        return await original_bytes(self, data)

    WebSocket.send_bytes = held_bytes
else:
    raise ValueError('Unknown fixture transport mode')
sys.argv = ['worker', *arguments]
runpy.run_module('src.services.agent_runner.worker', run_name='__main__')
