"""Independent Runner acceptance: real recovery policy, scripted desktop boundary only."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.local_tools.boss_visual_recovery import page_context, is_page_failure, recover_page
from src.local_tools.overlay_flow import _known
from src.local_tools.durable_flow import LocalContinuationRequired
from src.config.settings import settings

pytestmark = pytest.mark.unit


def original(name="boss_open_detail"):
    return {"success": False, "code": "EXECUTION_UNKNOWN", "effect": "unknown", "invocation_id": "original-invocation", "data": {
        "page_operation": {"schema_version": 1,
            "operation": {"name": name, "goal": "打开原候选人详情", "expected": "画布和右侧操作列完整可见", "result_kind": "page_state"},
            "execution": {"stage": "input", "input_status": "unknown", "attempts_used": 1, "ordinary_retries_used": 0},
            "verification": {"status": "inconclusive", "evidence": ["输入结果未知"]}}}}


def proxy(name="boss_open_detail"):
    return SimpleNamespace(name=name, heal_eligible=True)


def visual(*, obstruction="present", goal="not_satisfied", action=True, ref=True, receipt="visual-fee"):
    return {"success": True, "data": {**({"recovery_ref": "stored-close-reference"} if ref else {}), "visual": {
        "http_status": 200, "state": "completed", "receipt": {"id": receipt, "factor": 20},
        "diagnosis": {"obstruction": obstruction, "goal_state": goal, "proposed_action": {"kind": "dismiss"} if action else None},
        **({"action_error": "INVALID_MODEL_ACTION"} if not action else {})}}}


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, "overlay_heal_enabled", True)


def test_only_allowlisted_versioned_page_tool_may_pass_unknown_for_diagnosis():
    result = original()
    assert _known(result, proxy()) is result
    for name in ["boss_greet", "boss_greet_detail", "boss_send_to", "boss_select_job"]:
        candidate = original(name)
        assert not is_page_failure(proxy(name), candidate)
        with pytest.raises(LocalContinuationRequired):
            _known(candidate, proxy(name))


@pytest.mark.parametrize("change", [
    lambda f: f.update(schema_version=True),
    lambda f: f["operation"].update(name="boss_greet"),
    lambda f: f["operation"].update(result_kind="business_result"),
    lambda f: f["execution"].update(attempts_used=True),
    lambda f: f["execution"].update(attempts_used=3),
    lambda f: f["execution"].update(ordinary_retries_used=1),
    lambda f: f["verification"].update(status="failed"),
    lambda f: f["verification"].update(evidence=["x" * 501]),
])
def test_malformed_or_mismatched_page_facts_never_enable_replay(change):
    result = original(); change(result["data"]["page_operation"])
    assert page_context(proxy(), result) is None


async def test_invalid_action_keeps_diagnosis_but_never_dispatches_input():
    dispatch = AsyncMock(return_value=visual(action=False, ref=False))
    result = await recover_page(proxy(), original(), dispatch)
    assert dispatch.await_count == 1
    assert result["data"]["visual_recovery"]["state"] == "no_supported_action"
    assert result["data"]["visual_recovery"]["receipts"] == [{"id": "visual-fee", "factor": 20}]


@pytest.mark.parametrize("post", [
    visual(obstruction="present", goal="not_satisfied"),
    visual(obstruction="absent", goal="inconclusive"),
    {"success": False, "data": {"visual": {"http_status": 409, "state": "model_unknown"}}},
])
async def test_postcheck_negative_or_unknown_never_resumes_the_original_goal(post):
    dispatch = AsyncMock(side_effect=[visual(), {"success": True}, post])
    result = await recover_page(proxy(), original(), dispatch)
    assert [call.args[0] for call in dispatch.await_args_list] == ["boss_overlay_inspect", "boss_overlay_dismiss", "boss_overlay_inspect"]
    assert not result["success"]
    post_context = dispatch.await_args_list[2].args[2]["visual_context"]
    assert post_context["purpose"] == "verify" and post_context["available_actions"] == []
    assert post_context["verification"]["status"] == "inconclusive"
    assert "error" not in post_context


async def test_successful_recovery_uses_stored_action_then_two_receipts_and_exact_original_dispatch():
    resumed = {"success": True, "effect": "none", "data": {"opened": True}}
    dispatch = AsyncMock(side_effect=[visual(receipt="diagnose"), {"success": True},
        visual(obstruction="absent", goal="satisfied", action=False, ref=False, receipt="verify"), resumed])
    result = await recover_page(proxy(), original(), dispatch)
    assert dispatch.await_args_list[1].args == ("boss_overlay_dismiss", "dismiss", {"recovery_ref": "stored-close-reference"})
    assert dispatch.await_args_list[3].args == ("boss_open_detail", "resume", None)
    assert result["success"] and result["data"]["opened"]
    assert [r["id"] for r in result["data"]["visual_recovery"]["receipts"]] == ["diagnose", "verify"]
    assert result["data"]["visual_recovery"]["state"] == "recovered"


async def test_same_original_has_deterministic_request_ids_and_no_mutation():
    source = original(); saved = deepcopy(source)
    first, second = AsyncMock(return_value=visual(action=False, ref=False)), AsyncMock(return_value=visual(action=False, ref=False))
    await recover_page(proxy(), source, first); await recover_page(proxy(), source, second)
    assert first.await_args.args[2]["request_id"] == second.await_args.args[2]["request_id"]
    assert source == saved


async def test_disabled_healing_preserves_original_without_visual_fee(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, "overlay_heal_enabled", False)
    source = original(); dispatch = AsyncMock()
    assert await recover_page(proxy(), source, dispatch) is source
    dispatch.assert_not_awaited()


async def test_actual_proxy_roundtrip_keeps_original_device_arguments_and_has_no_legacy_heal_fee(monkeypatch):
    from src.local_tools import repository
    from src.local_tools.proxy_tool import BossOpenDetailTool, LocalToolProxyTool, ClientUsageLogDB

    original_result = original()
    results = [original_result, visual(receipt="diagnose"), {"success": True, "data": {"input_status": "attempted"}},
               visual(obstruction="absent", goal="satisfied", action=False, ref=False, receipt="verify"),
               {"success": True, "effect": "none", "data": {"opened": True}}]
    rows = {}
    for i, result in enumerate(results):
        rows[str(i)] = {"id": str(i), "state": "succeeded" if result["success"] else "failed",
            "effect": result.get("effect", "none"), "result_json": result,
            "error_code": result.get("code"), "error_message": "fixture failure"}
    identifiers = iter(rows)
    create = MagicMock(side_effect=lambda *args, **kwargs: next(identifiers))
    monkeypatch.setattr(repository, "create_invocation", create)
    monkeypatch.setattr(repository, "get_invocation", lambda identifier, tenant: rows[identifier])
    monkeypatch.setattr(repository, "list_events", lambda *_: [])
    device = {"id": "original-device"}
    monkeypatch.setattr(LocalToolProxyTool, "_find_ready_device", AsyncMock(return_value=(device, None)))
    charge = MagicMock()
    monkeypatch.setattr(ClientUsageLogDB, "record_tool_usage", charge)
    result = await BossOpenDetailTool().execute(name="原候选人", _trusted_tenant_id="tenant", _trusted_user_id="user", _session_id="session")
    assert result["success"] and result["data"]["opened"]
    assert [call.args[3] for call in create.call_args_list] == ["boss_open_detail", "boss_overlay_inspect", "boss_overlay_dismiss", "boss_overlay_inspect", "boss_open_detail"]
    assert all(call.args[2] == "original-device" for call in create.call_args_list)
    assert create.call_args_list[0].args[4] == create.call_args_list[-1].args[4] == {"name": "原候选人"}
    charge.assert_not_called()


async def test_pending_diagnosis_retrieves_same_request_once_without_another_observation():
    pending = {"success": True, "data": {"visual": {"state": "pending", "http_status": 202}}}
    dispatch = AsyncMock(side_effect=[pending, visual(action=False, ref=False)])
    result = await recover_page(proxy(), original(), dispatch)
    first, second = dispatch.await_args_list
    assert first.args[2]["request_id"] == second.args[2]["request_id"]
    assert second.args == ("boss_overlay_inspect", "diagnose.retrieve", {"request_id": first.args[2]["request_id"], "retrieve_only": True})
    assert result["data"]["visual_recovery"]["observations"][0]["diagnosis"]["obstruction"] == "present"


async def test_invalid_action_does_not_drop_correct_page_assessment_from_final_result():
    dispatch = AsyncMock(return_value=visual(action=False, ref=False))
    result = await recover_page(proxy(), original(), dispatch)
    observation = result["data"]["visual_recovery"]["observations"][0]
    assert observation["diagnosis"]["obstruction"] == "present"
    assert observation["action_error"] == "INVALID_MODEL_ACTION"


async def test_unknown_close_response_is_read_only_verified_with_truthful_input_context():
    dispatch = AsyncMock(side_effect=[visual(), {"success": False, "code": "EXECUTION_UNKNOWN"},
        visual(obstruction="unknown", goal="not_satisfied", action=False, ref=False)])
    result = await recover_page(proxy(), original(), dispatch)
    assert dispatch.await_count == 3
    context = dispatch.await_args_list[2].args[2]["visual_context"]
    assert context["execution"]["input_status"] == "unknown"
    assert all("正常返回" not in evidence for evidence in context["verification"]["evidence"])
    assert result["data"]["visual_recovery"]["state"] == "postcheck_inconclusive"


async def test_durable_saved_visual_close_unknown_can_reach_read_only_postcheck(monkeypatch):
    from datetime import datetime, timezone
    from src.local_tools.overlay_flow import poll_operation
    from src.local_tools.lifecycle import LocalOperationScope
    from src.local_tools.proxy_tool import BossOverlayDismissTool
    args = {"recovery_ref": "stored-close-reference"}
    owner = SimpleNamespace(saved_phase=AsyncMock(return_value={"request": {
        "tool_name": "boss_overlay_dismiss", "device_id": "original-device", "arguments": args},
        "invocation": {"id": "close-invocation", "deadline_at": datetime.now(timezone.utc)}}))
    close = BossOverlayDismissTool()
    unknown = {"success": False, "code": "EXECUTION_UNKNOWN", "effect": "none", "invocation_id": "close-invocation"}
    monkeypatch.setattr(close, "_poll_invocation", AsyncMock(return_value=unknown))
    result = await poll_operation(close, LocalOperationScope(owner, "heal.visual.dismiss", 0), args,
        tenant_id="tenant", user_id="user", session_id="session", progress_queue=None, expected_device_id="original-device", allow_visual_input_unknown=True)
    assert result is unknown
    # This exception is a readback permission for the saved recovery branch, not
    # permission to replay an ordinary unknown close or any external business write.
    with pytest.raises(LocalContinuationRequired):
        await poll_operation(close, LocalOperationScope(owner, "main", 0), args,
            tenant_id="tenant", user_id="user", session_id="session", progress_queue=None, expected_device_id="original-device")


async def test_actual_durable_runner_routes_unknown_recovery_input_to_readback_on_same_device(monkeypatch):
    from datetime import datetime, timezone
    from src.local_tools.overlay_flow import run_base
    from src.local_tools.lifecycle import LocalOperationScope, current_local_operation
    from src.local_tools.proxy_tool import BossOpenDetailTool, LocalToolProxyTool

    source = original(); args = {"name": "原候选人"}
    original_phase = {"request": {"tool_name": "boss_open_detail", "arguments": args, "device_id": "original-device"},
        "invocation": {"id": source["invocation_id"], "deadline_at": datetime.now(timezone.utc)}}
    owner = SimpleNamespace(saved_phase=AsyncMock(side_effect=lambda **kw: original_phase if kw["branch"] == "main" else None))
    monkeypatch.setattr(LocalToolProxyTool, "_poll_invocation", AsyncMock(return_value=source))
    monkeypatch.setattr(LocalToolProxyTool, "_find_ready_device", AsyncMock(return_value=({"id": "original-device"}, None)))
    replies = iter([visual(), {"success": False, "code": "EXECUTION_UNKNOWN", "effect": "none"},
        visual(obstruction="absent", goal="satisfied", action=False, ref=False),
        {"success": True, "data": {"opened": True}, "effect": "none"}])
    calls = []
    async def dispatched(self, tenant, user, session, device, arguments, progress):
        calls.append((self.name, current_local_operation().branch, device["id"], arguments))
        return next(replies)
    monkeypatch.setattr(LocalToolProxyTool, "_dispatch_and_wait", dispatched)
    result = await run_base(BossOpenDetailTool(), LocalOperationScope(owner, "main", 0),
        {**args, "_trusted_tenant_id": "tenant", "_trusted_user_id": "user", "_session_id": "session"})
    assert result["success"] and result["data"]["opened"]
    assert [(name, branch) for name, branch, _, _ in calls] == [
        ("boss_overlay_inspect", "heal.visual.diagnose"), ("boss_overlay_dismiss", "heal.visual.dismiss"),
        ("boss_overlay_inspect", "heal.visual.postcheck"), ("boss_open_detail", "heal.visual.resume")]
    assert all(device == "original-device" for _, _, device, _ in calls)
    assert calls[2][3]["visual_context"]["execution"]["input_status"] == "unknown"
    assert calls[-1][3] == args
