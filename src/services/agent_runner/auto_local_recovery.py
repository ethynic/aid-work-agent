"""Bounded recovery of the original local invocation and its owning Runner."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from src.local_tools import repository
from src.local_tools.durable_flow import LocalContinuationRequired
from src.local_tools.execution_receipts import has_receipt_effect_conflict

from .local_invocations import LocalPhase

RETRY_LIMIT = 2
WAIT_LIMIT = timedelta(minutes=5)
CHECKPOINT_KEY = 'automatic_local_recovery'
CANCEL_CHECKPOINT_KEY = 'automatic_local_cancel'

_ACTION_REQUIRED = {
    'NOT_LOGGED_IN': ('原设备尚未登录。', '登录后可在本会话重新发起。'),
    'NO_CREDIT': ('可用积分不足。', '充值后可在本会话重新发起。'),
    'CREDIT_BLOCKED': ('可用积分不足。', '充值后可在本会话重新发起。'),
    'PAYWALL': ('原操作需要账户权限。', '确认账户权限后可在本会话重新发起。'),
    'MEMBERSHIP_REQUIRED': ('原操作需要账户权限。', '确认账户权限后可在本会话重新发起。'),
    'DEVICE_UNAVAILABLE': ('原设备不可用，未能发起本机操作。', '确认原设备上的 Runtime 已上线后可重新发起。'),
    'LOCAL_DEVICE_NOT_READY': ('原设备不可用，未能发起本机操作。', '确认原设备已连接后可重新发起。'),
    'LOCAL_DEVICE_UNSUPPORTED': ('原设备未提供本次操作所需能力。', '检查原设备上的插件后可重新发起。'),
    'LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED': ('原设备还有操作未确认停稳。', '等原设备确认停稳后可在本会话重新发起。'),
    'LOCAL_RECOVERY_BUSY': ('原设备正在处理其他操作。', '等设备空闲后可在本会话重新发起。'),
    'TOOL_NOT_ALLOWED': ('原设备未授权该工具。', '检查设备的工具授权后可在本会话重新发起。'),
    'PROVIDER_NOT_AVAILABLE': ('原设备未提供本次操作所需能力。', '检查原设备上的插件后可重新发起。'),
}
_WAITING_DEVICE_CODES = {'LOCAL_DEVICE_OFFLINE', 'LOCAL_RECOVERY_BUSY',
                         'LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED'}
_UNKNOWN_CODES = {'EXECUTION_UNKNOWN', 'EXPIRED', 'LOCAL_EFFECT_VERIFICATION_REQUIRED',
                  'LOCAL_DEADLINE_VERIFICATION_REQUIRED'}


def enabled(owner, provider):
    providers = getattr(getattr(owner, 'root', None), 'auto_local_recovery_providers', ())
    return (provider in providers and owner.state.identity.source == 'chat'
            and owner.state.identity.session_kind == 'web')


def retry_branch(phase, runner_id):
    return 'auto.retry.' + phase.key(runner_id)


def _public_token(value):
    if isinstance(value, str) and 0 < len(value) <= 80 and value.isascii() and value.replace('_', '').isalnum():
        return value
    return None


def safe_to_retry(invocation):
    observed = repository.observed_invocation(invocation)
    receipt = observed.get('result_json') or {}
    return (observed['state'] == 'failed' and observed.get('effect') == 'none'
            and receipt.get('success') is False and receipt.get('retryable') is True
            and (observed.get('error_code') or receipt.get('code')) not in _ACTION_REQUIRED
            and not has_receipt_effect_conflict({'effect': observed.get('effect'), 'data': receipt.get('data')})
            and repository.operation_completion_known(observed))


async def _remember(owner, phase, provider, status, code, invocation=None):
    key = phase.key(owner.root.attempt.runner_id)
    episodes = owner.root.envelope.setdefault(CHECKPOINT_KEY, {})
    existing = episodes.get(key)
    receipt = (repository.observed_invocation(invocation).get('result_json') or {}) if invocation else {}
    data = receipt.get('data') if isinstance(receipt.get('data'), dict) else {}
    step = data.get('step')
    label = _public_token(step) or _public_token((invocation or {}).get('tool_name'))
    known_items = sum(isinstance(item, dict) and item.get('status') == 'completed'
                      for item in data.get('partial_results', [])
                      ) if isinstance(data.get('partial_results'), list) else 0
    marker = {'provider': provider, 'execution_id': phase.execution_id, 'tool_call_id': phase.tool_call_id,
        'step': label or (existing or {}).get('step'),
        'cause': _public_token(receipt.get('code')) or (existing or {}).get('cause'),
        'known_items': known_items or (existing or {}).get('known_items', 0),
        'branch': phase.branch, 'ordinal': phase.ordinal, 'status': status, 'code': code,
        'invocation_id': str(invocation['id']) if invocation else None,
        'deadline': (existing or {}).get('deadline') or (
            (datetime.now(timezone.utc) + WAIT_LIMIT).isoformat() if status in {'waiting', 'failed'} else None)}
    if marker != existing:
        episodes[key] = marker
        await owner.control.save(owner.state, 'automatic_local_recovery')
    return marker


async def note_unbound_failure(owner, provider, result):
    """Record actionable failures produced before a local invocation could be bound."""
    code = result.get('code')
    if (enabled(owner, provider) and not result.get('success') and not result.get('invocation_id')
            and code in _ACTION_REQUIRED):
        await _remember(owner, owner._phase('main', 0), provider, 'failed', code)


async def note_verification(owner, provider, error):
    if not enabled(owner, provider) or not error.invocation_id:
        return
    current = owner.root.envelope.get(CHECKPOINT_KEY) or {}
    if any(episode.get('invocation_id') == error.invocation_id and episode.get('status') != 'observing'
           for episode in current.values()):
        return
    for reference in (owner.state.resources.get('local_invocations') or {}).values():
        if reference.get('invocation_id') != error.invocation_id or reference.get('tool_call_id') != owner.call_id:
            continue
        phase = owner._phase(reference['branch'], reference['ordinal'])
        await _remember(owner, phase, provider, 'waiting', error.code, {'id': error.invocation_id})
        return


async def poll_owned(proxy, operation, invocation, tenant_id, progress_queue):
    """Resume the last bound retry, then only reissue a verified no-effect failure."""
    owner = operation.owner
    phase = owner._phase(operation.branch, operation.ordinal)
    key = phase.key(owner.root.attempt.runner_id)
    original = invocation
    provider = proxy.provider_key
    if key not in (owner.root.envelope.get(CHECKPOINT_KEY) or {}):
        await _remember(owner, phase, provider, 'observing', None, original)
    branch = retry_branch(phase, owner.root.attempt.runner_id)
    last = 0
    for ordinal in range(1, RETRY_LIMIT + 1):
        saved = await owner.saved_phase(branch=branch, ordinal=ordinal)
        if saved is None:
            break
        invocation, last = saved['invocation'], ordinal
    async def observe_queue(queued):
        from src.local_tools.device_policy import ONLINE_THRESHOLD_SECONDS
        now = datetime.now(timezone.utc)
        marker = (owner.root.envelope.get(CHECKPOINT_KEY) or {}).get(key) or {}
        deadline = marker.get('deadline')
        if deadline and now >= datetime.fromisoformat(deadline):
            raise LocalContinuationRequired('LOCAL_RECOVERY_DEADLINE', str(queued['id']))
        created_at = queued['created_at']
        observed_at = now if created_at.tzinfo else datetime.now()
        if deadline or (observed_at - created_at).total_seconds() < ONLINE_THRESHOLD_SECONDS:
            return
        for device in await asyncio.to_thread(repository.list_devices, tenant_id, owner.state.identity.user_id):
            if str(device['id']) != str(queued['device_id']):
                continue
            from src.local_tools.device_policy import device_ready_error
            if device_ready_error(device, proxy.name, now) is None:
                return
            break
        await _remember(owner, phase, provider, 'waiting', 'LOCAL_DEVICE_OFFLINE', queued)

    while True:
        result = await proxy._poll_invocation_once(invocation, tenant_id, progress_queue,
                                                    durable=True, observe_queue=observe_queue)
        if result.get('success'):
            if key in (owner.root.envelope.get(CHECKPOINT_KEY) or {}):
                await _remember(owner, phase, provider, 'recovered', None, invocation)
            return result
        current = await asyncio.to_thread(repository.get_invocation, str(invocation['id']), tenant_id)
        if not current:
            return result
        code = result.get('code')
        verified = owner.root.envelope.get('verified_local_invocations') or []
        if (result.get('effect') == 'unknown' or code in _UNKNOWN_CODES
                or has_receipt_effect_conflict(result)):
            state = 'failed' if repository.operation_completion_known(current) or str(invocation['id']) in verified else 'waiting'
            await _remember(owner, phase, provider, state, 'LOCAL_EFFECT_VERIFICATION_REQUIRED', current)
            if state == 'waiting':
                raise LocalContinuationRequired('LOCAL_EFFECT_VERIFICATION_REQUIRED', str(invocation['id']))
            return {**result, 'effect': 'unknown', 'code': 'EXECUTION_UNKNOWN'}
        if code in _ACTION_REQUIRED:
            await _remember(owner, phase, provider, 'failed', code, current)
            return result
        if not safe_to_retry(current):
            await _remember(owner, phase, provider, 'recovered', None, invocation)
            return result
        marker = await _remember(owner, phase, provider, 'waiting', code, current)
        deadline = datetime.fromisoformat(marker['deadline'])
        if last >= RETRY_LIMIT or datetime.now(timezone.utc) >= deadline:
            await _remember(owner, phase, provider, 'failed', 'LOCAL_RECOVERY_EXHAUSTED', current)
            return result
        try:
            invocation = await owner.bind_invocation(
                branch=branch, ordinal=last + 1, device={'id': original['device_id']},
                tool_name=original['tool_name'], arguments=original.get('arguments_json') or {},
                provider_key=original.get('provider_key'), authorization_epoch=original.get('authorization_epoch'),
                execution_lane=original.get('execution_lane', 'standard'), recovery_deadline=deadline,
                recovery_key=key)
        except repository.DeviceOperationUnresolved as error:
            if str(error) in _WAITING_DEVICE_CODES:
                raise LocalContinuationRequired(str(error), str(invocation['id'])) from error
            await _remember(owner, phase, provider, 'failed', str(error), invocation)
            return result
        last += 1
        proxy._push_progress(progress_queue, {'type': 'started', 'invocation_id': str(invocation['id']),
            'text': f'正在自动重试：{proxy.display_name}（{last}/{RETRY_LIMIT}）'})


def active_failure(checkpoint):
    episodes = checkpoint.get(CHECKPOINT_KEY) or {}
    failures = [(key, item) for key, item in episodes.items() if item.get('status') == 'failed']
    if not failures:
        return None
    executions = {}
    pending = [checkpoint.get('execution') or {}]
    while pending:
        node = pending.pop()
        executions[node.get('execution_id')] = node
        pending.extend(child['checkpoint'] for child in (node.get('children') or {}).values()
                       if child.get('checkpoint'))
    for key, failure in failures:
        node = executions.get(failure.get('execution_id')) or {}
        if not _succeeded_after_failure(node, key, failure, episodes):
            return failure
    return None


def _succeeded_after_failure(node, key, failure, episodes):
    """Only a proven no-effect failure and the same bound request can match."""
    bindings = (node.get('resources') or {}).get('local_invocations') or {}
    digest = (bindings.get(key) or {}).get('intent_digest')
    order = {message['tool_call_id']: index for index, message in enumerate(node.get('messages') or [])
             if message.get('role') == 'tool' and message.get('tool_call_id')}
    failed_at = order.get(failure.get('tool_call_id'))
    code = failure.get('code')
    if not digest or failed_at is None or code in _UNKNOWN_CODES or code == 'LOCAL_RECOVERY_DEADLINE':
        return False
    tools = node.get('tools') or {}
    if code != 'LOCAL_RECOVERY_EXHAUSTED':
        # Exhaustion already proves a stopped, no-effect invocation in poll_owned.
        fact = tools.get(failure.get('tool_call_id')) or {}
        result = fact.get('result')
        if (fact.get('phase') != 'completed' or fact.get('success') is not False or not isinstance(result, dict)
                or result.get('success') is not False or not failure.get('invocation_id')
                or result.get('invocation_id') != failure['invocation_id'] or result.get('effect') != 'none'
                or has_receipt_effect_conflict(result)
                or not repository.result_proves_completion(False, result.get('code'), 'none', result.get('data'))):
            return False
    for candidate_key, binding in bindings.items():
        if (binding.get('intent_digest') != digest or binding.get('branch') != failure.get('branch')
                or binding.get('ordinal') != failure.get('ordinal')):
            continue
        later = episodes.get(candidate_key) or {}
        call_id = binding.get('tool_call_id')
        if (later.get('status') != 'recovered' or later.get('provider') != failure.get('provider')
                or order.get(call_id, -1) <= failed_at):
            continue
        fact = tools.get(call_id) or {}
        result = fact.get('result')
        if (fact.get('phase') == 'completed' and fact.get('success') is True and isinstance(result, dict)
                and result.get('success') is True and later.get('invocation_id')
                and result.get('invocation_id') == later['invocation_id']
                and result.get('effect') in {'none', 'applied'} and not has_receipt_effect_conflict(result)):
            return True
    return False


def failure_summary(failure, checkpoint):
    code = failure.get('code')
    reason, action = _ACTION_REQUIRED.get(code, (
        ('原设备未在恢复期限内返回可用结果。' if code == 'LOCAL_RECOVERY_DEADLINE'
         else '本机操作未能自动恢复。'),
        '检查原设备后可在本会话重新发起。'))
    if code in _UNKNOWN_CODES or code == 'LOCAL_RECOVERY_DEADLINE':
        reason += ' 未确认的操作没有自动重做。'
    root = checkpoint.get('execution') or {}
    pending = [root]
    completed = 0
    tool_name = None
    while pending:
        node = pending.pop()
        tools = node.get('tools') or {}
        completed += sum(item.get('phase') == 'completed' and item.get('success') is True for item in tools.values())
        if node.get('execution_id') == failure.get('execution_id'):
            call = (tools.get(failure.get('tool_call_id')) or {}).get('call') or {}
            tool_name = _public_token(call.get('name'))
        pending.extend(child['checkpoint'] for child in (node.get('children') or {}).values()
                       if child.get('checkpoint'))
    step = failure.get('step')
    label = f'工具「{tool_name}」' if tool_name else f'步骤「{step or "本机操作"}」'
    if tool_name and step and step != tool_name:
        label += f'的「{step}」阶段'
    cause = failure.get('cause') or code
    if cause:
        reason += f'（{cause}）'
    items = f'该步骤已完成 {failure["known_items"]} 项。' if failure.get('known_items') else ''
    return f'{label}未完成。{reason} 已完成 {completed} 个工具步骤，记录已保留。{items}{action}'


def _parked_episode_key(row, episodes, providers):
    if (row['source'] != 'chat' or row['session_kind'] != 'web'
            or row['status'] not in {'waiting', 'interrupted'}
            or row.get('lease_valid') or row.get('worker_id') is not None
            or row.get('cancel_requested') or row.get('pause_requested')):
        return None
    return next((key for key, marker in episodes.items()
                 if marker.get('status') in {'waiting', 'observing'} and marker.get('provider') in providers), None)


def publicly_automatic(row, checkpoint, providers, local_wait):
    """Signal only recovery owned by the background flow, without exposing its private facts."""
    if row['source'] != 'chat' or row['session_kind'] != 'web':
        return False
    if checkpoint.get(CANCEL_CHECKPOINT_KEY) is True:
        return True
    episodes = checkpoint.get(CHECKPOINT_KEY) or {}
    status = row['status']
    if status in {'finalizing', 'failed', 'completed'}:
        stopped_key = (checkpoint.get('local_verification') or {}).get('automatic_key')
        return any(key == stopped_key or marker.get('status') == 'failed'
                   or (marker.get('status') == 'recovered' and marker.get('deadline'))
                   for key, marker in episodes.items())
    if row.get('cancel_requested') or row.get('pause_requested'):
        return False
    if local_wait and _parked_episode_key(row, episodes, providers) is not None:
        return True
    if status == 'running':
        return any(marker.get('provider') in providers and marker.get('deadline')
                   and marker.get('status') in {'waiting', 'observing'} for marker in episodes.values())
    return False


def _parked(cursor, runner_id, providers):
    from .ownership import lock_runner
    row = lock_runner(cursor, runner_id)
    episodes = (row.get('checkpoint') or {}).get(CHECKPOINT_KEY) or {}
    return row, _parked_episode_key(row, episodes, providers)


def _write_parked(cursor, row, *, finalizing=False):
    from .event_repository import EventRepository
    from .persistence_limits import capped_checkpoint_dumps
    from .repository import decoded
    cursor.execute("""UPDATE agent_runners SET checkpoint=%s::jsonb,
        status=CASE WHEN %s THEN 'finalizing' ELSE status END,
        resume_control_id=CASE WHEN %s THEN NULL ELSE resume_control_id END,
        wake_at=CASE WHEN %s THEN NULL ELSE wake_at END,
        revision=revision+1,view_revision=view_revision+1,updated_at=clock_timestamp()
        WHERE runner_id=%s RETURNING *""",
        (capped_checkpoint_dumps(row['checkpoint']), finalizing, finalizing, finalizing, row['runner_id']))
    saved = decoded(cursor.fetchone())
    return EventRepository.notify_in_tx(cursor, row, after=saved)


def _stage_failure(cursor, row, key, code):
    from src.core.agent_engine.contracts import ExecutionState
    from .control_repository import close_pending_controls
    from .local_recovery import _cancel_facts
    from .worker import execution_result

    # Serialize cancellation and the resulting per-device holds with new admits.
    repository.lock_operation_admission(cursor, row['tenant_id'])
    facts = _cancel_facts(cursor, row, request=False)
    if any(fact.get('kind') == 'domain' and not fact.get('known')
           or (fact.get('kind') == 'local' and not fact['completion_known'] and not fact['holdable'])
           for fact in facts):
        return False
    facts = _cancel_facts(cursor, row, request=True)
    checkpoint = row['checkpoint']
    verification_id = (checkpoint.get('local_verification') or {}).get('invocation_id')
    if verification_id:
        repository.request_cancel_in_tx(cursor, verification_id, row['tenant_id'])
    verified = set(checkpoint.get('verified_local_invocations') or ())
    held = sorted(str(fact['id']) for fact in facts if fact.get('kind') == 'local'
                  and not fact['completion_known'] and not fact.get('readonly_db') and str(fact['id']) not in verified)
    if held:
        checkpoint.update(detached_local_wait=True, detached_local_invocations=held)
    marker = checkpoint[CHECKPOINT_KEY][key]
    marker.update(status='failed', code=code)
    state = ExecutionState.restore(checkpoint['execution'])
    checkpoint['pending_finalization'] = execution_result(state, 'failed', code)
    checkpoint['pending_finalization']['output'] = failure_summary(marker, checkpoint)
    checkpoint['pending_finalization']['assistant_metadata'] = checkpoint.get('presentation') or {}
    close_pending_controls(cursor, row['runner_id'], 'RUNNER_EXECUTION_CLOSED')
    _write_parked(cursor, row, finalizing=True)
    return True


def _waiting_candidates(connection_factory, after):
    with connection_factory() as connection:
        cursor = connection.cursor()
        cursor.execute("""SELECT runner_id,queue_order FROM agent_runners
            WHERE source='chat' AND session_kind='web' AND status IN ('waiting','interrupted')
            AND worker_id IS NULL AND lease_until IS NULL AND NOT cancel_requested
            AND queue_order>%s AND checkpoint ? %s ORDER BY queue_order LIMIT 16""",
            (after, CHECKPOINT_KEY))
        return cursor.fetchall()


def owned_recovery_invocation(cursor, row, marker, *, lock=False):
    query = """SELECT *, lease_expires_at>clock_timestamp() AS device_lease_valid
        FROM local_tool_invocations WHERE id=%s AND tenant_id=%s AND user_id=%s AND session_id=%s
        AND business_kind='agent_runner' AND business_ref->>'runner_id'=%s
        AND business_ref->>'execution_id'=%s AND business_ref->>'tool_call_id'=%s"""
    cursor.execute(query + (' FOR UPDATE' if lock else ''),
        (marker['invocation_id'], row['tenant_id'], row['user_id'], row['session_id'],
         row['runner_id'], marker['execution_id'], marker['tool_call_id']))
    return cursor.fetchone()


def trusted_local_success(invocation):
    if not invocation:
        return False
    observed = repository.observed_invocation(invocation)
    result = observed.get('result_json') or {}
    return (observed.get('state') == 'succeeded' and observed.get('effect') in {'none', 'applied'}
            and result.get('success') is True and not has_receipt_effect_conflict(
                {'effect': observed['effect'], 'data': result.get('data')}))


def _advance_candidate(connection_factory, runner_id, providers):
    from .stop_continue import dispatch_continue_resume_in_tx
    with connection_factory() as connection:
        cursor = connection.cursor()
        row, key = _parked(cursor, runner_id, providers)
        if not key:
            return None
        checkpoint = row['checkpoint']
        marker = checkpoint[CHECKPOINT_KEY][key]
        cursor.execute('SELECT clock_timestamp() AS now')
        now = cursor.fetchone()['now']
        if not marker['deadline']:
            marker['deadline'] = (now + WAIT_LIMIT).isoformat()
            row = _write_parked(cursor, row)
        expired = now >= datetime.fromisoformat(marker['deadline'])
        if expired:
            # Lock in the same order as detachment: Runner, admission, original.
            # A device claim or final result cannot race the cutoff decision.
            repository.lock_operation_admission(cursor, row['tenant_id'])
        invocation = owned_recovery_invocation(cursor, row, marker, lock=expired)
        if expired:
            if trusted_local_success(invocation):
                dispatch_continue_resume_in_tx(cursor, row, checkpoint, automatic_key=key)
            elif not (invocation and invocation['state'] in {'claimed', 'running'}
                      and invocation['device_lease_valid']):
                active = row.get('resume_control_id')
                if active:
                    cursor.execute('SELECT client_request_id FROM agent_runner_controls WHERE control_id=%s',
                                   (active,))
                    control = cursor.fetchone()
                    active = not control or control['client_request_id'].split('#', 1)[0] != 'auto-local:' + key
                if not active:
                    _stage_failure(cursor, row, key, 'LOCAL_RECOVERY_DEADLINE')
            connection.commit()
            return None
        if row.get('resume_control_id') or not invocation:
            connection.commit()
            return None
        verified = checkpoint.get('verified_local_invocations') or []
        settled = repository.operation_completion_known(invocation) or str(invocation['id']) in verified
        if settled:
            if safe_to_retry(invocation):
                from src.local_tools.device_policy import device_ready_error
                cursor.execute("""SELECT * FROM local_tool_devices WHERE id=%s AND tenant_id=%s AND user_id=%s""",
                    (invocation['device_id'], row['tenant_id'], row['user_id']))
                device = cursor.fetchone()
                if not device or device_ready_error(device, invocation['tool_name'], now):
                    connection.commit()
                    return None
            dispatch_continue_resume_in_tx(cursor, row, checkpoint, automatic_key=key)
            connection.commit()
            return None
        connection.commit()
        if invocation['state'] in repository.TERMINAL_STATES:
            return row, key
        return None


def fail_automatic_resume(connection_factory, runner_id, control_id, error_code):
    with connection_factory() as connection:
        cursor = connection.cursor()
        cursor.execute("""SELECT client_request_id,status FROM agent_runner_controls
            WHERE control_id=%s AND runner_id=%s""", (control_id, runner_id))
        control = cursor.fetchone()
        prefix = 'auto-local:'
        if not control or control['status'] != 'rejected' or not control['client_request_id'].startswith(prefix):
            return
        key = control['client_request_id'][len(prefix):].split('#', 1)[0]
        from .ownership import lock_runner
        row = lock_runner(cursor, runner_id)
        marker = ((row.get('checkpoint') or {}).get(CHECKPOINT_KEY) or {}).get(key) or {}
        if (row['status'] in {'waiting', 'interrupted'} and not row['lease_valid']
                and row.get('worker_id') is None and not row.get('resume_control_id')
                and not row.get('cancel_requested') and not row.get('pause_requested')
                and marker.get('status') in {'waiting', 'observing'}):
            _stage_failure(cursor, row, key, error_code)
            connection.commit()


async def advance_waiting(runners, authorizer, providers, after):
    """Give parked local operations bounded background reconciliation."""
    from .contracts import RunnerError
    from .local_verification import verify_local
    candidates = await asyncio.to_thread(_waiting_candidates, runners.connection_factory, after)
    for candidate in candidates:
        plan = await asyncio.to_thread(_advance_candidate, runners.connection_factory,
                                       candidate['runner_id'], providers)
        if plan is None:
            continue
        row, key = plan
        try:
            principal = await asyncio.to_thread(authorizer.authorize_persisted, row, execute=False)
            await asyncio.to_thread(verify_local, runners, principal, row['runner_id'],
                'automatic:' + key, stop_resources=True, continue_after_stop=True, automatic_key=key)
        except RunnerError:
            # Offline, lack of stop capability and a concurrent transition are
            # handled by the same durable wait deadline, not another dispatch.
            continue
    return candidates[-1]['queue_order'] if candidates else 0


def reject_unknown_replay(cursor, row, tool_name, arguments):
    checkpoint = row.get('checkpoint') or {}
    ids = [marker['invocation_id'] for marker in (checkpoint.get(CHECKPOINT_KEY) or {}).values()
           if marker.get('status') == 'failed' and marker.get('code') in _UNKNOWN_CODES
           and marker.get('invocation_id')]
    if not ids:
        return
    cursor.execute("""SELECT 1 FROM local_tool_invocations WHERE tenant_id=%s
        AND id=ANY(%s::uuid[]) AND tool_name=%s AND arguments_json=%s::jsonb LIMIT 1""",
        (row['tenant_id'], ids, tool_name, json.dumps(arguments, ensure_ascii=False)))
    if cursor.fetchone():
        raise repository.DeviceOperationUnresolved('LOCAL_EFFECT_VERIFICATION_REQUIRED')
