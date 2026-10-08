"""Fresh Web subject and owned-session guards shared by short read operations.

fresh_web_user 仅是 HTTP 薄适配：中性主体校验在认证服务 fresh_web_subject，
AuthSubjectError 按同状态码/同 detail 转为 HTTPException。
"""

from fastapi import HTTPException

from src.db.database import get_db_connection
from src.services.auth_service import AuthSubjectError, fresh_web_subject


def fresh_web_user(authorization):
    try:
        return fresh_web_subject(authorization)
    except AuthSubjectError as error:
        raise HTTPException(error.status, error.code) from None


def owned_web_session(authorization, target_tenant, session_id):
    user = fresh_web_user(authorization)
    platform = user['role'] == 'platform_admin'
    tenant_id = (target_tenant or None) if platform else user['tenant_id']
    if (not platform and target_tenant and target_tenant != tenant_id) or (tenant_id is None and not platform):
        raise HTTPException(403, 'TENANT_FORBIDDEN')
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('''SELECT * FROM chat_sessions WHERE session_id=%s AND user_id=%s
                          AND tenant_id IS NOT DISTINCT FROM %s''', (session_id, user['user_id'], tenant_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, 'SESSION_NOT_FOUND')
        return dict(row)
