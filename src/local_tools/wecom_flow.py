"""Resume WeCom reads and reconcile an original send without replaying it."""

import asyncio

from .wecom_business import BUSINESS_TOOLS

RESOLVE_BRANCH = 'wecom.resolve'


def named_send(name, arguments):
    return (name == 'wecom_message_send' and isinstance(arguments.get('session'),str)
        and not arguments['session'].startswith('ws1.'))


def resolve_arguments(arguments):
    return {'session':arguments['session'],'limit':1,'resolve_only':True}


def canonical_send_arguments(arguments, invocation):
    """The original owned DB resolution, rather than a title, fixes the target."""
    from .repository import observed_invocation
    observed = observed_invocation(invocation)
    session = ((observed.get('result_json') or {}).get('data') or {}).get('session')
    if observed.get('state') == 'succeeded' and isinstance(session,str) and session.startswith('ws1.'):
        return {**arguments,'session':session}
    return None


def validate_resolve_phase(arguments, ref):
    return (ref.get('branch') == RESOLVE_BRANCH and ref.get('ordinal') == 0
        and (ref.get('request') or {}).get('tool_name') == 'wecom_read_session'
        and (ref.get('request') or {}).get('provider_key') == 'wecom'
        and (ref.get('request') or {}).get('arguments') == resolve_arguments(arguments))


def modern_call(name, arguments):
    return name == 'wecom_read_sessions' or (
        name in BUSINESS_TOOLS and isinstance(arguments.get('session'), str))


def readonly_db_call(name, arguments):
    return name == 'wecom_read_sessions' or (name == 'wecom_read_session'
        and (isinstance(arguments.get('session'), str) or arguments.get('channel') == 'local-db'))


async def _reconcile(owner, pending, identity, progress):
    from .proxy_tool import WecomSendStatusTool
    from .wecom_send_status import resolve_original, SendStatusScopeError

    await owner.authorize_operation()
    try:
        scope = await asyncio.to_thread(resolve_original, identity.tenant_id, identity.user_id,
            identity.session_id, pending['invocation_id'], device_id=pending['device_id'])
    except SendStatusScopeError:
        return None
    # Existing send-status admission validates the owned original receipt at
    # enqueue/claim/start. The deterministic key reconnects this read after a
    # server restart; it never names or creates a replacement send.
    key = ':'.join(('wecom-reconcile', owner.root.attempt.runner_id,
        owner.state.execution_id, owner.call_id, pending['invocation_id']))
    def enqueue():
        from src.services.agent_runner.ownership import lock_runner
        from .repository import create_invocation_in_tx, get_invocation
        with owner.root.repository.connection_factory() as connection:
            cursor = connection.cursor()
            lock_runner(cursor, owner.root.attempt.runner_id, owner.root.attempt, dispatch=True)
            invocation_id = create_invocation_in_tx(cursor, identity.tenant_id, identity.user_id,
                scope['device_id'], 'wecom_send_status', {'receipt_id': scope['receipt_id']},
                identity.session_id, provider_key='wecom', business_kind='wecom_send_status',
                business_ref={'version': 1, 'send_invocation_id': pending['invocation_id']},
                dedupe_key=key, verify_binding=True)
            connection.commit()
        return get_invocation(invocation_id, identity.tenant_id)

    async with owner.root.lock:
        owner.root._check_usage()
        invocation = await asyncio.to_thread(enqueue)
    return await WecomSendStatusTool()._poll_invocation(invocation, identity.tenant_id, progress, durable=True)


