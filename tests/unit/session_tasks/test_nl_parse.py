"""自然语言建任务解析（nl_parse）与列表降级容错测试。

- LLM 一律 monkeypatch（不依赖真实网关）；绑定用 conftest 真实 fixture；
- 权威性契约：missing 后端判定、幻觉 binding_id 丢弃、非法 completion 整体
  降级、LLM 失败可回退手动填表（parse_error / 503）；
- list_tasks：单条受控文本解不开只降级该行（projection_degraded），列表不 500。
"""

import json

import pytest

from src.session_tasks import service
from src.session_tasks.constants import ERR_PARSE_UNAVAILABLE, ERR_VALIDATION_FAILED
from src.session_tasks.models import TaskDraftCreatePayload
from src.session_tasks.nl_parse import (
    _assemble_spec,
    _clean_completion,
    _clean_expires,
    _match_bindings,
    _missing_fields,
    parse_natural_language,
)
from tests.unit.session_tasks.conftest import build_draft_payload


def _llm_payload(**overrides):
    payload = {
        "goal": "向对方介绍会员新版并确认是否愿意升级",
        "opening_text": "您好，打扰一下",
        "completion_rule": {"mode": "rounds", "rounds_target": 3},
        "reply_policy": {"style": "友好热情", "allowed_facts": ["新版支持批量导出"], "forbidden_commitments": ["价格承诺"]},
        "limits": {"max_replies": None, "max_decisions": None, "max_cost_units": None,
                   "peer_wait_timeout_seconds": None, "expires_at": "2099-01-01T00:00:00+08:00"},
        "binding_ids": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def mock_llm(monkeypatch):
    """替换网关 chat_no_thinking；用 set_payload 注入每次返回内容。"""
    holder = {"payload": _llm_payload(), "raw": None, "calls": 0}

    async def fake_chat_no_thinking(messages, **kwargs):  # noqa: ANN003
        holder["calls"] += 1
        if holder["raw"] is not None:
            return {"content": holder["raw"]}
        return {"content": json.dumps(holder["payload"], ensure_ascii=False)}

    from src.llm.gateway import llm_gateway

    monkeypatch.setattr(llm_gateway, "chat_no_thinking", fake_chat_no_thinking)
    return holder


class TestCleanHelpers:
    def test_rounds_clamped(self):
        assert _clean_completion({"mode": "rounds", "rounds_target": 5000}) == {"mode": "rounds", "rounds_target": 1000}
        assert _clean_completion({"mode": "rounds", "rounds_target": "abc"}) is None

    def test_invalid_mode_dropped(self):
        assert _clean_completion({"mode": "forever"}) is None
        assert _clean_completion("聊三轮") is None

    def test_judged_dedup(self):
        rule = _clean_completion({"mode": "judged", "criteria": ["同意", "同意", ""]})
        assert rule == {"mode": "judged", "criteria": ["同意"]}

    def test_peer_confirmed_key_autogen_and_accept_fix(self):
        rule = _clean_completion({"mode": "peer_confirmed", "fields": [
            {"key": "意向", "question": "是否愿意升级", "allowed_values": ["愿意", "不愿意"], "accepted_values": ["愿意", "随便"]},
            {"key": "budget", "question": "预算多少", "allowed_values": ["高", "低"], "accepted_values": ["中"]},
        ]})
        # 第二个字段接受值与允许值无交集 → 整字段丢弃，不伪造接受语义
        assert [f["key"] for f in rule["fields"]] == ["field_1"]
        assert rule["fields"][0]["accepted_values"] == ["愿意"]  # 不在允许值内的接受值被剔除

    def test_peer_confirmed_no_accepted_intersection_drops_all(self):
        assert _clean_completion({"mode": "peer_confirmed", "fields": [
            {"key": "a", "question": "问题", "allowed_values": ["x"], "accepted_values": ["y"]},
        ]}) is None

    def test_expires_naive_as_beijing_and_past_rejected(self):
        assert _clean_expires("2099-06-01T10:00:00").endswith("+08:00")
        assert _clean_expires("2000-01-01T00:00:00+08:00") is None
        assert _clean_expires("not-a-date") is None


class TestAssembleAndMissing:
    def test_defaults_filled(self):
        spec = _assemble_spec(_llm_payload())
        assert spec["limits"]["max_replies"] == 5  # rounds 3 + 2
        assert spec["limits"]["max_decisions"] == 10
        assert spec["reply_policy"]["style"] == "友好热情"
        assert spec["limits"]["expires_at"].startswith("2099-")

    def test_all_missing(self):
        spec = _assemble_spec(None)
        missing = [m["field"] for m in _missing_fields(spec, [])]
        assert missing == ["binding", "goal", "completion_rule", "expires_at"]

    def test_match_bindings_filters_hallucinated_ids(self):
        real = [{"id": "b1", "device_id": "d", "account_binding_id": "a", "conversation_type": "direct",
                 "conversation_label": "张三", "verification_status": "verified"}]
        parsed = {"binding_ids": ["b1", "ghost-id", "b1"]}
        assert [b["id"] for b in _match_bindings(parsed, real)] == ["b1"]
        assert _match_bindings({"binding_ids": ["ghost"]}, real) == []


@pytest.mark.asyncio
async def test_parse_full_success(tenant_id, pending_binding, mock_llm):
    mock_llm["payload"] = _llm_payload(binding_ids=[pending_binding["conversation_binding_id"]])
    result = await parse_natural_language(tenant_id, "user-1", "给张三介绍新版，聊3轮，2099年前完成")
    assert result["parse_error"] is None
    assert result["missing"] == []
    assert [b["id"] for b in result["binding_candidates"]] == [pending_binding["conversation_binding_id"]]
    assert result["spec"]["goal"].startswith("向对方介绍")
    assert mock_llm["calls"] == 1


@pytest.mark.asyncio
async def test_parse_reports_missing_and_drops_ghost_binding(tenant_id, pending_binding, mock_llm):
    mock_llm["payload"] = _llm_payload(
        goal=None, completion_rule=None,
        limits={"max_replies": None, "max_decisions": None, "max_cost_units": None,
                "peer_wait_timeout_seconds": None, "expires_at": None},
        binding_ids=["ghost"],
    )
    result = await parse_natural_language(tenant_id, "user-1", "帮我跟客户聊聊")
    fields = [m["field"] for m in result["missing"]]
    assert fields == ["binding", "goal", "completion_rule", "expires_at"]
    assert result["binding_candidates"] == []
    assert result["spec"]["reply_policy"]["style"]  # 默认风格兜底


@pytest.mark.asyncio
async def test_parse_llm_garbage_degrades(tenant_id, pending_binding, mock_llm):
    mock_llm["raw"] = "抱歉，我不明白您的问题"  # 非 JSON 输出
    result = await parse_natural_language(tenant_id, "user-1", "随便说点什么")
    assert result["parse_error"]
    assert len(result["missing"]) == 4  # 全缺失，前端回退手动填表


@pytest.mark.asyncio
async def test_parse_llm_unavailable_503(tenant_id, pending_binding, monkeypatch):
    from src.llm.gateway import llm_gateway

    async def boom(messages, **kwargs):  # noqa: ANN003
        raise RuntimeError("gateway down")

    monkeypatch.setattr(llm_gateway, "chat_no_thinking", boom)
    from src.session_tasks.constants import SessionTaskError

    with pytest.raises(SessionTaskError) as exc_info:
        await parse_natural_language(tenant_id, "user-1", "给张三发消息")
    assert exc_info.value.code == ERR_PARSE_UNAVAILABLE
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_parse_empty_text_400(tenant_id):
    from src.session_tasks.constants import SessionTaskError

    with pytest.raises(SessionTaskError) as exc_info:
        await parse_natural_language(tenant_id, "user-1", "  ")
    assert exc_info.value.code == ERR_VALIDATION_FAILED


@pytest.mark.asyncio
async def test_parse_too_long_text_400(tenant_id):
    from src.session_tasks.constants import SessionTaskError

    with pytest.raises(SessionTaskError) as exc_info:
        await parse_natural_language(tenant_id, "user-1", "长" * 4001)
    assert exc_info.value.code == ERR_VALIDATION_FAILED


@pytest.mark.asyncio
async def test_parse_unknown_scenario_fail_closed_400(tenant_id):
    from src.session_tasks.constants import SessionTaskError

    with pytest.raises(SessionTaskError) as exc_info:
        await parse_natural_language(tenant_id, "user-1", "给张三发消息", "no.such.scenario.v1")
    assert exc_info.value.code == ERR_VALIDATION_FAILED
    assert "不支持绑定管理" in str(exc_info.value)


class TestParseSpecEndpoint:
    @pytest.fixture()
    def client(self, monkeypatch, tenant_id):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.session_tasks import api as session_tasks_api

        app = FastAPI()
        app.include_router(session_tasks_api.router)

        @app.middleware("http")
        async def _inject_tenant(request, call_next):  # noqa: ANN202
            tid = request.headers.get("X-Tenant-Id")
            if tid:
                request.state.tenant_id = tid
            return await call_next(request)

        monkeypatch.setattr(session_tasks_api, "_get_current_user_sync", lambda request: {"user_id": "user-1"})
        with TestClient(app) as tc:
            yield tc
    def test_parse_spec_endpoint(self, client, tenant_id, pending_binding, mock_llm):
        mock_llm["payload"] = _llm_payload(binding_ids=[pending_binding["conversation_binding_id"]])
        resp = client.post("/api/session-tasks/parse-spec",
                           json={"text": "给张三介绍新版，聊3轮"},
                           headers={"X-Tenant-Id": tenant_id})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["missing"] == []
        assert data["binding_candidates"][0]["id"] == pending_binding["conversation_binding_id"]

    def test_parse_spec_empty_text_400(self, client, tenant_id):
        resp = client.post("/api/session-tasks/parse-spec", json={"text": ""},
                           headers={"X-Tenant-Id": tenant_id})
        assert resp.status_code == 400
        assert resp.json()["code"] == ERR_VALIDATION_FAILED


class TestListTasksResilience:
    def test_corrupt_text_degrades_single_row(self, tenant_id, pending_binding):
        """单条 spec 密文损坏：列表仍 200，坏行 projection_degraded，好行不受影响。"""
        from src.db.database import get_db_connection

        payload = TaskDraftCreatePayload.model_validate(build_draft_payload(pending_binding))
        created = service.create_draft(tenant_id, "user-1", payload)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_texts SET encrypted_payload='not-a-valid-token' "
                "WHERE tenant_id=%s AND task_id=%s",
                (tenant_id, created["task_id"]),
            )
            conn.commit()
        # 再建一条好任务（换个绑定避免会话占用冲突）
        from tests.unit.session_tasks.conftest import make_verified_binding

        good_binding = make_verified_binding(tenant_id, pending_binding["device_id"], conv_type="group")
        good_payload = TaskDraftCreatePayload.model_validate(build_draft_payload(good_binding))
        good = service.create_draft(tenant_id, "user-1", good_payload)

        result = service.list_tasks(tenant_id, "user-1")
        by_id = {str(item["id"]): item for item in result["items"]}
        assert str(created["task_id"]) in by_id and by_id[str(created["task_id"])]["projection_degraded"] is True
        assert by_id[str(created["task_id"])]["goal_summary"] == "历史数据不可读"
        assert str(good["task_id"]) in by_id and "projection_degraded" not in by_id[str(good["task_id"])]
        assert by_id[str(good["task_id"])]["goal_summary"].startswith("确认对方")
