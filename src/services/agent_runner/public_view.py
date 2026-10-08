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


def _public_waiting(value):
    if not isinstance(value, dict):
        return None
    result = _display_fields(value, ('kind', 'question', 'tool_call_id', 'execution_id',
        'invocation_id', 'assistance_id', 'child_status','wait_id','target_execution_id'))
    if isinstance(value.get('missing_info'), list):
        result['missing_info'] = [item for item in value['missing_info'] if isinstance(item, str)]
    if isinstance(value.get('child_wait'), dict):
        result['child_wait'] = _public_waiting(value['child_wait'])
    return result


def _public_snapshot(value):
    value = value if isinstance(value, dict) else {}
    result = _display_fields(value, ('progress', 'output', 'iteration', 'imagesPlacement'))
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
            result['supplementalInputs'].append(projected)
    if isinstance(value.get('clarificationQuestions'),list):
        result['clarificationQuestions'] = _display_items(value['clarificationQuestions'],
            ('message_id','wait_id','text','created_at'))
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


def public_runner(row):
    """Explicit allowlist: tokens, checkpoints, prompts, receipts never cross API."""
    return {
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
