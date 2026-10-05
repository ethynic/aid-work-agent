"""Keep control-owned display input when an execution saves its older snapshot."""

import copy


def merge_control_projection(snapshot, stored_snapshot):
    if snapshot is None:
        return None
    value = copy.deepcopy(snapshot)
    if 'supplementalInputs' in (stored_snapshot or {}):
        value['supplementalInputs'] = copy.deepcopy(stored_snapshot['supplementalInputs'])
    return value
