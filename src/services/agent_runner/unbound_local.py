"""Converge legacy Next leaves using original locked database facts, never replay."""

import hashlib

from src.core.agent_engine.contracts import CheckpointFailure, Identity
from .contracts import canonical_json
from .local_invocations import LocalPhase, phase_binding
from .local_owner import RunnerLocalLifecycle


def resolve_next_in_tx(cursor, row, node, call_id):
    """Return False for other adapters; mutate only this legacy leaf in memory.

    Caller holds the original Runner row lock; its existing recovery CAS or
    cancellation transaction persists the result. A present binding is adopted,
    not replaced. Unknown identities, domain effects and extra bindings reject.
    """
    fact = node.get('tools', {}).get(call_id) or {}
    call = fact.get('call') or {}
    if call.get('name') != 'boss_resume_next' or fact.get('invocation_id'):
        return False
    refs = node.get('resources', {}).get('local_invocations') or {}
    if any(ref.get('tool_call_id') == call_id for ref in refs.values()):
        return False
    if fact.get('phase') not in {'dispatching', 'waiting'}:
        return False
    identity = {key: row[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}
    if node.get('identity') != identity or call.get('id') != call_id:
        raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')
    from .resume_next_proof import continuation_arguments
    # The frozen canonical request must still match its parent and digest.
    def parent(identifier):
        cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s',
            (identifier, row['tenant_id']))
        return dict(cursor.fetchone() or {})
    phase = LocalPhase(node['execution_id'], call_id, 'main', 0)
    invocation = phase_binding(cursor, row, phase)
    def attempts(original):
        cursor.execute('''SELECT * FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s
            AND session_id=%s AND device_id=%s AND tool_name='boss_resume_next'
            AND arguments_json->>'continue_from'=%s''',
            (row['tenant_id'], row['user_id'], row['session_id'], original['device_id'], str(original['id'])))
        return [dict(value) for value in cursor.fetchall()]
    arguments = continuation_arguments(row, node, call_id, call.get('arguments') or {}, parent,
        load_attempts=attempts, current_invocation_id=str(invocation['id']) if invocation else None,
        require_binding=False)
    cursor.execute('''SELECT id FROM local_tool_invocations WHERE tenant_id=%s
        AND business_kind='agent_runner' AND business_ref->>'runner_id'=%s
        AND business_ref->>'execution_id'=%s AND business_ref->>'tool_call_id'=%s''',
        (row['tenant_id'], row['runner_id'], node['execution_id'], call_id))
    bound = {str(item['id']) for item in cursor.fetchall()}
    if bound != ({str(invocation['id'])} if invocation else set()):
        raise CheckpointFailure('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED')
    allowed = LocalPhase(node['execution_id'], call_id, 'resume.next.request', 0).key(row['runner_id'])
    if any(key != allowed for key, value in
            (node.get('resources', {}).get('local_domain_phases') or {}).items()
            if value.get('tool_call_id') == call_id):
        raise CheckpointFailure('LOCAL_PHASE_OWNER_VERIFICATION_REQUIRED')
    if invocation:
        # No new device selection or authorization is inferred from this row.
        saved = node['resources']['local_domain_phases'][allowed]['result']
        request = dict(device_id=saved['_resume_expected_device_id'], tool_name='boss_resume_next',
            arguments=arguments, provider_key=invocation.get('provider_key'),
            deadline_at=invocation['deadline_at'].isoformat() if invocation.get('deadline_at') else None,
            authorization_epoch=invocation.get('authorization_epoch'),
            execution_lane=invocation.get('execution_lane') or 'standard')
        ref = dict(runner_id=row['runner_id'], execution_id=node['execution_id'],
            tool_call_id=call_id, branch='main', ordinal=0, invocation_id=str(invocation['id']),
            request=request, intent_digest=hashlib.sha256(canonical_json(request).encode()).hexdigest())
        RunnerLocalLifecycle.validate_invocation(Identity(**identity), row['runner_id'], phase, ref, invocation)
        node.setdefault('resources', {}).setdefault('local_invocations', {})[phase.key(row['runner_id'])] = ref
    else:
        fact.update(phase='completed', success=False, result_recorded=False,
            result={'success': False, 'code': 'LOCAL_NOT_DISPATCHED', 'effect': 'none',
                'invocation_id': None, 'data': None,
                'message': '已核对原派发事务未提交，没有执行本机操作；原筛选记录及成果保留。'})
        node['waiting'] = None
    return True


def resolve_tree_in_tx(cursor, row, node):
    changed = False
    for call_id in node.get('tools', {}):
        changed = resolve_next_in_tx(cursor, row, node, call_id) or changed
    for child in node.get('children', {}).values():
        if isinstance(child.get('checkpoint'), dict):
            changed = resolve_tree_in_tx(cursor, row, child['checkpoint']) or changed
    return changed
