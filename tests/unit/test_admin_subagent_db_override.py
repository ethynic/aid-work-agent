"""
DB 定义覆盖内置数字员工时，admin API 删除/修改放行回归测试

背景（生产事故 #79）：subagents/pre-sales/ 内置目录与 DB 中 agent_id=pre-sales
的定义并存，DB 定义覆盖内置（registry 中 from_db=True，列表 type=custom）。
原判定 registry.is_builtin(agent_id) 按 _builtin_names（dir_name 集合）恒 True，
导致管理员无法删除 DB 定义恢复内置版，且与列表 type=custom 的删除按钮矛盾。
修复后：按当前生效配置的 from_db 判定——纯内置拦截，DB 覆盖内置放行。
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models.subagent import SubagentConfig


def _config(from_db: bool) -> SubagentConfig:
    return SubagentConfig(
        name="爱定义AI数字员工顾问",
        dir_name="pre-sales",
        description="desc",
        system_prompt="prompt body",
        from_db=from_db,
    )


def _mock_registry(config):
    registry = MagicMock()
    registry.get.return_value = config
    # is_builtin 按 dir_name 集合判定：DB 覆盖内置场景下恒为 True（即被修复的旧语义）
    registry.is_builtin.return_value = True
    return registry


def _patch_env(registry):
    from src.api import admin_subagent as api

    request = MagicMock()
    return api, request, patch.object(api.master_agent, "subagent_registry", registry)


@pytest.mark.asyncio
async def test_delete_db_overridden_builtin_allowed():
    """DB 定义覆盖内置（from_db=True）：删除放行，删除后 registry 自动恢复内置版"""
    api, request, env = _patch_env(_mock_registry(_config(from_db=True)))
    with env, \
         patch.object(api, "_require_admin", return_value={"user_id": "admin"}), \
         patch.object(api.SubagentDefinitionService, "delete_definition", return_value=True) as mock_del, \
         patch.object(api, "delete_cached", MagicMock()):
        resp = await api.delete_subagent(request=request, agent_id="pre-sales")

    payload = resp.body if hasattr(resp, "body") else resp
    data = json.loads(payload) if isinstance(payload, bytes) else payload
    assert data["success"] is True
    mock_del.assert_called_once_with("pre-sales")


@pytest.mark.asyncio
async def test_delete_pure_builtin_blocked():
    """纯内置（from_db=False）：仍拦截 400"""
    api, request, env = _patch_env(_mock_registry(_config(from_db=False)))
    with env, \
         patch.object(api, "_require_admin", return_value={"user_id": "admin"}):
        resp = await api.delete_subagent(request=request, agent_id="pre-sales")

    payload = resp.body if hasattr(resp, "body") else resp
    data = json.loads(payload) if isinstance(payload, bytes) else payload
    assert data["success"] is False
    assert "内置" in data["error"]


@pytest.mark.asyncio
async def test_update_db_overridden_builtin_allowed():
    """DB 定义覆盖内置：更新放行"""
    api, request, env = _patch_env(_mock_registry(_config(from_db=True)))
    body = SimpleNamespace(
        name="新名字", description="d", triggers={}, tools={}, skills={},
        context={}, system_prompt=None,
    )
    with env, \
         patch.object(api, "_require_admin", return_value={"user_id": "admin"}), \
         patch.object(api.SubagentDefinitionService, "update_definition", return_value=True) as mock_upd, \
         patch.object(api, "delete_cached", MagicMock()):
        resp = await api.update_subagent(request=request, agent_id="pre-sales", body=body)

    payload = resp.body if hasattr(resp, "body") else resp
    data = json.loads(payload) if isinstance(payload, bytes) else payload
    assert data["success"] is True
    mock_upd.assert_called_once()


@pytest.mark.asyncio
async def test_update_pure_builtin_blocked():
    """纯内置：更新仍拦截 400"""
    api, request, env = _patch_env(_mock_registry(_config(from_db=False)))
    body = SimpleNamespace(
        name="新名字", description="d", triggers={}, tools={}, skills={},
        context={}, system_prompt=None,
    )
    with env, \
         patch.object(api, "_require_admin", return_value={"user_id": "admin"}):
        resp = await api.update_subagent(request=request, agent_id="pre-sales", body=body)

    payload = resp.body if hasattr(resp, "body") else resp
    data = json.loads(payload) if isinstance(payload, bytes) else payload
    assert data["success"] is False
    assert "内置" in data["error"]
