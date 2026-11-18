"""Provider-neutral 1.1 receipt projection for ordinary Device results."""

import asyncio

from loguru import logger

from .catalog import EXECUTION_RECEIPT_VERSION

EFFECTS = ('none', 'applied', 'partial', 'unknown')
RECEIPT_IMAGE_REF_PREFIX = 'receipt_image_'
RECEIPT_FIELDS = ('receipt_version', 'step', 'reason', 'known_effect', 'partial_results', 'screenshot')


def strip_receipt_extension(data):
    """Only a versioned envelope owns extension fields."""
    if not isinstance(data, dict) or 'receipt_version' not in data:
        return data
    return {key: value for key, value in data.items() if key not in RECEIPT_FIELDS}


def has_receipt_effect_conflict(result):
    outer = result.get('effect')
    data = result.get('data')
    return (isinstance(data, dict) and data.get('receipt_version') == EXECUTION_RECEIPT_VERSION
            and outer in EFFECTS and data.get('known_effect') in EFFECTS
            and outer != data['known_effect'])


async def _owned_receipt(invocation_id, context, *, tool_name=None):
    """Load the accepted original result only for its owning execution."""
    execution_id = getattr(context, 'agent_execution_id', None)
    if not (invocation_id and execution_id and context.tenant_id and context.user_id and context.session_id):
        return None

    from . import repository

    try:
        row = await asyncio.to_thread(repository.get_invocation, invocation_id, context.tenant_id)
    except Exception:
        logger.warning('Execution receipt unavailable code=RECEIPT_INVOCATION_UNAVAILABLE')
        return None
    binding = (row.get('business_ref') or {}) if row else {}
    if (not row or row.get('tenant_id') != context.tenant_id
            or row.get('user_id') != context.user_id or row.get('session_id') != context.session_id
            or row.get('business_kind') != 'agent_runner'
            or binding.get('execution_id') != execution_id or not binding.get('tool_call_id')):
        return None
    if tool_name is not None and (row.get('tool_name') != tool_name
                                  or binding['tool_call_id'] != context.tool_call_id):
        return None
    accepted = (repository.observed_invocation(row).get('result_json') or {}).get('data')
    if isinstance(accepted, dict) and accepted.get('receipt_version') == EXECUTION_RECEIPT_VERSION:
        return accepted
    return None


async def read_receipt_image(reference, context):
    invocation_id = reference.removeprefix(RECEIPT_IMAGE_REF_PREFIX)
    accepted = await _owned_receipt(invocation_id, context)
    if not accepted or not accepted.get('screenshot'):
        raise ValueError('当前执行没有可读取的回执图片')
    from .result_images import decode_image_block

    image = accepted['screenshot']
    content, _ = await asyncio.to_thread(decode_image_block, image)
    return image['mimeType'], content


async def project_execution_receipt(result, *, tool_name, context):
    """Keep receipt metadata and expose only a private image reference."""
    if not isinstance(result, dict):
        return result
    data = result.get('data')
    public_data = strip_receipt_extension(data)
    projected = {**result, 'data': public_data} if public_data is not data else result
    if public_data is not None and not isinstance(public_data, dict):
        return projected

    invocation_id = result.get('invocation_id')
    accepted = await _owned_receipt(invocation_id, context, tool_name=tool_name)
    if accepted is None:
        return projected

    public_data = {**(public_data or {}), **{
        key: accepted[key] for key in RECEIPT_FIELDS if key in accepted and key != 'screenshot'
    }}
    if accepted.get('screenshot'):
        public_data['screenshot'] = {'image_ref': RECEIPT_IMAGE_REF_PREFIX + invocation_id}
    return {**result, 'data': public_data}
