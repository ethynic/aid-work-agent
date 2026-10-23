"""Pure original Runner row codec and public DTO allowlist, independent of DAL."""

import json


def decoded(row):
    if row is None:
        return None
    value = dict(row)
    for key in ("input", "checkpoint", "public_snapshot", "result"):
        if isinstance(value.get(key), str):
            value[key] = json.loads(value[key])
    return value


def _display_fields(value, keys):
    """Display fields have scalar values; nested execution facts are private."""
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value
            and (value[key] is None or isinstance(value[key], (str, int, float, bool)))}


def _display_items(values, keys):
    return [_display_fields(value, keys) for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _public_images(values):
    return _display_items(values, ('file_id', 'download_url', 'display_name', 'width', 'height',
                                  'mime_type', 'size_bytes', 'source', 'usage', 'placement'))


def _public_presentation(value):
    value = value if isinstance(value, dict) else {}
    result = {}
    for key, fields in (
        ('downloadableFiles', ('file_id', 'file_name', 'file_size', 'download_url', 'mime_type')),
        ('verboseMessages', ('eventId', 'data', 'source', 'timestamp', 'delivery')),
    ):
        if key in value:
            result[key] = _display_items(value[key], fields)
    if isinstance(value.get('progressMessages'),list):
        from .presentation import progress_display
        result['progressMessages'] = [progress_display(item) for item in value['progressMessages'] if isinstance(item,dict)]
    return result


def _public_form(value):
    """AskForm 白名单投影（结构化提问卡片数据源）；畸形字段直接丢弃。"""
    result = _display_fields(value, ('form_id', 'wait_id', 'title', 'intro',
                                     'allow_skip', 'expires_at'))
    questions = []
    for question in value.get('questions') or []:
        if not isinstance(question, dict):
            continue
        item = _display_fields(question, ('id', 'tag', 'prompt', 'type', 'required',
            'allow_custom_text', 'custom_placeholder', 'placeholder', 'max_select'))
        item['options'] = _display_items(question.get('options'), ('key', 'label', 'description'))
        questions.append(item)
    result['questions'] = questions
    return result


def _public_answers(values):
    """结构化答案摘要投影（历史只读卡片），仅 question_id/selected_keys/text。"""
    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        entry = _display_fields(item, ('question_id', 'text'))
        if isinstance(item.get('selected_keys'), list):
            entry['selected_keys'] = [key for key in item['selected_keys'] if isinstance(key, str)]
        result.append(entry)
    return result


def _public_waiting(value):
    if not isinstance(value, dict):
        return None
    result = _display_fields(value, ('kind', 'question', 'tool_call_id', 'execution_id',
        'invocation_id', 'assistance_id', 'child_status','wait_id','target_execution_id',
        'seconds','reason','wake_at','version'))
    if isinstance(value.get('missing_info'), list):
        result['missing_info'] = [item for item in value['missing_info'] if isinstance(item, str)]
    if isinstance(value.get('form'), dict):
        result['form'] = _public_form(value['form'])
    if isinstance(value.get('child_wait'), dict):
        result['child_wait'] = _public_waiting(value['child_wait'])
    return result


def _public_snapshot(value):
    value = value if isinstance(value, dict) else {}
    result = _display_fields(value, ('progress', 'output', 'iteration', 'business_iterations', 'imagesPlacement'))
    if value.get('history_version') == 1:
        result['history_version'] = 1
    if type(value.get('history_revision')) is int and value['history_revision']>=0:
        result['history_revision'] = value['history_revision']
    result.update(_public_presentation(value))
    if 'images' in value:
        result['images'] = _public_images(value['images'])
    if 'waiting' in value:
        result['waiting'] = _public_waiting(value['waiting'])
    from .presentation import browser_display, option_items
    if isinstance(value.get('browserAssistance'),dict):
        result['browserAssistance'] = browser_display(value['browserAssistance'])
    if isinstance(value.get('quickOptions'),list):
        result['quickOptions'] = option_items(value['quickOptions'])
    if isinstance(value.get('input'), dict):
        result['input'] = _display_fields(value['input'], ('message_id', 'text'))
        result['input']['attachments'] = _display_items(value['input'].get('attachments'),
            ('file_id', 'name', 'type', 'mime_type', 'size'))
    if isinstance(value.get('supplementalInputs'),list):
        result['supplementalInputs'] = []
        for item in value['supplementalInputs']:
            projected = _display_fields(item,('control_id','client_request_id','message_id','text','wait_id','target_execution_id','accepted_at'))
            projected['attachments'] = _display_items(item.get('attachments') if isinstance(item,dict) else None,
                ('file_id','name','type','mime_type','size'))
            if isinstance(item.get('form'),dict):
                # 结构化受理的扩展投影（§7.1 历史只读卡片）；纯文本历史不新增键。
                projected['form'] = _public_form(item['form'])
            if isinstance(item.get('answers'),list):
                projected['answers'] = _public_answers(item['answers'])
            result['supplementalInputs'].append(projected)
    if isinstance(value.get('clarificationQuestions'),list):
        result['clarificationQuestions'] = _display_items(value['clarificationQuestions'],
            ('message_id','wait_id','text','created_at','form_id'))
    return result


def _public_result(value):
    if not isinstance(value, dict):
        return None
    result = _display_fields(value, ('status', 'output', 'error_code'))
    if 'images' in value:
        result['images'] = _public_images(value['images'])
    if isinstance(value.get('assistant_metadata'), dict):
        result['assistant_metadata'] = _public_presentation(value['assistant_metadata'])
    return result


def local_recovery_applicable(row):
    if (row['session_kind'] != 'web' or row['source'] != 'chat'
            or row['status'] not in {'waiting', 'paused', 'interrupted'}
            or (row.get('checkpoint') or {}).get('detached_local_wait') is True
            or row.get('worker_id') is not None or row.get('lease_until') is not None):
        return False
    root = (row.get('checkpoint') or {}).get('execution') or {}
    def pending_local(node):
        waiting = node.get('waiting') or {}
        if waiting.get('kind') == 'clarification':
            return False
        refs = node.get('resources', {}).get('local_invocations') or {}
        return any(node.get('tools', {}).get(ref.get('tool_call_id'), {}).get('phase')
            in {'dispatching', 'waiting'} for ref in refs.values()) or any(
            fact.get('phase') in {'dispatching', 'waiting'}
            and (fact.get('call') or {}).get('name') == 'boss_resume_next'
            for fact in node.get('tools', {}).values()) or any(
            pending_local(child.get('checkpoint') or {}) for child in node.get('children', {}).values())
    return pending_local(root)


def with_local_operation_facts(cursor, row):
    """Transient read projection; frozen IDs and unknown history remain untouched."""
    if row is None:
        return None
    checkpoint = row.get('checkpoint') or {}
    held = set(checkpoint.get('detached_local_invocations') or []) - set(checkpoint.get('verified_local_invocations') or [])
    if not held or not checkpoint.get('detached_local_wait'):
        return row
    from src.local_tools.repository import operation_completion_known
    cursor.execute('SELECT * FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND id=ANY(%s::uuid[])',
        (row['tenant_id'], row['user_id'], row['session_id'], sorted(held)))
    settled = {str(item['id']) for item in cursor.fetchall() if operation_completion_known(dict(item))}
    return {**row, '_unresolved_local_invocations': sorted(held - settled)}


def public_runner(row):
    """Explicit allowlist: tokens, checkpoints, prompts, receipts never cross API."""
    value = {
        "runner_id": row["runner_id"], "session": {"kind": row["session_kind"], "session_id": row["session_id"]},
        "source": row["source"],
        "status": row["status"], "settlement_status": row["settlement_status"],
        "client_request_id": row["client_request_id"],
        "profile_id": row['profile_id'],
        "queue_order": row['queue_order'],
        "revision": row["revision"], "cancel_requested": row["cancel_requested"],
        "pause_requested": row.get('pause_requested',False),
        "resume_requested": bool(row.get('resume_control_id')),
        "view_revision": row["view_revision"], "control_revision": row["control_revision"],
        "accepted_at": row["accepted_at"], "updated_at": row["updated_at"],
        "finished_at": row.get("finished_at"), "snapshot": _public_snapshot(row["public_snapshot"]),
        "result": _public_result(row.get("result")),
    }
    checkpoint = row.get('checkpoint') or {}
    verification = checkpoint.get('local_verification')
    from .continuous import enabled
    if enabled(checkpoint,row.get('input')):
        value['execution_mode'] = 'continuous'
    usage = row.get('public_snapshot',{}).get('usage')
    if isinstance(usage,dict):
        value['usage'] = _display_fields(usage,('task_credits','session_credits','pending','updated_at'))
    blocked = checkpoint.get('sleep_blocked')
    if isinstance(blocked,dict) and isinstance(blocked.get('error_code'),str):
        code = blocked['error_code']
        value['sleep_blocked'] = {'error_code':code if len(code)<=128 and code.replace('_','').isalnum()
            else 'SLEEP_RECOVERY_BLOCKED'}
    if isinstance(verification, dict):
        value['local_verification'] = {key: verification.get(key) for key in
            ('invocation_id', 'state', 'resource_stopped', 'page_observed', 'target_relation', 'error_code', 'stop_error_code')}
        if isinstance(verification.get('page_summary'), dict):
            value['local_verification']['page_summary'] = _display_fields(verification['page_summary'],
                ('observed', 'page_kind', 'result', 'filter_count', 'panel_open', 'count_result'))
        value['local_verification']['waiting_ended'] = checkpoint.get('detached_local_wait') is True
        if verification.get('state') in {'queued', 'claimed', 'running'} and verification.get('deadline_at'):
            from datetime import datetime, timezone
            if datetime.fromisoformat(verification['deadline_at']) < datetime.now(timezone.utc):
                value['local_verification'].update(state='failed', error_code='LOCAL_VERIFICATION_FAILED')
    held = set(checkpoint.get('detached_local_invocations') or []) - set(checkpoint.get('verified_local_invocations') or [])
    held = set(row.get('_unresolved_local_invocations', held))
    if held and checkpoint.get('detached_local_wait'):
        value['local_verification_available'] = True
    if local_recovery_applicable(row):
        value['local_recovery'] = True
        root = checkpoint.get('execution') or {}
        from src.local_tools.repository import owned_invocation_ids
        value['local_recovery_kind'] = 'original_operation' if owned_invocation_ids(root) else 'dispatch_unconfirmed'
        value['local_recovery_actions'] = (['verify', 'stop', 'end'] if row.get('cancel_requested') else
            ['verify', 'stop', 'resume', 'end']) if owned_invocation_ids(root) else (
                ['end'] if row.get('cancel_requested') else ['resume', 'end'])
    return value
