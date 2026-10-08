"""Prepared real HTTP SSE reader; no server/repository/auth is substituted.

This module is only transport parsing. Payloads and request credentials are
never repr'd; tests must use the frozen server contract for their assertions.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import queue
import threading
import time

import httpx

SAFE_ERROR_CODES = frozenset({'RUNNER_STORAGE_UNAVAILABLE', 'RUNNER_SERVICE_UNAVAILABLE',
    'RUNNER_SERVICE_REDIRECT_FORBIDDEN', 'USER_UNAUTHORIZED', 'TENANT_FORBIDDEN',
    'SESSION_NOT_FOUND', 'RUNNER_NOT_FOUND', 'INVALID_REQUEST', 'INVALID_EVENT_CURSOR',
    'RUNNER_EVENT_OBSERVER_LIMIT', 'AGENT_RUNNER_DISABLED'})


@dataclass(frozen=True)
class EventFrame:
    event: str | None
    identifier: str | None
    payload: dict | None = field(repr=False)
    comment: bool = False


def frames(response):
    """Read actual wire fields without normalizing IDs or server payloads."""
    event, identifier, data = None, None, []
    size = 0
    for line in response.iter_lines():
        if not line:
            if data:
                payload = json.loads('\n'.join(data))
                assert isinstance(payload, dict), 'SSE data must be a JSON object'
                yield EventFrame(event, identifier, payload)
            event, identifier, data, size = None, None, [], 0
            continue
        size += len(line.encode('utf-8'))
        assert size <= 65536, 'SSE frame exceeded finite fixture reader bound'
        if line.startswith(':'):
            yield EventFrame(None, None, None, comment=True)
            continue
        name, separator, value = line.partition(':')
        if separator and value.startswith(' '):
            value = value[1:]
        if name == 'event':
            event = value
        elif name == 'id':
            identifier = value
        elif name == 'data':
            data.append(value)


@contextmanager
def actual_stream(url, *, headers, params=None, read_timeout=20):
    """Close the exact caller-owned real socket even when an oracle fails."""
    with httpx.Client(timeout=httpx.Timeout(read_timeout, connect=5)) as client:
        with client.stream('GET', url, headers=headers, params=params) as response:
            yield response


class StreamObserver:
    """One bounded real HTTP reader thread owned by the test's context."""
    def __init__(self, url, *, headers, params=None):
        self._url, self._headers, self._params = url, headers, params
        self._frames = queue.Queue(maxsize=128)
        self._ready, self.finished, self._stopping = (threading.Event() for _ in range(3))
        self.status, self.content_type, self.error_class = None, None, None
        self.error_code = None
        self.ready_at = self.finished_at = None
        self.close_started_at = self.close_finished_at = None
        self._response = None
        self._thread = threading.Thread(target=self._read, daemon=False)

    def _read(self):
        try:
            with actual_stream(self._url, headers=self._headers, params=self._params) as response:
                self._response = response
                self.status = response.status_code
                self.content_type = response.headers.get('content-type', '')
                if response.status_code == 200:
                    self.ready_at = time.monotonic()
                    self._ready.set()
                    for frame in frames(response):
                        if self._stopping.is_set():
                            break
                        self._frames.put(frame, timeout=1)
                else:
                    # Only the original stable public code is retained. Never
                    # print or persist the response body or echoed inputs.
                    response.read()
                    try:
                        value = response.json()
                    except ValueError:
                        value = {}
                    if isinstance(value, dict):
                        for candidate in (value.get('error'), value.get('code'),
                                          value.get('detail', {}).get('error_code')
                                          if isinstance(value.get('detail'), dict) else None):
                            if isinstance(candidate, str) and candidate in SAFE_ERROR_CODES:
                                self.error_code = candidate
                                break
                    self.ready_at = time.monotonic()
                    self._ready.set()
        except Exception as error:
            if not self._stopping.is_set():
                self.error_class = type(error).__name__
        finally:
            self.finished_at = time.monotonic()
            self._ready.set()
            self.finished.set()

    def __enter__(self):
        self._thread.start()
        try:
            assert self._ready.wait(10), 'Actual HTTP stream handshake did not finish'
            assert self.error_class is None, self.error_class
            return self
        except BaseException:
            self.close()
            raise

    def next(self, *, timeout=10, comments=False):
        deadline = time.monotonic() + timeout
        while True:
            try:
                frame = self._frames.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                raise AssertionError('Expected actual SSE frame was not observed') from None
            if comments or not frame.comment:
                return frame
            assert time.monotonic() < deadline, 'Only heartbeat frames were observed'

    def closed(self, timeout=8):
        assert self.finished.wait(timeout), 'Actual stream did not close within finite bound'
        assert self.error_class is None, self.error_class

    def close(self):
        self._stopping.set()
        began = time.monotonic()
        if self.close_started_at is None:
            self.close_started_at = began
        try:
            if self._response is not None:
                self._response.close()
        finally:
            # The socket read budget is20s; keep a finite matching total close
            # budget and join the actual thread even if response.close raises.
            self._thread.join(timeout=max(.01, 25 - (time.monotonic() - began)))
            if self.close_finished_at is None:
                self.close_finished_at = time.monotonic()
            assert not self._thread.is_alive(), 'Owned HTTP reader thread remained alive'
            assert time.monotonic() - began < 25, 'Owned HTTP close exceeded finite25s budget'

    def __exit__(self, *_):
        self.close()
