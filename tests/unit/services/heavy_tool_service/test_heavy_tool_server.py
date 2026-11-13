"""重工具服务 HTTP 面单测（server.py，设计 §4.1/§4.3/§8）。

覆盖：认证（缺 token fail-closed / 错 token 401 / 恒等比较通过）、路径 allowlist
（绝对路径 + 共享卷前缀）、busy 语义（信号量满 429）、同 tool_call_id 重复提交
协作取消旧 job（恢复重放 §4.3）、health 免认证计数。"""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from src.services.heavy_tool_service import handlers, server


@pytest.fixture
def rest_client(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", "tok")
    monkeypatch.setenv("HEAVY_TOOLS_CONCURRENCY", "1")
    monkeypatch.delenv("HEAVY_TOOLS_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("HEAVY_TOOLS_UPLOADS_ROOT", raising=False)
    server._semaphore = None
    server._jobs.clear()
    with TestClient(server.app) as test_client:
        yield test_client
    server._jobs.clear()


AUTH = {"Authorization": "Bearer tok"}


def _post_execute(rest_client, payload, headers=AUTH):
    return rest_client.post("/internal/heavy-tools/execute", json=payload, headers=headers)


class TestAuth:
    def test_missing_token_rejects_all(self, monkeypatch):
        monkeypatch.delenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", raising=False)
        server._jobs.clear()
        with TestClient(server.app) as rest_client:
            response = _post_execute(rest_client, {"tool": "excel_process"})
        assert response.status_code == 401

    def test_wrong_token_401(self, rest_client):
        response = _post_execute(rest_client, {"tool": "excel_process"},
                                 headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

    def test_health_requires_no_auth(self, rest_client):
        response = rest_client.get("/internal/heavy-tools/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "running_jobs" in body and "busy" in body


class TestPathAllowlist:
    def test_relative_path_rejected(self, rest_client):
        response = _post_execute(rest_client, {
            "tool": "paddleocr_doc_parsing",
            "params": {"file_path": "relative/file.pdf"}})
        assert response.status_code == 400
        assert "绝对路径" in response.json()["error"]

    def test_outside_shared_volume_rejected(self, rest_client):
        response = _post_execute(rest_client, {
            "tool": "paddleocr_doc_parsing",
            "params": {"file_path": "/etc/passwd"}})
        assert response.status_code == 400
        assert "允许范围" in response.json()["error"]

    def test_storage_path_accepted(self, rest_client):
        # 校验通过后进入 handler（文件不存在 -> 业务级失败，仍为 200）
        response = _post_execute(rest_client, {
            "tool": "paddleocr_doc_parsing",
            "params": {"file_path": "/app/storage/tenants/t/a.pdf"}})
        assert response.status_code == 200
        assert response.json()["success"] is False  # 文件不存在

    def test_uploads_path_accepted(self, rest_client):
        response = _post_execute(rest_client, {
            "tool": "paddleocr_doc_parsing",
            "params": {"file_path": "/app/uploads/file_x.pdf"}})
        assert response.status_code == 200

    def test_skill_workdir_validated(self, rest_client):
        response = _post_execute(rest_client, {
            "tool": "excel_process",
            "params": {},
            "context": {"skill_workdir": "/root/private"}})
        assert response.status_code == 400


class TestExecute:
    def test_unknown_tool_rejected(self, rest_client):
        response = _post_execute(rest_client, {
            "tool": "not_registered", "params": {}})
        assert response.status_code == 200
        assert response.json()["success"] is False

    def test_upload_parse_missing_file(self, rest_client):
        response = _post_execute(rest_client, {
            "tool": "upload_data_file", "handler": "parse",
            "params": {"file_path": "/app/storage/tenants/t/missing.xlsx"}})
        assert response.status_code == 200
        assert response.json()["success"] is False

    def test_fill_template_data_mode_rejected_remotely(self, rest_client):
        # 结构分析（LLM）必须在轻容器完成（§3/§7 计费前提）
        response = _post_execute(rest_client, {
            "tool": "excel_process", "handler": "fill_template",
            "params": {"data": {"rows": [{"a": 1}]}},
            "context": {}})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "fill_template_render" in body["result"]["error"]

    def test_success_wraps_result(self, rest_client, monkeypatch):
        monkeypatch.setattr(handlers, "dispatch",
                            lambda tool, handler, params: {"success": True, "value": 1})
        response = _post_execute(rest_client, {
            "tool": "excel_process", "handler": "read", "params": {}})
        body = response.json()
        assert body["success"] is True
        assert body["result"] == {"success": True, "value": 1}


class TestBusy:
    def test_busy_when_semaphore_full(self, monkeypatch):
        # 占满信号量后第二个请求应立即 429（不排队直接拒绝，§8）
        monkeypatch.setenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", "tok")
        monkeypatch.setenv("HEAVY_TOOLS_CONCURRENCY", "1")
        server._semaphore = None
        server._jobs.clear()
        import time as _time
        monkeypatch.setattr(handlers, "dispatch",
                            lambda tool, handler, params: (_time.sleep(0.4), {"success": True})[1])

        import httpx

        async def _run():
            transport = httpx.ASGITransport(app=server.app)
            results = {}
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                task_1 = asyncio.create_task(c.post(
                    "/internal/heavy-tools/execute", json={"tool": "excel_process"},
                    headers=AUTH))
                await asyncio.sleep(0.1)
                results["second"] = await c.post(
                    "/internal/heavy-tools/execute", json={"tool": "excel_process"},
                    headers=AUTH)
                results["first"] = await task_1
            return results

        try:
            results = asyncio.run(_run())
            assert results["first"].status_code == 200
            assert results["second"].status_code == 429
            body = results["second"].json()
            assert body["error_code"] == "busy"
            assert "稍后重试" in body["error"]
        finally:
            server._jobs.clear()

    def test_cancel_unknown_id_still_ok(self, rest_client):
        response = rest_client.post("/internal/heavy-tools/cancel",
                                    json={"tool_call_id": "nope"}, headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"success": True, "found": False}


class TestDuplicateToolCallId:
    def test_duplicate_id_cancels_previous_registration(self, rest_client):
        event_1 = server._register_job("call_dup")
        assert not event_1.is_set()
        server._register_job("call_dup")  # 恢复重放撞车：upsert + 旧 job 协作取消
        assert event_1.is_set()
        with server._jobs_lock:
            assert list(server._jobs.keys()) == ["call_dup"]


class TestHandlersPathCollect:
    def test_collect_known_path_keys_only(self):
        paths = handlers.collect_request_paths(
            {"file_path": "/app/storage/a.pdf", "file_paths": ["/app/uploads/b.xlsx"],
             "template_file": None, "content": "/etc/自由文本不校验", "instruction": "x"},
            {"skill_workdir": "/app/storage/agent_runner/workspaces/ws1"})
        assert set(paths) == {"/app/storage/a.pdf", "/app/uploads/b.xlsx",
                              "/app/storage/agent_runner/workspaces/ws1"}


def _occupy_async_holder():
    return _occupy_coroutine()


async def _occupy_coroutine():
    async with server._get_semaphore():
        await asyncio.sleep(0.3)
