"""One external HTTP addition for the unchanged original CLI-stop regression.

The added Business consumer reads customer info before the original Voice
window. This fixture allows that actual SDK request on the existing fictional
server; every other request retains the old server handler. No product or old
test/helper file changes, and no new server/process/resource is allocated.
"""
import json
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture(autouse=True)
def completion_customer_read_on_original_voice_peer(request, monkeypatch):
    if request.node.name != (
            'test_fixed_non_ai_voice_drains_with_native_off_and_proven_unwritten_stop_recovers_exactly_once'):
        return
    voice = request.getfixturevalue('context_voice_receipts')
    peer, scope = voice.peer, voice.scope
    handler = peer._server.RequestHandlerClass
    original_post = handler.do_POST

    def original_http_with_customer_read(self):
        parsed = urlsplit(self.path)
        if parsed.path != '/cgi-bin/kf/customer/batchget':
            return original_post(self)
        valid = False
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024:
                raise ValueError
            body = json.loads(self.rfile.read(size))
            valid = (isinstance(body, dict)
                and body.get('external_userid_list') == [scope.actor_id]
                and parse_qs(parsed.query).get('access_token') == [peer.upstream.foundation._access_token])
        except (ValueError, TypeError):
            pass
        with peer._lock:
            peer.calls.append({'kind': 'customer_read', 'scope_valid': valid})
            if not valid:
                peer.errors.append('ASSOCIATION_CUSTOMER_READ_SCOPE_INVALID')
        if not valid:
            return self.respond(409, b'{"errcode":-1}', 'application/json')
        return self.respond(200, json.dumps({'errcode': 0, 'customer_list': [{
            'external_userid': scope.actor_id, 'nickname': 'Fictional original voice customer'}]}).encode(),
            'application/json')
    monkeypatch.setattr(handler, 'do_POST', original_http_with_customer_read)
