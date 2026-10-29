"""scheduled 公开投影与 Web 网关读取接线单测（设计 §5.5/§5.2，Phase 2）。

- write_history：scheduled 来源把 request_data.scheduled（task_id/trigger 白名单）
  投影进 user 消息 metadata；
- public_runner：attempt（通用）/ scheduled（触发原因）/ result.credit_cost
  （终态结算补入）公开字段；
- EVENT_ERROR_CODES：scheduled 相关错误码允许经网关事件流透传；
- Web 网关：会话 runner 列表 source='scheduled' 透传（默认 chat 不变）。
"""

import copy
from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.agent_runner.public_view import public_runner
from src.services.agent_runner.transaction_repository import write_history
from src.services.agent_runner.web_client import EVENT_ERROR_CODES


# ==================== write_history 投影 ====================


class WriteHistoryCursor:
    def __init__(self, session_row):
        self.session_row, self._next, self.executed = session_row, session_row, []

    def execute(self, sql, params=()):
        self.executed.append(sql)
        self._next = self.session_row if "FROM chat_sessions" in sql else {"now": datetime(2026, 10, 28, 9, 5)}

    def fetchone(self):
        return copy.deepcopy(self._next)


def _scheduled_runner():
    return {
        "runner_id": "runner_1", "session_id": "sess_task", "session_kind": "web",
        "tenant_id": "tenant_1", "user_id": "u1", "queue_order": 3,
        "source": "scheduled",
        "accepted_at": datetime(2026, 10, 28, 9, 0),
        "input": {"text": "生成日报", "attachments": [],
                  "request_data": {"scheduled": {"task_id": "sched_x1", "trigger": "scheduled",
                                                 "extra": "不外发"}}},
        "checkpoint": {"supplemental_inputs": []},
    }


def test_write_history_projects_scheduled_metadata():
    session = {"session_id": "sess_task", "tenant_id": "tenant_1", "user_id": "u1"}
    cursor = WriteHistoryCursor(session)
    captured = {}
    with patch("src.services.agent_runner.transaction_repository.MessageDB.create_batch_in_tx",
               side_effect=lambda _cursor, session_id, normalized: captured.update(
                   session_id=session_id, normalized=normalized)):
        write_history(cursor, _scheduled_runner(), {"status": "completed", "output": "done", "images": []})
    user_message = captured["normalized"][0]
    # 白名单投影：仅 task_id/trigger，其余 request_data 字段不外发
    assert user_message["metadata"]["scheduled"] == {"task_id": "sched_x1", "trigger": "scheduled"}
    assert "extra" not in user_message["metadata"]["scheduled"]
    assert user_message["metadata"]["runner_id"] == "runner_1"


def test_write_history_chat_source_has_no_scheduled_metadata():
    session = {"session_id": "sess_chat", "tenant_id": "tenant_1", "user_id": "u1"}
    cursor = WriteHistoryCursor(session)
    runner = _scheduled_runner()
    runner.update(session_id="sess_chat", source="chat")
    runner["input"] = {"text": "hi", "attachments": []}
    captured = {}
    with patch("src.services.agent_runner.transaction_repository.MessageDB.create_batch_in_tx",
               side_effect=lambda _cursor, session_id, normalized: captured.update(
                   session_id=session_id, normalized=normalized)):
        write_history(cursor, runner, {"status": "completed", "output": "ok", "images": []})
    assert "scheduled" not in captured["normalized"][0]["metadata"]


# ==================== public_runner 扩展 ====================


def _runner_row(**extra):
    now = datetime(2026, 10, 28, 9, 0)
    row = {
        "runner_id": "runner_1", "session_kind": "web", "session_id": "sess_task",
        "source": "chat", "status": "completed", "settlement_status": "settled",
        "client_request_id": "web-1", "profile_id": "main", "queue_order": 1,
        "revision": 1, "cancel_requested": False, "pause_requested": False,
        "resume_control_id": None, "view_revision": 1, "control_revision": 1,
        "accepted_at": now, "updated_at": now, "finished_at": now,
        "attempt": 2, "public_snapshot": {}, "checkpoint": {},
        "input": {"text": "hi"}, "result": {"status": "completed", "output": "done",
                                            "error_code": None, "images": []},
    }
    row.update(extra)
    return row


def test_public_runner_attempt_is_universal():
    value = public_runner(_runner_row())
    assert value["attempt"] == 2


def test_public_runner_scheduled_trigger_projection():
    row = _runner_row(source="scheduled", client_request_id="sched_x1:occ",
        input={"text": "生成日报", "request_data": {"scheduled": {
            "task_id": "sched_x1", "trigger": "manual"}}})
    value = public_runner(row)
    assert value["scheduled"] == {"task_id": "sched_x1", "trigger": "manual"}


def test_public_runner_chat_has_no_scheduled_field():
    value = public_runner(_runner_row())
    assert "scheduled" not in value


def test_public_runner_credit_cost_passthrough():
    row = _runner_row(result={"status": "completed", "output": "done", "error_code": None,
                              "credit_cost": 0.5, "images": []})
    value = public_runner(row)
    assert value["result"]["credit_cost"] == 0.5


def test_public_runner_malformed_scheduled_projection_dropped():
    row = _runner_row(source="scheduled",
                      input={"text": "x", "request_data": {"scheduled": "not-a-dict"}})
    assert "scheduled" not in public_runner(row)


# ==================== 网关错误码透传 ====================


def test_event_error_codes_include_scheduled_codes():
    assert {"SCHEDULED_TASK_NOT_ACCEPTABLE", "SCHEDULED_SUBMIT_FORBIDDEN",
            "SCHEDULED_TASK_NOT_FOUND", "TASK_SESSION_CHAT_FORBIDDEN"} <= EVENT_ERROR_CODES


# ==================== Web 网关 source 透传 ====================


class RecordingGatewayClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, path, *, authorization, tenant=None, body=None,
                      params=None, accept_new=None, channel_source=None, channel_user=None,
                      channel_chat=None):
        self.calls.append({"method": method, "path": path, "params": params})
        return {"success": True, "runners": [], "active_runners": [],
                "has_more": False, "next_cursor": None}


def _gateway_app(client):
    from src.api.agent_runner_web import create_router
    app = FastAPI()
    app.include_router(create_router(client=client))
    return app


def test_gateway_forwards_scheduled_source_param():
    client = RecordingGatewayClient()
    with TestClient(_gateway_app(client)) as test_client:
        response = test_client.get("/api/chat/sessions/sess_task/runners",
                                   params={"source": "scheduled", "limit": 5})
    assert response.status_code == 200 and response.json()["success"] is True
    assert client.calls[0]["params"] == {"limit": 5, "source": "scheduled"}


def test_gateway_source_defaults_to_chat():
    client = RecordingGatewayClient()
    with TestClient(_gateway_app(client)) as test_client:
        response = test_client.get("/api/chat/sessions/sess_chat/runners")
    assert response.status_code == 200
    assert client.calls[0]["params"]["source"] == "chat"


def test_gateway_rejects_unknown_source():
    client = RecordingGatewayClient()
    with TestClient(_gateway_app(client)) as test_client:
        response = test_client.get("/api/chat/sessions/sess/runners",
                                   params={"source": "wecom_kf"})
    assert response.status_code == 422
    assert client.calls == []
