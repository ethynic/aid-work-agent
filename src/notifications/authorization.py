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
    if ref['type'] == 'scheduled_task':
        # 定时任务暂停通知（Phase 3，设计 §8.2）：任务行存在且归属接收人即可
        # 解析（通知到达时任务必然 paused，用户恢复后点击历史通知不应 404，
        # 故不限定任务状态）。
        cursor.execute('''SELECT task_id,session_id,name,status FROM scheduled_tasks
            WHERE task_id=%s AND user_id=%s AND tenant_id IS NOT DISTINCT FROM %s''',
            (ref['id'], subject.user_id, subject.tenant_id))
        row = cursor.fetchone()
        if not row:
            raise NotificationError('NOTIFICATION_RESOURCE_UNAVAILABLE', 404)
        return {**dict(row), 'type': ref['type'], 'tenant_id': subject.tenant_id}
    if ref['type'] == 'runner_waiting':
        # 提问等待通知（Phase 2，设计 §5.6/§7.1）：runner 仍处于 waiting 且
        # 归属/入口一致；点击解析后前端带 _load_session 回到会话即可见待答卡片。
        # scheduled 来源（Phase 3 复核：非提问类等待 needs_action）同为 web
        # 会话、属主即接收人——system/scheduler actor 不参与归属判定，
        # 以 runner.user_id 与会话归属为准（租户/用户/会话三方核对）。
        status_clause = "r.status = 'waiting'"
        actor_clause = "(r.actor_kind='user' AND r.actor_id=%(user)s AND r.source='chat' " \
                       "OR (r.source='scheduled' AND r.user_id=%(user)s))"
    elif ref['type'] == 'runner_result':
        status_clause = "r.status IN ('completed','failed')"
        actor_clause = "(r.actor_kind='user' AND r.actor_id=%(user)s AND r.source='chat' " \
                       "OR (r.source='scheduled' AND r.user_id=%(user)s))"
    else:
        raise NotificationError('NOTIFICATION_RESOURCE_UNSUPPORTED', 403)
    params = {'user': subject.user_id, 'runner': ref['id'], 'scope': subject.scope_key,
              'tenant': subject.tenant_id}
    # scheduled_task_id（Phase 5 二轮评审第 8 项）：scheduled 来源的 runner 资源
    # 透出所属任务行 id（scheduled_tasks.session_id UNIQUE，子查询至多一行），
    # 通知入口据此直接路由任务视图，不再经任务列表匹配。任务已删除时为 NULL。
    cursor.execute(f'''SELECT r.runner_id,r.session_id,r.profile_id,s.subagent_id,s.instance_id,s.title,
        (SELECT t.task_id FROM scheduled_tasks t WHERE t.session_id=r.session_id) AS scheduled_task_id
        FROM agent_runners r JOIN chat_sessions s ON s.session_id=r.session_id
        WHERE r.runner_id=%(runner)s AND r.scope_key=%(scope)s
        AND r.tenant_id IS NOT DISTINCT FROM %(tenant)s
        AND r.user_id=%(user)s AND r.session_kind='web' AND {status_clause}
        AND {actor_clause}
        AND s.user_id=%(user)s AND s.tenant_id IS NOT DISTINCT FROM %(tenant)s''',
        params)
    resource = cursor.fetchone()
    if not resource:
        raise NotificationError('NOTIFICATION_RESOURCE_UNAVAILABLE', 404)
    return {**dict(resource), 'tenant_id':subject.tenant_id}
