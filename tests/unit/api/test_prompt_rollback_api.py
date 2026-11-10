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


class TestVersionsDiffRouteOrder:
    def test_versions_diff_not_shadowed_by_version_route(self, client, monkeypatch):
        """/versions/diff 字面量子路径不被 /versions/{version} 遮蔽（version="diff" 曾 422）

        命中 diff 处理器时先过 _require_admin（已 mock 为通过），再走 _get_prompt_id；
        未命中则被 get_version 以 version="diff" 触发 422。用 404（无关联 Prompt）
        证明请求到达了 diff 处理器。
        """
        monkeypatch.setattr(api_mod, "_get_prompt_id", lambda agent_id: None)

        resp = client.get("/api/admin/agent-definitions/emp_a/versions/diff", params={"v1": 1, "v2": 2})

        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_versions_numeric_still_resolves_version_route(self, client, monkeypatch):
        """:int 转换器不影响正常数字版本号路由"""
        monkeypatch.setattr(api_mod, "_get_prompt_id", lambda agent_id: "pid-1")
        monkeypatch.setattr(api_mod.PromptRegistryService, "get_version", lambda prompt_id, version: {"version": version})

        resp = client.get("/api/admin/agent-definitions/emp_a/versions/3")

        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == 3
