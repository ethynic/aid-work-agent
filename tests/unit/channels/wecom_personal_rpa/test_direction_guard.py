"""Agent 前方向二次防线测试。"""

from unittest.mock import MagicMock, patch
import os

import pytest

from src.channels.wecom_personal_rpa.schemas import RpaCallbackEnvelope
from src.saas.api.wecom_personal_rpa_routes import _process_inbound_message, _record_self_echo_escape

os.environ.setdefault("RPA_SECRET_KEY", "test-rpa-audit-key-32-bytes-minimum")


def _env(sender: str, **extra):
    raw = {
        "event_id": "msg_guard",
        "client_id": "client_1",
        "account_id": "account_1",
        "event_type": "message",
        "occurred_at": "2026-07-13T00:00:00+00:00",
        "payload": {
            "conversation_id": "dm:peer_1",
            "conversation_type": "external_user",
            "sender_display_name": "peer",
            "sender_stable_id": sender,
            "message_type": "text",
            "text": "hello",
            **extra,
        },
    }
    return RpaCallbackEnvelope.model_validate(raw), raw


@pytest.mark.asyncio
async def test_guard_filters_self_before_adapter_and_agent_side_effects():
    env, raw = _env("self_1")
    fake_db = MagicMock()
    fake_db.get_account_for_tenant.return_value = {
        "id": "account_1", "tenant_id": "tenant_1",
        "wecom_user_id": "self_1", "wecom_user_aliases": [],
    }
    with patch("src.saas.api.wecom_personal_rpa_routes.db", fake_db), patch(
        "src.saas.api.wecom_personal_rpa_routes._record_self_echo_escape"
    ) as record_escape:
        await _process_inbound_message("tenant_1", env, raw, source="server_fetcher")

    fake_db.write_audit.assert_called_once()
    assert fake_db.write_audit.call_args.kwargs["category"] == "self_echo_escaped"
    record_escape.assert_called_once()


@pytest.mark.asyncio
async def test_guard_audit_failure_still_records_danger_and_filters():
    env, raw = _env("self_1")
    fake_db = MagicMock()
    fake_db.get_account_for_tenant.return_value = {
        "id": "account_1", "tenant_id": "tenant_1",
        "wecom_user_id": "self_1", "wecom_user_aliases": [],
    }
    fake_db.write_audit.side_effect = RuntimeError("audit unavailable")
    with patch("src.saas.api.wecom_personal_rpa_routes.db", fake_db), patch(
        "src.saas.api.wecom_personal_rpa_routes._record_self_echo_escape"
    ) as record_escape:
        await _process_inbound_message("tenant_1", env, raw, source="server_fetcher")

    record_escape.assert_called_once_with("tenant_1", "account_1", "msg_guard")


@pytest.mark.asyncio
async def test_server_fetcher_guard_requires_direction_metadata():
    env, raw = _env("peer_1")
    fake_db = MagicMock()
    fake_db.get_account_for_tenant.return_value = {
        "id": "account_1", "tenant_id": "tenant_1",
        "wecom_user_id": "self_1", "wecom_user_aliases": [],
    }
    with patch("src.saas.api.wecom_personal_rpa_routes.db", fake_db):
        await _process_inbound_message("tenant_1", env, raw, source="server_fetcher")

    assert fake_db.write_audit.call_args.kwargs["category"] == "direction_guard_filtered"


def test_echo_circuit_window_deduplicates_same_event():
    fake_redis = MagicMock()
    fake_redis.is_available.return_value = True
    fake_redis.zcard.return_value = 1
    with patch("src.saas.api.wecom_personal_rpa_routes.redis_client", fake_redis), patch(
        "src.saas.api.wecom_personal_rpa_routes.time.time", side_effect=[1000.0, 1001.0]
    ):
        _record_self_echo_escape("tenant_1", "account_1", "same_event")
        _record_self_echo_escape("tenant_1", "account_1", "same_event")

    first_member = fake_redis.zadd.call_args_list[0].args[1]
    second_member = fake_redis.zadd.call_args_list[1].args[1]
    assert set(first_member) == set(second_member)


def test_echo_circuit_open_is_audited_only_after_atomic_state_change():
    fake_redis = MagicMock()
    fake_redis.is_available.return_value = True
    fake_redis.zcard.return_value = 3
    fake_db = MagicMock()
    fake_db.get_account_for_tenant.return_value = {"client_id": "client_1"}
    fake_db.open_self_echo_circuit.side_effect = [True, False]
    with patch("src.saas.api.wecom_personal_rpa_routes.redis_client", fake_redis), patch(
        "src.saas.api.wecom_personal_rpa_routes.db", fake_db
    ):
        _record_self_echo_escape("tenant_1", "account_1", "event_1")
        _record_self_echo_escape("tenant_1", "account_1", "event_2")

    assert fake_db.open_self_echo_circuit.call_count == 2
    fake_db.write_audit.assert_called_once()


def test_echo_circuit_does_not_use_process_local_fallback():
    fake_redis = MagicMock()
    fake_redis.is_available.return_value = False
    with patch("src.saas.api.wecom_personal_rpa_routes.redis_client", fake_redis):
        _record_self_echo_escape("tenant_1", "account_1", "event_1")

    fake_redis.zadd.assert_not_called()


def test_filtered_audit_hash_failure_is_non_blocking():
    from src.channels.wecom_personal_rpa.archive import audit

    with patch.object(audit, "_hmac_key", side_effect=RuntimeError("missing key")), patch.object(
        audit.rpa_db, "write_audit"
    ) as write_audit:
        audit.log_direction_filtered(
            "tenant_1", "config_1", "account_1", "outbound_self",
            "sender_is_self", "sensitive_sender", "sensitive_peer", "event_1",
        )

    write_audit.assert_called_once()
    payload = write_audit.call_args.kwargs["payload_json"]
    assert "sensitive_sender" not in payload
    assert "sensitive_peer" not in payload
