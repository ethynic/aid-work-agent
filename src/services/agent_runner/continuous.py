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
