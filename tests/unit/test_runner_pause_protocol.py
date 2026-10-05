"""Stopped owned trees must never hide an unresolved dispatched effect."""
import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
from src.services.agent_runner.pause_protocol import safe_paused_tree


@pytest.mark.parametrize('outcome', [Outcome.FAILED, Outcome.CANCELLED])
@pytest.mark.parametrize('phase,expected', [('prepared', True), ('completed', True), ('dispatching', False), ('waiting', False)])
def test_stopped_child_safe_pause_keeps_unknown_effect_blocking(outcome, phase, expected):
    parent = ExecutionState(Identity('fixture-tenant', 'fixture-user', 'fixture-session'),
                            'fixture-root', AgentMode.MASTER, 'fixture', [], outcome=Outcome.PAUSED)
    child = ExecutionState(parent.identity, 'fixture-child', AgentMode.SUBAGENT, 'fixture', [], outcome=outcome)
    child.tools['fixture-write'] = ToolFact(ToolCall('fixture-write', 'write', {'file_path': 'fictional.txt'}), phase=phase)
    parent.children['fixture-delegate'] = {'execution_id': child.execution_id, 'checkpoint': child.checkpoint()}
    original = parent.checkpoint()
    assert safe_paused_tree(parent) is expected
    assert parent.checkpoint() == original
