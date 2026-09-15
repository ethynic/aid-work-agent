"""会话工具授权边界与服务契约（无数据库/设备动作）。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.session_tasks.constants import SessionTaskError
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.weixin import session_task_tools as tools

pytestmark = pytest.mark.tools


@pytest.fixture
def service(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(tools, "_get_service", lambda: mock)
    return mock


@pytest.fixture
def identity():
    with tool_execution_scope(ToolExecutionContext(tenant_id="tenant-a", user_id="user-a")):
        yield


def draft():
    return dict(device_id=str(uuid4()), account_binding_id=str(uuid4()),
                conversation_binding_id=str(uuid4()), spec={
                    "goal": "确认意向", "completion_rule": {"mode": "rounds", "rounds_target": 1},
                    "reply_policy": {"style": "简洁"}, "limits": {"max_replies": 2,
                    "max_decisions": 3, "max_cost_units": 10,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}})


@pytest.mark.asyncio
async def test_missing_context_never_reaches_service(service):
    with tool_execution_scope(None):
        result = await tools.SessionTaskManageTool().execute(action="list")
    assert result["success"] is False
    assert not service.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"tenant_id": "victim"}, {"user_id": "victim"}, {"command": "send"}])
async def test_identity_and_command_injection_rejected(identity, service, extra):
    result = await tools.SessionTaskManageTool().execute(action="list", **extra)
    assert result["code"] == "VALIDATION_FAILED"
    assert not service.mock_calls


@pytest.mark.asyncio
async def test_prepare_returns_workbench_only(identity, service):
    task_id = str(uuid4())
    service.create_draft.return_value = {"task_id": task_id, "version": 1, "status": "draft"}
    result = await tools.SessionTaskPrepareTool().execute(**draft())
    assert result["success"] and not result["published"]
    assert result["confirmation_url"] == f"/t/tenant-a/weixin-marketing/session-tasks/{task_id}"
    assert service.create_draft.call_args.args[:2] == ("tenant-a", "user-a")
    assert [call[0] for call in service.mock_calls] == ["create_draft"]


@pytest.mark.asyncio
async def test_update_requires_version_and_rejects_binding_change(identity, service):
    task_id = uuid4()
    payload = draft()
    result = await tools.SessionTaskPrepareTool().execute(task_id=task_id, **payload)
    assert result["code"] == "VALIDATION_FAILED"
    result = await tools.SessionTaskPrepareTool().execute(task_id=task_id, expected_version=2, **payload)
    assert result["code"] == "VALIDATION_FAILED"
    assert not service.mock_calls
    service.update_draft.return_value = {"task_id": str(task_id), "version": 3}
    result = await tools.SessionTaskPrepareTool().execute(task_id=task_id, expected_version=2, spec=payload["spec"])
    assert result["success"]
    assert service.update_draft.call_args.args[:4] == ("tenant-a", "user-a", task_id, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [{}, {"confirmed": True}, {"confirmation_id": "invented"}])
async def test_publish_requires_real_confirmation_shape(identity, service, credentials):
    result = await tools.SessionTaskPublishTool().execute(task_id=uuid4(), expected_version=1, **credentials)
    assert result["code"] == "VALIDATION_FAILED"
    assert not service.mock_calls


@pytest.mark.asyncio
async def test_publish_passes_confirmation_without_issuing(identity, service):
    task_id, confirmation_id = uuid4(), uuid4()
    service.publish_task_once.return_value = {"status": "active", "version": 2}
    result = await tools.SessionTaskPublishTool().execute(
        task_id=task_id, expected_version=1, confirmation_id=confirmation_id)
    assert result["success"]
    service.publish_task_once.assert_called_once_with("tenant-a", "user-a", task_id, 1, confirmation_id)
    assert [call[0] for call in service.mock_calls] == ["publish_task_once"]


@pytest.mark.asyncio
async def test_service_authorization_rejection_preserved(identity, service):
    service.publish_task_once.side_effect = SessionTaskError("确认已失效", "CONFIRMATION_INVALID", 409)
    result = await tools.SessionTaskPublishTool().execute(
        task_id=uuid4(), expected_version=1, confirmation_id=uuid4())
    assert result == {"success": False, "code": "CONFIRMATION_INVALID", "error": "确认已失效"}


@pytest.mark.asyncio
async def test_resume_requires_watermark_and_version(identity, service):
    task_id = uuid4()
    for kwargs in ({}, {"expected_version": 2}, {"expected_version": 2, "resume_from": {
            "mode": "replay_backlog", "expected_input_version": 3}}):
        result = await tools.SessionTaskManageTool().execute(action="resume", task_id=task_id, **kwargs)
        assert result["code"] == "VALIDATION_FAILED"
    assert not service.mock_calls
    service.control_task.return_value = {"status": "active", "version": 3}
    selection = {"mode": "fresh_baseline", "expected_input_version": 3}
    result = await tools.SessionTaskManageTool().execute(
        action="resume", task_id=task_id, expected_version=2, resume_from=selection)
    assert result["success"]
    service.control_task.assert_called_once_with(
        "tenant-a", "user-a", task_id, "resume", 2, None, resume_from=selection)


@pytest.mark.asyncio
async def test_unexpected_exception_does_not_echo_credentials(identity, service, caplog):
    service.list_tasks.side_effect = RuntimeError("secret-credential")
    result = await tools.SessionTaskManageTool().execute(action="list")
    assert result["code"] == "INTERNAL_ERROR"
    assert "secret-credential" not in str(result) + caplog.text


def test_catalog_contains_three_tools():
    from src.tools.base import _CATALOG
    names = {cls.name for cls in _CATALOG.values()}
    assert {"session_task_prepare", "session_task_publish", "session_task_manage"} <= names
    for cls in (tools.SessionTaskPrepareTool, tools.SessionTaskPublishTool, tools.SessionTaskManageTool):
        assert cls().to_tool_definition()["input_schema"]["additionalProperties"] is False
