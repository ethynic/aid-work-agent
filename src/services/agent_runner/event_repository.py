"""Cursor-only public event ledger. Callers own auth, root lock and commit.

Append is called once at the outer transaction's final public-state tail, never
from an inner domain hook. Reads require the caller's authorized root row and
REPEATABLE READ snapshot. There are no connections, queues or transport objects.
"""

import json
from datetime import timedelta

from src.core.agent_engine.contracts import CheckpointFailure

from .event_contracts import (EventContractError, change_kind, encoded_bytes,
                              invalidate_event, nonnegative, only_output_changed,
                              safe_public_state, stored_event, cancellation_became_ready,
                              cursor_state)
from .public_view import decoded, public_runner


class EventRepository:
    @staticmethod
    def notify_in_tx(cursor, before, *, after=None, kind='revision_changed', cancellation_ready=False):
        """One explicit outer commit tail after all inner public projections."""
        try:
            if after is None:
                cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (before['runner_id'],))
                after = decoded(cursor.fetchone())
            return EventRepository.append_in_tx(cursor, before, after, kind=kind,
                                               cancellation_ready=cancellation_ready)[0]
        except CheckpointFailure:
            raise
        except Exception as error:
            # A public ledger failure rolls back its original state transaction;
            # it cannot become a normal failed tool result after physical IO.
            raise CheckpointFailure('RUNNER_PUBLIC_EVENT_COMMIT_FAILED') from error

    @staticmethod
    def append_in_tx(cursor, before, after, *, kind='revision_changed', output_interval_ms=250,
                     cancellation_ready=False):
        """Original root must already be locked before any domain/claim row."""
        if type(output_interval_ms) is not int or not 0 <= output_interval_ms <= 60_000:
            raise EventContractError('INVALID_EVENT_OUTPUT_INTERVAL')
        if type(cancellation_ready) is not bool:
            raise EventContractError('INVALID_EVENT_CANCEL_READINESS')
        selected = change_kind(before, after, kind)
        if cancellation_ready:
            if before is None or not cancellation_became_ready(before, after):
                raise EventContractError('EVENT_CANCEL_READINESS_FACT_REQUIRED')
            selected = selected or 'revision_changed'
        if selected is None:
            return after, None
        cursor.execute('''SELECT *,clock_timestamp() AS database_now
            FROM agent_runners WHERE runner_id=%s FOR UPDATE''', (after['runner_id'],))
        current = decoded(cursor.fetchone())
        if (not current or current['view_revision'] != after['view_revision']
                or current['control_revision'] != after['control_revision']
                or safe_public_state(current) != safe_public_state(after)):
            raise EventContractError('EVENT_PUBLIC_ROW_CHANGED')
        head = nonnegative(current['event_seq'], 'head')
        floor = nonnegative(current['event_floor_seq'], 'floor')
        if floor > head:
            raise EventContractError('INVALID_EVENT_WATERMARK')
        if selected == 'created' and head != 0:
            raise EventContractError('EVENT_CREATED_ALREADY_EXISTS')
        if before is not None and only_output_changed(before, current) and output_interval_ms:
            cursor.execute('SELECT created_at FROM agent_runner_events WHERE runner_id=%s AND seq=%s',
                           (current['runner_id'], head))
            previous = cursor.fetchone()
            if previous and current['database_now'] < previous['created_at'] + timedelta(milliseconds=output_interval_ms):
                return current, None
        # Stable created/terminal facts cannot acquire a second event identity.
        if selected in {'created', 'terminal'}:
            cursor.execute('SELECT seq FROM agent_runner_events WHERE runner_id=%s AND kind=%s',
                           (current['runner_id'], selected))
            if cursor.fetchone():
                raise EventContractError('EVENT_STABLE_FACT_ALREADY_PUBLISHED')
        event = invalidate_event(current, selected, head + 1)
        cursor.execute('''INSERT INTO agent_runner_events
            (runner_id,seq,tenant_id,scope_key,kind,payload,created_at)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)''',
            (current['runner_id'], head + 1, current['tenant_id'], current['scope_key'],
             selected, json.dumps(event, ensure_ascii=False), current['database_now']))
        cursor.execute('''UPDATE agent_runners SET event_seq=%s
            WHERE runner_id=%s AND event_seq=%s RETURNING *''',
            (head + 1, current['runner_id'], head))
        updated = decoded(cursor.fetchone())
        if updated is None:
            raise EventContractError('EVENT_HEAD_CHANGED')
        return updated, event

    @staticmethod
    def read_page_in_tx(cursor, authorized_row, after_seq, *, limit=100, max_bytes=65_536):
        """Auth and this row/page must share the caller's short MVCC snapshot."""
        nonnegative(after_seq, 'cursor')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise EventContractError('INVALID_EVENT_PAGE_LIMIT')
        if type(max_bytes) is not int or not 1024 <= max_bytes <= 1_048_576:
            raise EventContractError('INVALID_EVENT_PAGE_BYTES')
        cursor.execute('SHOW transaction_isolation')
        if cursor.fetchone()['transaction_isolation'] != 'repeatable read':
            raise EventContractError('EVENT_CONSISTENT_SNAPSHOT_REQUIRED')
        head = nonnegative(authorized_row['event_seq'], 'head')
        floor = nonnegative(authorized_row['event_floor_seq'], 'floor')
        if floor > head:
            raise EventContractError('INVALID_EVENT_WATERMARK')

        state = cursor_state(authorized_row)

        def reset():
            return {'reset': True, 'runner': public_runner(authorized_row), 'head': head,
                    'floor': floor, 'events': [], 'last_seq': head, 'has_more': False,
                    'state': state}

        if after_seq < floor or after_seq > head:
            return reset()
        cursor.execute('''SELECT runner_id,seq,kind,payload FROM agent_runner_events
            WHERE runner_id=%s AND tenant_id IS NOT DISTINCT FROM %s AND scope_key=%s
            AND seq>%s AND seq<=%s ORDER BY seq LIMIT %s''',
            (authorized_row['runner_id'], authorized_row['tenant_id'], authorized_row['scope_key'],
             after_seq, head, limit))
        rows = cursor.fetchall()
        events, total, expected, byte_limited = [], 0, after_seq + 1, False
        for saved in rows:
            if saved['seq'] != expected:
                return reset()
            event = stored_event(saved)
            size = encoded_bytes(event)
            if total + size > max_bytes:
                if not events:
                    return reset()
                byte_limited = True
                break
            events.append(event)
            total += size
            expected += 1
        last = events[-1]['seq'] if events else after_seq
        if last < head and not events:
            return reset()
        if last < head and not byte_limited and len(rows) < limit:
            return reset()
        return {'reset': False, 'head': head, 'floor': floor, 'events': events,
                'last_seq': last, 'has_more': last < head, 'state': state}

    @staticmethod
    def prune_in_tx(cursor, root_row, before_seq, *, limit=1000):
        """Delete only a bounded contiguous prefix under the original root lock."""
        nonnegative(before_seq, 'retention_cursor')
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise EventContractError('INVALID_EVENT_RETENTION_LIMIT')
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE', (root_row['runner_id'],))
        row = decoded(cursor.fetchone())
        if row is None:
            raise EventContractError('EVENT_RUNNER_MISSING')
        head = nonnegative(row['event_seq'], 'head')
        floor = nonnegative(row['event_floor_seq'], 'floor')
        if floor > head:
            raise EventContractError('INVALID_EVENT_WATERMARK')
        cursor.execute('''SELECT seq FROM agent_runner_events WHERE runner_id=%s
            AND seq>%s AND seq<=%s ORDER BY seq LIMIT %s''',
            (row['runner_id'], floor, min(head, before_seq), limit))
        candidates = cursor.fetchall()
        expected, final = floor + 1, floor
        for value in candidates:
            if value['seq'] != expected:
                raise EventContractError('EVENT_RETENTION_HOLE')
            expected += 1
            final = value['seq']
        if len(candidates) < limit and final < min(head, before_seq):
            raise EventContractError('EVENT_RETENTION_HOLE')
        if final == floor:
            return row, 0
        cursor.execute('DELETE FROM agent_runner_events WHERE runner_id=%s AND seq>%s AND seq<=%s',
                       (row['runner_id'], floor, final))
        deleted = cursor.rowcount
        if deleted != final - floor:
            raise EventContractError('EVENT_RETENTION_PREFIX_CHANGED')
        cursor.execute('''UPDATE agent_runners SET event_floor_seq=%s WHERE runner_id=%s
            AND event_floor_seq=%s RETURNING *''', (final, row['runner_id'], floor))
        updated = decoded(cursor.fetchone())
        if updated is None:
            raise EventContractError('EVENT_FLOOR_CHANGED')
        return updated, deleted
