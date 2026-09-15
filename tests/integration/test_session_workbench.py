"""Independent C4 acceptance against isolated PostgreSQL tenants, no live Provider."""
import uuid

import pytest

from tests.unit.session_tasks.conftest import (
    _init_db_pool, _gate_open, tenant_id, device_row, verified_binding,
    build_draft_payload, build_spec, publish_task_helper,
)
from src.db.database import get_db_connection
from src.session_tasks import service, workbench
from src.session_tasks.constants import SessionTaskError
from src.session_tasks.models import TaskDraftCreatePayload


def sql(statement, params=()):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(statement, params)
        result = [dict(r) for r in cursor.fetchall()] if cursor.description else []
        conn.commit()
        return result


@pytest.fixture(autouse=True)
def credit(tenant_id):
    sql("INSERT INTO tenants (tenant_id,company_name,credit_balance) VALUES (%s,'C4 isolated acceptance',100)", (tenant_id,))
    yield
    sql("DELETE FROM tenants WHERE tenant_id=%s", (tenant_id,))


@pytest.fixture
def paused(tenant_id, verified_binding):
    task = publish_task_helper(tenant_id, verified_binding)
    return service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), 'pause', task['version'])


def resume(tenant_id, task, *, user='user-1', version=None, water=0):
    return service.control_task(tenant_id, user, uuid.UUID(task['task_id']), 'resume',
                                task['version'] if version is None else version,
                                resume_from={'mode': 'fresh_baseline', 'expected_input_version': water})


def assert_error(code, call):
    with pytest.raises(SessionTaskError) as exc:
        call()
    assert exc.value.code == code


def test_resume_retires_history_and_rotates_assignment(tenant_id, paused, device_row):
    task_id = paused['task_id']
    sql("INSERT INTO session_task_batches (tenant_id,task_id,batch_id,input_version,status) VALUES (%s,%s,%s,7,'accepted')", (tenant_id, task_id, str(uuid.uuid4())))
    assert_error('CONFLICT', lambda: resume(tenant_id, paused, water=6))
    active = resume(tenant_id, paused, water=7)
    assert active['control_epoch'] == paused['control_epoch'] + 1
    assignment = service.claim_task(device_row, 'independent-c4-runtime')
    assert assignment['fresh_baseline'] is True
    assert assignment['input_version_base'] == 7
    assert sql("SELECT status FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE", (tenant_id, task_id)) == [{'status': 'history_only'}]
    assert_error('CONFLICT', lambda: resume(tenant_id, paused, water=7))


@pytest.mark.parametrize('selection', [None, {}, {'mode': 'all_history', 'expected_input_version': 0}, {'mode': 'fresh_baseline', 'expected_input_version': True}])
def test_resume_requires_explicit_valid_selection(tenant_id, paused, selection):
    assert_error('RESUME_BLOCKED_UNTIL_VERIFIED', lambda: service.control_task(tenant_id, 'user-1', uuid.UUID(paused['task_id']), 'resume', paused['version'], resume_from=selection))


def test_resume_acl_and_version(tenant_id, paused):
    assert_error('NOT_FOUND', lambda: resume(tenant_id, paused, user='other-user'))
    assert_error('NOT_FOUND', lambda: resume('other-tenant', paused))
    assert_error('CONFLICT', lambda: resume(tenant_id, paused, version=paused['version']-1))


def test_resume_invalidates_old_runtime_lease(tenant_id, verified_binding, device_row):
    task = publish_task_helper(tenant_id, verified_binding)
    old = service.claim_task(device_row, 'old-runtime')
    paused_task = service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), 'pause', task['version'])
    resume(tenant_id, paused_task)
    new = service.claim_task(device_row, 'replacement-runtime')
    assert new['assignment_id'] != old['assignment_id']
    assert new['fence'] > old['fence']
    assert new['fresh_baseline'] is True
    assert_error('STALE_ASSIGNMENT', lambda: service.renew_assignment(tenant_id, uuid.UUID(str(device_row['id'])), uuid.UUID(old['assignment_id']), old['fence'], old['control_epoch']))
    sql("UPDATE session_task_assignments SET lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '5 seconds' WHERE tenant_id=%s AND id=%s", (tenant_id, new['assignment_id']))
    replacement = service.claim_task(device_row, 'replacement-runtime-2')
    assert replacement['control_epoch'] == new['control_epoch']
    assert replacement['fresh_baseline'] is False


