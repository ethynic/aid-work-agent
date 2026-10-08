"""Prepared real HTTP wire/proxy boundaries with a controlled upstream server.

This HTTP server is an external protocol IO fixture, NOT a Runner/auth/Owner.
Web tenant middleware is real PG-backed. Actor proof is never accepted by a fake
Authorizer. Request paths/headers are counted only; credentials aren't recorded.
"""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
import time

import pytest

from .event_stream import StreamObserver
from .test_api import require_status
from .web_gateway import WebGateway

pytestmark = pytest.mark.integration


class WirePeer:
    def __init__(self):
        self.calls = Counter()
        self.release = threading.Event()
        self.first_chunk = threading.Event()
        self.peer = 'event-wire-' + secrets.token_hex(4)
        self._token = secrets.token_urlsafe(24)
        self.header_checks = []
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def do_GET(self):
                name = 'redirect' if '/wire-redirect/' in self.path else 'headers' if '/wire-headers/' in self.path else 'target' if self.path == '/unwanted-target' else 'small'
                peer.calls[name] += 1
                peer.header_checks.append((self.headers.get('X-AgentRunner-Service') == peer.peer,
                    self.headers.get('X-AgentRunner-Service-Token') == peer._token,
                    self.headers.get('X-AgentRunner-Channel-User') is None))
                try:
                    if name == 'redirect':
                        self.send_response(302)
                        self.send_header('Location', '/unwanted-target')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    if name == 'headers':
                        peer.release.wait(8)
                        self.send_response(503)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    # Sub-65536 HTTP chunk must be yielded immediately. A
                    # client chunk_size aggregator would wait for our release.
                    chunk = b'id: 1\nevent: wire-fixture\ndata: {"protocol_fixture":true}\n\n'
                    self.wfile.write(f'{len(chunk):X}\r\n'.encode() + chunk + b'\r\n')
                    self.wfile.flush()
                    peer.first_chunk.set()
                    peer.release.wait(8)
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = False
        self.server.block_on_close = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=False)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive()


def test_real_web_wire_single_small_chunk_redirect_and_bounded_headers_preserve_tenant_gate(
        actors, service_processes):
    peer = WirePeer()
    gateway = None
    try:
        gateway = WebGateway(service_processes, environment={
            'AGENT_RUNNER_WEB_ENABLED': 'true', 'AGENT_RUNNER_API_URL': peer.url,
            'AGENT_RUNNER_WEB_SERVICE_ID': peer.peer,
            'AGENT_RUNNER_WEB_SERVICE_TOKEN': peer._token},
            factory='src.api.agent_runner_web:create_app', access_log=False)
        actor = actors['a']
        path = '/api/chat/runners/wire-small/events'
        for selected in (actors['b'].tenant_id, 'event-nonexistent-tenant'):
            require_status(gateway.call('GET', path, headers={**gateway.headers(actor), 'X-Tenant-Id': selected}), 403)
        assert not peer.calls  # No upstream IO before real middleware rejection.
        with StreamObserver(gateway.url + path, headers={**gateway.headers(actor),
                'X-AgentRunner-Service': 'incoming-spoof', 'X-AgentRunner-Channel-User': 'incoming-spoof'}) as observer:
            assert observer.status == 200
            frame = observer.next(timeout=3)
            assert peer.first_chunk.is_set() and not peer.release.is_set()
            assert frame.event == 'wire-fixture' and frame.identifier == '1'
            assert frame.payload == {'protocol_fixture': True}
            assert not observer.finished.is_set()
            peer.release.set()
            observer.closed()
        assert peer.calls['small'] == 1 and all(all(check) for check in peer.header_checks)
        redirect = require_status(gateway.call('GET', '/api/chat/runners/wire-redirect/events', actor=actor), 502)
        assert redirect['code'] == 'RUNNER_SERVICE_REDIRECT_FORBIDDEN'
        assert peer.calls['redirect'] == 1 and peer.calls['target'] == 0
        peer.release.clear()
        started = time.monotonic()
        failure = require_status(gateway.call('GET', '/api/chat/runners/wire-headers/events', actor=actor), 503)
        assert failure['code'] == 'RUNNER_SERVICE_UNAVAILABLE'
        assert 4 <= time.monotonic() - started < 8
        assert peer.calls['headers'] == 1
    finally:
        peer.release.set()
        if gateway is not None:
            gateway.close()
        peer.close()
