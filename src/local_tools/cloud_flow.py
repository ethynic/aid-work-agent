"""Finite cloud read and sent-message postprocessing for recruiting tools.

The runtime composition registers only the JobsList and script-lookup read
branches. This does not classify all branches of a send tool as replayable.
"""

import asyncio

from .durable_flow import LocalContinuationRequired
from .overlay_flow import run_base
from .recruiting_writers import write_comm_log


async def read_original(owner, *, branch, intent, invoke):
    original = await owner.saved_domain(branch=branch, ordinal=0)
    if original is not None:
        if original['request'] != intent or original.get('phase') != 'completed':
            raise LocalContinuationRequired('LOCAL_CLOUD_READ_VERIFICATION_REQUIRED')
        return original['result']
    # The actual runtime checks this tool's profile/identity before invoking the
    # read. A crash here can repeat only the explicitly registered read branch.
    result = await invoke()
    return await owner.complete_phase(branch=branch, ordinal=0, intent=intent, result=result)


async def send_message(proxy, operation, kwargs, *, write_back):
    result = await run_base(proxy, operation, kwargs)
    data = result.get('data') or {}
    if (not write_back or not result.get('success') or not isinstance(data, dict)
            or not data.get('sent') or data.get('dry_run')):
        return result
    tenant_id = kwargs.get('_trusted_tenant_id')
    name, message = ((kwargs.get(key) or '').strip() for key in ('to', 'message'))
    if not tenant_id or not name or not message:
        return result
    origin = {'invocation_id': result['invocation_id'], 'candidate_name': name,
        'message': message}
    binding = await operation.owner.saved_domain(branch='send.comm_binding', ordinal=0)
    if binding is None:
        from .proxy_tool import _find_resume_id_by_name
        try:
            resume_id = await asyncio.to_thread(_find_resume_id_by_name, tenant_id, name)
        except Exception as error:
            if (getattr(error, 'authoritative_storage_failure', False)
                    or getattr(error, 'tool_continuation_required', False)):
                raise
            # Lookup has no external effect. Preserve optional legacy logging
            # without swallowing an owner/fence failure or recording driver text.
            resume_id = None
        bound = await operation.owner.commit_postprocess(branch='send.comm_binding', ordinal=0,
            intent=origin, writer=lambda cursor,row,request: {'resume_id': resume_id})
    else:
        if binding['request'] != origin:
            raise LocalContinuationRequired('LOCAL_SEND_LOG_INTENT_CHANGED')
        bound = binding['result']
    if bound['resume_id'] is not None:
        await operation.owner.commit_postprocess(branch='send.comm_log', ordinal=0,
            intent={**origin, 'resume_id': bound['resume_id']}, writer=write_comm_log)
    # Original SendTo returns the original device result; its log is optional.
    # SendCurrent passes write_back=False and never invents a recipient/log.
    return result
