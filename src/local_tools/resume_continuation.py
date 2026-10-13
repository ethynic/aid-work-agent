"""Trusted resume cursors; model-provided names never define continuation history."""

import asyncio
from uuid import UUID

from . import repository
from .durable_flow import LocalContinuationRequired

FILTER_TERMINAL_REASONS = ('no_matching_candidates', 'recommendations_boundary')


def filter_stop_message(reason):
    if reason == 'no_matching_candidates':
        return '当前筛选条件下没有匹配候选人；下方为补充推荐，已排除，请告知用户并等待调整条件'
    if reason == 'recommendations_boundary':
        return '已到达补充推荐区域，没有更多匹配候选人；已排除推荐项并停止续筛'
    return ''


def filter_terminal_result(result, metadata):
    """Explicit empty matches are a normal stop, never a recognition/store request."""
    data = result.get('data') or {}
    reason = data.get('stop_reason')
    if reason not in FILTER_TERMINAL_REASONS:
        return None
    if result.get('effect') != 'none':
        return {**result, 'success': False, 'code': 'EXECUTION_UNKNOWN',
            'message': '执行效果未知，无法确认筛选结果状态；已停止识别和入库，请人工核对页面',
            'data': {**metadata, 'resumes': [], 'failures': []}}
    # A no-match receipt cannot also contain resumes or failed card attempts.
    # Fail closed before VL/fee/store if a faulty Provider mixes recommendations in.
    if reason == 'no_matching_candidates' and (
        data.get('resumes') != [] or data.get('failures')
        or type(data.get('attempted')) is not int or data['attempted'] != 0
    ):
        return {**result, 'success': False, 'code': 'RESUME_PAYLOAD_INVALID',
            'message': '客户端无匹配结果与卡片读取回执矛盾，已停止识别和入库',
            'data': {**metadata, 'resumes': [], 'failures': []}}
    if data.get('resumes') == [] and not data.get('failures'):
        return {**result, 'data': {**metadata, 'resumes': [], 'failures': []},
            'message': filter_stop_message(reason)}
    return None


def _names(data, arguments):
    names = data.get('processed_names')
    values = list(arguments.get('exclude_names') or []) + (names if isinstance(names, list) else [])
    resumes = data.get('resumes')
    for item in resumes if isinstance(resumes, list) else []:
        if isinstance(item, dict):
            values.append(item.get('candidate_name'))
    failures = data.get('failures')
    for item in failures if isinstance(failures, list) else []:
        if isinstance(item, dict):
            values.append(item.get('name'))
    return list(dict.fromkeys(value.strip() for value in values
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 30
        and not value.startswith('row@')))


def batch_metadata(result, arguments=None):
    """Only compact fields are exposed, including when all VL/store attempts fail."""
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    cumulative = _names(data, arguments or {})
    reason = data.get('stop_reason')
    reference = result.get('invocation_id')
    try:
        reference = str(UUID(str(reference)))
    except (ValueError, TypeError, AttributeError):
        reference = None
    if result.get('success') is not True or result.get('effect') != 'none':
        reason = 'unsafe_result'
    elif reason in FILTER_TERMINAL_REASONS:
        pass  # Terminal page evidence remains useful even without any processed name.
    elif (not reference or not cumulative or not isinstance(data.get('job_name'), str)
        or not data['job_name'].strip() or len(data['job_name'].strip()) > 100):
        reason = 'missing_context'
    elif len(cumulative) >= 1000:
        reason = 'history_limit'
    available = reason in ('limit_reached', 'viewport_complete')
    return {'continuation_id': result.get('invocation_id'),
        'processed_names': _names(data, {}), 'stop_reason': data.get('stop_reason'),
        'job_name': data.get('job_name'), 'attempted': data.get('attempted'),
        'continuation': {'available': available, 'reason': reason or 'missing_context',
            'tool': 'boss_resume_next' if available else None,
            'arguments': {'continue_from': reference} if available else None,
            'processed_count': len(cumulative), 'deduplication': 'candidate_name',
            'filters_verified': False}}


