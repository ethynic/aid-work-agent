"""Server-owned binding for a read-only check of one original WeCom send."""

import re
from copy import deepcopy
from uuid import UUID

TOOL = 'wecom_send_status'
BUSINESS_KIND = 'wecom_send_status'
NO_DISPATCH = '_wecom_send_status_no_dispatch'
RECEIPT_ID = re.compile(r'^send-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z(?:-[1-9]\d*)?$', re.ASCII)


class SendStatusScopeError(ValueError):
    def __init__(self):
        super().__init__('WECOM_SEND_SCOPE_INVALID')


def valid_invocation_id(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def original_scope(cursor, tenant_id, user_id, session_id, send_invocation_id, *, device_id=None):
    """Receipt IDs are locators, never authority. Read them only from an owned send."""
    if not tenant_id or not user_id or not session_id or not valid_invocation_id(send_invocation_id):
        raise SendStatusScopeError()
    cursor.execute('''SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s
        AND user_id=%s AND session_id=%s FOR SHARE''', (send_invocation_id, tenant_id, user_id, session_id))
    original = cursor.fetchone()
    if (not original or str(original['id']) != send_invocation_id
            or any(str(original.get(key)) != str(value) for key, value in
                   (('tenant_id', tenant_id), ('user_id', user_id), ('session_id', session_id)))
            or original.get('tool_name') != 'wecom_message_send' or original.get('provider_key') != 'wecom'
            or original.get('state') not in {'unknown', 'succeeded', 'failed', 'expired', 'cancelled'}
            or original.get('state') == 'cancelled' and original.get('claimed_at') is None
            or original.get('effect') not in {'unknown', 'applied', 'partial'}
            or not original.get('device_id')
            or device_id is not None and str(original['device_id']) != str(device_id)):
        raise SendStatusScopeError()
    result = original.get('result_json') or {}
    if not isinstance(result, dict):
        raise SendStatusScopeError()
    payloads = [result]
    late = result.get('_late_result')
    if late is not None:
        if (original['state'] != 'unknown' or not isinstance(late, dict)
                or late.get('effect') not in {'unknown', 'applied', 'partial'}):
            raise SendStatusScopeError()
        payloads.append(late)
    receipts = set()
    for payload in payloads:
        data = payload.get('data')
        if (isinstance(data, dict) and 'receipt_invalidation_failed' in data
                and data['receipt_invalidation_failed'] is not False):
            raise SendStatusScopeError()
        if isinstance(data, dict) and 'receipt_id' in data:
            receipt_id = data['receipt_id']
            if not isinstance(receipt_id, str) or not RECEIPT_ID.fullmatch(receipt_id):
                raise SendStatusScopeError()
            receipts.add(receipt_id)
    if len(receipts) != 1:
        raise SendStatusScopeError()
    return {'send_invocation_id': send_invocation_id, 'device_id': str(original['device_id']),
            'receipt_id': receipts.pop()}


def resolve_original(tenant_id, user_id, session_id, send_invocation_id, *, device_id=None):
    from .repository import get_db_connection
    with get_db_connection() as connection:
        return original_scope(connection.cursor(), tenant_id, user_id, session_id,
                              send_invocation_id, device_id=device_id)


def _runner_send_id(cursor, invocation, *, dispatch):
    binding = invocation.get('business_ref') or {}
    if (not isinstance(binding, dict) or set(binding) != {'runner_id', 'execution_id', 'tool_call_id', 'branch', 'ordinal'}
            or binding.get('branch') != 'main' or type(binding.get('ordinal')) is not int
            or binding['ordinal'] != 0):
        raise SendStatusScopeError()
    cursor.execute('SELECT checkpoint,user_id,session_id FROM agent_runners WHERE runner_id=%s AND tenant_id=%s',
                   (binding['runner_id'], invocation['tenant_id']))
    row = cursor.fetchone()
    if (not row or row['user_id'] != invocation['user_id'] or row['session_id'] != invocation['session_id']):
        raise SendStatusScopeError()
    root = (row.get('checkpoint') or {}).get('execution') or {}
    if not isinstance(root, dict) or root.get('execution_id') != binding['runner_id']:
        raise SendStatusScopeError()
    pending, found = [root], []
    while pending:
        node = pending.pop()
        if node.get('execution_id') == binding['execution_id']:
            found.append(node)
        for child in (node.get('children') or {}).values():
            checkpoint = child.get('checkpoint')
            if checkpoint is not None:
                if not isinstance(checkpoint, dict) or checkpoint.get('execution_id') != child.get('execution_id'):
                    raise SendStatusScopeError()
                pending.append(checkpoint)
    if len(found) != 1:
        raise SendStatusScopeError()
    call = ((found[0].get('tools') or {}).get(binding['tool_call_id']) or {}).get('call') or {}
    arguments = call.get('arguments') or {}
    if (call.get('id') != binding['tool_call_id'] or call.get('name') != TOOL
            or not isinstance(arguments, dict) or set(arguments) != {'send_invocation_id'}):
        raise SendStatusScopeError()
    if dispatch:
        saved = (found[0].get('resources', {}).get('local_invocations') or {}).get(invocation.get('dedupe_key'))
        expected_request = {'tool_name': TOOL, 'arguments': invocation.get('arguments_json'),
                            'device_id': str(invocation['device_id']), 'provider_key': 'wecom'}
        if (not isinstance(saved, dict) or saved.get('invocation_id') != str(invocation.get('id'))
                or any(saved.get(key) != value for key, value in binding.items())
                or any((saved.get('request') or {}).get(key) != value for key, value in expected_request.items())):
            raise SendStatusScopeError()
    return arguments['send_invocation_id']


def _send_id(cursor, invocation, *, dispatch=False):
    if (invocation.get('tool_name') != TOOL or invocation.get('provider_key') != 'wecom'
            or not invocation.get('device_id')):
        raise SendStatusScopeError()
    if invocation.get('business_kind') == 'agent_runner':
        send_id = _runner_send_id(cursor, invocation, dispatch=dispatch)
    elif invocation.get('business_kind') == BUSINESS_KIND:
        binding = invocation.get('business_ref') or {}
        if (not isinstance(binding, dict) or set(binding) != {'version', 'send_invocation_id'} or type(binding.get('version')) is not int
                or binding['version'] != 1):
            raise SendStatusScopeError()
        send_id = binding['send_invocation_id']
    else:
        raise SendStatusScopeError()
    if not valid_invocation_id(send_id):
        raise SendStatusScopeError()
    return send_id


def no_dispatch_marker(cursor, invocation):
    """Capture only the accepted Cloud pointer, without treating an invalid source as authority."""
    try:
        return {'version': 1, 'send_invocation_id': _send_id(cursor, invocation),
            'invocation_id': str(invocation['id']), 'business_kind': invocation.get('business_kind'),
            'business_ref': deepcopy(invocation.get('business_ref')),
            'native_request': {'tool_name': invocation['tool_name'], 'provider_key': invocation['provider_key'],
                'device_id': str(invocation['device_id']), 'arguments': deepcopy(invocation.get('arguments_json'))}}
    except SendStatusScopeError:
        return None


def rejected_before_dispatch(invocation, cloud_arguments, request):
    """Only our private server receipt can replace a now-invalid source read during recovery."""
    if (not isinstance(invocation, dict) or not {'id', 'tool_name', 'provider_key', 'business_kind',
            'business_ref', 'arguments_json', 'device_id', 'started_at', 'state', 'effect',
            'error_code', 'result_json'} <= invocation.keys()):
        return False
    result = invocation.get('result_json') or {}
    if not isinstance(result, dict):
        return False
    marker = result.get(NO_DISPATCH)
    if (invocation.get('tool_name') != TOOL or invocation.get('provider_key') != 'wecom'
            or invocation.get('business_kind') not in {'agent_runner', BUSINESS_KIND}
            or invocation.get('state') != 'failed' or invocation.get('effect') != 'none'
            or invocation.get('started_at') is not None or invocation.get('error_code') != 'WECOM_SEND_SCOPE_INVALID'
            or result.get('success') is not False or result.get('code') != 'WECOM_SEND_SCOPE_INVALID'
            or result.get('retryable') is not False or result.get('data') is not None
            or not isinstance(marker, dict) or set(marker) != {'version', 'send_invocation_id',
                'invocation_id', 'business_kind', 'business_ref', 'native_request'}
            or type(marker.get('version')) is not int or marker['version'] != 1
            or not valid_invocation_id(marker.get('send_invocation_id'))
            or marker.get('invocation_id') != str(invocation.get('id'))
            or marker.get('business_kind') != invocation.get('business_kind')
            or marker.get('business_ref') != invocation.get('business_ref')
            or marker.get('native_request') != {'tool_name': TOOL, 'provider_key': 'wecom',
                'device_id': str(invocation.get('device_id')), 'arguments': invocation.get('arguments_json')}
            or cloud_arguments != {'send_invocation_id': marker['send_invocation_id']}):
        return False
    native = invocation.get('arguments_json')
    return (isinstance(native, dict) and set(native) == {'receipt_id'}
        and isinstance(native['receipt_id'], str) and RECEIPT_ID.fullmatch(native['receipt_id']) is not None
        and request.get('tool_name') == TOOL and request.get('provider_key') == 'wecom'
        and request.get('device_id') == str(invocation.get('device_id')) and request.get('arguments') == native)


def no_dispatch_completion_known(invocation):
    """Physical completion only; this private receipt grants no recovery or new dispatch."""
    result = invocation.get('result_json') or {}
    marker = result.get(NO_DISPATCH) if isinstance(result, dict) else None
    if not isinstance(marker, dict) or not isinstance(marker.get('native_request'), dict):
        return False
    return rejected_before_dispatch(invocation,
        {'send_invocation_id': marker.get('send_invocation_id')}, marker['native_request'])


def invocation_scope(cursor, invocation, *, dispatch=False):
    """Revalidate at enqueue, claim and start. This scope bypasses only its own send hold."""
    send_id = _send_id(cursor, invocation, dispatch=dispatch)
    scope = original_scope(cursor, invocation.get('tenant_id'), invocation.get('user_id'),
                           invocation.get('session_id'), send_id, device_id=invocation.get('device_id'))
    if invocation.get('arguments_json') != {'receipt_id': scope['receipt_id']}:
        raise SendStatusScopeError()
    return scope
