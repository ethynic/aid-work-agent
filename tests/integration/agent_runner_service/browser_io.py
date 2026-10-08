"""Own loopback page and Redis namespace for real Browser producer tests.

The Redis server is the independently tracked, task-owned temporary helper.
This fixture never creates containers, changes application configuration, or
uses the shared application's Redis. Browser/model/core results are untouched.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import uuid

import pytest
import redis


class BrowserPagePeer:
    def __init__(self):
        self.path = '/fixture-' + uuid.uuid4().hex
        self.requests = []
        self.unexpected = []
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                peer.requests.append(self.path)
                if self.path == peer.path:
                    payload = (b'<!doctype html><html><head><title>Browser fixture ready</title>'
                               b'</head><body><h1>Fictional browser fixture</h1>'
                               b'<p>This page has no external resources.</p>'
                               b'<input aria-label="Fixture field"><button>Fixture button</button>'
                               b'</body></html>')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'none'")
                elif self.path == '/favicon.ico':
                    payload = b''
                    self.send_response(204)
                else:
                    peer.unexpected.append(self.path)
                    payload = b''
                    self.send_response(404)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}{self.path}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.fixture
def browser_page():
    page = BrowserPagePeer()
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def browser_redis(service_processes):
    # Chromium's original shared-memory path fails on the inherited host-backed
    # temporary mount. Keep its original flags and give only Browser children a
    # private Linux temporary parent; files stay mode0700 and cleanup waits for
    # all fixture-owned subprocesses, rather than changing container settings.
    import tempfile
    temporary = Path(tempfile.mkdtemp(prefix='runner-browser-temp-', dir='/tmp'))
    assert temporary.stat().st_mode & 0o777 == 0o700
    previous = getattr(service_processes, 'browser_temporary_directory', None)
    service_processes.browser_temporary_directory = temporary
    service_processes.additional_owned_directories.append(temporary)
    # Match the task-owned helper ledger, not master settings/default port6379.
    port = int(os.environ.get('AID_TEST_BROWSER_REDIS_PORT', '44635'))
    assert port != 6379
    client = redis.Redis(host='127.0.0.1', port=port, db=0, decode_responses=True,
                         socket_connect_timeout=2, socket_timeout=2)
    assert client.ping(), 'Task-owned real Redis helper is unavailable'
    assert client.config_get('appendonly')['appendonly'] == 'no'
    assert client.config_get('save')['save'] == ''
    prefix = 'runner-browser-fixture-' + uuid.uuid4().hex
    environment = {'REDIS_ENABLED': 'true', 'REDIS_HOST': '127.0.0.1',
                   'REDIS_PORT': str(port), 'REDIS_DB': '0', 'REDIS_PASSWORD': '',
                   'REDIS_SSL': 'false', 'REDIS_KEY_PREFIX': prefix,
                   'REDIS_INSPECTION_ENABLED': 'false'}
    try:
        yield client, prefix, environment
    finally:
        # Exact independently generated namespace only. No global flush/scan.
        for key in client.scan_iter(match=prefix + ':*', count=100):
            client.delete(key)
        assert list(client.scan_iter(match=prefix + ':*', count=100)) == []
        client.close()
        if previous is None:
            del service_processes.browser_temporary_directory
        else:
            service_processes.browser_temporary_directory = previous


def owned_descendants(parent_pid):
    """Read only PID/parent facts; never retain argv or process environments."""
    parents = {}
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (directory / 'status').read_text().splitlines() if ':' in line)
            parents[int(directory.name)] = int(fields['PPid'].strip())
        except (FileNotFoundError, ProcessLookupError, PermissionError, KeyError):
            continue
    descendants = set()
    frontier = {parent_pid}
    while frontier:
        frontier = {pid for pid, parent in parents.items() if parent in frontier} - descendants
        descendants.update(frontier)
    return descendants


def process_is_live(pid):
    try:
        status = (Path('/proc') / str(pid) / 'status').read_text()
        state = next(line for line in status.splitlines() if line.startswith('State:'))
        return '\tZ ' not in state
    except (FileNotFoundError, ProcessLookupError):
        return False


def redis_run_view(client, run_key, owner_key):
    """Never expose the private original Redis owner token to pytest output."""
    raw = client.get(run_key)
    if raw is None:
        return None
    record = json.loads(raw)
    token = record.get('owner_token')
    lease_live = bool(token and client.get(owner_key) == token and client.ttl(owner_key) > 0)
    return {'run_id': record.get('run_id'), 'state': record.get('state'), 'lease_live': lease_live}
