"""WP2 错误链路：表单校验 422 的逐题明细全程结构化与 BFF 条件序列化。

docs/plans/plan-ask-user-interactive-question.md §WP2 错误传输链路（P1-1）：
- Runner API（api.py）422 响应体携带 errors（question_id/code/message），不并入
  通用错误报文；pydantic 原始报文仍只映射为 INVALID_REQUEST；
- RunnerServiceClient（web_client.py）结构化透传，不降级为通用错误字符串，
  结构不合法的明细整体丢弃；
- Web BFF（agent_runner_web.py）errors 透传前端，controls 透传改为与
  intent() 相同口径的条件序列化——旧形状请求字节不变（滚动窗口内旧 runner
  service 的 extra='forbid' 不再因恒带 answers:null/skip:false 报 422）。

数据库侧受理失败（422 不落库）归 tests/integration/agent_runner_service/；
本文件 api 层与 BFF 层的两个 422 响应体同构，组合即全程链路证据。
"""

import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from types import SimpleNamespace

from src.api.agent_runner_web import control_body, create_router
from src.services.agent_runner import web_client as web_client_module
from src.services.agent_runner.api import create_app as create_runner_api
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.control_contracts import RunnerControl
from src.services.agent_runner.web_client import RunnerServiceClient, form_error_details

pytestmark = pytest.mark.unit

# 与真实 Runner API 422 响应体同构（api 层用例实测产出该形状）。
DETAILS = [{"question_id": "q1", "code": "FORM_ANSWER_INVALID", "message": "选择的选项不属于该题"},
           {"question_id": "q2", "code": "FORM_ANSWER_INVALID", "message": "该题为必答"}]
UPSTREAM_BODY = {"success": False, "error": "FORM_ANSWER_INVALID",
                 "debug": "FORM_ANSWER_INVALID", "errors": DETAILS}


def structured_request(**answers_override):
    body = {"client_request_id": "chain-key-1", "action": "reply",
            "target_execution_id": "runner-1", "wait_id": "wait-1",
            "answers": [{"question_id": "q1", "selected_keys": ["q1o9"], "text": None}]}
    body.update(answers_override)
    return body


class TestRunnerApiErrorBody:
    def test_form_validation_422_carries_per_question_errors(self):
        """受理前表单校验失败：422 + errors 逐题明细，不并入通用错误报文。"""

        def authorize_row(row, credentials):
            raise RunnerError('FORM_ANSWER_INVALID', 422, errors=DETAILS)

        manager = SimpleNamespace(
            authorizer=SimpleNamespace(verify_service=lambda *args: None,
                                       authorize_persisted=lambda row, **kwargs: None,
                                       assert_credit=lambda principal: None),
            repository=SimpleNamespace(connection_factory=None, get=lambda runner_id: {}),
            _authorize_row=authorize_row)
        app = create_runner_api(config=SimpleNamespace(enabled=True), manager=manager)
        response = TestClient(app).post('/v1/runners/runner-1/controls',
                                        json=structured_request())
        assert response.status_code == 422
        assert response.json() == UPSTREAM_BODY

    def test_entry_model_errors_stay_generic_without_raw_input(self):
        """pydantic 原始报文（含用户输入回显）不透传，统一映射 INVALID_REQUEST。"""
        app = create_runner_api(config=SimpleNamespace(enabled=True), manager=None)
        response = TestClient(app).post('/v1/runners/runner-1/controls',
                                        json={"client_request_id": "k", "action": "reply",
                                              "wait_id": "w", "target_execution_id": "r",
                                              "answer": "", "secret_extra": "leak"})
        assert response.status_code == 422
        assert response.json() == {"success": False, "error": "INVALID_REQUEST",
                                   "debug": "INVALID_REQUEST"}


class TestFormErrorDetails:
    def test_keeps_only_registered_dto_fields(self):
        value = [{"question_id": "q1", "code": "FORM_ANSWER_INVALID", "message": "x", "junk": 1}]
        assert form_error_details(value) == [
            {"question_id": "q1", "code": "FORM_ANSWER_INVALID", "message": "x"}]

    def test_malformed_details_dropped_entirely_never_downgraded_to_string(self):
        assert form_error_details(None) is None
        assert form_error_details([]) is None
        assert form_error_details(["FORM_ANSWER_INVALID"]) is None
        assert form_error_details([{"question_id": "q1", "code": "X"}]) is None


class TestWebClientPassthrough:
    @pytest.fixture
    def transport_handler(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(422, json=UPSTREAM_BODY)

        real_client = httpx.AsyncClient

        def patched(*args, **kwargs):
            return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(web_client_module.httpx, "AsyncClient", patched)
        return captured

    @pytest.fixture
    def gateway_app(self, transport_handler):
        config = SimpleNamespace(api_url="http://runner.test", web_service_id="svc",
                                 web_enabled=True)
        client = RunnerServiceClient(config, token="service-token")
        app = FastAPI()
        app.include_router(create_router(config, client=client))
        return app

    def test_errors_survive_client_and_bff_without_downgrade(self, gateway_app, transport_handler):
        """Runner API 422 → RunnerServiceClient → BFF 前端响应，errors 全程保留。"""
        response = TestClient(gateway_app).post(
            '/api/chat/runners/runner-1/controls', json=structured_request())
        assert response.status_code == 422
        assert response.json()["errors"] == DETAILS
        assert response.json()["error"] == "FORM_ANSWER_INVALID"

    def test_bff_forwards_structured_keys_and_omits_absent_ones(
            self, gateway_app, transport_handler):
        body = structured_request(skip=True, answers=None)
        body.pop("answers")
        assert TestClient(gateway_app).post(
            '/api/chat/runners/runner-1/controls', json=body).status_code == 422
        forwarded = transport_handler["body"]
        assert forwarded["skip"] is True
        assert "answers" not in forwarded and forwarded["answer"] == ""

    def test_legacy_request_bytes_unchanged_through_bff(self, gateway_app, transport_handler):
        """旧形状（无 answers/skip）请求上游字节与升级前一致。"""
        legacy = {"client_request_id": "legacy-key", "action": "pause"}
        assert TestClient(gateway_app).post(
            '/api/chat/runners/runner-1/controls', json=legacy).status_code == 422
        forwarded = transport_handler["body"]
        assert "answers" not in forwarded and "skip" not in forwarded
        assert set(forwarded) == {"client_request_id", "action", "target_execution_id",
                                  "wait_id", "answer", "attachments", "completion_ref"}


class TestControlBodyConditionalSerialization:
    def test_legacy_reply_serializes_byte_identically_to_pre_wp1_shape(self):
        legacy = RunnerControl(client_request_id="k", action="reply",
                               target_execution_id="r", wait_id="w", answer="文字")
        expected = {"client_request_id": "k", "action": "reply", "target_execution_id": "r",
                    "wait_id": "w", "answer": "文字", "attachments": [], "completion_ref": None}
        assert control_body(legacy) == expected
        assert json.dumps(control_body(legacy), ensure_ascii=False, sort_keys=True) == \
            json.dumps(expected, ensure_ascii=False, sort_keys=True)

    def test_structured_fields_only_present_when_supplied(self):
        answers = [{"question_id": "q1", "selected_keys": ["q1o1"], "text": None}]
        body = control_body(RunnerControl(client_request_id="k", action="reply",
                                          target_execution_id="r", wait_id="w", answers=answers))
        assert body["answers"] == answers and "skip" not in body
        body = control_body(RunnerControl(client_request_id="k", action="reply",
                                          target_execution_id="r", wait_id="w", skip=True))
        assert body["skip"] is True and "answers" not in body
