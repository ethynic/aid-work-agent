"""Recruiting notification continuation over the existing neutral phase port.

The registered domain composition supplies the existing log DAL's cursor
writer and includes these HTTP facts in its cancellation proof. A started POST is never an instruction to send it again.
"""

import asyncio
import hashlib
import json

import httpx

from .durable_flow import LocalContinuationRequired


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def _configuration(settings):
    """Bind a current trusted configuration without persisting its credential."""
    version = settings.get('updated_at')
    if version is None:
        raise LocalContinuationRequired('LOCAL_NOTIFY_CONFIGURATION_VERIFICATION_REQUIRED')
    return {'tenant_id': settings['tenant_id'], 'version': version.isoformat(),
        'at_digest': _digest(list(settings.get('at_mobiles') or []))}


async def _post_phase(owner, *, branch, ordinal, intent, dispatch):
    previous = await owner.saved_domain(branch=branch, ordinal=ordinal)
    if previous is not None:
        if previous['request'] != intent:
            raise LocalContinuationRequired('LOCAL_NOTIFY_INTENT_CHANGED')
        if previous.get('phase') != 'completed':
            raise LocalContinuationRequired('LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED')
        result = previous['result']
    else:
        await owner.authorize_operation()
        url, payload = await dispatch()
        # The original dispatch fence and durable intent precede all network IO.
        await owner.start_domain(branch=branch, ordinal=ordinal, intent=intent)
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(url, json=payload)
            data = response.json() if response.status_code == 200 else None
            code = data.get('errcode') if isinstance(data, dict) else None
            if type(code) is not int:
                result = {'outcome': 'unknown', 'error_code': 'LOCAL_NOTIFY_ACK_UNKNOWN'}
            else:
                result = {'outcome': 'acknowledged' if code == 0 else 'rejected',
                    'provider_code': code}
        except (httpx.HTTPError, ValueError):
            # No provider text, URL or driver exception is copied to the fact.
            result = {'outcome': 'unknown', 'error_code': 'LOCAL_NOTIFY_ACK_UNKNOWN'}
        # Observation verifies the original started intent and valid attempt;
        # it grants no permission to send the next chunk after cancellation.
        result = await owner.observe_domain(branch=branch, ordinal=ordinal,
            intent=intent, writer=lambda cursor, row, request: result)
    if result.get('outcome') not in {'acknowledged', 'rejected'}:
        raise LocalContinuationRequired('LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED')
    return result


async def _send(owner, *, branch, ordinal, configuration, payload_digest, dispatch):
    """Retry only a known provider rejection; every new POST rechecks settings."""
    for attempt in range(2):
        intent = {'configuration': configuration, 'payload_digest': payload_digest,
            'chunk': ordinal, 'attempt': attempt, 'notify_phase_version': 1}
        result = await _post_phase(owner, branch=branch, ordinal=ordinal * 2 + attempt,
            intent=intent, dispatch=dispatch)
        if result['outcome'] == 'acknowledged':
            return result
        if attempt == 0:
            await asyncio.sleep(1)
    return result


async def _audit(owner, policy, log_writer):
    """Audit only known original POSTs, including a cancelled known prefix.

    The application owner independently proves this exact intent from all
    original HTTP facts in the log's cursor transaction. This helper grants no
    permission to send, and a started/unknown result cannot become a failed ACK.
    """
    value = policy['result']
    attempted, acknowledged, complete = 0, 0, True
    for branch, count in (('notify.markdown', value['markdown_count']),
                          ('notify.mention', int(value['mention_required']))):
        for chunk in range(count):
            chunk_ack = False
            for index in range(2):
                fact = await owner.saved_domain(branch=branch, ordinal=chunk * 2 + index)
                if fact is None:
                    continue
                result = fact.get('result') or {}
                if (fact.get('phase') != 'completed'
                        or result.get('outcome') not in {'acknowledged', 'rejected'}):
                    raise LocalContinuationRequired('LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED')
                attempted += 1
                chunk_ack |= result['outcome'] == 'acknowledged'
            acknowledged += int(chunk_ack)
            complete &= chunk_ack
    if not attempted:
        return {'pushed': False, 'reason': '通知尚未发送'}
    request = policy['request']
    intent = {'kind': request['kind'], 'candidates': request['candidates'],
        'content': value['content'], 'resume_id': request['resume_id'],
        'configuration': value['configuration'], 'policy_digest': _digest(request),
        'status': 'sent' if complete else 'failed',
        'error': None if complete else '通知未完整送达，已确认的发送已留痕',
        'http_summary': {'attempted': attempted, 'acknowledged': acknowledged,
                         'complete': bool(complete)}, 'notify_log_version': 1}
    return await owner.commit_postprocess(branch='notify.log', ordinal=0,
        intent=intent, writer=log_writer)


