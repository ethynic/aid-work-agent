"""Attempt-owned Boss overlay continuation; legacy proxy flow remains separate."""

import hashlib
import json

from .durable_flow import LocalContinuationRequired, requires_healing, healing_requires_verification
from .lifecycle import local_operation_scope


def _known(result):
    if result.get('code')=='EXECUTION_UNKNOWN' or result.get('effect')=='unknown':
        raise LocalContinuationRequired('LOCAL_EFFECT_VERIFICATION_REQUIRED',result.get('invocation_id'))
    return result


async def poll_operation(proxy, operation, arguments, *, tenant_id, user_id, session_id, progress_queue,
                         expected_device_id=None):
    """Only a new original phase selects a device; saved phases poll their old ID."""
    original = await operation.owner.saved_phase(branch=operation.branch,ordinal=operation.ordinal)
    if original is not None:
        request, invocation = original['request'],original['invocation']
        if (request['arguments']!=arguments or request['tool_name']!=proxy.name
                or expected_device_id is not None and request['device_id']!=expected_device_id):
            raise LocalContinuationRequired('LOCAL_PHASE_INTENT_CHANGED',str(invocation['id']))
        proxy._push_progress(progress_queue,{'type':'started','invocation_id':str(invocation['id']),
            'text':f'⏳ 正在等待原本机操作：{proxy.display_name}'})
        return _known(await proxy._poll_invocation(invocation,tenant_id,progress_queue,
            durable_deadline=invocation['deadline_at']))
    if not tenant_id or not user_id:
        return {'success':False,'code':'NO_IDENTITY','message':'无法确定用户身份，请重新登录后再试'}
    error = proxy._validate_args(arguments)
    if error:
        return {'success':False,'code':'INVALID_ARGS','message':error}
    device, error = await proxy._find_ready_device(tenant_id,user_id)
    if device is None:
        return {'success':False,'code':'DEVICE_UNAVAILABLE','message':error}
    if expected_device_id is not None and str(device['id'])!=expected_device_id:
        return {'success':False,'code':'DEVICE_UNAVAILABLE','message':'原操作的设备已变更，未派发后续操作'}
    if proxy._tool_credit_price()>0:
        error = await proxy._tenant_credit_blocked(tenant_id)
        if error:
            return {'success':False,'code':'NO_CREDIT','message':error,'effect':None,'data':None,'invocation_id':None}
    with local_operation_scope(operation.owner,branch=operation.branch,ordinal=operation.ordinal):
        return _known(await proxy._dispatch_and_wait(tenant_id,user_id,session_id,device,arguments,progress_queue))


async def observed_model(owner, branch, ordinal, *, intent, invoke):
    """A saved provider response is a domain fact, never an Engine model step."""
    return await owner.model_phase(branch=branch,ordinal=ordinal,intent=intent,invoke=invoke)


async def run_base(proxy, operation, kwargs):
    from .lifecycle import LocalOperationScope
    from .proxy_tool import BossOverlayInspectTool, BossOverlayDismissTool, settings, OVERLAY_HEAL_TOOL_NAME
    from src.services import overlay_heal_service
    from .domain_fee import write_domain_fee, invalidate_domain_fee_cache
    tenant_id,user_id,session_id = (kwargs.get(key) for key in ('_trusted_tenant_id','_trusted_user_id','_session_id'))
    progress = kwargs.get('_progress_queue')
    arguments = {key:value for key,value in kwargs.items() if not key.startswith('_')}
    original = await poll_operation(proxy,operation,arguments,tenant_id=tenant_id,user_id=user_id,
        session_id=session_id,progress_queue=progress)
    if original.get('success') or not requires_healing(proxy,original.get('code')):
        return original
    # A retry repeats the original requested action. Its error code does not
    # prove that action had no effect (partial/applied/missing are unsafe).
    if healing_requires_verification(proxy,original.get('code'),original.get('effect')):
        raise LocalContinuationRequired('LOCAL_HEAL_EFFECT_VERIFICATION_REQUIRED',original.get('invocation_id'))
    original_phase = await operation.owner.saved_phase(branch=operation.branch,ordinal=operation.ordinal)
    original_device_id = original_phase['request']['device_id']
    policy = await operation.owner.saved_domain(branch='heal.policy',ordinal=operation.ordinal)
    if policy is None:
        enabled = bool(settings.boss_tool_billing.overlay_heal_enabled)
        policy_result = await operation.owner.complete_phase(branch='heal.policy',ordinal=operation.ordinal,
            intent={'invocation_id':original['invocation_id']},
            result={'enabled':enabled,'credit_cost':proxy._heal_price()})
    else:
        policy_result = policy['result']
        enabled = policy_result['enabled']
    if not enabled:
        return original
    proxy._push_progress(progress,{'type':'progress','text':'检测到页面异常（疑似弹层遮挡），正在尝试智能识别关闭…'})
    inspect = await poll_operation(BossOverlayInspectTool(),LocalOperationScope(operation.owner,'heal.inspect',operation.ordinal),{},
        tenant_id=tenant_id,user_id=user_id,session_id=session_id,progress_queue=progress,
        expected_device_id=original_device_id)
    data = (inspect.get('data') or {}) if inspect.get('success') else {}
    candidates, icons = data.get('candidates') or [],data.get('icon_candidates') or []
    if not candidates and not icons:
        return proxy._with_heal_info(original,dismissed_text=None,llm_used=False)
    text = overlay_heal_service.pick_heuristic(candidates,icons)
    used_llm = not bool(text)
    if used_llm:
        digest = hashlib.sha256(json.dumps([candidates,icons],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        async def model_call(index, invoke):
            return await observed_model(operation.owner,'heal.choice',operation.ordinal*2+index,
                intent={'invocation_id':inspect['invocation_id'],'candidates_digest':digest},invoke=invoke)
        text = await overlay_heal_service.pick_dismiss_text_with_llm(tenant_id,user_id,candidates,icons,
            model_call=model_call)
    if not text:
        return proxy._with_heal_info(original,dismissed_text=None,llm_used=used_llm)
    dismiss = await poll_operation(BossOverlayDismissTool(),LocalOperationScope(operation.owner,'heal.dismiss',operation.ordinal),
        {'text':text},tenant_id=tenant_id,user_id=user_id,session_id=session_id,progress_queue=progress,
        expected_device_id=original_device_id)
    if not dismiss.get('success'):
        return proxy._with_heal_info(original,dismissed_text=text,llm_used=used_llm,dismissed=False)
    retry = await poll_operation(proxy,LocalOperationScope(operation.owner,'heal.retry',operation.ordinal),arguments,
        tenant_id=tenant_id,user_id=user_id,session_id=session_id,progress_queue=progress,
        expected_device_id=original_device_id)
    healed = bool(retry.get('success'))
    if healed:
        previous = await operation.owner.saved_domain(branch='heal.fee',ordinal=operation.ordinal)
        if previous is None:
            saved = await operation.owner.saved_phase(branch='heal.retry',ordinal=operation.ordinal)
            intent = {'billing_tool_name':OVERLAY_HEAL_TOOL_NAME,'credit_cost':policy_result['credit_cost'],
                'invocation_id':retry['invocation_id'],'device_id':saved['request']['device_id']}
        else:
            intent = previous['request']
        billed = await operation.owner.commit_domain(branch='heal.fee',ordinal=operation.ordinal,
            intent=intent,writer=write_domain_fee)
        invalidate_domain_fee_cache(tenant_id,billed)
    return proxy._with_heal_info(retry,dismissed_text=text,llm_used=used_llm,dismissed=True,healed=healed)
