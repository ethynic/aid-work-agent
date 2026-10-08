"""Fictional external KF wire, preserving original SDK HTTP/write observation.

This helper imports no production modules at load. It forwards original pull,
token and account reads to the existing fixture and scripts only real HTTP
business endpoints. Lost response closes the accepted socket after consuming
one request, rather than mocking a SDK return or durable send fact.
"""
from dataclasses import dataclass, field
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


@dataclass(repr=False)
class CompletionWireReply:
    payload: dict = field(default_factory=lambda: {'errcode': 0}, repr=False)
    status: int = 200
    raw_body: bytes | None = field(default=None, repr=False)
    drop_response: bool = False
    release: threading.Event | None = field(default=None, repr=False)
    arrived: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: threading.Event = field(default_factory=threading.Event, repr=False)


class KfCompletionPeer:
    """Only external IO changes; calls contain safe digests and scope booleans."""
    READ_FORWARD = frozenset(('/cgi-bin/gettoken', '/cgi-bin/kf/account/list',
        '/cgi-bin/kf/sync_msg'))
    SCRIPT_ENDPOINTS = frozenset(('/cgi-bin/kf/service_state/get',
        '/cgi-bin/kf/service_state/trans', '/cgi-bin/kf/send_msg',
        '/cgi-bin/kf/send_msg_on_event', '/cgi-bin/kf/customer/batchget'))

    def __init__(self, foundation, actor_id, *, welcome_code=None):
        self.foundation, self.actor_id = foundation, actor_id
        self._actors = {actor_id}
        self._welcome_code = welcome_code
        self.calls, self.errors = [], []
        self._scripts, self._next = {}, {}
        self._lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/cgi-bin/media/get':
                    # 原行为的素材读取（context 媒体展示/语音素材）是只读 GET；
                    # 套件默认返回确定性「素材不可用」，调用方按原有界降级处理，
                    # 不计入平台错误。
                    return self.respond(200, b'{"errcode":40007,"errmsg":"invalid media id"}')
                if path not in peer.READ_FORWARD:
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
                    return self.respond(400, b'{"errcode":-1}')
                path = urlsplit(self.path).path
                if path in peer.READ_FORWARD:
                    return self.forward(raw)
                if path not in peer.SCRIPT_ENDPOINTS:
                    return self.refuse()
                credential = parse_qs(urlsplit(self.path).query).get('access_token') == [peer.foundation._access_token]
                account = body.get('open_kfid') == peer.foundation.open_kfid
                target_actor = body.get('external_userid', body.get('touser'))
                actor = isinstance(target_actor, str) and target_actor in peer._actors
                if path == '/cgi-bin/kf/customer/batchget':
                    targets = body.get('external_userid_list')
                    scope = (isinstance(targets, list) and len(targets) == 1
                        and isinstance(targets[0], str) and targets[0] in peer._actors)
                elif path == '/cgi-bin/kf/send_msg_on_event':
                    scope = peer._welcome_code is not None and body.get('code') == peer._welcome_code
                else:
                    scope = account and actor
                canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                observation = {'path': path, 'keys': sorted(body), 'credential_valid': credential,
                    'scope_valid': scope, 'request_sha256': hashlib.sha256(canonical).hexdigest(),
                    'msgtype': body.get('msgtype'), 'requested_state': body.get('service_state')}
                with peer._lock:
                    peer.calls.append(observation)
                    index = peer._next.get(path, 0)
                    script = peer._scripts.get(path, [])
                    if not credential or not scope or index >= len(script):
                        peer.errors.append('UNSCRIPTED_OR_WRONG_SCOPE_COMPLETION_REQUEST:' + path
                            + (':NO_SCRIPT' if index >= len(script) else ':SCOPE'))
                        return self.respond(409, b'{"errcode":-1}')
                    reply = script[index]
                    peer._next[path] = index + 1
                reply.arrived.set()
                try:
                    if reply.release is not None and not reply.release.wait(20):
                        return self.respond(504, b'{"errcode":-1}')
                    if reply.drop_response:
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        self.connection.close()
                        return
                    payload = reply.raw_body if reply.raw_body is not None else json.dumps(copy.deepcopy(reply.payload)).encode()
                    self.respond(reply.status, payload)
                finally:
                    reply.finished.set()

            def forward(self, raw):
                request = Request(peer.foundation.base_url + self.path, data=raw,
                    headers={'Content-Type': 'application/json'} if raw is not None else {},
                    method='POST' if raw is not None else 'GET')
                try:
                    with urlopen(request, timeout=12) as response:
                        self.respond(response.status, response.read())
                except HTTPError as response:
                    self.respond(response.code, response.read())
                    response.close()
                except (URLError, TimeoutError, OSError):
                    with peer._lock:
                        peer.errors.append('FOUNDATION_HTTP_UNAVAILABLE')
                    self.respond(502, b'{"errcode":-1}')

            def refuse(self):
                with peer._lock:
                    peer.errors.append('UNEXPECTED_COMPLETION_ENDPOINT:' + urlsplit(self.path).path)
                self.respond(409, b'{"errcode":-1}')

            def respond(self, status, raw):
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self):
        return 'http://127.0.0.1:' + str(self._server.server_port)

    def script(self, path, *replies):
        assert path in self.SCRIPT_ENDPOINTS
        with self._lock:
            self._scripts.setdefault(path, []).extend(replies)

    def count(self, path):
        with self._lock:
            return sum(call['path'] == path for call in self.calls)

    def allow_customer_actor(self, actor_id):
        """An explicit second fictional HTTP target; never a product grant."""
        assert isinstance(actor_id, str) and 0 < len(actor_id) <= 128
        with self._lock:
            assert len(self._actors | {actor_id}) <= 2
            self._actors.add(actor_id)

    def close(self):
        with self._lock:
            for script in self._scripts.values():
                for reply in script:
                    if reply.release is not None:
                        reply.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(2)
        assert not self._thread.is_alive(), 'OWN_COMPLETION_WIRE_THREAD_NOT_CLOSED'
