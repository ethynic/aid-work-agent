"""Task-owned loopback page for observing real native pointer/keyboard IO.

This peer is only external page IO. It cannot manufacture Browser/Owner facts,
tool outcomes, completion predicates, authorization, or protocol ACKs.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid

import pytest


class HumanPagePeer:
    def __init__(self):
        self.path = '/human-fixture-' + uuid.uuid4().hex
        self.requests, self.events, self.unexpected = [], [], []
        self._lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                with peer._lock:
                    peer.requests.append(self.path)
                if self.path in {peer.path, peer.path + '/ready'}:
                    html = '''<!doctype html><html><head><title>Fictional human page</title></head>
<body><h1>Fictional human page</h1><input id="field" aria-label="Fictional field"
 style="position:fixed;left:40px;top:100px;width:200px;height:40px">
<button id="ready" style="position:fixed;left:320px;top:100px;width:240px;height:40px">
Mark fictional page ready</button><p id="status" aria-live="polite">Waiting</p>
<script>
const publish = (kind, value) => fetch(EVENT_PATH, {method:'POST',
 headers:{'Content-Type':'application/json'}, body:JSON.stringify({kind,value})});
const field = document.getElementById('field');
field.addEventListener('input', () => publish('input', field.value));
document.getElementById('ready').addEventListener('click', () => {
 document.getElementById('status').textContent = 'Fictional page ready';
 history.replaceState({}, '', READY_PATH); publish('ready', true);
});
</script></body></html>'''.replace('EVENT_PATH', json.dumps(peer.path + '/events')).replace(
                        'READY_PATH', json.dumps(peer.path + '/ready'))
                    payload = html.encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Security-Policy',
                        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'")
                elif self.path == '/favicon.ico':
                    payload = b''
                    self.send_response(204)
                else:
                    with peer._lock:
                        peer.unexpected.append(self.path)
                    payload = b''
                    self.send_response(404)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if self.path != peer.path + '/events' or not 0 < size <= 4096:
                    with peer._lock:
                        peer.unexpected.append(self.path)
                    self.send_error(400)
                    return
                try:
                    event = json.loads(self.rfile.read(size))
                    if (not isinstance(event, dict) or set(event) != {'kind', 'value'}
                            or event['kind'] not in {'input', 'ready'}
                            or (event['kind'] == 'input' and not isinstance(event['value'], str))
                            or (event['kind'] == 'ready' and event['value'] is not True)):
                        raise ValueError('FICTIONAL_PAGE_EVENT_INVALID')
                except (ValueError, UnicodeDecodeError):
                    self.send_error(400)
                    return
                with peer._lock:
                    peer.events.append(event)
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}{self.path}'

    def observed_events(self):
        with self._lock:
            return [dict(event) for event in self.events]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.fixture
def browser_human_page():
    peer = HumanPagePeer()
    try:
        yield peer
    finally:
        peer.close()
