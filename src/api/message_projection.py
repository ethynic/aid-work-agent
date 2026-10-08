"""Public conversation rows, separate from private model/tool history."""

import copy

from src.services.agent_runner.presentation import (
    browser_display, option_items, progress_display, scalar_fields,
)


def _items(values, fields):
    return [scalar_fields(value, fields) for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def public_conversation_messages(messages):
    result = []
    for message in messages:
        metadata = message.get('metadata') or {}
        if (message.get('role') not in {'user', 'assistant'} or message.get('tool_calls')
                or isinstance(metadata, dict) and metadata.get('tool_calls')):
            continue
        if not isinstance(metadata, dict) or not metadata.get('runner_id'):
            # Existing legacy history keeps its established presentation policy.
            result.append(copy.deepcopy(message))
            continue
        projected = scalar_fields(message, ('message_id', 'session_id', 'role', 'content'))
        if 'created_at' in message:
            projected['created_at'] = message['created_at']
        display = scalar_fields(metadata, ('runner_id', 'runner_queue_order', 'control_id', 'wait_id', 'client_request_id',
            'accepted_at', 'sent_at', 'cancelled', 'imagesPlacement'))
        for key, fields in (
            ('attachments', ('file_id', 'name', 'type', 'mime_type', 'size')),
            ('images', ('file_id', 'download_url', 'display_name', 'width', 'height', 'mime_type', 'size_bytes', 'source', 'usage', 'placement')),
            ('downloadableFiles', ('file_id', 'file_name', 'file_size', 'download_url', 'mime_type')),
            ('verboseMessages', ('eventId', 'data', 'source', 'timestamp', 'delivery')),
        ):
            if key in metadata:
                display[key] = _items(metadata[key], fields)
        if isinstance(metadata.get('progressMessages'), list):
            display['progressMessages'] = [progress_display(item) for item in metadata['progressMessages'] if isinstance(item, dict)]
        if isinstance(metadata.get('browserAssistance'), dict):
            display['browserAssistance'] = browser_display(metadata['browserAssistance'])
        if isinstance(metadata.get('quickOptions'), list):
            display['quickOptions'] = option_items(metadata['quickOptions'])
        projected['metadata'] = display
        result.append(projected)
    return result
