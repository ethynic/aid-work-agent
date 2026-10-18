"""Legacy client recovery compatibility; current CLI owns the complete recovery flow."""

from copy import deepcopy
import re
from uuid import NAMESPACE_URL, UUID, uuid5

# Compatibility only: deployed adapters use their server-owned policy and canonical facts.
_LEGACY_PAGE_TOOLS = frozenset({'boss_filter', 'boss_clear_filter', 'boss_open_detail'})
_POLICIES = {'page_state': 'page_state', 'readonly': 'readonly', 'observe_only': 'external_effect'}
_CURRENT_CONTACT_TOOLS = frozenset({'boss_send_current', 'boss_reject_current', 'boss_accept_resume'})


def cli_owns_recovery(result):
    """An explicit CLI receipt prevents a second server repair, including failed local repairs."""
    data = result.get('data')
    report = data.get('visual_recovery') if isinstance(data, dict) else None
    return (isinstance(report, dict) and type(report.get('schema_version')) is int
            and report['schema_version'] == 1 and report.get('owner') == 'cli')


def recovery_context(proxy, result):
    if not getattr(proxy, 'heal_eligible', False) or len(proxy.name) > 100 or not re.fullmatch(r'boss_[a-z0-9_]+', proxy.name):
        return None
    data = result.get('data')
    if not isinstance(data, dict):
        return None
    modern = 'operation_recovery' in data
    facts = data.get('operation_recovery' if modern else 'page_operation')
    policy = getattr(proxy, 'visual_recovery_policy', None)
    if not modern:
        if proxy.name not in _LEGACY_PAGE_TOOLS:
            return None
        policy = 'page_state'
    if policy not in _POLICIES or not isinstance(facts, dict) or type(facts.get('schema_version')) is not int or facts['schema_version'] != 1:
        return None
    target_ref = data.get('browser_target_ref')
    fact_ref = facts.get('browser_target_ref')
    if target_ref is not None or fact_ref is not None:
        try:
            if not isinstance(target_ref, str) or str(UUID(target_ref)) != target_ref or fact_ref not in (None, target_ref):
                return None
        except (ValueError, AttributeError):
            return None
    if modern and facts.get('operation_class') != _POLICIES[policy]:
        return None
    operation, execution, verification = (facts.get(key) for key in ('operation', 'execution', 'verification'))
    if not all(isinstance(value, dict) for value in (operation, execution, verification)):
        return None
    kind = operation.get('result_kind')
    kinds = {'page_state', 'business_result'} if policy == 'readonly' else {'business_result'} if policy == 'observe_only' else {'page_state'}
    if operation.get('name') != proxy.name or kind not in kinds:
        return None
    if policy == 'readonly' and kind == 'business_result' and result.get('success') is not True:
        return None  # Retained read results use a successful receipt plus a separate cleanup error.
    business = facts.get('business_effect')
    if business is not None and (policy != 'observe_only' or not isinstance(business, dict)
        or business.get('status') not in {'not_started', 'confirmed', 'unknown'}
        or type(business.get('completed_count')) is not int or business['completed_count'] < 0
        or business['status'] == 'not_started' and business['completed_count'] != 0):
        return None
    contact_ref = facts.get('recovery_contact_ref')
    if contact_ref is not None and (not isinstance(contact_ref, str) or not re.fullmatch(r'[a-f0-9]{64}', contact_ref)):
        return None
    if any(not isinstance(operation.get(key), str) or not 0 < len(operation[key]) <= 1000 for key in ('goal', 'expected')):
        return None
    target = operation.get('target_label')
    if target is not None and (not isinstance(target, str) or not 0 < len(target) <= 200):
        return None
    attempts, retries = execution.get('attempts_used'), execution.get('ordinary_retries_used')
    if type(attempts) is not int or type(retries) is not int or not 1 <= attempts or not 0 <= retries < attempts:
        return None
    if not modern and attempts > 2:
        return None
    if execution.get('stage') not in {'prepared', 'input', 'postcheck'} or execution.get('input_status') not in {'not_started', 'attempted', 'unknown'}:
        return None
    if verification.get('status') not in {'satisfied', 'not_satisfied', 'inconclusive'}:
        return None
    evidence = verification.get('evidence')
    if not isinstance(evidence, list) or len(evidence) > 10 or any(not isinstance(item, str) or not 0 < len(item) <= 500 for item in evidence):
        return None
    if modern and verification['status'] != 'inconclusive' and not evidence:
        return None
    context = {key: deepcopy(facts[key]) for key in ('operation', 'execution', 'verification')}
    if isinstance(facts.get('error'), dict):
        error = facts['error']
        if all(isinstance(error.get(key), str) and 0 < len(error[key]) <= limit for key, limit in [('code', 100), ('source', 100), ('summary', 1000)]):
            context['error'] = deepcopy(error)
    return context, 'preserve_result' if policy == 'readonly' and kind == 'business_result' else policy, modern


