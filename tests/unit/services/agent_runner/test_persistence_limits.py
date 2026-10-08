"""持久化字节上限：capped dumps、入口 413 拒绝与配置形态。"""

from datetime import datetime

import pytest

from src.config.settings import AgentRunnerConfig, AgentRunnerLimitsConfig
from src.core.agent_engine.contracts import CheckpointFailure
from src.services.agent_runner import persistence_limits
from src.services.agent_runner.contracts import Principal, RunnerError, RunnerSubmit
from src.services.agent_runner.control_contracts import RunnerControl
from src.services.agent_runner.control_repository import ControlRepository
from src.services.agent_runner.input_repository import InputRepository
from src.core.agent_engine.contracts import Identity

pytestmark = pytest.mark.unit


class TestDefaultsAndConfig:
    def test_defaults_are_wide_for_existing_large_sessions(self):
        limits = AgentRunnerLimitsConfig()
        assert limits.request_bytes == 2 * 1024 * 1024
        assert limits.checkpoint_bytes == 8 * 1024 * 1024
        assert limits.snapshot_bytes == 2 * 1024 * 1024

    def test_config_accepts_explicit_limits_and_rejects_below_floor(self):
        config = AgentRunnerConfig.model_validate(
            {"limits": {"checkpoint_bytes": 65536, "snapshot_bytes": 65536}})
        assert config.limits.checkpoint_bytes == 65536
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentRunnerLimitsConfig(checkpoint_bytes=65535)
        with pytest.raises(ValidationError):
            AgentRunnerLimitsConfig(request_bytes=1023)


class TestCappedDumps:
    def test_under_limit_returns_the_serialization_reused_as_sql_parameter(self):
        value = {"execution": {"messages": [{"role": "user", "content": "小"}]}}
        import json
        assert (persistence_limits.capped_checkpoint_dumps(value)
                == json.dumps(value, ensure_ascii=False))
        assert (persistence_limits.capped_snapshot_dumps(value)
                == json.dumps(value, ensure_ascii=False))

    def test_oversized_checkpoint_raises_checkpoint_failure(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "checkpoint_bytes", 128)
        with pytest.raises(CheckpointFailure) as error:
            persistence_limits.capped_checkpoint_dumps({"blob": "x" * 256})
        assert "CHECKPOINT_TOO_LARGE" in str(error.value)

    def test_oversized_snapshot_raises_checkpoint_failure(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "snapshot_bytes", 128)
        with pytest.raises(CheckpointFailure) as error:
            persistence_limits.capped_snapshot_dumps({"progressMessages": ["x" * 256]})
        assert "SNAPSHOT_TOO_LARGE" in str(error.value)


def settings_limits():
    from src.config.settings import settings
    return settings.agent_runner.limits


class TestAdmissionGate:
    def test_request_intent_over_limit_is_rejected_with_413(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "request_bytes", 256)
        request = RunnerSubmit(client_request_id="k", text="x" * 512,
                               session={"kind": "web", "session_id": "s"})
        with pytest.raises(RunnerError) as error:
            persistence_limits.assert_intent_within_request_limit(
                request.intent(), code="REQUEST_TOO_LARGE")
        assert error.value.status == 413 and error.value.code == "REQUEST_TOO_LARGE"

    def test_control_intent_uses_its_own_code(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "request_bytes", 256)
        control = RunnerControl(client_request_id="k", action="reply",
                                target_execution_id="e", wait_id="w", answer="y" * 512)
        with pytest.raises(RunnerError) as error:
            persistence_limits.assert_intent_within_request_limit(
                control.intent(), code="CONTROL_TOO_LARGE")
        assert error.value.status == 413 and error.value.code == "CONTROL_TOO_LARGE"

    def test_intent_bytes_count_serialized_payload_in_utf8(self):
        intent = {"text": "图" * 100}
        assert persistence_limits.intent_bytes(intent) > 300  # 每个汉字 3 字节


class _FakeCursor:
    """按序返回预置行；execute 记录语句用于断言字节闸的位置。"""

    def __init__(self, rows):
        self._rows = list(rows)
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class TestAcceptInTxByteGate:
    def _repository(self):
        return InputRepository(connection_factory=lambda: None)

    def _locator(self):
        from src.services.agent_runner.source_receipts import SourceLocator
        return SourceLocator("wecom_kf", "account", "ns", "m1")

    def test_oversized_source_input_rejected_after_idempotent_lookup(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "request_bytes", 256)
        request = RunnerSubmit(client_request_id="k", text="x" * 512,
                               session={"kind": "web", "session_id": "s"},
                               profile_id="main", source="wecom_kf")
        cursor = _FakeCursor([None])  # find_in_tx: 无既有 receipt
        with pytest.raises(RunnerError) as error:
            self._repository().accept_in_tx(
                cursor, self._locator(), request, {"execution_binding": "b"}, 1,
                Principal(Identity("t", "u", "s", "wecom_kf", "channel"), "user", "u", "svc"),
                repository=object(), fingerprint="f", execution_context={})
        assert error.value.status == 413 and error.value.code == "REQUEST_TOO_LARGE"
        # 只执行了幂等查找一条 SQL，字节闸先于任何写入
        assert len(cursor.statements) == 1

    def test_existing_receipt_returns_before_the_gate(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "request_bytes", 8)
        request = RunnerSubmit(client_request_id="k", text="x" * 512,
                               session={"kind": "web", "session_id": "s"},
                               profile_id="main", source="wecom_kf")
        receipt = {"input_ref": "i", "phase": "accepted", "accepted_runner_id": "r",
                   "current_runner_id": "r", "provenance": {"execution_binding": "b"},
                   "intent_digest": request.digest()}
        cursor = _FakeCursor([receipt])
        response = self._repository().accept_in_tx(
            cursor, self._locator(), request, {"execution_binding": "b"}, 1,
            Principal(Identity("t", "u", "s", "wecom_kf", "channel"), "user", "u", "svc"),
            repository=object(), fingerprint="f", execution_context={})
        assert response["input_ref"] == "i" and not response["created"]
        # 已受理输入的幂等重放只做一次查找，不受字节闸影响
        assert len(cursor.statements) == 1


class TestControlSubmitByteGate:
    def test_oversized_control_rejected_before_further_writes(self, monkeypatch):
        monkeypatch.setattr(settings_limits(), "request_bytes", 256)
        request = RunnerControl(client_request_id="k", action="reply",
                                target_execution_id="runner_x", wait_id="w1",
                                answer="y" * 512)
        now = datetime(2026, 10, 4, 12, 0, 0)
        row = {"runner_id": "runner_x", "tenant_id": "t", "scope_key": "tenant:t",
               "session_kind": "web", "session_id": "s", "actor_kind": "user",
               "actor_id": "u", "user_id": "u", "source": "chat", "revision": 3,
               "status": "waiting", "cancel_requested": False,
               "pause_requested": False, "resume_control_id": None,
               "lease_until": None, "attempt": 1, "worker_id": None}
        cursor = _FakeCursor([row, {"database_now": now}, None])
        principal = Principal(Identity("t", "u", "s", "chat", "web"), "user", "u", "svc")
        with pytest.raises(RunnerError) as error:
            ControlRepository(connection_factory=lambda: None).submit_in_tx(
                cursor, principal, "runner_x", request)
        assert error.value.status == 413 and error.value.code == "CONTROL_TOO_LARGE"
        # runner 锁 + 幂等查找后、任何 INSERT/UPDATE 前拒绝
        assert len(cursor.statements) == 3
