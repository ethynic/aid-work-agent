"""Aggregate lifecycle facts across an owned execution tree, not asyncio tasks."""

from src.core.agent_engine.contracts import Outcome


def child_tree_finished(node):
    return all(isinstance(child.get('checkpoint'),dict)
        and child['checkpoint'].get('outcome') in {'completed','failed','cancelled','iteration_limit'}
        and child_tree_finished(child['checkpoint']) for child in node.get('children',{}).values())


def park_unfinished_tree(state):
    """Return a parked status, or interrupt when a leaf has no safe fact."""
    def unfinished(node):
        for call_id, child in node.get('children', {}).items():
            checkpoint = child.get('checkpoint')
            if not isinstance(checkpoint, dict):
                return call_id, child, None
            nested = unfinished(checkpoint)
            if nested:
                # Keep the direct delegation binding; carry its actual leaf wait.
                return call_id, child, nested[2]
            if checkpoint.get('outcome') not in {'completed', 'failed', 'cancelled', 'iteration_limit'}:
                return call_id, child, checkpoint
        return None

    outstanding = unfinished(state.checkpoint())
    if outstanding is None:
        return None
    call_id, child, leaf = outstanding
    if leaf is None or leaf.get('outcome') not in {'paused', 'waiting'}:
        return 'interrupted'
    if state.outcome == Outcome.COMPLETED:
        state.resources['root_execution_outcome'] = 'completed'
    state.outcome = Outcome.WAITING if leaf['outcome'] == 'waiting' else Outcome.PAUSED
    state.waiting = {'kind': 'child_wait', 'tool_call_id': call_id,
        'execution_id': child['execution_id'], 'child_status': leaf['outcome'],
        'child_wait': leaf.get('waiting')}
    return state.outcome.value
