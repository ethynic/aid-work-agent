"""Fresh recipient/resource read guards; never retain an end-user bearer."""
from src.notifications.contracts import NotificationError, Subject


def assert_subject_in_tx(cursor, subject):
    cursor.execute('SELECT user_id,tenant_id,role,status FROM users WHERE user_id=%s', (subject.user_id,))
    user = cursor.fetchone()
    if not user or user['status'] != 'active':
        raise NotificationError('NOTIFICATION_UNAUTHORIZED', 401)
    platform = user['role'] == 'platform_admin'
    if (not platform and user['tenant_id'] != subject.tenant_id) or (subject.tenant_id is None and not platform):
        raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
    if subject.tenant_id is not None:
        cursor.execute('''SELECT 1 FROM tenants WHERE tenant_id=%s AND status='active'
            AND (expire_at IS NULL OR expire_at>CURRENT_TIMESTAMP)''', (subject.tenant_id,))
        if not cursor.fetchone():
            raise NotificationError('NOTIFICATION_TENANT_UNAVAILABLE', 403)
    return user


def resource_in_tx(cursor, message):
    subject = Subject(message['recipient_user_id'], message['tenant_id'])
    if subject.scope_key != message['scope_key']:
        raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
    assert_subject_in_tx(cursor, subject)
    ref = message['resource_ref']
    if ref['type'] == 'notification_test':
        return None
    if ref['type'] in ('tenant_credit', 'tenant_profile', 'abuse_event'):
        # 租户级事件：resource 即租户本身，可见性由接收人管理员身份与租户隔离校验保证。
        return {'type': ref['type'], 'id': ref['id'], 'tenant_id': subject.tenant_id}
    if ref['type'] != 'runner_result':
        raise NotificationError('NOTIFICATION_RESOURCE_UNSUPPORTED', 403)
    cursor.execute('''SELECT r.runner_id,r.session_id,r.profile_id,s.subagent_id,s.instance_id,s.title
        FROM agent_runners r JOIN chat_sessions s ON s.session_id=r.session_id
        WHERE r.runner_id=%s AND r.scope_key=%s AND r.tenant_id IS NOT DISTINCT FROM %s
        AND r.user_id=%s AND r.actor_kind='user' AND r.actor_id=%s
        AND r.source='chat' AND r.session_kind='web' AND r.status IN ('completed','failed')
        AND s.user_id=%s AND s.tenant_id IS NOT DISTINCT FROM %s''',
        (ref['id'], subject.scope_key, subject.tenant_id, subject.user_id, subject.user_id,
         subject.user_id, subject.tenant_id))
    resource = cursor.fetchone()
    if not resource:
        raise NotificationError('NOTIFICATION_RESOURCE_UNAVAILABLE', 404)
    return {**dict(resource), 'tenant_id':subject.tenant_id}
