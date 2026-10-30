"""Keep unknown incoming identities recoverable without retaining bodies in checkpoints.

The original invocation remains the message authority. Its private pending offsets
are consumed atomically with a later read's persisted recovered candidates, so a
crash before the tool checkpoint cannot lose a candidate.
"""

import asyncio
import copy
from datetime import datetime
from uuid import UUID

READ_TOOLS = {'wecom_read_session', 'wecom_read_sessions'}
INDEX = '_wecom_inbound'
RECOVERED = '_wecom_recovered'
SCAN = 'wecom_inbound_scan'
BATCH = 4


def _effective(row):
    from .repository import observed_invocation
    return observed_invocation(row)


def _payload(row):
    return _effective(row).get('result_json') or {}


def _unknowns(result):
    data = result.get('data') or {}
    return [(row['session'], bool(item.get('baseline',row.get('baseline',data.get('baseline')))), item)
        for row in data.get('pending_sessions', []) if isinstance(row, dict) and row.get('session')
        for item in row.get('unknown_messages', []) if isinstance(item, dict)
        and item.get('msg_id') is not None and item.get('sender_id') is not None
        and isinstance(item.get('recovery_cursor'), str)]


def _owned(row, owner):
    identity = owner.state.identity
    reference = row.get('business_ref') or {}
    return (row.get('tenant_id') == identity.tenant_id and row.get('user_id') == identity.user_id
        and row.get('session_id') == identity.session_id and row.get('provider_key') == 'wecom'
        and row.get('business_kind') == 'agent_runner' and row.get('tool_name') in READ_TOOLS
        and _effective(row).get('state') == 'succeeded' and reference.get('runner_id') == owner.root.attempt.runner_id
        and reference.get('execution_id') == owner.state.execution_id
        and str(row.get('device_id')) == str(owner.wecom_device_id()))


def _pending(row):
    marker = (row.get('result_json') or {}).get(INDEX) or {}
    return marker.get('pending', []) if marker.get('version') == 1 else []


def _rows(owner, scan=None):
    identity = owner.state.identity
    with owner.root.repository.connection_factory() as connection:
        cursor = connection.cursor()
        clauses = ''
        values = [identity.tenant_id, identity.user_id, identity.session_id,
            owner.root.attempt.runner_id, owner.state.execution_id, str(owner.wecom_device_id())]
        if scan:
            clauses = 'AND (created_at,id)>=(%s,%s::uuid)'
            values.extend([datetime.fromisoformat(scan['created_at']), scan['invocation_id']])
        cursor.execute('''SELECT * FROM local_tool_invocations
            WHERE tenant_id=%s AND user_id=%s AND session_id=%s
            AND business_kind='agent_runner' AND business_ref->>'runner_id'=%s
            AND business_ref->>'execution_id'=%s AND device_id=%s::uuid
            AND provider_key='wecom' AND tool_name IN ('wecom_read_session','wecom_read_sessions')
            AND state IN ('succeeded','unknown') AND result_json->'_wecom_inbound'->>'version'='1'
            AND jsonb_array_length(result_json->'_wecom_inbound'->'pending')>0 '''+clauses+'''
            ORDER BY created_at,id LIMIT 5''', values)
        return [dict(row) for row in cursor.fetchall()]


def _select(owner):
    scan = owner.state.resources.get(SCAN)
    for page in (scan, None) if scan else (None,):
        selected = []
        for row in _rows(owner, page):
            if not _owned(row, owner):
                continue
            unknowns = _unknowns(_payload(row))
            for offset in _pending(row):
                if (not isinstance(offset, int) or not 0 <= offset < len(unknowns)
                        or page and str(row['id']) == page['invocation_id'] and offset <= page['offset']):
                    continue
                session, baseline, item = unknowns[offset]
                selected.append({'invocation_id':str(row['id']), 'offset':offset,
                    'source_invocation_id':str(row['id']),
                    'session':session, 'baseline':baseline, **item,
                    'scan':{'created_at':row['created_at'].isoformat(), 'invocation_id':str(row['id']), 'offset':offset}})
                if len(selected) == BATCH:
                    return selected
        if selected:
            return selected
    return []


async def before_read(owner, reread):
    """Read at most four original CLI cursors; do not consume pending evidence.

    ``reread(item)`` uses the existing internally owned read operation,
    with limit=1. It must not pass through these hooks recursively.
    """
    await owner.authorize_operation()
    # The first main read selects/binds the Runtime through the normal path.
    # There cannot be old device-owned candidates to refresh before that bind.
    if not owner.wecom_device_id():
        return []
    selected = await asyncio.to_thread(_select, owner)
    for item in selected:
        result = await reread(item)
        data = result.get('data') or {}
        if result.get('success') and data.get('session') == item['session']:
            item['message'] = next((copy.deepcopy(message) for message in data.get('messages', [])
                if str(message.get('msg_id')) == str(item['msg_id'])
                and str(message.get('sender_id')) == str(item['sender_id'])
                and message.get('sender_platform') in {'wechat', 'wecom'}
                and message.get('side') == 'peer'), None)
        owner.state.resources[SCAN] = item.pop('scan')
    return selected


