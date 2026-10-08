"""Owned real Web gateway process; only the public opaque token goes to it.

Configuration is supplied by the caller after the M3 bootstrap contract freezes.
This helper does not pre-initialize pools, replace auth, or provide a fallback
Agent implementation if the independent service is unavailable.
"""
import re
import socket

import httpx

from .conftest import wait_for


class WebGateway:
    def __init__(self, processes, *, environment, factory, access_log=True):
        self.processes = processes
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.port = reserve.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        arguments = ["-m", "uvicorn", factory, "--factory", "--host", "127.0.0.1", "--port", str(self.port)]
        if not access_log:
            arguments.append('--no-access-log')
        self.child = processes.start(
            arguments,
            environment=environment, private_working_directory=True)
        try:
            wait_for(self._ready, timeout=15)
        except BaseException:
            self.close()
            raise

    def _ready(self):
        if self.child.poll() is not None:
            log = (self.processes.root / f"child-{self.processes.children.index(self.child)}.log").read_text()
            locations = re.findall(r'File "([^"]+)", line (\d+)', log)
            classes = re.findall(r'^([A-Za-z_.]+(?:Error|Exception|Failure)):', log, re.MULTILINE)
            raise AssertionError(f"Web gateway exited {self.child.returncode}; classes={classes}; locations={locations[-8:]}")
        try:
            # Uvicorn only accepts requests after its actual lifespan completes.
            return httpx.get(self.url + "/openapi.json", timeout=.5).status_code == 200
        except httpx.TransportError:
            return False

    @staticmethod
    def headers(actor):
        return {"Authorization": "Bearer " + actor.token} if actor else {}

    def call(self, method, path, *, actor=None, headers=None, **kwargs):
        return httpx.request(method, self.url + path,
                             headers=self.headers(actor) if headers is None else headers,
                             timeout=10, **kwargs)

    def close(self):
        self.processes.stop(self.child)
