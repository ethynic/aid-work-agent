"""Trusted continuous policy and fixed-purpose clarification, never prompt inference."""

from .contracts import RunnerError


def supported(source, session_kind):
    from src.config.settings import settings
    return (source == 'chat' and session_kind == 'web'
            and bool(getattr(settings.agent_runner, 'durable_sleep_enabled', False)))


def assert_continuous_supported(source, session_kind):
    if source != 'chat' or session_kind != 'web':
        raise RunnerError('CONTINUOUS_SOURCE_UNSUPPORTED', 400)
    if not supported(source, session_kind):
        raise RunnerError('DURABLE_SLEEP_DISABLED', 503)


def enabled(envelope, intent=None):
    policy = (envelope or {}).get('continuous') or {}
    return (policy.get('version') == 1 and policy.get('enabled') is True
            or (intent or {}).get('execution_mode') == 'continuous')


def interrupted_candidate(row):
    """Only an owned continuous interruption is an automatic recovery source."""
    checkpoint = row.get('checkpoint') or {}
    root = checkpoint.get('execution')
    def manual_wait(node):
        if (node.get('waiting') or {}).get('kind') in {
                'clarification','child_clarification','human_assistance','continuous_guard'}:
            return True
        return any(manual_wait(child['checkpoint']) for child in node.get('children',{}).values()
            if isinstance(child,dict) and isinstance(child.get('checkpoint'),dict) and child['checkpoint'].get('outcome') not in {
                'completed','cancelled','failed','iteration_limit'})
    return (row.get('status') == 'interrupted'
        and row.get('source') == 'chat' and row.get('session_kind') == 'web'
        and not row.get('cancel_requested') and not row.get('pause_requested')
        and not row.get('resume_control_id') and not checkpoint.get('pending_finalization')
        and enabled(checkpoint, row.get('input'))
        and (root is None or isinstance(root,dict) and root.get('execution_id') == row.get('runner_id'))
        and (root or {}).get('outcome') not in {'cancelled','failed','iteration_limit'}
        and not (checkpoint.get('continuous_recovery') or {}).get('blocked')
        and not manual_wait(root or {}))


def feature_enabled(envelope):
    if enabled(envelope):
        return True
    def visit(node):
        return ((node.get('resources') or {}).get('durable_sleep_version') == 1
            or any(visit(child.get('checkpoint') or {}) for child in node.get('children',{}).values()))
    return visit((envelope or {}).get('execution') or {})


def confirmation_arguments():
    return {'title': '是否持续跟进', 'allow_skip': True,
        'questions': [{'prompt': '这个任务是否持续执行，直到你主动停止？',
            'type': 'single', 'required': True, 'allow_custom_text': False,
            'options': [{'label': '持续跟进，直到我停止'}, {'label': '只做本次'}]}]}


def apply_confirmation(row, control, target, states, checkpoint, wait, fact):
    """Prepared checkpoint is committed atomically with original reply consumption."""
    binding = wait.get('continuous_confirmation')
    if not isinstance(binding, dict):
        return
    form = wait.get('form') or {}
    payload = control['payload']
    if (binding.get('version') != 1 or binding.get('purpose') != 'confirm_continuous'
            or binding.get('execution_id') != target.execution_id
            or binding.get('tool_call_id') != fact.call.id or fact.call.name != 'ask_user'
            or binding.get('form_id') != form.get('form_id')
            or payload.get('wait_id') != wait.get('wait_id')
            or payload.get('target_execution_id') != target.execution_id):
        raise RunnerError('CONTINUOUS_CONFIRMATION_STALE', 409)
    if row.get('cancel_requested'):
        raise RunnerError('CONTROL_EXECUTION_CLOSED', 409)
    answers = payload.get('answers')
    if payload.get('skip') or not isinstance(answers, list):
        return  # Free text, including "yes", never grants policy.
    if (payload.get('form') or {}).get('form_id') != form.get('form_id'):
        raise RunnerError('CONTINUOUS_CONFIRMATION_STALE', 409)
    from .ask_form import normalize_answers
    answers = normalize_answers(answers)
    accepted = any(answer.get('question_id') == binding.get('question_id')
        and answer.get('selected_keys') == [binding.get('positive_key')]
        and not str(answer.get('text') or '').strip() for answer in answers)
    if not accepted or enabled(checkpoint):
        return
    assert_continuous_supported(row['source'], row['session_kind'])
    checkpoint['continuous'] = {'version': 1, 'enabled': True,
        'source': 'clarification', 'control_id': control['control_id'], 'policy_revision': 1}
    for state in states.values():
        state.resources['continuous_enabled'] = True
        state.resources.setdefault('business_iterations',state.iteration)
        state.resources['continuous_segment_start'] = state.resources['business_iterations']
