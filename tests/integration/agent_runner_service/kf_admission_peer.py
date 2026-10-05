"""Prepared external KF current-state HTTP boundary, not an admission proof.

The original foundation provider handles original token/account/pull wire calls.
This loopback front adds only the actual get_service_state endpoint and refuses
all other new endpoints. No production module is imported during preparation.
"""
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


@dataclass
class StateReply:
    payload: dict = field(default_factory=lambda: {'errcode': 0, 'service_state': 1})
    release: threading.Event | None = None
    arrived: threading.Event = field(default_factory=threading.Event)
    status: int = 200


class KfAdmissionPeer:
    """Real localhost socket IO only; original SDK remains the test subject."""
    def __init__(self, foundation_peer, actor_id):
        self.foundation = foundation_peer
        self.actor_id = actor_id
        self.states, self.calls, self.errors = [], [], []
        self._lock = threading.Lock()
        self._next = 0
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if urlsplit(self.path).path != '/cgi-bin/gettoken':
                    return self.refuse()
                self.forward(None)

            def do_POST(self):
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2 * 1024 * 1024:
                        raise ValueError
                    raw = self.rfile.read(size)
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    return self.respond(400, {'errcode': -1})
                path = urlsplit(self.path).path
                if path in ('/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg'):
                    return self.forward(raw)
                if path != '/cgi-bin/kf/service_state/get':
                    return self.refuse()
                credential_valid = parse_qs(urlsplit(self.path).query).get('access_token') == [peer.foundation._access_token]
                scope_valid = body.get('open_kfid') == peer.foundation.open_kfid and body.get('external_userid') == peer.actor_id
                with peer._lock:
                    peer.calls.append({'path': path, 'credential_valid': credential_valid,
                        'account_actor_match': scope_valid, 'keys': sorted(body)})
                    if not credential_valid or not scope_valid or peer._next >= len(peer.states):
                        peer.errors.append('UNEXPECTED_CURRENT_STATE_REQUEST')
                        return self.respond(409, {'errcode': -1})
                    reply = peer.states[peer._next]
                    peer._next += 1
                reply.arrived.set()
                if reply.release is not None and not reply.release.wait(10):
                    return self.respond(504, {'errcode': -1})
                self.respond(reply.status, reply.payload)

            def forward(self, raw):
                # Only fictional local endpoints; response is real original
                # foundation HTTP bytes, not substituted SDK method results.
                request = Request(peer.foundation.base_url + self.path, data=raw,
                    headers={'Content-Type': 'application/json'} if raw is not None else {},
                    method='POST' if raw is not None else 'GET')
                try:
                    response = urlopen(request, timeout=12)
                except HTTPError as response:
                    self.raw_response(response.code, response.read())
                    response.close()
                except (URLError, TimeoutError, OSError):
                    with peer._lock:
                        peer.errors.append('FOUNDATION_HTTP_UNAVAILABLE')
                    self.respond(502, {'errcode': -1})
                else:
                    with response:
                        self.raw_response(response.status, response.read())

            def refuse(self):
                with peer._lock:
                    peer.errors.append('UNEXPECTED_PLATFORM_WRITE_OR_ENDPOINT')
                self.respond(409, {'errcode': -1})

            def respond(self, status, payload):
                self.raw_response(status, json.dumps(payload).encode())

            def raw_response(self, status, data):
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self):
        return 'http://127.0.0.1:' + str(self._server.server_port)

    def close(self):
        with self._lock:
            for reply in self.states:
                if reply.release is not None:
                    reply.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        assert not self._thread.is_alive()
