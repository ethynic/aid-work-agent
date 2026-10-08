"""Only upstream HTTP bodies are faulty; the actual Web client/gateway is used."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from .test_web_gateway import api_pair, gateways, workers, prices, submission
from .test_api import require_status

pytestmark=pytest.mark.integration


@contextmanager
def invalid_upstream(body):
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def reply(self):
            observed.append((self.command,self.path))
            length=int(self.headers.get("Content-Length","0"))
            if length:
                self.rfile.read(length)
            content=json.dumps(body).encode()
            self.send_response(202 if self.command=="POST" else 200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        do_GET=reply
        do_POST=reply
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}",observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("endpoint,body",[
    ("submit",{"success":False,"error":"fictional-private-upstream-error"}),
    ("submit",{"success":True}),
    ("read",{"success":True,"runner":"fictional-invalid-runner"}),
    ("list",{"success":True,"runners":[]}),
])
def test_invalid_success_http_body_is_explicit_bad_gateway_without_execution(gateways, actors, service_database, endpoint, body):
    with invalid_upstream(body) as (url,observed):
        gateway=gateways.start(api_url=url)
        if endpoint=="submit":
            response=gateway.call("POST","/api/chat/runners",actor=actors["a"],json=submission(actors["a"]))
        elif endpoint=="read":
            response=gateway.call("GET","/api/chat/runners/fictional-runner",actor=actors["a"])
        else:
            response=gateway.call("GET","/api/chat/sessions/"+actors["a"].session_id+"/runners",actor=actors["a"])
        result=require_status(response,502)
        assert result["code"]=="RUNNER_SERVICE_INVALID_RESPONSE"
        assert "fictional-private-upstream-error" not in json.dumps(result)
        assert len(observed)==1
        assert service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s",(actors["a"].user_id,))==[]
        assert not gateways.workers.provider.errors


def test_web_validation_never_echoes_untrusted_sensitive_input(gateways, actors):
    gateway=gateways.start()
    fake_value="fictional-invalid-sensitive-request-value"
    response=gateway.call("POST","/api/chat/runners",actor=actors["a"],
                          json={**submission(actors["a"]),"service_token":fake_value})
    result=require_status(response,422)
    assert fake_value not in json.dumps(result)
    require_status(gateway.call("GET","/api/chat/sessions/"+actors["a"].session_id+"/runners?limit=101",actor=actors["a"]),422)


@pytest.mark.parametrize("field",["client_request_id","queue_order","profile_id"])
def test_missing_client_binding_order_or_profile_in_actual_runner_response_is_rejected(gateways, actors, field):
    # Start from an actual accepted DTO so this is a consumer contract check,
    # rather than a hand-built duplicate of the production schema validator.
    real=gateways.start()
    actual=require_status(real.call("POST","/api/chat/runners",actor=actors["a"],json=submission(actors["a"])),202)
    actual["runner"].pop(field)
    with invalid_upstream(actual) as (url,observed):
        gateway=gateways.start(api_url=url)
        failure=require_status(gateway.call("GET","/api/chat/runners/"+actual["runner"]["runner_id"],actor=actors["a"]),502)
        assert failure["code"]=="RUNNER_SERVICE_INVALID_RESPONSE" and len(observed)==1
