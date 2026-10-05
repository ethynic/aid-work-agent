"""Recruiting effects composed with one original domain phase transaction.

These writers neither open connections nor perform filesystem/provider IO.
Known business SQL failures retain the legacy nonblocking per-item behavior;
connection loss and the outer checkpoint/fence remain authority failures.
"""

import psycopg2


def _known_write(cursor, label, operation, error_code):
    cursor.execute('SAVEPOINT ' + label)
    try:
        result = operation()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        raise
    except (psycopg2.DatabaseError, ValueError):
        cursor.execute('ROLLBACK TO SAVEPOINT ' + label)
        cursor.execute('RELEASE SAVEPOINT ' + label)
        return {'applied': False, 'error_code': error_code}
    cursor.execute('RELEASE SAVEPOINT ' + label)
    return result


def write_resume(cursor, row, intent):
    from src.services.recruiting_resume_service import create_resume_record_in_tx

    def insert():
        record = create_resume_record_in_tx(cursor, row['tenant_id'], row['user_id'],
            candidate_name=intent['candidate_name'], job_name=intent.get('job_name'),
            job_id=intent.get('job_id'), candidate_info=intent.get('candidate_info'),
            images=intent['images'], resume_summary=intent.get('resume_summary'),
            source='boss', fetched_at=intent.get('fetched_at'), remark=intent.get('remark'))
        # The original DAL owns the full record; the phase only needs its stable
        # reference and the existing small tool display fields.
        return {'applied': True, 'resume_id': record['id'],
            'candidate_name': record['candidate_name'], 'job_name': record.get('job_name'),
            'job_id': record.get('job_id'), 'image_count': len(record.get('images') or []),
            'summary_preview': (record.get('resume_summary') or '')[:80]}

    return _known_write(cursor, 'local_resume_insert', insert, 'RESUME_STORE_FAILED')


def write_match(cursor, row, intent):
    from src.services import recruiting_match_service, recruiting_job_service

    def update():
        score = intent.get('score')
        if score is None:
            return {'applied': False, 'match_score': None, 'match_status': None,
                'match_summary': None, 'match_note': '未评分'}
        threshold = intent.get('match_threshold')
        if threshold is None:
            threshold = recruiting_job_service.DEFAULT_MATCH_THRESHOLD
        status = recruiting_match_service._decide_match_status(score, threshold)
        applied = recruiting_match_service.update_match_fields_in_tx(cursor,
            row['tenant_id'], intent['resume_id'], score, intent.get('match_summary'),
            status, intent.get('key_info'))
        if not applied:
            return {'applied': False, 'match_score': None, 'match_status': None,
                'match_summary': None, 'match_note': '简历不存在（评分期间被删除）'}
        return {'applied': True, 'match_score': score, 'match_status': status,
            'match_summary': intent.get('match_summary')}

    return _known_write(cursor, 'local_resume_match', update, 'RESUME_MATCH_NOT_APPLIED')


def write_comm_log(cursor, row, intent):
    from src.services.recruiting_resume_timeline_service import create_comm_log_in_tx

    def insert():
        record = create_comm_log_in_tx(cursor, row['tenant_id'], intent['resume_id'],
            'out', 'boss', intent['message'], row['user_id'])
        return {'applied': True, 'log_id': record['id']}

    return _known_write(cursor, 'local_resume_comm', insert, 'RESUME_COMM_LOG_NOT_APPLIED')


def write_notify_log(cursor, row, intent):
    from src.services.recruiting_notify_service import insert_log_in_tx

    def insert():
        log_id = insert_log_in_tx(cursor, row['tenant_id'], intent['kind'],
            intent['candidates'], intent['content'], intent['status'],
            intent.get('error'), intent.get('resume_id'))
        result = {'applied': True, 'pushed': intent['status'] == 'sent', 'log_id': log_id}
        if intent.get('error'):
            result['error'] = intent['error']
        return result

    return _known_write(cursor, 'local_notify_log', insert, 'LOCAL_NOTIFY_LOG_NOT_APPLIED')