async def push_notify(owner, *, tenant_id, kind, job_name, candidates, note=None,
                      resume_id=None, log_writer):
    """Original templates/chunks over bounded, immutable physical POST facts.

    The application owner implements the finite notify-log postprocess proof
    and includes all original HTTP facts in cancellation safety.
    """
    from src.services import recruiting_notify_service as service, wecom_bot

    if kind not in service.NOTIFY_KINDS:
        return {'pushed': False, 'reason': '未知通知类型'}
    original = {'kind': kind, 'job_name': job_name,
        'candidates': service._normalize_candidates(candidates), 'note': note}
    completed = await owner.saved_domain(branch='notify.log', ordinal=0)
    if completed is not None:
        if completed.get('phase') != 'completed':
            raise LocalContinuationRequired('LOCAL_NOTIFY_LOG_VERIFICATION_REQUIRED')
        return completed['result']
    binding_intent = {**original, 'notify_binding_version': 1}
    binding = await owner.saved_domain(branch='notify.binding', ordinal=0)
    if binding is None:
        bound = await owner.complete_phase(branch='notify.binding', ordinal=0,
            intent=binding_intent, result={'resume_id': resume_id})
    else:
        if binding.get('phase') != 'completed' or binding['request'] != binding_intent:
            raise LocalContinuationRequired('LOCAL_NOTIFY_BINDING_CHANGED')
        bound = binding['result']
    policy_intent = {**original, 'resume_id': bound['resume_id'], 'notify_policy_version': 1}
    policy = await owner.saved_domain(branch='notify.policy', ordinal=0)
    if policy is None:
        settings = await asyncio.to_thread(service.get_settings, tenant_id)
        if not settings or not settings.get('enabled') or not settings.get('webhook_url'):
            return {'pushed': False, 'reason': '未启用'}
        if kind == 'pre' and not settings.get('pre_notify_enabled', True):
            return {'pushed': False, 'reason': '事前知会未启用'}
        content = (service.format_pre_content(job_name, candidates, note) if kind == 'pre'
            else service.format_done_content(job_name, candidates))
        mention = {'msgtype': 'text', 'text': {'content': '面试邀约已完成，请查看上条详情',
            'mentioned_mobile_list': list(settings.get('at_mobiles') or [])}}
        value = await owner.complete_phase(branch='notify.policy', ordinal=0,
            intent=policy_intent, result={'configuration': _configuration(settings),
                'content': content, 'markdown_count': len(wecom_bot._split_markdown(content)),
                'mention_required': bool(kind == 'done' and settings.get('at_mobiles')),
                'mention_payload_digest': _digest(mention)})
        policy = {'request': policy_intent, 'result': value}
    else:
        if policy.get('phase') != 'completed' or policy['request'] != policy_intent:
            raise LocalContinuationRequired('LOCAL_NOTIFY_POLICY_CHANGED')
        value = policy['result']

    async def dispatch(payload_factory):
        # This is called only for a genuinely new POST. Pure old ACK/log reads
        # do not depend on a later disabled configuration or changed target.
        current = await asyncio.to_thread(service.get_settings, tenant_id)
        if (not current or not current.get('enabled') or not current.get('webhook_url')
                or kind == 'pre' and not current.get('pre_notify_enabled', True)
                or _configuration(current) != value['configuration']):
            raise LocalContinuationRequired('LOCAL_NOTIFY_CONFIGURATION_CHANGED')
        return current['webhook_url'], payload_factory(current)

    try:
        for ordinal, chunk in enumerate(wecom_bot._split_markdown(value['content'])):
            payload = {'msgtype': 'markdown', 'markdown': {'content': chunk}}
            result = await _send(owner, branch='notify.markdown', ordinal=ordinal,
                configuration=value['configuration'], payload_digest=_digest(payload),
                dispatch=lambda payload=payload: dispatch(lambda settings: payload))
            if result['outcome'] != 'acknowledged':
                return await _audit(owner, policy, log_writer)
        if value['mention_required']:
            await _send(owner, branch='notify.mention', ordinal=0,
                configuration=value['configuration'], payload_digest=value['mention_payload_digest'],
                dispatch=lambda: dispatch(lambda settings: {'msgtype': 'text', 'text': {
                    'content': '面试邀约已完成，请查看上条详情',
                    'mentioned_mobile_list': list(settings.get('at_mobiles') or [])}}))
        return await _audit(owner, policy, log_writer)
    except BaseException as error:
        if getattr(error, 'command', None) == 'cancel':
            # Retain original known delivery audit, then let the execution owner
            # stop. Pause/lease loss/unknown ACK do not acquire this permission.
            await _audit(owner, policy, log_writer)
        raise
