"""D2 plan ownership and selected membership in the isolated PostgreSQL fixture."""

import base64
import json
import uuid

import pytest

from src.db.database import get_db_connection
from src.scheduler.db import ScheduledTaskDB
from src.wecom_conversation import archive, plans
from src.wecom_conversation.service import ConversationServiceError


def _locator(account, conversation):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()
    ).decode().rstrip('=')


def _register(tenant_id, user_id, account, conversation, device):
    with get_db_connection() as conn:
        row, _ = archive.register_scope(
            conn, tenant_id, user_id, _locator(account, conversation),
            device_id=device, reason='manual_add')
        conn.commit()
    return row


@pytest.mark.usefixtures('db_ready')
def test_one_plan_includes_paused_and_cannot_move_source(tenant_id):
    user_id, account, device = 'user-1', 'acc1', str(uuid.uuid4())
    first = _register(tenant_id, user_id, account, 'R:a', device)
    plan = plans.save_daily_plan(
        tenant_id, user_id, create=True, account=account, device=device,
        target_day='yesterday', scope_mode='selected',
        conversation_ids=[first['id']])
    assert plan['profile_id'] == 'main'
    assert plan['next_due_at'] is not None
    assert plans.get_daily_plan(tenant_id, user_id)['task_id'] == plan['task_id']
    assert ScheduledTaskDB.pause(plan['task_id'], tenant_id=tenant_id,
                                 user_id=user_id, allow_special=True)
    with pytest.raises(ConversationServiceError) as duplicate:
        plans.save_daily_plan(
            tenant_id, user_id, create=True, account=account, device=device,
            target_day='yesterday', scope_mode='all', conversation_ids=[])
    assert duplicate.value.code == 'PLAN_EXISTS'
    with pytest.raises(ConversationServiceError) as changed_source:
        plans.save_daily_plan(
            tenant_id, user_id, create=False, account=account,
            device=str(uuid.uuid4()), target_day='today', scope_mode='all',
            conversation_ids=[], interval_hours=2)
    assert changed_source.value.code == 'PLAN_SOURCE_MISMATCH'


@pytest.mark.usefixtures('db_ready')
def test_selected_membership_updates_atomically_and_all_remains_dynamic(tenant_id):
    user_id, account, device = 'user-2', 'acc1', str(uuid.uuid4())
    first = _register(tenant_id, user_id, account, 'R:a', device)
    second = _register(tenant_id, user_id, account, 'R:b', device)
    foreign = _register(tenant_id, user_id, account, 'R:foreign', str(uuid.uuid4()))
    plan = plans.save_daily_plan(
        tenant_id, user_id, create=True, account=account, device=device,
        target_day='today', scope_mode='selected',
        conversation_ids=[first['id']], interval_hours=2)
    with pytest.raises(ConversationServiceError):
        plans.save_daily_plan(
            tenant_id, user_id, create=False, account=account, device=device,
            target_day='today', scope_mode='selected',
            conversation_ids=[second['id'], foreign['id']], interval_hours=2)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id,daily_plan_task_id FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s',
                       (tenant_id, user_id))
        memberships = {row['id']: row['daily_plan_task_id'] for row in cursor.fetchall()}
    assert memberships[first['id']] == plan['task_id']
    assert memberships[second['id']] is None
    plans.save_daily_plan(
        tenant_id, user_id, create=False, account=account, device=device,
        target_day='today', scope_mode='all', conversation_ids=[], interval_hours=2)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT daily_plan_task_id FROM bs_wecom_conversations WHERE id=%s',
                       (first['id'],))
        assert cursor.fetchone()['daily_plan_task_id'] is None
