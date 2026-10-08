"""Original desktop-operation continuation for the base proxy flow.

The execution owner supplies fencing/storage through the neutral lifecycle
port. Special resume recognition and overlay healing have their own stages.
"""

from datetime import datetime, timedelta, timezone


class LocalContinuationRequired(RuntimeError):
    tool_continuation_required = True
    def __init__(self, code, invocation_id=None):
        super().__init__(code)
        self.code, self.invocation_id = code, invocation_id


def supports_original_invocation(tool):
    from .proxy_tool import LocalToolProxyTool
    return isinstance(tool, LocalToolProxyTool) and type(tool).execute is LocalToolProxyTool.execute


async def original_invocation(proxy, operation, device, arguments):
    saved = await operation.owner.saved_phase(branch=operation.branch, ordinal=operation.ordinal)
    deadline = (datetime.fromisoformat(saved['request']['deadline_at']) if saved is not None
        else datetime.now(timezone.utc) + timedelta(seconds=proxy.timeout_seconds))
    return await operation.owner.bind_invocation(branch=operation.branch,
        ordinal=operation.ordinal, tool_name=proxy.name, device=device,
        arguments=arguments, provider_key=proxy.invocation_provider_key, deadline_at=deadline)


async def recover_original(proxy, operation, progress_queue, *, arguments):
    if not supports_original_invocation(proxy):
        raise LocalContinuationRequired('LOCAL_DOMAIN_CONTINUATION_REQUIRED')
    from src.tools.context import current_tool_execution_context
    from .overlay_flow import run_base
    context = current_tool_execution_context()
    from src.tools.executor import normalize_provided_parameters
    return await run_base(proxy,operation,{**normalize_provided_parameters(proxy,arguments),
        '_trusted_tenant_id':context.tenant_id,'_trusted_user_id':context.user_id,
        '_session_id':context.session_id,'_progress_queue':progress_queue})


def finish_original(proxy, result):
    if result.get('code') == 'EXECUTION_UNKNOWN' or result.get('effect') == 'unknown':
        raise LocalContinuationRequired('LOCAL_EFFECT_VERIFICATION_REQUIRED', result.get('invocation_id'))
    if not result.get('success') and requires_healing(proxy, result.get('code')):
        raise LocalContinuationRequired('LOCAL_HEAL_CONTINUATION_REQUIRED', result.get('invocation_id'))
    return result


def requires_healing(proxy, error_code):
    from .proxy_tool import HEALABLE_ERROR_CODES
    return proxy.heal_eligible and error_code in HEALABLE_ERROR_CODES


def healing_requires_verification(proxy, error_code, effect):
    """Only the original device's explicit none effect permits a retry."""
    return requires_healing(proxy,error_code) and effect!='none'
