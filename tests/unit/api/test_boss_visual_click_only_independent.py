"""Independent regression: withdrawing keyboard permission must not erase evidence or bill twice."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from src.services import boss_visual_service as visual

pytestmark = [pytest.mark.unit, pytest.mark.api]


def request(actions=None):
    image = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(image, format="PNG")
    return visual.BossVisualRequestV2.model_validate({
        "api_version": 2, "request_id": str(uuid4()), "context": {"mode": "standalone"},
        "purpose": "diagnose", "operation": {"name": "boss_filter", "goal": "应用筛选",
            "expected": "筛选按钮显示2", "result_kind": "page_state"},
        "execution": {"stage": "postcheck", "input_status": "attempted", "attempts_used": 2,
            "ordinary_retries_used": 1}, "error": None,
        "verification": {"status": "not_satisfied", "evidence": ["筛选数字未变化"]},
        "available_actions": ["dismiss_click"] if actions is None else actions,
        "observation": {"image_base64": base64.b64encode(image.getvalue()).decode(),
            "width": 12, "height": 8, "space": "page_image_px",
            "captured_at": "2026-10-18T03:00:00Z", "after_attempt": 2}})


def assessment(action=None):
    return {"page_summary": "打印预览挡住页面", "goal_state": "not_satisfied", "obstruction": "present",
        "reason": "可见打印预览及取消按钮", "missing_evidence": [], "proposed_action": action}


@pytest.fixture
def harness(monkeypatch):
    rows, settlements, unknown = {}, [], []
    binding = SimpleNamespace(tenant_id="click_only", binding_id="test_binding")

    class Ledger:
        @staticmethod
        def get(tenant, rid):
            return rows.get(rid)

        @staticmethod
        def reserve(tenant, bid, rid, digest, provider, model, price):
            return rows.setdefault(rid, {"binding_id": bid, "status": "reserved", "provider": provider,
                "model": model, "detail": {"input_digest": digest, "price_snapshot": price, "billing_factor": 20}})

        @staticmethod
        def dispatch(tenant, bid, rid):
            row = rows[rid]
            if row["status"] != "reserved":
                return False
            row["status"] = "dispatched"
            return True

        @staticmethod
        def settle(tenant, bid, rid, usage, cost, credits, response):
            settlements.append((rid, deepcopy(usage), cost, credits))
            response["receipt"].update(token_cost_yuan=str(cost), credit_cost=str(credits), factor=20)
            rows[rid]["detail"]["response"] = response
            rows[rid]["status"] = "completed"
            return response

        @staticmethod
        def mark_unknown(tenant, bid, rid):
            unknown.append(rid)
            rows[rid]["status"] = "model_unknown"

    gateway = AsyncMock(return_value={"content": json.dumps(assessment()),
        "usage": {"prompt_tokens": 9000, "completion_tokens": 1000}})
    monkeypatch.setattr(visual, "BossVisualUsageDB", Ledger)
    monkeypatch.setattr(visual.TokenCostPriceDB, "get_by_model_name", lambda _: {
        "input_price_per_m": "0.8", "output_price_per_m": "2.8"})
    monkeypatch.setattr(visual.llm_gateway, "chat_direct", gateway)
    return SimpleNamespace(rows=rows, settlements=settlements, unknown=unknown, binding=binding, gateway=gateway)


@pytest.mark.parametrize("actions", [["press_escape"], ["dismiss_click", "press_escape"], []])
def test_legacy_capabilities_do_not_grant_keyboard_action_in_schema_or_model_prompt(actions):
    req = request(actions)
    prompt = visual.render_prompt_v2(req)
    supported = json.loads(prompt.split("客户端能力：", 1)[1].split("\n", 1)[0])
    schema = json.loads(prompt.split("输出JSON schema：", 1)[1])
    assert supported == [a for a in actions if a == "dismiss_click"]
    assert "press_escape" not in json.dumps(schema)
    assert req.available_actions == actions, "compatibility filtering must not mutate the request identity"


@pytest.mark.parametrize("actions", [["press_escape"], ["dismiss_click"], ["dismiss_click", "press_escape"]])
async def test_old_escape_suggestion_preserves_assessment_usage_and_single_charge(harness, actions):
    req = request(actions)
    harness.gateway.return_value["content"] = json.dumps(assessment({"kind": "press_escape", "target_label": "打印预览"}))
    result = await visual.diagnose_visual(req, harness.binding)
    assert result["diagnosis"] == assessment()
    assert result["action_error"] == "INVALID_MODEL_ACTION"
    assert result["diagnosis_error"] is None
    assert result["receipt"]["factor"] == 20
    assert result["receipt"]["credit_cost"] == "0.20"
    assert await visual.diagnose_visual(req, harness.binding) == result
    assert len(harness.settlements) == 1
    harness.gateway.assert_awaited_once()


async def test_legacy_request_capability_filtered_only_at_model_boundary_not_idempotency_digest(harness):
    req = request(["dismiss_click", "press_escape"])
    before = req.model_dump(mode="json")
    _, digest = visual.image_input(req)
    intent = {"api_version": 2, "context": before["context"],
        "operation": {k: v for k, v in before["operation"].items() if k != "result_kind"},
        "image_sha256": hashlib.sha256(base64.b64decode(before["observation"]["image_base64"])).hexdigest(),
        "space": "page_image_px", "width": 12, "height": 8}
    intent.update({k: v for k, v in before.items() if k not in {
        "request_id", "retrieve_only", "observation", "context", "operation"}})
    intent.update(captured_at=req.observation.captured_at.isoformat(), after_attempt=2)
    assert digest == hashlib.sha256(json.dumps(intent, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    await visual.diagnose_visual(req, harness.binding)
    messages = harness.gateway.call_args.kwargs["messages"]
    model_context = json.loads(messages[1]["content"][0]["text"])
    assert model_context["available_actions"] == ["dismiss_click"]
    assert req.model_dump(mode="json") == before
    assert harness.rows[str(req.request_id)]["detail"]["input_digest"] == digest


async def test_historical_escape_receipt_is_withdrawn_without_mutating_cached_billing_or_recalling_model(harness):
    rid = str(uuid4())
    stored = {"api_version": 2, "request_id": rid, "state": "completed",
        "diagnosis": assessment({"kind": "press_escape", "target_label": "打印预览"}),
        "receipt": {"factor": 50, "credit_cost": "1.23", "usage": {"prompt_tokens": 300}}}
    harness.rows[rid] = {"binding_id": harness.binding.binding_id, "status": "completed",
        "detail": {"response": stored, "billing_factor": 50}}
    pristine = deepcopy(harness.rows[rid])
    req = visual.BossVisualRequestV2(api_version=2, request_id=rid, retrieve_only=True)
    for _ in range(2):
        result = await visual.diagnose_visual(req, harness.binding)
        assert result["diagnosis"] == assessment()
        assert result["action_error"] == "INVALID_MODEL_ACTION"
        assert result["receipt"] == stored["receipt"]
    assert harness.rows[rid] == pristine
    assert not harness.settlements
    harness.gateway.assert_not_awaited()


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_unknown_or_cancelled_provider_never_replays_or_settles_as_free_success(harness, failure):
    req = request()
    harness.gateway.side_effect = failure()
    with pytest.raises(asyncio.CancelledError if failure is asyncio.CancelledError else HTTPException):
        await visual.diagnose_visual(req, harness.binding)
    assert harness.unknown == [str(req.request_id)]
    retry = visual.BossVisualRequestV2(api_version=2, request_id=req.request_id, retrieve_only=True)
    with pytest.raises(HTTPException) as error:
        await visual.diagnose_visual(retry, harness.binding)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "MODEL_UNKNOWN"
    assert not harness.settlements
    harness.gateway.assert_awaited_once()