def _apply(owner, result, recovery):
    from psycopg2.extras import Json
    from src.services.agent_runner.ownership import lock_runner

    current_id = str(UUID(result['invocation_id']))
    identifiers = sorted({current_id, *(item['invocation_id'] for item in recovery)})
    with owner.root.repository.connection_factory() as connection:
        cursor = connection.cursor()
        lock_runner(cursor, owner.root.attempt.runner_id, owner.root.attempt, dispatch=True)
        cursor.execute('SELECT * FROM local_tool_invocations WHERE id=ANY(%s::uuid[]) ORDER BY id FOR UPDATE',
            (identifiers,))
        rows = {str(row['id']):dict(row) for row in cursor.fetchall()}
        current = rows.get(current_id)
        if not current or not _owned(current, owner):
            raise ValueError('WECOM_INBOUND_OWNER_MISMATCH')
        payload = copy.deepcopy(current.get('result_json') or {})
        current['result_json'] = payload
        changed = set()
        unknowns = _unknowns(_payload(current))
        if INDEX not in payload and unknowns:
            payload[INDEX] = {'version':1, 'pending':list(range(len(unknowns)))}
            changed.add(current_id)
        recovered = []
        for item in recovery:
            original = rows.get(item['invocation_id'])
            if not original or not _owned(original, owner) or item['offset'] not in _pending(original):
                continue
            entries = _unknowns(_payload(original))
            if not 0 <= item['offset'] < len(entries) or not item.get('message'):
                continue
            session, baseline, evidence = entries[item['offset']]
            message = item['message']
            if (session != item['session'] or evidence['msg_id'] != item['msg_id']
                    or evidence['sender_id'] != item['sender_id']
                    or str(message.get('msg_id')) != str(evidence['msg_id'])
                    or str(message.get('sender_id')) != str(evidence['sender_id'])
                    or message.get('sender_platform') not in {'wechat','wecom'} or message.get('side') != 'peer'):
                continue
            # Identity comes from the fresh read; original content and history
            # status come from the owned immutable read payload.
            data = _payload(original).get('data') or {}
            sessions = data.get('sessions') or [data]
            original_message = next((candidate for entry in sessions
                if entry.get('session') == session for candidate in entry.get('messages', [])
                if str(candidate.get('msg_id')) == str(evidence['msg_id'])
                and str(candidate.get('sender_id')) == str(evidence['sender_id'])), None)
            if original_message is None:
                continue
            recovered.append({'session':session, 'baseline':baseline, 'recovered':True,
                'source_invocation_id':item['invocation_id'],
                'message':{**copy.deepcopy(original_message), 'sender_platform':message['sender_platform'],
                    'sender_platform_source':message.get('sender_platform_source')}})
            original['result_json'][INDEX]['pending'].remove(item['offset'])
            changed.add(item['invocation_id'])
        if recovered:
            payload.setdefault(RECOVERED, []).extend(recovered)
            changed.add(current_id)
        for identifier in changed:
            cursor.execute('UPDATE local_tool_invocations SET result_json=%s WHERE id=%s',
                (Json(rows[identifier]['result_json']), identifier))
        connection.commit()
        # Polling this original current invocation after a crash returns the same
        # recovered candidates even though their old pending offsets are retired.
        data = copy.deepcopy(_payload(current).get('data') or {})
        if payload.get(RECOVERED):
            data['recovered_messages'] = copy.deepcopy(payload[RECOVERED])
        return {**{key:value for key,value in result.items() if key not in {INDEX,RECOVERED}}, 'data':data}


async def after_read(owner, result, arguments, *, recovery=None):
    """Index this successful read and atomically attach recovered old messages."""
    if not result.get('success') or not result.get('invocation_id'):
        return result
    async with owner.root.lock:
        owner.root._check_usage()
        return await asyncio.to_thread(_apply, owner, result, recovery or [])


def validate_read_phase(row, state, fact, ref, invocation, *, load_original=None):
    """Verify a frozen private reread against original, immutable CLI evidence."""
    from src.core.agent_engine.contracts import CheckpointFailure
    from .repository import get_invocation

    def reject():
        raise CheckpointFailure('WECOM_INBOUND_OWNER_MISMATCH')

    prefix = 'wecom.inbound.'
    branch = ref.get('branch', '')
    if not branch.startswith(prefix) or fact.call.name not in READ_TOOLS or ref.get('ordinal') != 0:
        reject()
    source_id, separator, message_id = branch[len(prefix):].rpartition('.')
    try:
        if not separator or str(UUID(source_id)) != source_id:
            reject()
    except ValueError:
        reject()
    original = (load_original or get_invocation)(source_id, row['tenant_id'])
    if original:
        original = _effective(original)
    identity = state.identity
    request = ref.get('request') or {}
    reference = (original or {}).get('business_ref') or {}
    if (not original or original.get('state') != 'succeeded' or original.get('tool_name') not in READ_TOOLS
            or original.get('provider_key') != 'wecom' or original.get('business_kind') != 'agent_runner'
            or reference.get('runner_id') != row['runner_id'] or reference.get('execution_id') != state.execution_id
            or any(str(original.get(key)) != str(value) for key, value in
                (('tenant_id',identity.tenant_id),('user_id',identity.user_id),('session_id',identity.session_id),
                 ('device_id',request.get('device_id'))))
            or request.get('tool_name') != 'wecom_read_session'
            or request.get('provider_key') != 'wecom'
            or str(invocation.get('device_id')) != str(original.get('device_id'))):
        reject()
    if not any(str(item.get('msg_id')) == message_id
            and request.get('arguments') == {'session':session,'cursor':item['recovery_cursor'],'limit':1}
            for session, _, item in _unknowns(original.get('result_json') or {})):
        reject()
    return True
