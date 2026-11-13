"""Platform-only observations of long-running Runner attempts."""

import copy
from datetime import datetime, timezone
import json
import uuid

from src.core.agent_events import mask_tool_args
from src.db.database import get_db_connection


DEFAULT_THRESHOLD_MINUTES = 30
MONITOR_KEY = 'runner-long-running'
SCAN_LIMIT = 1024


def close_alert_in_tx(cursor, runner_id, attempt):
    """Close only the attempt whose business state just ended in this transaction."""
    cursor.execute('''UPDATE agent_runner_alerts SET ended_at=clock_timestamp()
        WHERE runner_id=%s AND attempt=%s AND ended_at IS NULL''', (runner_id, attempt))


class RunnerAlertRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    @staticmethod
    def threshold_in_tx(cursor, tenant_id, profile_id):
        if tenant_id is None:
            return DEFAULT_THRESHOLD_MINUTES
        cursor.execute('''SELECT threshold_minutes FROM agent_runner_alert_thresholds
            WHERE tenant_id=%s AND profile_id=%s''', (tenant_id, profile_id))
        row = cursor.fetchone()
        return row['threshold_minutes'] if row else DEFAULT_THRESHOLD_MINUTES

    def candidates(self):
        # The slot topology bounds active rows; the extra row makes overflow loud
        # rather than silently starving a later attempt on every scan.
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT runner_id,attempt,execution_started_at FROM agent_runners
                WHERE status IN ('running','finalizing') AND execution_started_at IS NOT NULL
                ORDER BY queue_order LIMIT %s''', (SCAN_LIMIT + 1,))
            rows = cursor.fetchall()
        if len(rows) > SCAN_LIMIT:
            raise RuntimeError('RUNNER_ALERT_SCAN_CAPACITY_EXCEEDED')
        return rows

    def inspect_candidate(self, candidate):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT runner_id,attempt,status,tenant_id,profile_id,load_class,
                execution_started_at,clock_timestamp() AS database_now
                FROM agent_runners WHERE runner_id=%s FOR UPDATE''', (candidate['runner_id'],))
            runner = cursor.fetchone()
            if (runner is None or runner['attempt'] != candidate['attempt']
                    or runner['execution_started_at'] != candidate['execution_started_at']
                    or runner['status'] not in ('running', 'finalizing')):
                return False
            threshold = self.threshold_in_tx(cursor, runner['tenant_id'], runner['profile_id'])
            duration_seconds = int((runner['database_now'] - runner['execution_started_at']).total_seconds())
            if duration_seconds < threshold * 60:
                return False
            alert_id = 'runner_alert_' + uuid.uuid4().hex
            cursor.execute('''INSERT INTO agent_runner_alerts
                (alert_id,runner_id,attempt,tenant_id,profile_id,started_at,threshold_minutes)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (runner_id,attempt) DO NOTHING''',
                (alert_id, runner['runner_id'], runner['attempt'], runner['tenant_id'],
                 runner['profile_id'], runner['execution_started_at'], threshold))
            cursor.execute('''SELECT alert_id,notification_ref FROM agent_runner_alerts
                WHERE runner_id=%s AND attempt=%s''', (runner['runner_id'], runner['attempt']))
            alert = cursor.fetchone()
            if alert['notification_ref'] is None:
                from src.notifications.producer import publish_platform_runner_alert_in_tx
                notification_ref = publish_platform_runner_alert_in_tx(
                    cursor, runner=runner, duration_seconds=duration_seconds)
                cursor.execute('''UPDATE agent_runner_alerts SET notification_ref=%s
                    WHERE alert_id=%s''', (notification_ref, alert['alert_id']))
            conn.commit()
            return True

    def scan_once(self):
        candidates = self.candidates()
        for candidate in candidates:
            self.inspect_candidate(candidate)
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO agent_runner_monitor_status
                (monitor_key,last_successful_scan_at) VALUES (%s,clock_timestamp())
                ON CONFLICT (monitor_key) DO UPDATE
                SET last_successful_scan_at=EXCLUDED.last_successful_scan_at''', (MONITOR_KEY,))
            conn.commit()
        return len(candidates)

    def healthy(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT last_successful_scan_at >= clock_timestamp()-INTERVAL '3 minutes' AS healthy
                FROM agent_runner_monitor_status WHERE monitor_key=%s''', (MONITOR_KEY,))
            row = cursor.fetchone()
            return bool(row and row['healthy'])

    def monitor_status(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT last_successful_scan_at,
                last_successful_scan_at IS NULL OR
                last_successful_scan_at < clock_timestamp()-INTERVAL '3 minutes' AS stalled
                FROM agent_runner_monitor_status WHERE monitor_key=%s''', (MONITOR_KEY,))
            row = cursor.fetchone()
            return {'last_successful_scan_at': _iso(row['last_successful_scan_at']) if row else None,
                    'stalled': bool(row['stalled']) if row else True}

    def list_alerts(self, *, state='all', runner_id=None, page=1, page_size=20):
        clauses = []
        params = []
        if state == 'current':
            clauses.append('ended_at IS NULL')
        elif state == 'history':
            clauses.append('ended_at IS NOT NULL')
        elif state != 'all':
            raise ValueError('INVALID_ALERT_STATE')
        if runner_id:
            clauses.append('runner_id=%s')
            params.append(runner_id)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) AS total FROM agent_runner_alerts' + where, params)
            total = cursor.fetchone()['total']
            cursor.execute('''SELECT alert_id,runner_id,attempt,tenant_id,profile_id,
                started_at,alerted_at,ended_at,threshold_minutes,
                EXTRACT(EPOCH FROM (COALESCE(ended_at,clock_timestamp())-started_at))::bigint AS elapsed_seconds
                FROM agent_runner_alerts''' + where + ''' ORDER BY alerted_at DESC,alert_id DESC
                LIMIT %s OFFSET %s''', (*params, page_size, (page - 1) * page_size))
            rows = cursor.fetchall()
        return {'items': [_alert_view(row) for row in rows], 'total': total,
                'page': page, 'page_size': page_size}

    def detail(self, alert_id):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT a.*, EXTRACT(EPOCH FROM
                (COALESCE(a.ended_at,clock_timestamp())-a.started_at))::bigint AS elapsed_seconds,
                r.input,r.checkpoint,r.load_class,r.attempt AS current_attempt FROM agent_runner_alerts a
                JOIN agent_runners r ON r.runner_id=a.runner_id WHERE a.alert_id=%s''', (alert_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        # The Runner checkpoint is mutable across attempts. Never present a
        # later attempt's model response as this historical alert's context.
        same_attempt = row['attempt'] == row['current_attempt']
        checkpoint = row['checkpoint'] if isinstance(row['checkpoint'], dict) else {}
        execution = (checkpoint.get('execution') or {}) if same_attempt else {}
        if not isinstance(execution, dict):
            execution = {}
        facts = execution.get('model_calls') or []
        if not isinstance(facts, list):
            facts = []
        latest = next((fact for fact in reversed(facts)
                       if isinstance(fact, dict) and fact.get('phase') == 'completed'
                       and isinstance(fact.get('response'), dict)), None)
        response = _redact_model_view(latest['response']) if latest else None
        response_text = None
        context = None
        saved_at = None
        if response is not None:
            content = response.get('content') or ''
            calls = response.get('tool_calls') or []
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            response_text = (json.dumps({'content': content, 'tool_calls': calls}, ensure_ascii=False)
                             if calls else content)
            context = _redact_model_view({'system_prompt': execution.get('system_prompt'),
                                          'messages': execution.get('messages')})
            if type(latest.get('end_ts')) in (int, float):
                try:
                    saved_at = datetime.fromtimestamp(latest['end_ts'], timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    pass
        return {'alert': _alert_view(row), 'runner': {
            'request_text': (row['input'] or {}).get('text'),
            'latest_model_response': response_text,
            'latest_model_context': context,
            'model_saved_at': saved_at,
            'context_redacted': response is not None,
            'load_class': row.get('load_class') if same_attempt else None,
            'model_unavailable_reason': None if same_attempt else 'CURRENT_ATTEMPT_CHANGED'}}

    def thresholds(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT tenant_id,profile_id,threshold_minutes
                FROM agent_runner_alert_thresholds ORDER BY tenant_id,profile_id''')
            rows = cursor.fetchall()
        return [{'tenant_id': row['tenant_id'], 'profile_id': row['profile_id'],
                 'minutes': row['threshold_minutes']} for row in rows]

    def set_threshold(self, tenant_id, profile_id, minutes):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO agent_runner_alert_thresholds
                (tenant_id,profile_id,threshold_minutes) VALUES (%s,%s,%s)
                ON CONFLICT (tenant_id,profile_id) DO UPDATE SET
                threshold_minutes=EXCLUDED.threshold_minutes,updated_at=clock_timestamp()''',
                (tenant_id, profile_id, minutes))
            conn.commit()

    def delete_threshold(self, tenant_id, profile_id):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''DELETE FROM agent_runner_alert_thresholds
                WHERE tenant_id=%s AND profile_id=%s''', (tenant_id, profile_id))
            conn.commit()


def _iso(value):
    return value.isoformat() if value is not None else None


def _alert_view(row):
    return {'alert_id': row['alert_id'], 'runner_id': row['runner_id'], 'attempt': row['attempt'],
            'tenant_id': row['tenant_id'], 'profile_id': row['profile_id'],
            'started_at': _iso(row['started_at']), 'alerted_at': _iso(row['alerted_at']),
            'ended_at': _iso(row['ended_at']), 'elapsed_seconds': row['elapsed_seconds'],
            'threshold_minutes': row['threshold_minutes'],
            'status': 'current' if row['ended_at'] is None else 'history'}


def _redact_model_view(value):
    """Reuse the Runner's existing tool-argument masking before UI projection."""
    copied = copy.deepcopy(value)
    def walk(node):
        if isinstance(node, dict):
            for call in node.get('tool_calls') or []:
                if not isinstance(call, dict):
                    continue
                function = call.get('function') or {}
                if not isinstance(function, dict):
                    continue
                arguments = function.get('arguments')
                if isinstance(arguments, str):
                    try:
                        parsed = json.loads(arguments)
                    except (TypeError, ValueError):
                        parsed = {}
                    function['arguments'] = parsed if isinstance(parsed, dict) else {}
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(copied)
    return mask_tool_args(copied)
