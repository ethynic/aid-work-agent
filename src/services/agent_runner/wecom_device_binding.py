"""Private root-owned device pin for group monitoring; never model authority."""

import copy
from collections.abc import Mapping

from .ownership import LeaseLost

RESOURCE = 'wecom_task_device'
IDENTITY_FIELDS = ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')


def accepted_daily_device(request_data):
    """Only a server-accepted v2 daily descriptor can fix first dispatch."""
    if not isinstance(request_data, Mapping):
        return None
    descriptor = request_data.get('wecom_conversation_business')
    if (not isinstance(descriptor, Mapping) or descriptor.get('schema_version') != 2
            or descriptor.get('kind') != 'daily'):
        return None
    device = descriptor.get('device')
    if (not isinstance(device, str) or not device
            or request_data.get(RESOURCE) != device):
        raise LeaseLost('WECOM_DAILY_DEVICE_INVALID')
    return device


def bound_device(root, runner_id, identity):
    if root.get('execution_id') != runner_id or root.get('identity') != identity:
        raise LeaseLost('WECOM_TASK_OWNER_MISMATCH')
    resources = root.get('resources', {})
    if RESOURCE not in resources:
        return None
    marker = resources[RESOURCE]
    if (not isinstance(marker, dict) or type(marker.get('version')) is not int
            or marker['version'] not in {1, 2} or marker.get('runner_id') != runner_id
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
    daily_device = accepted_daily_device((row.get('input') or {}).get('request_data'))
    modern = tool_name == 'wecom_read_sessions' or bool(arguments.get('session'))
    from src.local_tools.repository import DeviceOperationUnresolved

    if daily_device is not None and daily_device != str(device_id):
        raise DeviceOperationUnresolved('WECOM_TASK_DEVICE_CHANGED')
    if original is not None and original != str(device_id):
        raise DeviceOperationUnresolved('WECOM_TASK_DEVICE_CHANGED')
    marker = root.get('resources', {}).get(RESOURCE) or {}
    if original is not None and modern and marker.get('version') == 1:
        # A new three-tool read/send remains on the original device. Old
        # accepted group_cursor calls retain their original validation below.
        marker['version'] = 2
    if original is not None and not modern:
        if tool_name in {'wecom_send_image', 'wecom_send_file'}:
            raise DeviceOperationUnresolved('WECOM_TASK_SEND_SCOPE_REQUIRED')
        if tool_name == 'wecom_message_send' and (
                not isinstance(arguments.get('group_cursor'), str) or not arguments['group_cursor']
                or not arguments.get('target_name') or arguments.get('target_ref')):
            raise DeviceOperationUnresolved('WECOM_TASK_SEND_SCOPE_REQUIRED')
    if original is None and (tool_name == 'wecom_group_poll' or modern):
        root.setdefault('resources', {})[RESOURCE] = {
            'version': 2 if modern else 1, 'runner_id': row['runner_id'], 'identity': copy.deepcopy(identity),
            'device_id': str(device_id)}
    return original is not None or daily_device is not None
