"""Finite registered Local domain composition; no Engine or Runner imports.

Device invocation continuation and domain postprocessing share one operation
owner. Cloud read branches reuse their original DTO and trusted tenant scope.
"""

from .durable_flow import LocalContinuationRequired


def domain_classes():
    from .proxy_tool import (BossResumeDetailTool, BossResumeBatchTool,
        BossJobsListTool, BossSendToTool, BossSendCurrentTool, BossInterviewNotifyTool)
    return (BossResumeDetailTool, BossResumeBatchTool, BossJobsListTool,
        BossSendToTool, BossSendCurrentTool, BossInterviewNotifyTool)


def supports_owned_device_class(cls):
    from .proxy_tool import LocalToolProxyTool, BossJobsListTool, BossInterviewNotifyTool
    return (cls.execute is LocalToolProxyTool.execute
        or cls in domain_classes() and cls not in (BossJobsListTool,BossInterviewNotifyTool))


def supports_owned_tool(tool):
    from .durable_flow import supports_original_invocation
    return supports_original_invocation(tool) or type(tool) in domain_classes()


def cloud_read_branch(cls, arguments):
    from .proxy_tool import BossJobsListTool, BossSendToTool, BossSendCurrentTool
    if cls is BossJobsListTool:
        return 'cloud.jobs'
    if cls in (BossSendToTool, BossSendCurrentTool) and str(arguments.get('script_title') or '').strip():
        return 'cloud.script'
    return None


async def recover_owned_tool(proxy, operation, progress_queue, *, arguments):
    from src.tools.context import current_tool_execution_context
    from src.tools.executor import normalize_provided_parameters
    from .durable_flow import supports_original_invocation, recover_original
    if supports_original_invocation(proxy):
        return await recover_original(proxy, operation, progress_queue, arguments=arguments)
    if type(proxy) not in domain_classes():
        raise LocalContinuationRequired('LOCAL_DOMAIN_CONTINUATION_REQUIRED')
    context = current_tool_execution_context()
    kwargs = {**normalize_provided_parameters(proxy, arguments),
        '_trusted_tenant_id': context.tenant_id, '_trusted_user_id': context.user_id,
        '_session_id': context.session_id, '_progress_queue': progress_queue}
    return await execute_domain(proxy, operation, kwargs)


async def execute_domain(proxy, operation, kwargs):
    from .cloud_flow import read_original, send_message
    from .proxy_tool import BossResumeDetailTool, BossResumeBatchTool, BossSendToTool, BossSendCurrentTool, BossInterviewNotifyTool
    if type(proxy) is BossInterviewNotifyTool:
        return await notify_result(proxy,operation,kwargs)
    arguments = {key: value for key, value in kwargs.items() if not key.startswith('_')}
    branch = cloud_read_branch(type(proxy), arguments)
    if branch:
        return await read_original(operation.owner, branch=branch, intent=arguments,
            invoke=lambda: proxy._execute_legacy(**kwargs))
    if type(proxy) in (BossSendToTool, BossSendCurrentTool):
        if not str(kwargs.get('message') or '').strip():
            return {'success': False, 'code': 'INVALID_ARGS',
                'message': '缺少 message（最终消息全文）；或改用 script_title 话术模式'}
        return await send_message(proxy, operation, kwargs, write_back=type(proxy) is BossSendToTool)
    if type(proxy) not in (BossResumeDetailTool, BossResumeBatchTool):
        raise LocalContinuationRequired('LOCAL_DOMAIN_CONTINUATION_REQUIRED')
    return await resume_result(proxy, operation, kwargs, batch=type(proxy) is BossResumeBatchTool)


