"""A conflicting device must abort the whole verified addition before commit."""
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from src.wecom_conversation import service
from .test_directory_api import locator


def test_late_device_conflict_rolls_back_earlier_scope_write():
    events = []
    class Cursor:
        def execute(self, sql, params):
            if 'bs_wecom_conversations' in sql:
                events.append(('check', params[-1]))
        def fetchone(self):
            return None if events[-1][1] == 'R:group-0' else {'device_id': 'other-device'}
    class Connection:
        def cursor(self):
            return Cursor()
        def commit(self):
            events.append(('commit',))
        def rollback(self):
            events.append(('rollback',))
    @contextmanager
    def connection():
        handle = Connection()
        try:
            yield handle
        except Exception:
            handle.rollback()
            raise
    def register(*args, **kwargs):
        events.append(('register',))
        return {'id': 1}, True
    targets = [{'session': locator(0)}, {'session': locator(1)}]
    with patch.object(service, 'get_db_connection', connection), \
         patch('src.wecom_conversation.archive.register_scope', register):
        with pytest.raises(service.ConversationServiceError, match='已绑定其他设备'):
            service.add_verified_conversations('tenant', 'user', targets,
                                               account='account-1', device_id='device-1')
    assert events.count(('register',)) == 1
    assert ('rollback',) in events and ('commit',) not in events
