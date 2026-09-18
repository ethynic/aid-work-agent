"""Submission acknowledges a scoped command, never verified delivery."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.desktop_automation import runs
from src.local_tools import operation_result as results
from src.weixin_conversation import adapters


def check(monkeypatch, **changes):
    adapter = adapters.WeixinConversationAdapter()
    monkeypatch.setattr(results.TrustedAdapterRegistry, "get", lambda _: adapter)
    # B1.2：submitted 接纳白名单移到场景描述器 receipt_policy（值与原硬编码逐字一致）
    from src.session_tasks import scenario_descriptor as sd_module
    from src.weixin_conversation.descriptor import build_weixin_descriptor

    monkeypatch.setattr(sd_module, "get_descriptor", lambda _key: build_weixin_descriptor())
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": "registered"}
    args = {"receipt_mode": "submission", "receipt_context": "weixin_name", "target_ref": "target", "payload_hash": "hash"}
    values = dict(tenant_id="tenant", user_id="owner", scenario_key="weixin.conversation.v1",
                  evidence_ref="weixin-submission:request:1", invocation={"id": "inv", "device_id": "device", "tool_name": "weixin_message_send_v2", "execution_lane": "session_task"},
                  attempt={"id": "attempt", "request_id": "request"}, delivery={"target_ref": "target", "payload_hash": "hash"},
                  args=args, request_id="request", effect="applied", phase="submitted")
    values.update(changes)
    return results._check_evidence_and_register(cursor, **values), cursor


def test_submission_registers_bound_command_evidence(monkeypatch):
    reason, cursor = check(monkeypatch)
    assert reason is None
    assert cursor.execute.call_args.args[1][-2:] == ("applied", "submitted")


@pytest.mark.parametrize("changes", [
    {"scenario_key": "weixin.marketing.v1"},
    {"args": {"target_ref": "target", "payload_hash": "hash"}},
    {"evidence_ref": "weixin-evidence:request:1"},
    {"evidence_ref": "weixin-submission:other:1"},
    {"delivery": {"target_ref": "other", "payload_hash": "hash"}},
    {"delivery": {"target_ref": "target", "payload_hash": "other"}},
    {"attempt": {"id": "attempt", "request_id": "other"}},
    {"invocation": {"id": "inv", "device_id": "device", "tool_name": "other", "execution_lane": "session_task"}},
])
def test_submission_cannot_bypass_scope_or_bindings(monkeypatch, changes):
    reason, cursor = check(monkeypatch, **changes)
    assert reason is not None
    cursor.execute.assert_not_called()


def test_verified_namespace_stays_separate(monkeypatch):
    reason, _ = check(monkeypatch, phase="verified")
    assert reason == "adapter_rejected"
    reason, _ = check(monkeypatch, phase="verified", evidence_ref="weixin-evidence:request:1")
    assert reason is None


def test_only_valid_name_context_gets_frozen_submission_policy(monkeypatch):
    ctx = SimpleNamespace(tenant_id="tenant")
    adapter = adapters.WeixinConversationAdapter()
    binding = {"verifier_version": "current-login-name-v1", "verification_status": "resolved", "identity_version": 1,
               "expires_at": datetime.now(timezone.utc) + timedelta(minutes=2)}
    monkeypatch.setattr(adapters, "load_conversation_binding", lambda *_: binding)
    assert adapter.invocation_receipt_arguments(ctx, "target") == {"receipt_mode": "submission", "receipt_context": "weixin_name"}
    binding["verifier_version"] = "legacy"
    assert adapter.invocation_receipt_arguments(ctx, "target") == {}


def test_submitted_aggregation_does_not_claim_delivery():
    delivery = results._map_delivery_outcome("applied", "submitted")
    assert delivery == {"state": "succeeded", "effect": "applied", "phase": "submitted"}
    assert runs.compute_run_terminal_state([delivery]) == "succeeded"
    assert runs.compute_run_terminal_state([delivery, {"state": "unknown", "effect": "unknown"}]) == "partial"
    assert not runs.delivery_is_success({**delivery, "state": "unknown"})
    assert adapters.WeixinConversationAdapter().aggregate_result(None, [delivery]).verdict == "reply_submitted"
