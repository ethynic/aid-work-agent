"""Gated fictional desktop/webhook IO for actual Local domain-flow acceptance.

This harness knows no runner checkpoint, phase name, recovery or billing rule.
The desktop uses the original physical-client DAL; webhook requests pass through
real HTTP. Only fictional effects/results are scripted. Never use customer data.
"""
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid

from .conftest import wait_for


_DESKTOP = r'''
import json,secrets,sys,time
from pathlib import Path
from src.db.database import init_postgres_pool,close_postgres_pool
from src.local_tools import repository
from src.local_tools.security import sha256_hex
init_postgres_pool()
try:
    tenant,user,folder=sys.argv[1:4]
    root=Path(folder)
    script=json.loads((root/'script.json').read_text())
    device=repository.create_device(tenant,user,sha256_hex(secrets.token_urlsafe(32)),
        name='Fictional domain Runtime',platform='windows',
        capabilities={'provider_id':'ai.aidwork.boss-recruiting'})
    assert repository.select_device(tenant,user,str(device['id']))
    (root/'ready.json').write_text(json.dumps({'device_id':str(device['id'])}))
    last_seen=0
    def heartbeat():
        global last_seen
        if time.monotonic()-last_seen>=1:
            assert repository.touch_device_seen(str(device['id']))
            last_seen=time.monotonic()
    def wait_until(predicate):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            heartbeat()
            if (root/'stop').exists(): return False
            result=predicate()
            if result: return result
            time.sleep(.05)
        raise AssertionError('FICTIONAL_DEVICE_GATE_TIMEOUT')
    for ordinal,step in enumerate(script):
        if not wait_until(lambda:(root/f'{ordinal}-claim').exists()): break
        claim_hash=sha256_hex(secrets.token_urlsafe(32))
        invocation=wait_until(lambda:repository.claim_next(str(device['id']),tenant,claim_hash,90))
        if not invocation: break
        assert invocation['tool_name']==step['tool_name'], 'FICTIONAL_DEVICE_UNEXPECTED_TOOL'
        identifier=str(invocation['id'])
        assert repository.mark_started(identifier,tenant,claim_hash)['state']=='running'
        repository.append_event(identifier,tenant,claim_hash,f'fixture-step-{ordinal}',1,1,
            'Fictional physical step started',90)
        (root/f'{ordinal}-claimed.json').write_text(json.dumps({
            'invocation_id':identifier,'tool_name':invocation['tool_name']}))
        if not wait_until(lambda:(root/f'{ordinal}-result').exists()): break
        result=step['result']
        stored=repository.write_result(identifier,tenant,claim_hash,result.get('success',True),
            code=result.get('code'),message='Fictional physical result',
            effect=result.get('effect','applied'),data=result.get('data',{}))
        duplicate=repository.write_result(identifier,tenant,claim_hash,result.get('success',True),
            code=result.get('code'),message='Duplicate physical result transport',
            effect=result.get('effect','applied'),data=result.get('data',{}))
        assert stored['state']==duplicate['state']
        (root/f'{ordinal}-done.json').write_text(json.dumps({
            'invocation_id':identifier,'state':stored['state']}))
finally:
    close_postgres_pool()
'''


class ScriptedDesktop:
    def __init__(self, workers, actor, steps):
        self.workers = workers
        self.folder = workers.root / ('domain-desktop-' + uuid.uuid4().hex)
        self.folder.mkdir()
        (self.folder / 'script.json').write_text(json.dumps(steps))
        self.child = workers.processes.start(['-c', _DESKTOP, actor.tenant_id,
            actor.user_id, str(self.folder)], environment=workers.environment,
            private_working_directory=True)
        wait_for((self.folder / 'ready.json').exists, timeout=15)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def allow_claim(self, ordinal):
        (self.folder / f'{ordinal}-claim').touch()

    def claimed(self, ordinal):
        path = self.folder / f'{ordinal}-claimed.json'
        wait_for(path.exists, timeout=20)
        return json.loads(path.read_text())

    def allow_result(self, ordinal):
        (self.folder / f'{ordinal}-result').touch()

    def completed(self, ordinal):
        path = self.folder / f'{ordinal}-done.json'
        wait_for(path.exists, timeout=20)
        return json.loads(path.read_text())

    def close(self):
        (self.folder / 'stop').touch()
        self.workers.processes.stop(self.child)


@dataclass
class WebhookReply:
    status: int = 200
    payload: dict = field(default_factory=lambda: {'errcode': 0, 'errmsg': 'ok'})
    release: threading.Event | None = None
    arrived: threading.Event = field(default_factory=threading.Event)


class WebhookPeer:
    """Real localhost HTTP ACKs; no credentials or notification/DAL replacement."""
    def __init__(self, *replies):
        self.replies = replies
        self._requests, self._errors = [], []
        self._lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    if self.path != '/fixture-webhook':
                        raise ValueError('unexpected-route')
                    size = int(self.headers.get('Content-Length', 0))
                    if not 0 < size <= 32768:
                        raise ValueError('invalid-body-size')
                    body = json.loads(self.rfile.read(size))
                    if body.get('msgtype') not in {'markdown', 'text'}:
                        raise ValueError('unexpected-message-type')
                    with peer._lock:
                        ordinal = len(peer._requests)
                        peer._requests.append(body)
                        if ordinal >= len(peer.replies):
                            raise ValueError('unexpected-notification-replay')
                        reply = peer.replies[ordinal]
                    reply.arrived.set()
                    if reply.release is not None and not reply.release.wait(45):
                        raise ValueError('notification-gate-timeout')
                    self.respond(reply.status, reply.payload)
                except (ValueError, TypeError, AttributeError) as error:
                    with peer._lock:
                        peer._errors.append(type(error).__name__)
                    self.respond(409, {'errcode': 409, 'errmsg': 'fixture rejected request'})

            def respond(self, status, payload):
                encoded = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass # Deliberate worker termination loses the physical ACK.

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @property
    def url(self):
        return f'http://127.0.0.1:{self._server.server_port}/fixture-webhook'

    @property
    def requests(self):
        with self._lock:
            return json.loads(json.dumps(self._requests))

    @property
    def errors(self):
        with self._lock:
            return tuple(self._errors)

    def close(self):
        for reply in self.replies:
            if reply.release is not None:
                reply.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)
        assert not self._thread.is_alive()
