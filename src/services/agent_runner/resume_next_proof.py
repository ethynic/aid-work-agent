"""Prove continuation arguments from the frozen request and its owned parent.

Device arguments include server-derived history; comparing them directly with
the model's cursor/limit loses that distinction. This proof never dispatches or
reconstructs history from a selected device or an untrusted model field.
"""

import hashlib
from uuid import UUID

from src.core.agent_engine.contracts import CheckpointFailure
from src.local_tools.resume_continuation import _names, batch_metadata
from .contracts import canonical_json
from .local_invocations import LocalPhase


def continuation_arguments(row, node, call_id, arguments, load_parent):
    """Return public canonical arguments, or reject any changed ownership fact."""
    def reject():
        raise CheckpointFailure('LOCAL_RESUME_CONTINUATION_PROOF_INVALID')

    identity = {key: row[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}
    if node.get('identity') != identity:
        reject()
    intent = {'continue_from': arguments.get('continue_from'), 'limit': arguments.get('limit', 3)}
    try:
        parent_id = str(UUID(str(intent['continue_from'])))
    except (ValueError, TypeError, AttributeError):
        reject()
    if type(intent['limit']) is not int or not 1 <= intent['limit'] <= 3:
        reject()
    phase = LocalPhase(node['execution_id'], call_id, 'resume.next.request', 0)
    saved = (node.get('resources', {}).get('local_domain_phases') or {}).get(phase.key(row['runner_id'])) or {}
    expected = dict(runner_id=row['runner_id'], execution_id=node['execution_id'],
                    tool_call_id=call_id, branch=phase.branch, ordinal=0)
    if (any(saved.get(key) != value for key, value in expected.items())
            or saved.get('phase') != 'completed' or saved.get('request') != intent
            or saved.get('intent_digest') != hashlib.sha256(canonical_json(intent).encode()).hexdigest()):
        reject()
    parent = load_parent(parent_id) or {}
    if (str(parent.get('id')) != parent_id
            or any(str(parent.get(key)) != str(row[key]) for key in ('tenant_id', 'user_id', 'session_id'))
            or parent.get('tool_name') not in {'boss_resume_batch', 'boss_resume_next'}
            or parent.get('state') != 'succeeded' or parent.get('effect') != 'none'):
        reject()
    result, previous = parent.get('result_json'), parent.get('arguments_json')
    if not isinstance(result, dict) or not isinstance(previous, dict) or result.get('success') is not True:
        reject()
    data = result.get('data')
    if not isinstance(data, dict):
        reject()
    capability = batch_metadata({**result, 'invocation_id': parent_id, 'effect': parent['effect']}, previous)
    if not capability['continuation']['available']:
        reject()
    canonical = {'continue_from': parent_id, 'limit': intent['limit'],
                 'exclude_names': _names(data, previous), 'expected_job_name': data['job_name'].strip(),
                 '_resume_expected_device_id': str(parent['device_id'])}
    main = LocalPhase(node['execution_id'], call_id, 'main', 0)
    ref = (node.get('resources', {}).get('local_invocations') or {}).get(main.key(row['runner_id'])) or {}
    if (saved.get('result') != canonical
            or ref.get('request', {}).get('device_id') != canonical['_resume_expected_device_id']):
        reject()
    return {key: value for key, value in canonical.items() if not key.startswith('_')}
