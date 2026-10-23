"""Actual settled credit projection scoped to the original session owner."""


def usage_in_tx(cursor, runner, settlement):
    cursor.execute('''SELECT COALESCE(SUM(credit_cost),0) AS session_credits,
        COALESCE(SUM(CASE WHEN record_id=%s THEN credit_cost ELSE 0 END),0) AS task_credits
        FROM chat_records WHERE tenant_id IS NOT DISTINCT FROM %s
        AND user_id IS NOT DISTINCT FROM %s AND session_id=%s AND source_type=%s''',
        (runner['record_id'],runner['tenant_id'],runner['user_id'],runner['session_id'],runner['source']))
    totals = cursor.fetchone()
    values = {'task_credits':float(totals['task_credits']), 'session_credits':float(totals['session_credits']),
        'pending':settlement != 'settled'}
    previous = (runner.get('public_snapshot') or {}).get('usage') or {}
    if all(previous.get(key)==value for key,value in values.items()) and previous.get('updated_at'):
        return {**values,'updated_at':previous['updated_at']}
    cursor.execute('SELECT clock_timestamp() AS now')
    return {**values,'updated_at':cursor.fetchone()['now'].isoformat()}