async def resume_result(proxy, operation, kwargs, *, batch):
    from .overlay_flow import run_base
    from .resume_flow import process_item
    from src.tools.context import current_tool_execution_context
    original = await run_base(proxy, operation, kwargs)
    if not original.get('success'):
        return {**original, 'data': None}
    ref = await operation.owner.saved_phase(branch=operation.branch, ordinal=operation.ordinal)
    context = current_tool_execution_context()
    data = original.get('data') or {}
    payloads = data.get('resumes') if batch and isinstance(data, dict) else [data]
    if not isinstance(payloads, list) or not payloads:
        failures = [{'name': f.get('name'), 'error': str(f.get('error') or '')[:120]}
            for f in (data.get('failures') or []) if isinstance(f, dict)][:5]
        return {**original, 'success': False, 'code': 'RESUME_PAYLOAD_INVALID',
            'message': 'CLI 批量结果缺少 resumes 数组或为空（未读取到任何简历）',
            'data': {'attempted': data.get('attempted'), 'failures': failures}}
    failures = [dict(f) for f in (data.get('failures') or []) if isinstance(f, dict)] if batch else []
    summaries = []
    for ordinal, payload in enumerate(payloads):
        outcome = await process_item(operation.owner, tenant_id=context.tenant_id,
            execution_id=context.agent_execution_id, tool_call_id=context.tool_call_id,
            invocation_id=original['invocation_id'], device_id=ref['request']['device_id'],
            item_ordinal=ordinal, payload=payload)
        if not outcome['success']:
            if not batch:
                return {**original, **outcome, 'data': None}
            failures.append({'name': payload.get('candidate_name') if isinstance(payload, dict) else None,
                'error': outcome['message']})
            continue
        summary = outcome['data']
        summaries.append(summary)
        if batch:
            proxy._push_progress(kwargs.get('_progress_queue'), {'type': 'progress',
                'invocation_id': original['invocation_id'],
                'text': f"✅ 第 {ordinal + 1}/{len(payloads)} 份已入库：{summary['candidate_name']}"})
    if not summaries:
        return {**original, 'success': False, 'code': 'RESUME_STORE_FAILED', 'data': None,
            'message': f'批量读取 {len(payloads)} 份简历但全部入库失败，请稍后重试或联系管理员'}
    if batch:
        message = f"已存入简历库 {len(summaries)} 份：" + '、'.join(s['candidate_name'] or '?' for s in summaries)
        if failures:
            message += f"；{len(failures)} 份失败（第一个：{failures[0].get('name') or '未知姓名'}—{failures[0].get('error')}）"
        return {**original, 'data': {'resumes': summaries, 'failures': failures}, 'message': message}
    summary = summaries[0]
    message = f"简历已存入简历库：{summary['candidate_name']}"
    if summary.get('job_name'):
        message += f" · {summary['job_name']}"
    message += f" · {summary['image_count']} 张截图"
    if summary.get('warning'):
        message += '；' + summary['warning']
    return {**original, 'data': summary, 'message': message}


async def notify_result(proxy,operation,kwargs):
    import asyncio
    from pydantic import BaseModel
    from .notify_flow import push_notify
    from .recruiting_writers import write_notify_log
    tenant_id = kwargs.get('_trusted_tenant_id')
    if not tenant_id:
        return {'success':False,'code':'NO_IDENTITY','message':'无法确定用户身份，请重新登录后再试'}
    candidates = [value.model_dump(exclude_none=True) if isinstance(value,BaseModel) else value
        for value in kwargs.get('candidates') or []]
    binding = await operation.owner.saved_domain(branch='notify.binding',ordinal=0)
    resume_id = None
    if binding is None and len(candidates)==1:
        from .proxy_tool import _find_resume_id_by_name
        name = str((candidates[0] or {}).get('name') or '').strip()
        if name:
            try:
                resume_id = await asyncio.to_thread(_find_resume_id_by_name,tenant_id,name)
            except Exception as error:
                if (getattr(error,'authoritative_storage_failure',False)
                        or getattr(error,'tool_continuation_required',False)):
                    raise
    result = await push_notify(operation.owner,tenant_id=tenant_id,kind=kwargs.get('kind'),
        job_name=kwargs.get('job_name') or '',candidates=candidates,note=kwargs.get('note'),
        resume_id=resume_id,log_writer=write_notify_log)
    pushed = bool(result.get('pushed'))
    if pushed:
        message = '事前知会已发送至企微群' if kwargs.get('kind')=='pre' else '面试邀约通报已发送至企微群'
    elif result.get('reason')=='未启用':
        message = '企微通知未启用（可在通知设置开启），本次未发送群通知'
    else:
        message = '通知发送失败（不影响邀约，可继续）：'+(result.get('error') or result.get('reason') or '未知原因')
    return {'success':True,'code':None,'message':message,
        'data':{'pushed':pushed,'log_id':result.get('log_id')}}
