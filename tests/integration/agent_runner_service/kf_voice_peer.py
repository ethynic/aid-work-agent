"""Prepared fictional media/ASR socket boundary; no production imports.

The Speech tool's token getter may supply this fixture's private token under an
explicit external-token IO DI. Original Speech POST/parsing and receipts remain
real test subjects. No provider response body or credentials are logged.
"""
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import secrets
import socket
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import wave


@dataclass(repr=False)
class AsrReply:
    payload: dict = field(default_factory=lambda: {'status': 20000000, 'result': 'Fictional recognized speech'})
    status: int = 200
    raw_body: bytes | None = field(default=None, repr=False)
    release: threading.Event | None = field(default=None, repr=False)
    arrived: threading.Event = field(default_factory=threading.Event, repr=False)
    lose_response: bool = False


def wav_bytes(samples=1600):
    target = io.BytesIO()
    with wave.open(target, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\x00\x00' * samples)
    return target.getvalue()


class KfVoicePeer:
    """Original SDK requests forward; only media download and ASR are added."""
    def __init__(self, state_peer):
        self.upstream = state_peer
        self._asr_token = secrets.token_urlsafe(24)
        self._appkey = secrets.token_urlsafe(24)
        self.media, self.replies = {}, []
        self.calls, self.errors = [], []
        self._next = 0
        self._lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == '/cgi-bin/media/get':
                    query = parse_qs(parsed.query)
                    valid = query.get('access_token') == [peer.upstream.foundation._access_token]
                    identifiers = query.get('media_id', [])
                    value = peer.media.get(identifiers[0]) if len(identifiers) == 1 else None
                    with peer._lock:
                        peer.calls.append({'kind': 'media', 'credential_valid': valid, 'known_media': value is not None})
                    if not valid or value is None:
                        return self.respond(409, b'{"errcode":-1}', 'application/json')
                    data, content_type = value
                    return self.respond(200, data, content_type)
                if parsed.path == '/cgi-bin/gettoken':
                    return self.forward(None)
                return self.refuse()

            def do_POST(self):
                parsed = urlsplit(self.path)
                if parsed.path == '/stream/v1/asr':
                    return self.asr(parsed)
                if parsed.path not in ('/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg', '/cgi-bin/kf/service_state/get'):
                    return self.refuse()
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2 * 1024 * 1024:
                        raise ValueError
                except ValueError:
                    return self.respond(400, b'{"errcode":-1}', 'application/json')
                return self.forward(self.rfile.read(size))

            def asr(self, parsed):
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 10 * 1024 * 1024:
                        raise ValueError
                except ValueError:
                    return self.respond(400, b'{}', 'application/json')
                audio = self.rfile.read(size)
                query = parse_qs(parsed.query)
                valid = self.headers.get('X-NLS-Token') == peer._asr_token and query.get('appkey') == [peer._appkey]
                with peer._lock:
                    peer.calls.append({'kind': 'asr', 'credential_valid': valid,
                        'bytes': len(audio), 'sha256': hashlib.sha256(audio).hexdigest(),
                        'format': query.get('format'), 'sample_rate': query.get('sample_rate')})
                    if not valid or peer._next >= len(peer.replies):
                        peer.errors.append('UNEXPECTED_ASR_POST')
                        return self.respond(409, b'{}', 'application/json')
                    reply = peer.replies[peer._next]
                    peer._next += 1
                reply.arrived.set()
                if reply.release is not None and not reply.release.wait(40):
                    return self.respond(504, b'{}', 'application/json')
                if reply.lose_response:
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                body = reply.raw_body if reply.raw_body is not None else json.dumps(reply.payload).encode()
                self.respond(reply.status, body, 'application/json')

            def forward(self, raw):
                request = Request(peer.upstream.base_url + self.path, data=raw,
                    headers={'Content-Type': 'application/json'} if raw is not None else {},
                    method='POST' if raw is not None else 'GET')
                try:
                    response = urlopen(request, timeout=15)
                except HTTPError as response:
                    with response:
                        self.respond(response.code, response.read(), 'application/json')
                except (URLError, TimeoutError, OSError):
                    with peer._lock:
                        peer.errors.append('UPSTREAM_FIXTURE_UNAVAILABLE')
                    self.respond(502, b'{"errcode":-1}', 'application/json')
                else:
                    with response:
                        self.respond(response.status, response.read(), 'application/json')

            def refuse(self):
                with peer._lock:
                    peer.errors.append('UNEXPECTED_ENDPOINT_OR_PLATFORM_WRITE')
                self.respond(409, b'{"errcode":-1}', 'application/json')

            def respond(self, status, data, content_type):
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', content_type)
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
        for reply in self.replies:
            if reply.release is not None:
                reply.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        assert not self._thread.is_alive()
