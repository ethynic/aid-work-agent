"""Private root-owned device pin for group monitoring; never model authority."""

import copy

from .ownership import LeaseLost

RESOURCE = 'wecom_task_device'
IDENTITY_FIELDS = ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')


def bound_device(root, runner_id, identity):
    if root.get('execution_id') != runner_id or root.get('identity') != identity:
        raise LeaseLost('WECOM_TASK_OWNER_MISMATCH')
    resources = root.get('resources', {})
    if RESOURCE not in resources:
        return None
    marker = resources[RESOURCE]
    if (not isinstance(marker, dict) or type(marker.get('version')) is not int
            or marker['version'] != 1 or marker.get('runner_id') != runner_id
            or marker.get('identity') != identity
            or not isinstance(marker.get('device_id'), str) or not marker['device_id']):
        raise LeaseLost('WECOM_TASK_DEVICE_INVALID')
    return marker['device_id']


def bind_or_validate(checkpoint, row, tool_name, provider_key, device_id, arguments):
    """Called under the root row lock, in the invocation insertion transaction."""
    if provider_key != 'wecom':
        return False
    root = checkpoint['execution']
    identity = {key: row[key] for key in IDENTITY_FIELDS}
    original = bound_device(root, row['runner_id'], identity)
    from src.local_tools.repository import DeviceOperationUnresolved
    if original is not None and original != str(device_id):
        raise DeviceOperationUnresolved('WECOM_TASK_DEVICE_CHANGED')
    if original is not None:
        if tool_name in {'wecom_send_image', 'wecom_send_file'}:
            raise DeviceOperationUnresolved('WECOM_TASK_SEND_SCOPE_REQUIRED')
        if tool_name == 'wecom_message_send' and (
                not isinstance(arguments.get('group_cursor'), str) or not arguments['group_cursor']
                or not arguments.get('target_name') or arguments.get('target_ref')):
            raise DeviceOperationUnresolved('WECOM_TASK_SEND_SCOPE_REQUIRED')
    elif tool_name == 'wecom_group_poll':
        root.setdefault('resources', {})[RESOURCE] = {
            'version': 1, 'runner_id': row['runner_id'], 'identity': copy.deepcopy(identity),
            'device_id': str(device_id)}
    return original is not None
