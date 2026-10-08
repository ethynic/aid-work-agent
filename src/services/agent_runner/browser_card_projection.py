"""Finite public Browser card projection under the caller's original root lock.

No execution permission, claim, checkpoint or Browser operation is granted here.
Domain writers update only the public watermark; execution writers project their
actual next checkpoint before their existing public-state transaction commits.
"""

import copy

from .presentation import browser_display
from .persistence_limits import capped_snapshot_dumps


_BINDING = ('runner_id', 'run_id', 'runner_execution_id', 'runner_tool_call_id',
            'owner_worker_id', 'owner_boot_id', 'browser_epoch', 'owner_endpoint')
_TERMINAL = {'completed', 'failed', 'cancelled'}
_NOTICE = '原浏览器状态尚待核对，任务和文件已保留。'


def _nodes(checkpoint):
    """Bounded shape checks for display, including known terminal tool facts."""
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get('execution'), dict):
        return []
    pending, seen, values = [checkpoint['execution']], set(), []
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            return []
        identity = node.get('execution_id')
        if not isinstance(identity, str) or identity in seen or len(seen) >= 1000:
            return []
        seen.add(identity)
        if any(not isinstance(node.get(key, {}), dict) for key in ('resources', 'tools', 'children')):
            return []
        if not isinstance(node.get('waiting') or {}, dict):
            return []
        resources = node.get('resources', {})
        if any(not isinstance(resources.get(key, {}), dict) for key in ('browser_runs', 'browser_waits')):
            return []
        for tool in node.get('tools', {}).values():
            if not isinstance(tool, dict) or not isinstance(tool.get('call'), dict):
                return []
        values.append(node)
        if len(node.get('children', {})) + len(pending) + len(seen) > 1000:
            return []
        for child in node.get('children', {}).values():
            if not isinstance(child, dict):
                return []
            saved = child.get('checkpoint')
            if isinstance(saved, dict):
                pending.append(saved)
            elif saved is not None or child.get('unstarted') is not True:
                return []
    return values


def _reference(row, checkpoint, card):
    from .browser_binding import owned_browser_execution
    from .ownership import LeaseLost
    matches = []
    for node in _nodes(checkpoint):
        for call_id, ref in node.get('resources', {}).get('browser_runs', {}).items():
            if (isinstance(ref, dict) and ref.get('run_id') == card.get('run_id')
                    and isinstance(ref.get('waits'), dict)
                    and card.get('assistance_id') in ref['waits'].values()):
                matches.append((node, call_id, ref))
    if len(matches) != 1:
        return None
    node, call_id, ref = matches[0]
    if (ref.get('version') != 1 or any(not isinstance(ref.get(key), str) or not ref[key] for key in _BINDING)):
        return None
    try:
        owned, _, digest = owned_browser_execution(row, checkpoint, node['execution_id'], call_id)
    except (LeaseLost, TypeError, AttributeError):
        return None
    if (ref['runner_id'] != row['runner_id'] or ref['runner_execution_id'] != owned['execution_id']
            or ref['runner_tool_call_id'] != call_id or ref.get('arguments_digest') != digest):
        return None
    return node, call_id, ref


def project_card(card, row, run, wait, fact, now):
    """Pure, allowlisted mapping of already-proven original public facts."""
    value = browser_display(card)
    phase = (fact.get('continuation') or {}).get('phase') if fact else None
    live = (run['runtime_state'] == 'live' and run.get('owner_lease_until') is not None
            and run['owner_lease_until'] > now)
    available = (live and wait['state'] in {'pending', 'controlling'}
                 and wait['expiry_instant'] > now and not fact
                 and row['status'] not in _TERMINAL and not row.get('cancel_requested'))
    value['view_available'] = bool(available)
    value['expires_at'] = wait['expiry_instant'].isoformat()
    if run['runtime_state'] == 'closed':
        value['state'] = ('cancelled' if run['state'] == 'CANCELLED' else
                          'resumed' if run['state'] == 'SUCCEEDED' else 'failed')
        value['completion_status'] = 'closed'
    elif phase == 'completed':
        result = fact['continuation']['result']
        value['state'] = 'failed' if result.get('success') is False else 'resumed'
        value['completion_status'] = 'completed'
    elif phase in {'observed', 'started'}:
        value['state'] = 'resume_queued'
        value['completion_status'] = phase
    else:
        value['state'] = wait['state'] if wait['state'] in {'pending', 'controlling'} else 'failed'
        value['completion_status'] = 'pending'
    waiting = row.get('public_snapshot', {}).get('waiting') or {}
    if (phase != 'completed' and (row.get('cancel_requested') or not live or wait['expiry_instant'] <= now
            or waiting.get('kind') == 'verification')) and run['runtime_state'] != 'closed':
        value['view_available'] = False
        value['completion_status'] = 'verification_required'
    if row['status'] in _TERMINAL:
        value['view_available'] = False
        if value['state'] in {'pending', 'controlling', 'resume_queued'}:
            value['state'] = 'failed'
            value['completion_status'] = 'verification_required'
    return value


