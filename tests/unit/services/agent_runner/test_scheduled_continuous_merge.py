"""Scheduled 装配与持续任务恢复不能在 master 整合时互相覆盖。"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.core.agent_engine.contracts import Identity, Outcome
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.ownership import StopRequested
from src.services.agent_runner.runtime.child import ChildExecution
from src.services.agent_runner.worker import RunnerWorker
from src.tools.control_set import ControlToolDependencies, ToolControlSet


@pytest.mark.parametrize("non_interactive", [False, True])
def test_sleep_stays_visible_when_scheduled_filters_only_interactive_controls(non_interactive):
    registry = SimpleNamespace(get_skill_tool_definition=lambda: {"name": "use_skill"})
    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=object(), skill_registry=registry, skill_executor=object(),
        tool_registry=object(), non_interactive=non_interactive))
    names = {item["name"] for item in controls.definitions(available_subagents=[])}
    assert "agent_sleep" in names and controls.get("agent_sleep") is not None
    assert ("ask_user" in names) is not non_interactive
    assert (controls.get("ask_user") is not None) is not non_interactive


@pytest.mark.asyncio
async def test_scheduled_child_keeps_augmentations_when_admission_parks_before_dispatch():
    child = object.__new__(ChildExecution)
    child.ports_factory = None
    child.restored_state = None
    child.prompt_augmentations = ("本次为定时自动执行，无法向用户提问",)
    state = SimpleNamespace(outcome=Outcome.RUNNING, resources={})
    control = SimpleNamespace(save=AsyncMock())
    requests = []
    stopped = StopRequested("pause")
    stopped.credit_blocked = True

    async def run(text, **kwargs):
        requests.append(kwargs["request_context"])
        raise stopped
        yield  # pragma: no cover

    child.runtime = SimpleNamespace(identity=Identity("t", "u", "s", "scheduled", "web"),
        run=run, state=state, control=control)
    with pytest.raises(StopRequested) as error:
        await child.execute_as_subagent("生成日报", "s")
    assert error.value is stopped
    assert requests[0].prompt_augmentations == child.prompt_augmentations
    assert state.outcome == Outcome.PAUSED and state.resources["credit_blocked"] is True
    control.save.assert_awaited_once_with(state, "pause_pre_dispatch")


def test_continuous_finalizer_returns_public_cost_without_loading_receipt_history(monkeypatch):
    from src.services.agent_runner import finalizer as module
    from src.services.agent_runner import usage_pricing

    previous = {"version": 1, "groups": {}}
    cursor = Mock()
    cursor.fetchone.side_effect = [
        {"usage_breakdown": {"runner_accumulator": previous}}, {"pending": True}]
    observed = Mock(return_value=[])
    monkeypatch.setattr(module.UsageRepository, "observed_in_tx", observed)
    monkeypatch.setattr(module.UsageRepository, "facts_in_tx",
        Mock(side_effect=AssertionError("持续结算不能重新载入所有历史 receipts")))
    price = Mock(return_value=(Decimal("1.25"), {}, {}))
    monkeypatch.setattr(usage_pricing, "accumulate_cost", price)
    monkeypatch.setattr(module, "settle_record", Mock(return_value=Decimal(".25")))
    row = {"runner_id": "r", "record_id": "record",
        "checkpoint": {"continuous": {"version": 1, "enabled": True}}}
    result = RunnerFinalizer()._settle(cursor, row, {"status": "completed"})
    assert result == ("pending", Decimal(".25"), Decimal("1.25"))
    observed.assert_called_once_with(cursor, "r", unapplied_only=True)
    price.assert_called_once_with([], previous)


def test_failed_attempt_trace_keeps_error_and_never_copies_cumulative_credits():
    worker = object.__new__(RunnerWorker)
    worker.repository = SimpleNamespace(get=lambda _: {
        "attempt": 3, "status": "failed", "record_id": "stable-record",
        "result": {"error_code": "ITERATION_LIMIT"},
        "checkpoint": {"execution": {"iteration": 999}}})
    trace = SimpleNamespace(trace_id="tr_r:3", error_message=None, metadata={},
        spans=[SimpleNamespace(name="llm_call", usage={"prompt_tokens": 4, "completion_tokens": 2})])
    collector = SimpleNamespace(trace=trace, on_complete=MagicMock())
    control = SimpleNamespace(attempt=SimpleNamespace(number=3), snapshot={})
    worker._persist_trace(collector, "r", control)
    assert trace.error_message == "ITERATION_LIMIT"
    assert trace.total_tokens == 6 and trace.agent_iterations == 1
    assert trace.total_cost == 0 and trace.metadata["cost_scope"] == "chat_record_only"
    collector.on_complete.assert_called_once_with(None, persist_sync=True)