def test_late_unacked_batch_between_resume_and_claim_is_history(tenant_id, verified_binding, device_row):
    from src.session_tasks.decisions import _load_transcript

    task = publish_task_helper(tenant_id, verified_binding)
    old = service.claim_task(device_row, 'old-runtime')
    paused_task = service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), 'pause', task['version'])
    resume(tenant_id, paused_task)
    batch_id = str(uuid.uuid4())
    receipt = service.ingest_events(tenant_id, device_row['id'], uuid.UUID(old['assignment_id']), old['fence'], [{
        'event_id': str(uuid.uuid4()), 'local_seq': 1, 'type': 'batch', 'payload': {
            'batch_id': batch_id, 'input_version': 1,
            'conversation_binding_id': verified_binding['conversation_binding_id'],
            'messages': [{'local_message_id': 'late-m1', 'sender': 'peer', 'text': '旧代未同步消息', 'source_evidence_ref': 'fake-evidence'}],
        },
    }])
    assert receipt['historical'] is True
    assert sql('SELECT status FROM session_task_batches WHERE tenant_id=%s AND batch_id=%s', (tenant_id, batch_id)) == [{'status': 'historical'}]
    with get_db_connection() as conn:
        assert _load_transcript(conn, tenant_id, uuid.UUID(task['task_id']), 100) == []
    new = service.claim_task(device_row, 'new-runtime')
    assert new['fence'] > old['fence']
    assert new['fresh_baseline'] is True


def test_phase_projection_survives_observations_and_ignores_old_assignment(tenant_id, verified_binding, device_row):
    task = publish_task_helper(tenant_id, verified_binding)
    old = service.claim_task(device_row, 'old-runtime')
    paused_task = service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), 'pause', task['version'])
    resume(tenant_id, paused_task)
    current = service.claim_task(device_row, 'new-runtime')
    events = [{'event_id': str(uuid.uuid4()), 'local_seq': 1, 'type': 'decision_phase', 'payload': {'phase_to': 'waiting_peer'}}]
    events += [{'event_id': str(uuid.uuid4()), 'local_seq': seq, 'type': 'observation', 'payload': {}} for seq in range(2, 44)]
    service.ingest_events(tenant_id, device_row['id'], uuid.UUID(current['assignment_id']), current['fence'], events)
    service.ingest_events(tenant_id, device_row['id'], uuid.UUID(old['assignment_id']), old['fence'], [{'event_id': str(uuid.uuid4()), 'local_seq': 1, 'type': 'decision_phase', 'payload': {'phase_to': 'waiting_model'}}])
    assert service.get_task(tenant_id, 'user-1', uuid.UUID(task['task_id']))['phase'] == 'waiting_peer'


def test_changed_draft_requires_new_confirmation(tenant_id, paused):
    spec = build_spec()
    spec['goal'] = '新的目标，须再次由用户确认发布'
    updated = service.update_draft(tenant_id, 'user-1', uuid.UUID(paused['task_id']), paused['version'], spec)
    assert_error('CONFLICT', lambda: resume(tenant_id, updated))


@pytest.mark.parametrize('reason', ['coverage_gap', 'UNKNOWN_SEND', 'IDENTITY_DRIFT'])
def test_blocked_evidence_cannot_be_washed_by_resume(tenant_id, paused, reason):
    sql("UPDATE session_tasks SET status='blocked',blocked_reason=%s WHERE tenant_id=%s AND id=%s", (reason, tenant_id, paused['task_id']))
    assert_error('RESUME_BLOCKED_UNTIL_VERIFIED', lambda: resume(tenant_id, paused))


def test_credit_recovery_requires_balance_and_explicit_resume(tenant_id, paused):
    sql("UPDATE session_tasks SET status='blocked',blocked_reason='INSUFFICIENT_TENANT_CREDIT' WHERE tenant_id=%s AND id=%s", (tenant_id, paused['task_id']))
    sql("UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s", (tenant_id,))
    assert_error('INSUFFICIENT_TENANT_CREDIT', lambda: resume(tenant_id, paused))
    sql("UPDATE tenants SET credit_balance=100 WHERE tenant_id=%s", (tenant_id,))
    assert service.get_task(tenant_id, 'user-1', uuid.UUID(paused['task_id']))['status'] == 'blocked'
    assert resume(tenant_id, paused)['status'] == 'active'