async def run(proxy, operation, kwargs):
    from .overlay_flow import poll_operation
    from .repository import DeviceOperationUnresolved
    from .wecom_send_status import SendStatusScopeError

    owner = operation.owner
    state = owner.state
    identity = state.identity
    arguments = {key: value for key, value in kwargs.items() if not key.startswith('_')}
    progress = kwargs.get('_progress_queue')
    pending = state.resources.setdefault('wecom_pending_sends', {})
    requested = target = arguments.get('session')
    blocked_key = next((key for key, item in pending.items()
        if target is not None and target in {key, item.get('requested_session')}), None)
    expected_device = None
    if named_send(proxy.name,arguments) and blocked_key is None:
        from .lifecycle import LocalOperationScope
        from .proxy_tool import WecomReadSessionTool
        # Only first-use names need resolution. The read's existing owned phase
        # durably records the exact locator before the main write can be bound.
        resolved = await poll_operation(WecomReadSessionTool(),
            LocalOperationScope(owner,RESOLVE_BRANCH,0),resolve_arguments(arguments),
            tenant_id=identity.tenant_id,user_id=identity.user_id,session_id=identity.session_id,
            progress_queue=progress)
        saved = await owner.saved_phase(branch=RESOLVE_BRANCH,ordinal=0) if resolved.get('success') else None
        canonical = canonical_send_arguments(arguments,saved['invocation']) if saved else None
        if canonical is None:
            failure = ('DB_READ_INTERRUPTED' if resolved.get('effect') == 'unknown'
                or resolved.get('code') == 'EXECUTION_UNKNOWN' else resolved.get('code'))
            return {'success':False,'code':(failure if not resolved.get('success') else None)
                    or 'WECOM_SESSION_RESOLUTION_FAILED',
                'effect':'none','invocation_id':resolved.get('invocation_id'),
                'message':'目标会话尚未唯一定位，本次没有发送消息；可按原因恢复后重试，其他会话可继续。',
                'recovery_action':'retry_original_read',
                'data':{'stage':'resolve','requested_session':requested,'cause':resolved.get('code')}}
        arguments = canonical
        target = arguments['session']
        expected_device = saved['request']['device_id']
        blocked_key = next((key for key,item in pending.items()
            if target in {key,item.get('requested_session')}),None)
    blocked = pending.get(blocked_key)
    resolutions = []
    # Bound the work per tool invocation; the next ordinary read continues
    # the remaining targets without another scheduler or a model status tool.
    for session, item in list(pending.items())[:4]:
        try:
            checked = await _reconcile(owner, item, identity, progress)
        except (DeviceOperationUnresolved, SendStatusScopeError):
            checked = None
        if checked and checked.get('success') and (checked.get('data') or {}).get('send_status') == 'confirmed':
            resolutions.append({'session': session, 'invocation_id': item['invocation_id'],
                'send_status': 'confirmed', 'reconciliation': checked['data'].get('reconciliation')})
            del pending[session]
        else:
            # Persist round-robin order in the existing checkpoint map, so a
            # fifth target is not starved by four long-lived unknown sends.
            pending[session] = pending.pop(session)
    if proxy.name == 'wecom_message_send' and blocked is not None:
        return {'success': False, 'code': 'WECOM_SEND_PENDING' if blocked_key in pending else 'WECOM_SEND_RECONCILED',
            'effect': 'none', 'invocation_id': None,
            'message': ('该会话上一条发送仍待核对，本次没有再次发送；可继续读取及处理其他会话。'
                if blocked_key in pending else '上一条发送已确认，本次没有再次发送；请按最新会话内容判断后续回复。'),
            'data': {'session': target, 'send_invocation_id': blocked['invocation_id'], 'reconciled_sends': resolutions}}
    recovery = []
    reading = proxy.name in {'wecom_read_session', 'wecom_read_sessions'} and not arguments.get('attachment')
    if reading:
        from .wecom_inbound import before_read
        from .proxy_tool import WecomReadSessionTool

        async def reread(item):
            await owner.authorize_operation()
            invocation = await owner.bind_invocation(
                branch='wecom.inbound.' + item['source_invocation_id'] + '.' + str(item['msg_id']), ordinal=0,
                tool_name='wecom_read_session', device={'id': owner.wecom_device_id()},
                arguments={'session': item['session'], 'cursor': item['recovery_cursor'], 'limit': 1},
                provider_key='wecom')
            return await WecomReadSessionTool()._poll_invocation(
                invocation, identity.tenant_id, progress, durable=True)

        recovery = await before_read(owner, reread)
    result = await poll_operation(proxy, operation, arguments,
        tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=identity.session_id,
        progress_queue=progress,expected_device_id=expected_device)
    unknown = result.get('effect') == 'unknown' or result.get('code') == 'EXECUTION_UNKNOWN'
    if unknown and readonly_db_call(proxy.name, arguments):
        result = {**result, 'success': False, 'code': 'DB_READ_INTERRUPTED', 'effect': 'none',
            'message': '数据库读取中断，未发送消息、未推进游标；保留原游标后重读即可。',
            'recovery_action': 'retry_original_read'}
    elif unknown and proxy.name == 'wecom_message_send':
        original = await owner.saved_phase(branch=operation.branch, ordinal=operation.ordinal)
        if original and result.get('invocation_id'):
            canonical = target if target.startswith('ws1.') else (result.get('data') or {}).get('session') or target
            pending[canonical] = {'invocation_id': result['invocation_id'], 'requested_session': requested,
                'device_id': original['request']['device_id']}
        result = {**result, 'message': '该次发送可能已发生，原任务会只读对账；勿重发，其他独立会话可继续。',
            'recovery_action': 'reconcile_original_send'}
    if reading:
        from .wecom_inbound import after_read
        result = await after_read(owner, result, arguments, recovery=recovery)
    if resolutions:
        result = {**result, 'data': {**(result.get('data') or {}), 'reconciled_sends': resolutions}}
    return result
