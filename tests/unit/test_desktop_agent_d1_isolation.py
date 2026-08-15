import os
import subprocess
import sys
import asyncio

from src.core.agent import master_agent


def _probe(enabled: bool | None, *, secret: str | None = None) -> str:
    environment = dict(os.environ)
    if enabled is None:
        environment.pop("DESKTOP_AGENT_ENABLED", None)
    else:
        environment["DESKTOP_AGENT_ENABLED"] = "true" if enabled else "false"
    if secret is None:
        environment.pop("DESKTOP_AGENT_AUTHORIZATION_SECRET", None)
    else:
        environment["DESKTOP_AGENT_AUTHORIZATION_SECRET"] = secret
    code = """
import sys
from src.main import app
paths = [route.path for route in app.routes]
print('D1_IMPORTED=' + str('src.desktop_agent.api' in sys.modules))
print('D1_ROUTES=' + str(any(path.startswith('/api/desktop/v1') for path in paths)))
"""
    completed = subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), env=environment, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_default_backend_does_not_import_or_register_desktop_d1():
    output = _probe(None)
    assert "D1_IMPORTED=False" in output
    assert "D1_ROUTES=False" in output


def test_explicit_false_backend_does_not_import_or_register_desktop_d1():
    output = _probe(False)
    assert "D1_IMPORTED=False" in output
    assert "D1_ROUTES=False" in output


def test_explicit_enable_imports_and_registers_desktop_d1_after_restart():
    output = _probe(True)
    assert "D1_IMPORTED=True" in output
    assert "D1_ROUTES=True" in output


def test_short_d1_secret_fails_only_d1_service_not_existing_app_routes():
    environment = dict(os.environ)
    environment["DESKTOP_AGENT_ENABLED"] = "true"
    environment["DESKTOP_AGENT_AUTHORIZATION_SECRET"] = "short"
    code = """
from fastapi import HTTPException
from fastapi.testclient import TestClient
from src.main import app
from src.desktop_agent import api
response = TestClient(app).get('/openapi.json')
print('OPENAPI=' + str(response.status_code))
health = TestClient(app).get('/health')
print('HEALTH=' + str(health.status_code))
try:
    api._services()
except HTTPException as exc:
    print('D1_STATUS=' + str(exc.status_code))
else:
    print('D1_STATUS=unexpected-success')
"""
    completed = subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), env=environment, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert "OPENAPI=200" in completed.stdout
    assert "HEALTH=200" in completed.stdout
    assert "D1_STATUS=503" in completed.stdout


def test_default_continue_tool_call_passes_only_legacy_arguments(monkeypatch):
    captured = {}

    async def fake_process_message(**kwargs):
        captured.update(kwargs)
        yield {"type": "response", "data": "ok"}

    monkeypatch.setattr(master_agent, "process_message", fake_process_message)

    async def scenario():
        events = [event async for event in master_agent.continue_tool_call(session_id="session", tool_call_id="call", result={"ok": True})]
        assert events == [{"type": "response", "data": "ok"}]

    asyncio.run(scenario())
    assert captured == {
        "user_input": "",
        "session_id": "session",
        "user": None,
        "_continuation_tool_result": {"tool_call_id": "call", "content": {"ok": True}},
    }
