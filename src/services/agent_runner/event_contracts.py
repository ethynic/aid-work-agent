"""Finite public notification contracts; execution and transport stay elsewhere."""

import copy
import json

from .contracts import RunnerStatus, TERMINAL_STATUSES, canonical_json
from .public_view import public_runner


EVENT_KINDS = frozenset({'created', 'revision_changed', 'terminal', 'settlement_changed'})
_PUBLIC_PROGRESS = frozenset({'progress', 'tool_start', 'tool_result', 'thinking', 'clarification'})


class EventContractError(ValueError):
    pass


def nonnegative(value, name):
    if type(value) is not int or value < 0:
        raise EventContractError('INVALID_EVENT_' + name.upper())
    return value


def safe_public_state(row):
    """Compare public meaning, excluding clocks, private iteration and CAS churn."""
    value = public_runner(row)
    snapshot = copy.deepcopy(value['snapshot'])
    snapshot.pop('iteration', None)
    if 'progressMessages' in snapshot:
        snapshot['progressMessages'] = [item for item in snapshot['progressMessages']
                                        if item.get('type') in _PUBLIC_PROGRESS]
    return {key: value[key] for key in ('status', 'settlement_status', 'cancel_requested',
            'pause_requested', 'resume_requested', 'control_revision')} | {
                'snapshot': snapshot, 'result': value['result']}


def change_kind(before, after, requested='revision_changed'):
    if requested not in EVENT_KINDS:
        raise EventContractError('INVALID_EVENT_KIND')
    if before is None:
        if requested != 'created':
            raise EventContractError('EVENT_CREATED_FACT_REQUIRED')
        return 'created'
    if before['runner_id'] != after['runner_id']:
        raise EventContractError('EVENT_RUNNER_MISMATCH')
    previous, current = safe_public_state(before), safe_public_state(after)
    if previous == current:
        return None
    if requested == 'created':
        raise EventContractError('EVENT_CREATED_ALREADY_EXISTS')
    if after['status'] in TERMINAL_STATUSES and before['status'] not in TERMINAL_STATUSES:
        return 'terminal'
    if requested == 'terminal':
        raise EventContractError('EVENT_TERMINAL_FACT_REQUIRED')
    if previous['settlement_status'] != current['settlement_status']:
        return 'settlement_changed'
    if requested == 'settlement_changed':
        raise EventContractError('EVENT_SETTLEMENT_FACT_REQUIRED')
    return 'revision_changed'


def only_output_changed(before, after):
    previous, current = safe_public_state(before), safe_public_state(after)
    old_output = previous['snapshot'].pop('output', None)
    new_output = current['snapshot'].pop('output', None)
    return old_output != new_output and previous == current


def cancellation_became_ready(before, after):
    """One finite public cancellation notice from the original proof sweep."""
    old = (before.get('checkpoint') or {}).get('cancel_completion_blocked') or {}
    new = (after.get('checkpoint') or {}).get('cancel_completion_blocked') or {}
    return (before['cancel_requested'] is True and after['cancel_requested'] is True
            and before['status'] == after['status'] == 'waiting'
            and old.get('ready') is False and new.get('ready') is True
            and old.get('attempt') == new.get('attempt') == after['attempt']
            and new.get('revision') == after['revision'])


def cursor_state(row):
    """Small typed public state from the same authorized event-read snapshot."""
    if (not isinstance(row['runner_id'], str) or not row['runner_id']
            or row['status'] not in {status.value for status in RunnerStatus}
            or row['settlement_status'] not in {'pending', 'settled'}):
        raise EventContractError('INVALID_EVENT_PAYLOAD')
    return {'runner_id': row['runner_id'], 'version': 1,
            'attempt': nonnegative(row['attempt'], 'attempt'),
            'view_revision': nonnegative(row['view_revision'], 'view_revision'),
            'control_revision': nonnegative(row['control_revision'], 'control_revision'),
            'status': row['status'], 'settlement_status': row['settlement_status']}


def invalidate_event(row, kind, seq):
    """No prompts, token deltas, frames, receipts or cumulative text in the ledger."""
    if kind not in EVENT_KINDS or nonnegative(seq, 'seq') == 0:
        raise EventContractError('INVALID_EVENT_PAYLOAD')
    return {**cursor_state(row), 'seq': seq, 'kind': kind, 'invalidate': True}


def encoded_bytes(value):
    return len(canonical_json(value).encode('utf-8'))


def stored_event(row):
    payload = row['payload']
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise EventContractError('INVALID_STORED_EVENT')
    required = {'runner_id', 'seq', 'kind', 'version', 'attempt', 'invalidate', 'view_revision',
                'control_revision', 'status', 'settlement_status'}
    if (set(payload) != required or type(payload.get('seq')) is not int
            or payload['seq'] != row['seq'] or type(payload.get('version')) is not int
            or payload['version'] != 1 or payload.get('invalidate') is not True
            or payload != invalidate_event(payload, row['kind'], row['seq'])):
        raise EventContractError('INVALID_STORED_EVENT')
    if payload['runner_id'] != row['runner_id'] or payload['kind'] != row['kind']:
        raise EventContractError('INVALID_STORED_EVENT')
    return payload