def _derive(cursor, row, checkpoint, card):
    reference = _reference(row, checkpoint, card)
    if reference is None:
        return None
    node, call_id, ref = reference
    cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s', (ref['run_id'],))
    run = cursor.fetchone()
    expected = {**{key: ref[key] for key in _BINDING},
                **{key: row[key] for key in ('tenant_id', 'user_id', 'session_id')}}
    if not run or any(run.get(key) != value for key, value in expected.items()):
        return None
    cursor.execute('''SELECT *,expires_at::timestamptz AS expiry_instant
        FROM bs_browser_assistance_requests WHERE assistance_id=%s''', (card['assistance_id'],))
    wait = cursor.fetchone()
    expected_wait = dict(assistance_id=card['assistance_id'], run_id=ref['run_id'], runner_id=row['runner_id'],
        tenant_id=row['tenant_id'], user_id=row['user_id'], agent_execution_id=node['execution_id'],
        tool_call_id=call_id, owner_boot_id=ref['owner_boot_id'], browser_epoch=ref['browser_epoch'])
    if (not wait or any(wait.get(key) != value for key, value in expected_wait.items())
            or ref['waits'].get(wait.get('runner_wait_id')) != wait['assistance_id']):
        return None
    # A late old card cannot replace the exact new wait saved in this checkpoint.
    current = node.get('waiting') or node.get('resources', {}).get('browser_waits', {}).get(call_id) or {}
    if not isinstance(current, dict):
        return None
    if current.get('kind') == 'human_assistance' and current.get('tool_call_id') == call_id:
        if current.get('assistance_id') != wait['assistance_id']:
            return None
        if (current.get('wait_id') != wait['runner_wait_id']
                or current.get('target_execution_id') != node['execution_id']):
            return None
    fact = None
    if wait.get('completion_ref'):
        from .browser_completion import BrowserCompletionRepository, BrowserRecoveryUnavailable
        try:
            fact = BrowserCompletionRepository.fact(wait)
        except BrowserRecoveryUnavailable:
            return None
        if any(fact['binding'].get(key) != value for key, value in expected.items()):
            return None
    cursor.execute('SELECT clock_timestamp() AS database_now')
    return project_card(card, row, run, wait, fact, cursor.fetchone()['database_now'])


def project_snapshot(cursor, row, *, checkpoint=None, snapshot=None, status=None):
    """Caller already holds root. Never locks another root after run/assistance."""
    checkpoint = row.get('checkpoint') if checkpoint is None else checkpoint
    snapshot = copy.deepcopy(row.get('public_snapshot') or {}) if snapshot is None else copy.deepcopy(snapshot)
    effective = dict(row, public_snapshot=snapshot, status=status or row['status'])
    incoming = snapshot.get('browserAssistance')
    stored = (row.get('public_snapshot') or {}).get('browserAssistance')
    candidates = [value for value in (incoming, stored) if isinstance(value, dict)]
    waiting = checkpoint.get('execution', {}).get('waiting') if isinstance(checkpoint, dict) and isinstance(checkpoint.get('execution'), dict) else None
    for _ in range(64):
        if not isinstance(waiting, dict) or waiting.get('kind') != 'child_wait':
            break
        waiting = waiting.get('child_wait')
    if isinstance(waiting, dict) and waiting.get('kind') == 'human_assistance':
        # The checkpoint being saved, including its selected child leaf, wins
        # over an older live snapshot's otherwise valid sibling card.
        candidates = [value for value in candidates if value.get('assistance_id') == waiting.get('assistance_id')]
    for card in candidates:
        if not isinstance(card.get('assistance_id'), str) or not isinstance(card.get('run_id'), str):
            continue
        projected = _derive(cursor, effective, checkpoint, card)
        if projected is not None:
            snapshot['browserAssistance'] = projected
            return snapshot
    snapshot.pop('browserAssistance', None)
    if candidates and effective['status'] not in _TERMINAL:
        waiting = snapshot.get('waiting')
        if not isinstance(waiting, dict) or waiting.get('kind') == 'verification':
            snapshot['waiting'] = dict(kind='verification', question=_NOTICE)
    return snapshot


def sync_browser_display(cursor, row, *, advance_view=True):
    """Domain transaction: change only public snapshot/view, never execution CAS."""
    snapshot = project_snapshot(cursor, row)
    if snapshot == (row.get('public_snapshot') or {}):
        return row
    cursor.execute('''UPDATE agent_runners SET public_snapshot=%s::jsonb,
        view_revision=view_revision+%s,updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
        (capped_snapshot_dumps(snapshot), int(advance_view), row['runner_id']))
    return dict(cursor.fetchone())


def project_terminal_result(result, snapshot):
    """Local history projection; the original finalization intent stays immutable."""
    result = copy.deepcopy(result)
    metadata = result.setdefault('assistant_metadata', {})
    metadata.pop('browserAssistance', None)
    if snapshot.get('browserAssistance'):
        metadata['browserAssistance'] = copy.deepcopy(snapshot['browserAssistance'])
    return result