def recovery_arguments(original, original_args, phase_args):
    """Keep local browser identity outside model context and preserve the exact intent."""
    arguments = dict(original_args if phase_args is None else phase_args)
    target_ref = (original.get('data') or {}).get('browser_target_ref')
    if target_ref:
        arguments['_boss_target_ref'] = target_ref
    facts = (original.get('data') or {}).get('operation_recovery') or {}
    if phase_args is None:
        contact_ref = facts.get('recovery_contact_ref')
        if contact_ref:
            arguments['_boss_expected_contact_ref'] = contact_ref
        operation = facts.get('operation') or {}
        if operation.get('name') == 'boss_read_chat' and not arguments.get('contact') and operation.get('target_label'):
            arguments['contact'] = operation['target_label']
    return arguments


def public_recovery_metadata(result):
    """Keep Runner's compact recovery report when resume images are consumed privately."""
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    return {key: deepcopy(data[key]) for key in ('visual_recovery', 'cleanup_warning') if key in data}


def page_context(proxy, result):
    """Legacy name retained for existing consumers."""
    admitted = recovery_context(proxy, result)
    return admitted[0] if admitted else None


def is_visual_failure(proxy, result):
    if cli_owns_recovery(result):
        return False
    admitted = recovery_context(proxy, result)
    if admitted is None:
        return False
    context, policy, _ = admitted
    codes = {'UI_CHANGED', 'BUSY', 'EXECUTION_UNKNOWN', 'CHROME_UNAVAILABLE'}
    if not result.get('success'):
        return result.get('code') in codes
    # A completed read/send can still need page cleanup. Preserve its outcome.
    return policy in {'preserve_result', 'observe_only'} and context.get('error', {}).get('code') in codes


is_page_failure = is_visual_failure


def _assessment(result):
    data = result.get('data') if isinstance(result.get('data'), dict) else {}
    visual = data.get('visual') if isinstance(data.get('visual'), dict) else {}
    diagnosis = visual.get('diagnosis')
    if visual.get('http_status') != 200 or visual.get('state') != 'completed' or not isinstance(diagnosis, dict):
        diagnosis = None
    return diagnosis, data, visual


def _post_context(context, dismissed):
    status = 'attempted' if dismissed.get('success') else 'unknown'
    return {**context, 'purpose': 'verify', 'available_actions': [],
        'operation': {'name': 'boss_overlay_dismiss', 'result_kind': 'page_state',
            'goal': '核验刚才建议关闭的阻挡界面是否消失',
            'expected': '刚才的阻挡界面及其遮罩已消失，露出被遮挡的页面内容；无需达成其他页面或业务目标，原操作尚未重新执行'},
        'execution': {'stage': 'postcheck', 'input_status': status, 'attempts_used': 1, 'ordinary_retries_used': 0},
        'verification': {'status': 'inconclusive', 'evidence': [
            '关闭输入正常返回，等待只读核验；原操作尚未重做' if status == 'attempted'
            else '关闭输入响应未知；只读核验，不重放关闭输入；原操作尚未重做']}}


