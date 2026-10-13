"""Revoke only the authenticated browser binding in the original logout transaction."""
import re

from .contracts import NotificationError, Subject
from .repository import NotificationRepository


def revoke_browser_logout_in_tx(cursor, user_id, headers):
    if not user_id:
        return
    target_id = headers.get('X-Notification-Target-Id', '')
    version = headers.get('X-Notification-Binding-Version', '')
    if not target_id or not re.fullmatch(r'[1-9][0-9]{0,17}',version):
        return
    cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s AND recipient_user_id=%s',
                   (target_id, user_id))
    target = cursor.fetchone()
    if not target or target['channel'] != 'web_push':
        return
    try:
        NotificationRepository.revoke_in_tx(cursor, Subject(user_id,target['tenant_id']),target_id,int(version))
    except NotificationError:
        # A stale tab can log itself out, but cannot revoke a newer binding.
        pass
