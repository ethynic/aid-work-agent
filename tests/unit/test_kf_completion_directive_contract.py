"""Kernel compatibility only; not KF ACK/proof or physical IO evidence.

Uses the original Engine and its declared model/tool boundary ports. The
socket/PG/Worker transfer risk independently proves the trusted producer.
"""
from copy import deepcopy

import pytest


def test_original_checkpoint_without_directive_restores_none_and_unknown_directive_is_rejected():
    from src.core.agent_engine.contracts import ExecutionState, Identity, AgentMode, ToolCall, ToolFact
    current = ExecutionState(Identity('fixture_tenant', None, 'fixture_sid'),
        'fixture_execution', AgentMode.STANDALONE, 'Fixture kernel contract', [])
    current.tools['fixture_call'] = ToolFact(ToolCall('fixture_call', 'read', {}))
    legacy = current.checkpoint()
    legacy.pop('terminal_directive')
    legacy['tools']['fixture_call'].pop('terminal_directive')
    restored = ExecutionState.restore(legacy)
    assert restored.terminal_directive is None
    assert restored.tools['fixture_call'].terminal_directive is None
    for placement in ('root', 'tool'):
        invalid = deepcopy(legacy)
        target = invalid if placement == 'root' else invalid['tools']['fixture_call']
        target['terminal_directive'] = 'untrusted_unknown_directive'
        with pytest.raises(ValueError):
            ExecutionState.restore(invalid)


@pytest.mark.asyncio
async def test_original_stop_directive_preserves_dispatched_and_child_unknown_without_replaying_ports():
    from src.core.agent_engine import AgentEngine
    from src.core.agent_engine.contracts import (ExecutionState, Identity, AgentMode,
        ToolCall, ToolFact, TerminalDirective, Outcome)
    current = ExecutionState(Identity('fixture_tenant', None, 'fixture_sid'),
        'fixture_execution', AgentMode.STANDALONE, 'Fixture kernel contract', [])
    current.terminal_directive = TerminalDirective.STOP_EXECUTION
    for call_id, phase in (('already_dispatched', 'dispatching'), ('child_unknown', 'waiting'),
                           ('not_started', 'prepared')):
        call = ToolCall(call_id, 'read', {})
        current.pending.append(call)
        current.tools[call_id] = ToolFact(call, phase=phase)
    current.children['child_unknown'] = {'execution_id': 'fixture_child',
        'checkpoint': {'outcome': 'waiting', 'tools': {}, 'children': {}}}
    before = current.checkpoint()

    class Ports:
        def __init__(self):
            self.saved, self.events = [], []
        def command(self):
            return None
        async def save(self, actual, boundary):
            self.saved.append((boundary, actual.checkpoint()))
        async def event(self, actual, event):
            self.events.append(deepcopy(event))
        async def complete(self, *_):
            raise AssertionError('MODEL_MUST_NOT_BE_DISPATCHED')
        async def dispatch(self, *_):
            raise AssertionError('TOOL_MUST_NOT_BE_DISPATCHED')
            yield  # AsyncIterator port shape, never a successful result.
        async def recover(self, *_):
            raise AssertionError('UNKNOWN_TOOL_MUST_NOT_BE_RECOVERED')

    ports = Ports()
    events = [event async for event in AgentEngine().run(current, ports, ports, ports, ports)]
    assert events and ports.saved
    assert current.outcome == Outcome.WAITING
    assert current.waiting['reason'] == 'TERMINAL_EFFECT_PENDING_VERIFICATION'
    assert current.waiting['tool_call_ids'] == ['already_dispatched', 'child_unknown']
    assert current.tools['already_dispatched'].__dict__ == ExecutionState.restore(before).tools['already_dispatched'].__dict__
    assert current.tools['child_unknown'].__dict__ == ExecutionState.restore(before).tools['child_unknown'].__dict__
    assert current.children == before['children']
    assert current.tools['not_started'].phase == 'not_dispatched'
    assert current.tools['not_started'].result_recorded is True
    assert [call.id for call in current.pending] == ['already_dispatched', 'child_unknown']
    assert sum(message.get('tool_call_id') == 'not_started' for message in current.messages) == 1
    assert not any(message.get('tool_call_id') in {'already_dispatched', 'child_unknown'}
        for message in current.messages)