def error(code, message):
    return {'success': False, 'code': code, 'message': message, 'data': None}


async def prepare_next(kwargs, operation=None):
    intent = {'continue_from': kwargs.get('continue_from'), 'limit': kwargs.get('limit', 3)}
    try:
        parent_id = str(UUID(str(intent['continue_from'])))
    except (ValueError, TypeError, AttributeError):
        return None, error('INVALID_ARGS', '续筛需要上一次结果的 continuation_id，不能从头猜测')
    limit = intent['limit']
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3:
        return None, error('INVALID_ARGS', '每批续筛必须为1-3份')
    identity = [kwargs.get(key) for key in ('_trusted_tenant_id', '_trusted_user_id', '_session_id')]
    if not all(identity):
        return None, error('NO_IDENTITY', '续筛需要当前用户及会话身份')
    if operation is not None:
        saved = await operation.owner.saved_domain(branch='resume.next.request', ordinal=operation.ordinal)
        if saved is not None:
            if saved['request'] != intent or saved.get('phase') != 'completed':
                raise LocalContinuationRequired('LOCAL_RESUME_INTENT_CHANGED')
            canonical = saved['result']
            return {**{k: v for k, v in kwargs.items() if k.startswith('_')}, **canonical}, None
    tenant_id, user_id, session_id = identity
    parent = await asyncio.to_thread(repository.get_invocation, parent_id, tenant_id)
    if (not parent or str(parent.get('tenant_id')) != str(tenant_id)
        or str(parent.get('user_id')) != str(user_id) or str(parent.get('session_id')) != str(session_id)
        or parent.get('tool_name') not in ('boss_resume_batch', 'boss_resume_next')
        or parent.get('state') != 'succeeded' or parent.get('effect') != 'none'):
        return None, error('INVALID_CONTINUATION', '上次筛选记录不存在、归属不符或未安全完成，请恢复原筛选记录')
    arguments = parent.get('arguments_json') or {}
    result = parent.get('result_json') or {}
    if not isinstance(result, dict):
        return None, error('INVALID_CONTINUATION', '上次筛选记录格式无效，未重新读取')
    data = result.get('data') or {}
    if not isinstance(data, dict) or not isinstance(arguments, dict) or result.get('success') is not True:
        return None, error('INVALID_CONTINUATION', '上次筛选缺少可靠读取结果，未重新读取')
    capability = batch_metadata({**result, 'invocation_id': parent_id, 'effect': parent['effect']}, arguments)['continuation']
    if not capability['available']:
        return None, error('CONTINUATION_STOPPED', f"原筛选不可直接续读（{capability['reason']}），未重新读取")
    names = _names(data, arguments)
    job = data.get('job_name')
    if not names or len(names) >= 1000 or not isinstance(job, str) or not job.strip():
        return None, error('INVALID_CONTINUATION', '上次筛选缺少职位或已处理名单，或已达1000人续筛边界')
    canonical = {'continue_from': parent_id, 'limit': limit, 'exclude_names': names,
        'expected_job_name': job.strip(), '_resume_expected_device_id': str(parent['device_id'])}
    if operation is not None:
        canonical = await operation.owner.complete_phase(branch='resume.next.request', ordinal=operation.ordinal,
            intent=intent, result=canonical)
    return {**{k: v for k, v in kwargs.items() if k.startswith('_')}, **canonical}, None


def duplicate_failure(payload, excluded, seen, expected_job=None):
    """Gate BEFORE any model/fee/store work, also covering a faulty Provider."""
    if not isinstance(payload, dict):
        return None
    name = str(payload.get('candidate_name') or '').strip()
    if name and (name in excluded or name in seen):
        return {'name': name, 'error': '已处理候选人，已跳过重复识别、计费和入库'}
    if expected_job and payload.get('job_name') != expected_job:
        return {'name': name or None, 'error': '候选人职位与原筛选职位不符，已跳过识别和计费'}
    if name:
        seen.add(name)
    return None