@pytest.mark.parametrize('pending', ['send', 'model'])
def test_resume_rejects_unresolved_side_effects(tenant_id, paused, pending):
    if pending == 'send':
        sql("INSERT INTO session_task_execution_links (tenant_id,task_id,decision_id) VALUES (%s,%s,%s)", (tenant_id, paused['task_id'], str(uuid.uuid4())))
        code = 'UNRESOLVED_SEND'
    else:
        sql("INSERT INTO session_task_decisions (tenant_id,task_id,spec_revision,batch_id,decision_kind,model_call_pending,status) VALUES (%s,%s,1,%s,'reply',TRUE,'superseded')", (tenant_id, paused['task_id'], str(uuid.uuid4())))
        code = 'MODEL_CALL_PENDING'
    assert_error(code, lambda: resume(tenant_id, paused))


def test_drafts_available_with_closed_gate_but_publish_denied(tenant_id, verified_binding, monkeypatch):
    monkeypatch.setattr(service, 'tenant_allowed', lambda _: False)
    created = service.create_draft(tenant_id, 'user-1', TaskDraftCreatePayload.model_validate(build_draft_payload(verified_binding)))
    assert workbench.capabilities(tenant_id)['draft_enabled'] is True
    assert workbench.capabilities(tenant_id)['publish_enabled'] is False
    assert_error('FEATURE_DISABLED', lambda: service.publish_task(tenant_id, 'user-1', uuid.UUID(created['task_id']), created['version'], uuid.uuid4()))


def test_owner_only_projections_and_timeline(tenant_id, paused):
    task_id = uuid.UUID(paused['task_id'])
    detail = service.get_task(tenant_id, 'user-1', task_id)
    assert detail['input_version'] == 0
    assert workbench.timeline(tenant_id, 'user-1', task_id)['messages'] == []
    for tenant, user in [(tenant_id, 'other-user'), ('other-tenant', 'user-1')]:
        assert_error('NOT_FOUND', lambda: service.get_task(tenant, user, task_id))
        assert_error('NOT_FOUND', lambda: workbench.timeline(tenant, user, task_id))
        assert service.list_tasks(tenant, user)['items'] == []


def test_notices_only_actionable_changes_deduped_and_owner_scoped(tenant_id, paused):
    from src.session_tasks.notifications import list_notices, record_notice

    task_id = uuid.UUID(paused['task_id'])
    assert list_notices(tenant_id, 'user-1')['total'] == 0
    with get_db_connection() as conn:
        for status in ('active', 'paused', 'waiting_peer', 'waiting_model'):
            record_notice(conn, tenant_id, task_id, status, None, paused['control_epoch'])
        conn.commit()
    assert list_notices(tenant_id, 'user-1')['total'] == 0
    stopped = service.control_task(tenant_id, 'user-1', task_id, 'stop', paused['version'], reason_code='USER_STOP')
    notices = list_notices(tenant_id, 'user-1')
    assert notices['total'] == 1
    assert notices['items'][0]['status'] == 'stopped'
    assert notices['items'][0]['reason'] == 'USER_STOP'
    with get_db_connection() as conn:
        for _ in range(3):
            record_notice(conn, tenant_id, task_id, 'stopped', 'USER_STOP', stopped['control_epoch'])
        conn.commit()
    assert list_notices(tenant_id, 'user-1')['total'] == 1
    assert list_notices(tenant_id, 'other-user')['total'] == 0
    assert list_notices('other-tenant', 'user-1')['total'] == 0


def test_tool_publication_receipt_replays_without_reactivating(tenant_id, verified_binding):
    task = service.create_draft(tenant_id, 'user-1', TaskDraftCreatePayload.model_validate(build_draft_payload(verified_binding)))
    task_id = uuid.UUID(task['task_id'])
    confirmation = service.issue_publish_confirmation(tenant_id, 'user-1', task_id, task['version'])
    cid = uuid.UUID(confirmation['confirmation_id'])
    published = service.publish_task_once(tenant_id, 'user-1', task_id, task['version'], cid)
    assert published['status'] == 'active'
    assert service.publish_task_once(tenant_id, 'user-1', task_id, task['version'], cid) == published
    paused_task = service.control_task(tenant_id, 'user-1', task_id, 'pause', published['version'])
    assert service.publish_task_once(tenant_id, 'user-1', task_id, task['version'], cid) == published
    current = service.get_task(tenant_id, 'user-1', task_id)
    assert current['status'] == 'paused'
    assert current['version'] == paused_task['version']
    assert_error('IDEMPOTENCY_CONFLICT', lambda: service.publish_task_once(tenant_id, 'user-1', task_id, paused_task['version'], cid))
    assert_error('NOT_FOUND', lambda: service.publish_task_once(tenant_id, 'other-user', task_id, task['version'], cid))
