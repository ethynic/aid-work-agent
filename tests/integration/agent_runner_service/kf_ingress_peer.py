"""Prepared loopback WeCom KF provider IO only, with real protocol bytes.

No database/owner/authorization imports. All platform credentials are fictional
and kept private; captured observations expose path classes and safe flags only.
Original API client, crypto, ingress repository and worker remain test subjects.
"""
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import copy
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree


@dataclass(repr=False)
class CallbackMaterial:
    corp_id: str
    token: str = field(repr=False)
    encoding_aes_key: str = field(repr=False)
    pull_token: str = field(repr=False)

    @classmethod
    def fresh(cls, corp_id):
        return cls(corp_id, secrets.token_urlsafe(24),
            base64.b64encode(secrets.token_bytes(32)).decode().rstrip('='), secrets.token_urlsafe(24))

    def encrypted_callback(self, open_kfid, *, event='kf_msg_or_event', receiver=None, fields=None, timestamp=None):
        # Lazy original import only at an approved actual test window.
        from src.channels.wecom.crypto import WeComCrypto
        root = ElementTree.Element('xml')
        for tag, value in [('ToUserName', self.corp_id), ('CreateTime', str(int(time.time()))),
                ('MsgType', 'event'), ('Event', event), ('Token', self.pull_token), ('OpenKfId', open_kfid)]:
            ElementTree.SubElement(root, tag).text = value
        for tag, value in (fields or {}).items():
            if root.find(tag) is not None:
                raise ValueError('DUPLICATE_CALLBACK_FIXTURE_FIELD')
            ElementTree.SubElement(root, tag).text = str(value)
        crypto = WeComCrypto(self.token, self.encoding_aes_key, receiver or self.corp_id)
        plaintext = ElementTree.tostring(root, encoding='unicode')
        encrypted = crypto.encrypt(plaintext)
        timestamp, nonce = str(int(time.time()) if timestamp is None else timestamp), secrets.token_hex(8)
        signature = crypto.generate_signature(timestamp, nonce, encrypted)
        envelope = ElementTree.Element('xml')
        ElementTree.SubElement(envelope, 'ToUserName').text = self.corp_id
        ElementTree.SubElement(envelope, 'Encrypt').text = encrypted
        return {'msg_signature': signature, 'timestamp': timestamp, 'nonce': nonce}, ElementTree.tostring(envelope)


@dataclass
class PullPage:
    payload: dict
    release: threading.Event | None = None
    arrived: threading.Event = field(default_factory=threading.Event)
    status: int = 200


class KfProviderPeer:
    """Only access-token/account-list/sync_msg HTTP; every other method is refused."""
    def __init__(self, *, corp_id, open_kfid):
        self.corp_id, self.open_kfid = corp_id, open_kfid
        self._secret, self._access_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        self.pages, self.calls, self.errors = [], [], []
        self.account_response = {'errcode': 0, 'account_list': [{'open_kfid': open_kfid, 'name': 'Fictional account'}]}
        self._lock = threading.Lock()
        self._next = 0
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                allowed = parsed.path == '/cgi-bin/gettoken' and query.get('corpid') == [peer.corp_id] \
                    and query.get('corpsecret') == [peer._secret]
                with peer._lock:
                    peer.calls.append({'path': parsed.path, 'method': 'GET', 'credential_valid': allowed})
                if not allowed:
                    return self.respond(409, {'errcode': -1})
                self.respond(200, {'errcode': 0, 'access_token': peer._access_token, 'expires_in': 7200})

            def do_POST(self):
                parsed = urlsplit(self.path)
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2 * 1024 * 1024:
                        raise ValueError
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    return self.respond(400, {'errcode': -1})
                token_valid = parse_qs(parsed.query).get('access_token') == [peer._access_token]
                with peer._lock:
                    peer.calls.append({'path': parsed.path, 'method': 'POST', 'credential_valid': token_valid,
                        'keys': sorted(body), 'cursor': body.get('cursor'),
                        'account_matches': body.get('open_kfid') == peer.open_kfid})
                if not token_valid:
                    return self.respond(409, {'errcode': -1})
                if parsed.path == '/cgi-bin/kf/account/list':
                    return self.respond(200, copy.deepcopy(peer.account_response))
                if parsed.path != '/cgi-bin/kf/sync_msg':
                    with peer._lock:
                        peer.errors.append('UNEXPECTED_PROVIDER_WRITE_OR_ENDPOINT')
                    return self.respond(409, {'errcode': -1})
                with peer._lock:
                    if peer._next >= len(peer.pages):
                        peer.errors.append('UNEXPECTED_PULL_WITHOUT_SCRIPT')
                        return self.respond(409, {'errcode': -1})
                    page = peer.pages[peer._next]
                    peer._next += 1
                page.arrived.set()
                if page.release is not None and not page.release.wait(10):
                    return self.respond(504, {'errcode': -1})
                self.respond(page.status, copy.deepcopy(page.payload))

            def respond(self, status, payload):
                data = json.dumps(payload).encode()
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

    @property
    def secret(self):
        return self._secret

    def script(self, *pages):
        self.pages.extend(pages)

    def close(self):
        for page in self.pages:
            if page.release is not None:
                page.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(2)
        assert not self._thread.is_alive(), 'OWNED_PROVIDER_THREAD_NOT_CLOSED'
