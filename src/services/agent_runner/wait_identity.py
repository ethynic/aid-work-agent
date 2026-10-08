"""Stable identity of one owned execution wait, independent of its attempt."""

import uuid


def owned_wait_id(runner_id, execution_id, waiting):
    key = ':'.join(str(value) for value in (runner_id, execution_id,
        waiting.get('tool_call_id'), waiting.get('kind'), waiting.get('assistance_id')))
    return 'wait_' + uuid.uuid5(uuid.NAMESPACE_URL, key).hex
