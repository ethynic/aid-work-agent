"""Apply the cancellation owner's committed checkpoint without replacing live state."""

import copy

from src.core.agent_engine.contracts import CheckpointFailure


def accept_cancellation_row(control,row,state=None):
    if row is None:
        return
    if (row['runner_id']!=control.attempt.runner_id or row['attempt']!=control.attempt.number
            or row['worker_id']!=control.attempt.worker_id):
        raise CheckpointFailure('LOCAL_CANCEL_OWNER_MISMATCH')
    if state is not None:
        saved = (row.get('checkpoint') or {}).get('execution')
        if not isinstance(saved,dict) or saved.get('execution_id')!=state.execution_id:
            raise CheckpointFailure('LOCAL_CANCEL_OWNER_MISMATCH')
        # Only cancellation audit resources changed. Preserve model messages,
        # tool outcomes and other live state, and patch complete owned descendants.
        def patch(live,node):
            if live.get('execution_id')!=node.get('execution_id'):
                raise CheckpointFailure('LOCAL_CANCEL_OWNER_MISMATCH')
            if 'local_domain_phases' in node.get('resources',{}):
                live.setdefault('resources',{})['local_domain_phases'] = copy.deepcopy(
                    node['resources']['local_domain_phases'])
            for key,fact in (live.get('children') or {}).items():
                child = fact.get('checkpoint')
                saved_child = (node.get('children',{}).get(key) or {}).get('checkpoint')
                if isinstance(child,dict):
                    if not isinstance(saved_child,dict):
                        raise CheckpointFailure('LOCAL_CANCEL_OWNER_MISMATCH')
                    patch(child,saved_child)
        checkpoint = state.checkpoint()
        patch(checkpoint,saved)
        state.resources = checkpoint['resources']
        state.children = checkpoint['children']
    control.envelope = copy.deepcopy(row['checkpoint'])
    control.revision = row['revision']
    control.snapshot = copy.deepcopy(row['public_snapshot'])
    control.cancel_requested = row['cancel_requested']
    control.pause_requested = row.get('pause_requested',False)
