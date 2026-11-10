"""平台侧 system_prompt 一键回退端点单元测试

POST /api/admin/agent-definitions/{agent_id}/system-prompt/rollback
（docs/plans/plan-prompt-direct-delivery-rollback.md Phase 2）
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import agent_definitions as api_mod

pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch):
    """最小 app 挂 agent-definitions 路由，鉴权与 DB 边界全部 mock"""
    app = FastAPI()
    app.include_router(api_mod.router)
    admin = {"user_id": "admin1", "phone": "13800000000", "role": "platform_admin"}
    monkeypatch.setattr(api_mod, "_require_admin", lambda request: admin)
    yield TestClient(app, raise_server_exceptions=False)


class TestRollbackSystemPrompt:
    def test_rollback_success_returns_version_and_content(self, client, monkeypatch):
        monkeypatch.setattr(api_mod, "_get_prompt_id", lambda agent_id: "pid-1")
        commit_result = {"version": {"version": 4, "content": "回退后的提示词"}, "dedup": False}
        monkeypatch.setattr(api_mod.PromptRegistryService, "rollback_to_previous",
                            lambda prompt_id, created_by=None: commit_result)

        resp = client.post("/api/admin/agent-definitions/emp_a/system-prompt/rollback")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["version"] == 4
        assert body["data"]["content"] == "回退后的提示词"

    def test_rollback_without_previous_returns_400(self, client, monkeypatch):
        """仅一版 → 明确报「无可回退版本」"""
        monkeypatch.setattr(api_mod, "_get_prompt_id", lambda agent_id: "pid-1")
        monkeypatch.setattr(api_mod.PromptRegistryService, "rollback_to_previous",
                            lambda prompt_id, created_by=None: None)

        resp = client.post("/api/admin/agent-definitions/emp_a/system-prompt/rollback")

        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert "无可回退版本" in body["error"]

    def test_rollback_without_prompt_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(api_mod, "_get_prompt_id", lambda agent_id: None)

        resp = client.post("/api/admin/agent-definitions/emp_a/system-prompt/rollback")

        assert resp.status_code == 404
        assert resp.json()["success"] is False
