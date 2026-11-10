"""租户实例 update_instance 双写租户定制版本链单元测试

补充双写（用户已确认的授权扩展）：租户对话框保存走实例行 API，链上「上一版」会与
实例行脱节；显式保存非空 extra_prompt 时同步 commit_version，使回退上一版可用。

验证：
- 非空 extra_prompt → 实例行更新 + 版本链 commit（dedup 由 commit_version 内建）
- 空/纯空白 extra_prompt（=清除定制）→ 只清实例行，不写空版本、不建 registry
- 未携带 extra_prompt → 不写链
- 链写失败 → 实例行保存不受阻（best-effort，运行时实例行权威）
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.api import tenant_agent_instances as api_mod

pytestmark = pytest.mark.unit

ADMIN = {"tenant_id": "tenant_001", "user_id": "user_001"}
ROW = {
    "agent_id": "emp_a", "base_agent_id": "emp_a", "base_source": "builtin",
    "display_name": "员工A", "description": None, "extra_prompt": "定制",
    "greeting_summary": None, "greeting_prompts": None,
    "llm_provider": None, "status": "active",
    "created_at": "2026-01-01", "updated_at": "2026-01-01",
}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api_mod.router)
    app.dependency_overrides[api_mod.require_admin] = lambda: ADMIN
    yield TestClient(app, raise_server_exceptions=False)


def _patch_success_paths(monkeypatch):
    """实例行更新成功 + registry/commit 可用（断言入参用）"""
    monkeypatch.setattr(
        "src.saas.services.tenant_agent_instance_service.update_overrides",
        lambda tenant_id, agent_id, overrides: dict(ROW, **overrides),
    )
    get_prompt = patch("src.api.subagent_extra._get_or_create_prompt",
                       return_value={"id": "pid-1"})
    commit = patch("src.prompts.prompt_registry_service.PromptRegistryService.commit_version",
                   return_value={"version": {"version": 5, "content": "定制"}, "dedup": False})
    return get_prompt, commit


class TestUpdateInstanceExtraPromptDualWrite:
    def test_non_empty_extra_prompt_commits_version_chain(self, client, monkeypatch):
        """非空 extra_prompt：实例行更新 + 版本链 commit（commit_message=租户前台编辑）"""
        get_prompt, commit = _patch_success_paths(monkeypatch)
        with get_prompt as mock_get, commit as mock_commit:
            resp = client.put("/api/saas/tenant/agent-instances/emp_a",
                              json={"extra_prompt": "  新定制 \n"})

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_get.assert_called_once_with("emp_a", "tenant_001", "user_001")
        mock_commit.assert_called_once_with(
            prompt_id="pid-1",
            content="新定制",
            commit_message="租户前台编辑",
            created_by="user_001",
        )

    def test_blank_extra_prompt_clears_instance_only(self, client, monkeypatch):
        """纯空白 extra_prompt（=清除定制）：只清实例行，不写空版本、不建 registry"""
        get_prompt, commit = _patch_success_paths(monkeypatch)
        with get_prompt as mock_get, commit as mock_commit:
            resp = client.put("/api/saas/tenant/agent-instances/emp_a",
                              json={"extra_prompt": "   \n"})

        assert resp.status_code == 200
        mock_get.assert_not_called()
        mock_commit.assert_not_called()

    def test_absent_extra_prompt_does_not_touch_chain(self, client, monkeypatch):
        """未携带 extra_prompt（改显示名等）：不写链"""
        get_prompt, commit = _patch_success_paths(monkeypatch)
        with get_prompt as mock_get, commit as mock_commit:
            resp = client.put("/api/saas/tenant/agent-instances/emp_a",
                              json={"display_name": "新名字"})

        assert resp.status_code == 200
        mock_get.assert_not_called()
        mock_commit.assert_not_called()

    def test_chain_write_failure_does_not_block_instance_save(self, client, monkeypatch):
        """best-effort：链写失败不阻塞实例行保存（运行时实例行权威）"""
        monkeypatch.setattr(
            "src.saas.services.tenant_agent_instance_service.update_overrides",
            lambda tenant_id, agent_id, overrides: dict(ROW, **overrides),
        )
        with patch("src.api.subagent_extra._get_or_create_prompt",
                   return_value={"id": "pid-1"}), \
             patch("src.prompts.prompt_registry_service.PromptRegistryService.commit_version",
                   side_effect=RuntimeError("db down")):
            resp = client.put("/api/saas/tenant/agent-instances/emp_a",
                              json={"extra_prompt": "定制"})

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["data"]["extra_prompt"] == "定制"

    def test_registry_creation_failure_does_not_block_instance_save(self, client, monkeypatch):
        """registry 获取失败同样仅记日志，实例行已保存"""
        monkeypatch.setattr(
            "src.saas.services.tenant_agent_instance_service.update_overrides",
            lambda tenant_id, agent_id, overrides: dict(ROW, **overrides),
        )
        with patch("src.api.subagent_extra._get_or_create_prompt",
                   side_effect=RuntimeError("db down")):
            resp = client.put("/api/saas/tenant/agent-instances/emp_a",
                              json={"extra_prompt": "定制"})

        assert resp.status_code == 200
        assert resp.json()["data"]["extra_prompt"] == "定制"
