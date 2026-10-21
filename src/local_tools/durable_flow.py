"""Original desktop-operation continuation for the base proxy flow.

The execution owner supplies fencing/storage through the neutral lifecycle
port. Special resume recognition and overlay healing have their own stages.
"""

from datetime import datetime


class LocalContinuationRequired(RuntimeError):
    tool_continuation_required = True
    def __init__(self, code, invocation_id=None):
        super().__init__(code)
        self.code, self.invocation_id = code, invocation_id


class LocalModelAttemptConsumed(RuntimeError):
    """A dispatched model attempt already holds its immutable usage receipt.

    That attempt ordinal is never replayed. Recovery consumes the fetched
    item on a new attempt ordinal (an independently traceable physical call
    under the original business intent) or converges it as a per-item
    failure. Unlike the no-response protection this is not a continuation
    block, so upper retry loops may advance to their next attempt slot.
    """
    attempt_consumed = True

    def __init__(self, code='LOCAL_MODEL_ATTEMPT_ALREADY_CONSUMED'):
        super().__init__(code)
        self.code = code


def supports_original_invocation(tool):
    from .proxy_tool import LocalToolProxyTool
    return isinstance(tool, LocalToolProxyTool) and type(tool).execute is LocalToolProxyTool.execute


async def original_invocation(proxy, operation, device, arguments):
    saved = await operation.owner.saved_phase(branch=operation.branch, ordinal=operation.ordinal)
    # Phase 4：工具默认 timeout 不再持久化为健康后台任务的总时限。新绑定的
    # durable invocation 不带 deadline，其生命期由设备活动续租（claim/进度续租，
    # 停续由 expire_stale_claims 收敛为 unknown）与显式取消治理，Runner 仅观察
    # 持久原调用直至终态。已保存 phase 的 deadline 是存量持久事实，原样复用、
    # 到期语义不忽略（存量投影见 proxy_tool._poll_invocation / local_recovery）。
    deadline = (datetime.fromisoformat(saved['request']['deadline_at'])
        if saved is not None and saved['request'].get('deadline_at') else None)
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
