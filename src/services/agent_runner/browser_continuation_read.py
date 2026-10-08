"""Thin read-only continuation events for old cached Browser cards.

An old cached HumanAssistanceCard only knows its random ``bac`` and polls
``GET /api/agent/continuations/{bac}/events``. Native Runner waits now persist
that bac with their execution/call/wait row, so this module resolves the card
from durable facts alone:

* the persisted wait row is joined with its Runner and the current owner /
  selected tenant is re-verified (fresh subject, owned chat session, matching
  web Runner identity) before anything is returned;
* while the Redis continuation projection still exists its events are returned
  exactly as stored;
* once that projection is gone, only stable Runner facts are replayed: the
  terminal output as one cursor-deduplicated text increment (never the
  cumulative text on every poll), stop events and a second human wait. Mid-run
  text deltas are not reconstructed from guesses.

The bridge is strictly read-only: it never enqueues ``bs_browser_resume_jobs``,
never invokes the legacy continuation callback and never rebuilds an Agent.
"""

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.db.database import get_db_connection
from src.services.auth_service import AuthSubjectError, fresh_web_subject

from .contracts import RunnerError
from .repository import decoded


def _not_found():
    return RunnerError('CONTINUATION_NOT_FOUND', 404)


def native_continuation_events(authorization, target_tenant, continuation_id, after_seq):
    """Return ``(events, last_seq)`` for a persisted native wait.

    ``None`` means the bac has no native persisted row and the caller must keep
    the original legacy Redis behaviour. Ownership failures raise 404 so the
    endpoint never discloses whether another tenant's bac exists.
    """
    try:
        user = fresh_web_subject(authorization)
    except AuthSubjectError as error:
        raise RunnerError(error.code, error.status) from None
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('''SELECT a.* FROM bs_browser_assistance_requests a
                          WHERE a.continuation_id=%s AND a.runner_id IS NOT NULL''',
                       (continuation_id,))
        wait = cursor.fetchone()
        if wait is None:
            return None
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (wait['runner_id'],))
        raw = cursor.fetchone()
        runner = decoded(raw) if raw else None
        if (runner is None or runner['session_kind'] != 'web' or runner['source'] != 'chat'
                or runner['tenant_id'] != wait['tenant_id'] or runner['user_id'] != wait['user_id']):
            raise _not_found()
        if (user['user_id'] != wait['user_id']
                or (user['role'] != 'platform_admin' and user['tenant_id'] != wait['tenant_id'])
                or (target_tenant is not None and wait['tenant_id'] != target_tenant)):
            raise _not_found()
        # The wait row stores no session; the owned chat session comes from the
        # joined Runner, keeping the same owned-session predicate as the gateway.
        cursor.execute('''SELECT 1 FROM chat_sessions WHERE session_id=%s AND user_id=%s
                          AND tenant_id IS NOT DISTINCT FROM %s''',
                       (runner['session_id'], user['user_id'], wait['tenant_id']))
        if cursor.fetchone() is None:
            raise _not_found()
        cursor.execute('''SELECT * FROM bs_browser_assistance_requests WHERE run_id=%s
                          AND runner_id=%s AND id>%s AND state IN ('pending','controlling')
                          ORDER BY id LIMIT 1''',
                       (wait['run_id'], wait['runner_id'], wait['id']))
        second = cursor.fetchone()
        snapshot = (runner.get('public_snapshot') or {})
        card = snapshot.get('browserAssistance') if isinstance(snapshot, dict) else None
        if not (isinstance(card, dict) and card.get('assistance_id') == (second or {}).get('assistance_id')):
            card = None
        return _events(wait, runner, second, card, after_seq)


def _events(wait, runner, second, card, after_seq):
    """Redis projection first; durable-fact replay only after it is gone."""
    state = redis_client.get(redis_client.make_key(
        CacheKeys.AGENT_CONTINUATION_EVENTS, f"{wait['tenant_id']}:{wait['continuation_id']}"))
    if state is not None:
        events = [item for item in state.get('events', [])
                  if int(item.get('seq', 0)) > after_seq]
        return events, events[-1]['seq'] if events else after_seq
    # Replayed sequence numbers are derived deterministically from the persisted
    # wait row, never from the client cursor: a poll with an advanced cursor must
    # return nothing again, and the same facts keep the same identity.
    base = wait['id'] * 16
    events = []

    def mint(payload, ordinal):
        events.append({**payload, 'seq': base + ordinal})

    if wait.get('completion_ref'):
        mint({'type': 'browser_resume_started', 'run_id': wait['run_id']}, 1)
    if second is not None:
        payload = {'type': 'browser_human_required', 'surface': 'server_web',
                   'assistance_id': second['assistance_id'], 'run_id': second['run_id'],
                   'continuation_id': second['continuation_id'] or '',
                   'reason_code': second['reason_code'],
                   'completion_mode': second['completion_mode'],
                   'completion_status': 'waiting'}
        if card:
            payload.update({key: card[key] for key in ('title', 'steps', 'expires_at', 'view_available')
                            if key in card})
        mint(payload, 2)
    status = runner['status']
    result = runner.get('result') or {}
    if status == 'cancelled':
        mint({'type': 'browser_run_closed', 'error_code': 'RUNNER_CANCELLED'}, 3)
    elif status == 'failed':
        mint({'type': 'browser_run_closed',
              'error_code': result.get('error_code') or 'RUNNER_EXECUTION_FAILED'}, 3)
    elif status == 'completed':
        # The terminal output is the only stable text fact. It is delivered as
        # one increment beyond the client's cursor; the deterministic seq keeps
        # later polls from re-sending any cumulative text. Mid-run deltas are
        # not rebuilt.
        output = result.get('output') if isinstance(result.get('output'), str) else ''
        if output:
            mint({'type': 'response', 'data': output}, 3)
        mint({'type': 'agent_continuation_completed',
              'continuation_id': wait['continuation_id']}, 4)
    events = [item for item in events if item['seq'] > after_seq]
    return events, events[-1]['seq'] if events else after_seq
