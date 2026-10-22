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


def _page_restored(result):
    """CLI 自动恢复已确认页面可用的回执标记（data.visual_recovery.page_restored）。

    标记由 CLI 恢复 owner（localOperationRecovery）在确认原页面可继续操作后
    写入（page_restored_result_preserved 等恢复成功状态）；旧回执无此标记
    → False，行为不变。stop_reason 诊断信息保留在回执中，不由本函数改写。
    """
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    report = data.get('visual_recovery')
    return isinstance(report, dict) and report.get('page_restored') is True


# 页面状态类停止原因：批次因页面无法继续操作提前停止（详情关不掉/页面结构
# 异常/列表两次未推进等），部分读取成果仍保留在游标里。自动恢复未成功（回执
# 无 page_restored 标记）时不再仅凭旧回执永久禁止续筛——人工修复页面后重新
# 调用经既有入口校验（boss_resume_next 的推荐页/职位核验）本次再核验，通过
# 即沿原游标继续；校验不过返回可重试错误（WRONG_PAGE/UI_CHANGED 等，重试侧
# 另有无成果放行规则），不形成永久阻断。语义区别：自动恢复=回执已核验页面
# 可用；人工恢复=本次调用时再核验。
# search_limit（滚动 30 轮上限）、cancelled（用户主动取消）、unknown_stop
# 语义不同，维持禁止。
PAGE_STATE_STOP_REASONS = frozenset({'detail_not_closed', 'page_operation_failed',
    'UI_CHANGED', 'no_progress'})


def failed_attempt_names(attempts, exclude_ids=(), *, history_version=1):
    """沿同一游标「失败且可重试」的续筛尝试中已标记处理过的候选人名单。

    取卡即标记含失败路径：processed_names 有名字不代表有简历读取成果
    （成果只在 resumes）。这些失败候选人沿用游标排除语义合并进新的
    exclude_names——人工修复页面后的重试不再重复尝试（与批次内单份失败
    自动跳过一致）；非 retry-safe 的记录（成功/挂起/有成果失败）不合并，
    其名单已进入各自回执的游标。exclude_ids 用于恢复证明时排除当前调用
    自身的记录（派发时尚不存在，恢复时已存在）。

    返回按名字排序：派发（prepare_next）与模型授权（continuation_arguments）
    各自独立查询历史尝试，DB 返回顺序不保证一致——名单顺序敏感会让恢复
    证明按列表严格比较时误拒（LOCAL_RESUME_CONTINUATION_PROOF_INVALID）。
    排序后合成结果与两侧查询顺序无关。
    """
    merged = []
    for record in attempts or ():
        # Frozen v1 requests predate eligibility of returned readonly unknowns.
        if history_version == 1 and repository.observed_invocation(record).get('state') != 'failed':
            continue
        if str(record.get('id')) in set(exclude_ids):
            continue
        if not repository.continuation_retry_safe(record):
            continue
        data = (record.get('result_json') or {}).get('data')
        if isinstance(data, dict):
            if history_version == 1:
                # Existing frozen requests used take-card exclusion semantics.
                # Preserve their proof rather than reinterpret an accepted call.
                merged.extend(_names(data, {}))
                continue
            # A temporary input error is not a completed candidate. Only final
            # per-item failures belong to the exclusion history of a retry.
            failures = data.get('failures') or []
            completed = [item for item in failures if isinstance(item, dict)
                and not isinstance(item.get('operation_recovery'), dict)]
            merged.extend(_names({'failures': completed}, {}))
    return sorted(dict.fromkeys(merged))


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
    if (not available and reason not in ('unsafe_result', 'missing_context', 'history_limit')
            and reason not in FILTER_TERMINAL_REASONS
            and (_page_restored(result) or reason in PAGE_STATE_STOP_REASONS)):
        # 页面已确认或待重核验即允许沿游标续筛：
        # - 自动恢复确认（回执 visual_recovery.page_restored=true）：已核验页面可用；
        # - 页面状态类停止原因（detail_not_closed/page_operation_failed 等）：自动
        #   恢复未成功也不永久禁止，人工修复后重新调用时经既有入口校验再核验。
        # 已读成果经 exclude_names 游标保留（不整批重读、不重复计费），原 stop_reason
        # 诊断信息原样保留在回执与 reason 中；unsafe/missing_context/history_limit/
        # 终局筛选门禁不被本放宽凌驾。
        available = True
    from .boss_visual_recovery import public_recovery_metadata
    return {**public_recovery_metadata(result), 'continuation_id': result.get('invocation_id'),
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
        or parent.get('tool_name') not in ('boss_resume_batch', 'boss_resume_next')):
        return None, error('INVALID_CONTINUATION', '上次筛选记录不存在、归属不符或未安全完成，请恢复原筛选记录')
    parent = repository.observed_invocation(parent)
    if parent.get('state') != 'succeeded' or parent.get('effect') != 'none':
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
    # 沿用游标排除语义：合并「失败且可重试」历史尝试中已标记处理过的候选人
    # （含失败路径的取卡标记），人工修复后重试不再重复尝试这些失败候选人
    attempts = await asyncio.to_thread(repository.list_resume_next_attempts, parent_id,
        tenant_id, user_id, session_id, str(parent['device_id']))
    names = list(dict.fromkeys(names + failed_attempt_names(attempts, history_version=2)))
    job = data.get('job_name')
    if not names or len(names) >= 1000 or not isinstance(job, str) or not job.strip():
        return None, error('INVALID_CONTINUATION', '上次筛选缺少职位或已处理名单，或已达1000人续筛边界')
    canonical = {'continue_from': parent_id, 'limit': limit, 'exclude_names': names,
        'expected_job_name': job.strip(), '_resume_expected_device_id': str(parent['device_id']),
        '_resume_history_version': 2}
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