async def recover_operation(proxy, original, dispatch):
    """The dispatcher binds phases to the original device and immutable operation.

    No business selectors, model SDK, tool fees or ordinary retry live here.
    An external effect can share page repair without acquiring replay permission.
    """
    from .proxy_tool import settings
    if not settings.boss_tool_billing.overlay_heal_enabled or not is_visual_failure(proxy, original):
        return original
    context, policy, modern = recovery_context(proxy, original)
    if policy == 'preserve_result':
        # Read payloads are already retained. The model judges the outstanding page cleanup only.
        context = deepcopy(context)
        context['operation']['result_kind'] = 'page_state'
    receipts, observations, steps = [], [], []
    seen_receipts = set()
    max_steps = settings.boss_tool_billing.visual_recovery_max_steps if modern else 1
    mode = 'page'

    async def observe(branch, visual_context, observation_mode):
        request_id = str(uuid5(NAMESPACE_URL, f"boss-visual:{original['invocation_id']}:{branch}"))
        arguments = {'request_id': request_id, 'visual_context': visual_context}
        if modern or observation_mode != 'page':
            arguments['observation_mode'] = observation_mode
        result = await dispatch('boss_overlay_inspect', branch, arguments)
        diagnosis, data, visual = _assessment(result)
        # Only retrieve the same paid request. Unknown usage is never retried with a new ID.
        for index in range(2 if modern else 1):
            if visual.get('state') not in {'pending', 'unknown'} and visual.get('http_status') != 202:
                break
            suffix = f'{branch}.retrieve' + (f'{index}' if modern else '')
            result = await dispatch('boss_overlay_inspect', suffix, {'request_id': request_id, 'retrieve_only': True})
            diagnosis, data, visual = _assessment(result)
        observations.append({'mode': observation_mode, **{key: deepcopy(visual.get(key)) for key in
            ('request_id', 'http_status', 'state', 'diagnosis', 'diagnosis_error', 'action_error')}})
        if visual.get('receipt') and request_id not in seen_receipts:
            receipts.append(visual['receipt']); seen_receipts.add(request_id)
        return diagnosis, data, visual, result

    def finish(result, state, **extra):
        return {**result, 'data': {**(result.get('data') or {}), 'visual_recovery': {
            'state': state, 'receipts': receipts, 'observations': observations, 'steps': steps,
            'original_invocation_id': original.get('invocation_id'), 'policy': policy, **extra,
        }}}

    async def resume():
        if policy == 'preserve_result':
            return finish(original, 'page_restored_result_preserved', page_restored=True)
        if policy == 'readonly' and proxy.name == 'boss_read_chat' and not context['operation'].get('target_label'):
            return finish(original, 'page_restored_target_unbound', page_restored=True)
        if policy == 'observe_only':
            facts = (original.get('data') or {}).get('operation_recovery') or {}
            business = facts.get('business_effect') or {}
            not_started = (business.get('status') == 'not_started' and business.get('completed_count') == 0
                and original.get('effect') == 'none' and original.get('code') != 'EXECUTION_UNKNOWN')
            if proxy.name in _CURRENT_CONTACT_TOOLS and not facts.get('recovery_contact_ref'):
                not_started = False
            if not not_started:
                state = 'page_restored_business_preserved' if business.get('status') == 'confirmed' else 'page_restored_business_unconfirmed'
                return finish(original, state, page_restored=True)
        result = await dispatch(proxy.name, 'resume', None)
        return finish(result, 'recovered' if result.get('success') else 'original_goal_unconfirmed', page_restored=True)

    last_state = 'diagnosis_unavailable'
    for index in range(max_steps):
        prefix = f'step{index}.' if modern else ''
        diagnose_context = {**context, 'purpose': 'diagnose',
            'available_actions': ['dismiss_click', 'press_escape'] if modern else ['dismiss_click']}
        diagnosis, data, visual, inspection = await observe(f'{prefix}diagnose', diagnose_context, mode)
        # Business unknown and current page readiness are independent facts.
        if modern and policy == 'observe_only' and diagnosis is not None and diagnosis.get('obstruction') == 'absent' and (mode == 'window' or context['execution']['stage'] != 'input'):
            return await resume()
        # Native UI may block page CDP or remain invisible in a normal page screenshot.
        capture_failed = not visual and inspection.get('code') in {'CHROME_UNAVAILABLE', 'UI_CHANGED', 'INTERNAL_ERROR'}
        page_cannot_explain = diagnosis is not None and (diagnosis.get('obstruction') == 'unknown'
            or context['execution']['stage'] == 'input' and diagnosis.get('obstruction') == 'absent'
            or diagnosis.get('goal_state') != 'satisfied' and not data.get('recovery_ref'))
        if modern and mode == 'page' and (capture_failed or page_cannot_explain):
            mode = 'window'
            diagnosis, data, visual, inspection = await observe(f'{prefix}diagnose.window', diagnose_context, mode)
        if diagnosis is None:
            return finish(original, 'diagnosis_unavailable')
        if modern and policy == 'observe_only' and diagnosis.get('obstruction') == 'absent':
            return await resume()
        # A visible already-ready page permits a declared safe domain recheck, not fabricated completion.
        if modern and diagnosis.get('obstruction') == 'absent' and diagnosis.get('goal_state') == 'satisfied':
            return await resume()
        action = diagnosis.get('proposed_action')
        if not data.get('recovery_ref') or not action or diagnosis.get('obstruction') != 'present':
            return finish(original, 'no_supported_action')
        dismissed = await dispatch('boss_overlay_dismiss', f'{prefix}dismiss', {'recovery_ref': data['recovery_ref']})
        reason = (dismissed.get('data') or {}).get('recovery_action_error')
        input_unknown = dismissed.get('code') == 'EXECUTION_UNKNOWN' or reason == 'ALREADY_CONSUMED'
        step = {'index': index, 'mode': mode, 'action_kind': action.get('kind'),
            'input_status': 'attempted' if dismissed.get('success') else 'unknown' if input_unknown else 'not_started'}
        steps.append(step)
        if not dismissed.get('success') and not input_unknown:
            step['action_error'] = reason
            if modern and reason == 'STALE_OBSERVATION':
                last_state = 'observation_changed'
                context = deepcopy(context)
                context['verification']['evidence'] = (context['verification']['evidence'] + ['恢复观察已失效，守卫零输入拒绝；重新采图定位'])[-10:]
                continue
            return finish(original, 'dismiss_unconfirmed')
        post = _post_context(context, dismissed)
        post.pop('error', None)
        after, _, post_visual, post_inspection = await observe(f'{prefix}postcheck', post, mode)
        post_capture_failed = not post_visual and post_inspection.get('code') in {'CHROME_UNAVAILABLE', 'UI_CHANGED', 'INTERNAL_ERROR'}
        if modern and mode == 'page' and (post_capture_failed or after is not None and after.get('obstruction') == 'unknown'):
            after, _, _, _ = await observe(f'{prefix}postcheck.window', post, 'window')
            mode = 'window'
        step['post_obstruction'] = after.get('obstruction') if after else 'unknown'
        step['post_goal_state'] = after.get('goal_state') if after else 'inconclusive'
        if after is None or after.get('obstruction') == 'unknown':
            return finish(original, 'postcheck_inconclusive')
        if after.get('obstruction') == 'present':
            last_state = 'obstruction_remains'
            context = deepcopy(context)
            context['verification']['evidence'] = (context['verification']['evidence'] + [
                f"恢复步骤{index + 1}动作{step['action_kind']}，输入状态{step['input_status']}；fresh后态明确仍有遮挡，选择适合当前状态的动作，不重放旧引用"])[-10:]
            if modern:
                continue
            return finish(original, last_state)
        if after.get('goal_state') == 'inconclusive':
            return finish(original, 'postcheck_inconclusive')
        if after.get('obstruction') != 'absent' or after.get('goal_state') != 'satisfied':
            return finish(original, 'postcheck_not_satisfied')
        return await resume()
    return finish(original, 'recovery_limit_reached', last_state=last_state, max_steps=max_steps)


# Prior imports remain valid; all current adapters use the same implementation.
recover_page = recover_operation
