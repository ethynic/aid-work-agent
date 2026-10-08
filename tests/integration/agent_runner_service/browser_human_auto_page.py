"""Fictional CAPTCHA-labelled local DOM; only a real pointer removes the marker."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid

import pytest


class AutoHumanPage:
    def __init__(self):
        self.path = '/auto-human-fixture-' + uuid.uuid4().hex
        self.requests, self.events, self.unexpected = [], [], []
        self._lock = threading.Lock()
        peer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                with peer._lock:
                    peer.requests.append(self.path)
                if self.path == peer.path:
                    text = '''<!doctype html><html><head><title>Fictional local challenge</title></head>
<body><h1>Fictional local challenge</h1><button id="finish" aria-label="captcha"
style="position:fixed;left:320px;top:100px;width:240px;height:40px">captcha</button>
<script>document.getElementById('finish').addEventListener('click',()=>{
const button=document.getElementById('finish');button.textContent='Finished';
button.setAttribute('aria-label','Finished');fetch(EVENT_PATH,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'ready',value:true})});
});</script></body></html>'''.replace('EVENT_PATH', json.dumps(peer.path + '/events'))
                    payload, status = text.encode(), 200
                elif self.path == '/favicon.ico':
                    payload, status = b'', 204
                else:
                    with peer._lock:
                        peer.unexpected.append(self.path)
                    payload, status = b'', 404
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'")
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_POST(self):
                size = int(self.headers.get('Content-Length', '0'))
                if self.path != peer.path + '/events' or not 0 < size <= 4096:
                    self.send_error(400)
                    return
                value = json.loads(self.rfile.read(size))
                if value != {'kind': 'ready', 'value': True}:
                    self.send_error(400)
                    return
                with peer._lock:
                    peer.events.append(value)
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
            return [dict(item) for item in self.events]
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.fixture
def browser_human_auto_page():
    page = AutoHumanPage()
    try:
        yield page
    finally:
        page.close()
