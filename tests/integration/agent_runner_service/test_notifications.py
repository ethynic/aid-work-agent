"""Real PostgreSQL notification transactions; transport is a non-Web fake."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import threading
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml
from psycopg2 import sql

from src.config.settings import NotificationsConfig, settings
from src.core.agent_engine.contracts import Identity, CheckpointFailure
from src.notifications.contracts import AdapterCapabilities, AdapterRegistry, NotificationError, SendResult, Subject
from src.notifications.repository import NotificationRepository, protected, unprotect
from src.notifications.producer import publish_runner_terminal_in_tx
from src.services.agent_runner.contracts import Principal, RunnerSubmit
from src.services.agent_runner.event_repository import EventRepository
from src.services.agent_runner.repository import RunnerRepository
from src.services.agent_runner.public_view import decoded

pytestmark=pytest.mark.integration


class NonWebAdapter:
    channel='fixture_transport'
    provider='fixture_provider'
    capabilities=AdapterCapabilities()
    def available(self): return True
    def render(self,message,target):
        # Deliberately no browser endpoint/key/permission/VAPID fields.
        return {'ref':message['notification_ref'],'event':message['event_type']}
    def send(self,configuration,payload,ttl):
        assert set(configuration)=={'account'}
        return SendResult('accepted')


@pytest.fixture
def notifications(service_database,actors,monkeypatch):
    config=NotificationsConfig(enabled=True)
    monkeypatch.setattr(settings,'notifications',config)
    repo=NotificationRepository(config,AdapterRegistry([NonWebAdapter()]),service_database.connect)
    repo.assert_schema()
    yield repo
    ids=[actor.user_id for actor in actors.values()]
    service_database.rows('DELETE FROM notification_deliveries WHERE notification_id IN (SELECT notification_id FROM notification_messages WHERE recipient_user_id=ANY(%s))',(ids,))
    for table in ('notification_messages','notification_preferences','notification_targets'):
        service_database.rows('DELETE FROM '+table+' WHERE recipient_user_id=ANY(%s)',(ids,))


def enroll(database,actor,count=1,enabled=True):
    subject=Subject(actor.user_id,actor.tenant_id)
    identifiers=[]
    with database.connect() as conn,conn.cursor() as cursor:
        for _ in range(count):
            identifier='target_'+uuid.uuid4().hex
            cursor.execute('''INSERT INTO notification_targets
                (target_id,scope_key,tenant_id,recipient_user_id,channel,provider,installation_ref,
                identity_digest,configuration_encrypted,enabled_since) VALUES
                (%s,%s,%s,%s,'fixture_transport','fixture_provider',%s,%s,%s,clock_timestamp()-interval '1 minute')''',
                (identifier,subject.scope_key,actor.tenant_id,actor.user_id,uuid.uuid4().hex,uuid.uuid4().hex,protected({'account':identifier})))
            identifiers.append(identifier)
        cursor.execute('''INSERT INTO notification_preferences
            (scope_key,tenant_id,recipient_user_id,category,channel,provider,enabled,enabled_since)
            VALUES(%s,%s,%s,'task','fixture_transport','fixture_provider',%s,clock_timestamp()-interval '1 minute')
            ON CONFLICT(scope_key,recipient_user_id,category,channel,provider)
            DO UPDATE SET enabled=EXCLUDED.enabled,enabled_since=EXCLUDED.enabled_since''',
            (subject.scope_key,actor.tenant_id,actor.user_id,enabled))
    return identifiers


def terminal(database,actor,status='completed'):
    principal=Principal(Identity(actor.tenant_id,actor.user_id,actor.session_id),'user',actor.user_id,'notification_fixture')
    repo=RunnerRepository(database.connect)
    row,_=repo.submit(principal,RunnerSubmit(client_request_id=uuid.uuid4().hex,text='fixture',
        session={'kind':'web','session_id':actor.session_id}),'fixture')
    with database.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE',(row['runner_id'],))
        before=decoded(cursor.fetchone())
        cursor.execute('''UPDATE agent_runners SET status=%s,finished_at=clock_timestamp(),
            view_revision=view_revision+1 WHERE runner_id=%s RETURNING *''',(status,row['runner_id']))
        return EventRepository.notify_in_tx(cursor,before,after=decoded(cursor.fetchone()),kind='terminal')


def test_incremental_schema_is_fresh_installable_and_idempotent(service_database):
    migration=next(item['statements'] for item in yaml.safe_load(Path('deploy/db_update.yaml').read_text())
                   if item['datetime']=='2026-10-10 17:10:00')
    namespace='notification_schema_'+uuid.uuid4().hex
    with service_database.connect() as conn,conn.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(namespace)))
        cursor.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(namespace)))
        cursor.execute(migration)
        NotificationRepository.assert_schema_in_tx(cursor)
        cursor.execute(migration)
        NotificationRepository.assert_schema_in_tx(cursor)
        cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(namespace)))


@pytest.mark.asyncio
async def test_dispatcher_real_schema_start_send_drain(notifications,actors,service_database):
    from src.notifications.dispatcher import NotificationDispatcher
    actor=actors['a'];enroll(service_database,actor);terminal(service_database,actor)
    dispatcher=NotificationDispatcher(notifications)
    await dispatcher.start()
    try:
        async def accepted():
            while True:
                rows=await asyncio.to_thread(service_database.rows,
                    '''SELECT d.state FROM notification_deliveries d JOIN notification_messages m USING(notification_id)
                       WHERE m.recipient_user_id=%s''',(actor.user_id,))
                if rows and all(row['state']=='accepted' for row in rows):
                    return
                await asyncio.sleep(0.01)
        await asyncio.wait_for(accepted(),timeout=5)
    finally:
        await dispatcher.stop()
    assert dispatcher._task is None


def test_one_message_multiple_nonweb_targets_and_stable_title_snapshot(notifications,actors,service_database):
    actor=actors['a'];targets=enroll(service_database,actor,2)
    row=terminal(service_database,actor)
    with service_database.connect() as conn,conn.cursor() as cursor:
        publish_runner_terminal_in_tx(cursor,row,notifications.config)
        cursor.execute('UPDATE chat_sessions SET title=%s WHERE session_id=%s',('Renamed afterwards',actor.session_id))
    messages=service_database.rows('SELECT * FROM notification_messages WHERE recipient_user_id=%s',(actor.user_id,))
    assert len(messages)==1
    assert unprotect(messages[0]['parameters_encrypted'])['session_title']=='Runner acceptance'
    assert notifications.expand()==1 and notifications.expand()==0
    claims=notifications.claim('fixture_owner',10)
    assert {item['target_id'] for item in claims}==set(targets)
    for claim in claims:
        adapter,config,payload,ttl=notifications.prepare_send(claim)
        assert set(config)=={'account'} and set(payload)=={'ref','event'} and ttl>0
        assert notifications.finish(claim,adapter.send(config,payload,ttl))
    deliveries=service_database.rows('SELECT * FROM notification_deliveries WHERE notification_id=%s',(messages[0]['notification_id'],))
    assert all(d['state']=='accepted' and d['accepted_at'] and d['displayed_at'] is None for d in deliveries)


@pytest.mark.parametrize('boundary',['target','preference'])
def test_no_historical_backfill_when_enabled_after_acceptance(notifications,actors,service_database,boundary):
    actor=actors['a'];targets=enroll(service_database,actor)
    row=terminal(service_database,actor)
    table='notification_targets' if boundary=='target' else 'notification_preferences'
    service_database.rows('UPDATE '+table+' SET enabled_since=clock_timestamp() WHERE recipient_user_id=%s',(actor.user_id,))
    notifications.expand()
    assert notifications.claim('owner',10)==[]
    enroll(service_database,actor)
    notifications.expand()
    assert notifications.claim('owner',10)==[]


def test_concurrent_claims_skip_locked_and_expired_lease_is_fenced(notifications,actors,service_database):
    actor=actors['a'];enroll(service_database,actor,4);terminal(service_database,actor);notifications.expand()
    barrier=threading.Barrier(2)
    def claim(owner):
        barrier.wait(timeout=5)
        return notifications.claim(owner,2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=list(pool.map(claim,['one','two']))
    assert len(a)==len(b)==2 and {d['delivery_id'] for d in a}.isdisjoint({d['delivery_id'] for d in b})
    old=a[0]
    service_database.rows("UPDATE notification_deliveries SET lease_until=clock_timestamp()-interval '1 second' WHERE delivery_id=%s",(old['delivery_id'],))
    new=notifications.claim('recovered',1)[0]
    assert new['delivery_id']==old['delivery_id'] and new['claim_version']==old['claim_version']+1
    assert notifications.prepare_send(old) is None
    assert not notifications.finish(old,SendResult('accepted'))
    assert notifications.finish(new,SendResult('accepted'))


@pytest.mark.parametrize('revocation',['target','version','preference','user','tenant','session','tenant_expiry'])
def test_revocation_blocks_send_with_fresh_resource_permission(notifications,actors,service_database,revocation):
    actor=actors['a'];target=enroll(service_database,actor)[0];terminal(service_database,actor);notifications.expand()
    claim=notifications.claim('owner',1)[0]
    if revocation=='target': notifications.revoke_target(Subject(actor.user_id,actor.tenant_id),target,1)
    elif revocation=='version': service_database.rows('UPDATE notification_targets SET binding_version=2 WHERE target_id=%s',(target,))
    elif revocation=='preference': service_database.rows('UPDATE notification_preferences SET enabled=false WHERE recipient_user_id=%s',(actor.user_id,))
    elif revocation=='user': service_database.rows("UPDATE users SET status='disabled' WHERE user_id=%s",(actor.user_id,))
    elif revocation=='tenant': service_database.rows("UPDATE tenants SET status='suspended' WHERE tenant_id=%s",(actor.tenant_id,))
    elif revocation=='tenant_expiry': service_database.rows("UPDATE tenants SET expire_at=clock_timestamp()-interval '1 day' WHERE tenant_id=%s",(actor.tenant_id,))
    else: service_database.rows('DELETE FROM chat_sessions WHERE session_id=%s',(actor.session_id,))
    assert notifications.prepare_send(claim) is None
    assert not notifications.finish(claim,SendResult('accepted'))
    assert service_database.rows('SELECT state FROM notification_deliveries WHERE delivery_id=%s',(claim['delivery_id'],))[0]['state']=='skipped'


def test_retry_after_and_ttl_terminate_without_changing_runner_result(notifications,actors,service_database):
    actor=actors['a'];enroll(service_database,actor);row=terminal(service_database,actor);notifications.expand()
    claim=notifications.claim('owner',1)[0]
    assert notifications.finish(claim,SendResult('retryable','rate_limited',120))
    retry=service_database.rows('SELECT *,clock_timestamp() AS database_now FROM notification_deliveries WHERE delivery_id=%s',(claim['delivery_id'],))[0]
    assert retry['state']=='pending' and retry['next_attempt_at']>retry['database_now']+timedelta(seconds=110)
    service_database.rows("UPDATE notification_messages SET expires_at=clock_timestamp()-interval '1 second' WHERE recipient_user_id=%s",(actor.user_id,))
    service_database.rows('UPDATE notification_deliveries SET next_attempt_at=clock_timestamp() WHERE delivery_id=%s',(claim['delivery_id'],))
    assert notifications.claim('owner',1)==[]
    assert service_database.rows('SELECT state FROM notification_deliveries WHERE delivery_id=%s',(claim['delivery_id'],))[0]['state']=='expired'
    assert service_database.rows('SELECT status FROM agent_runners WHERE runner_id=%s',(row['runner_id'],))[0]['status']=='completed'


def test_outbox_failure_rolls_back_original_terminal_event(notifications,actors,service_database):
    with service_database.connect() as conn,conn.cursor() as cursor:
        cursor.execute("ALTER TABLE notification_messages ADD CONSTRAINT fixture_reject_runner CHECK(producer<>'runner') NOT VALID")
    try:
        with pytest.raises(CheckpointFailure,match='RUNNER_PUBLIC_EVENT_COMMIT_FAILED'):
            terminal(service_database,actors['a'])
        assert service_database.rows('SELECT status FROM agent_runners WHERE user_id=%s',(actors['a'].user_id,))[0]['status']=='queued'
        assert service_database.rows("SELECT 1 FROM agent_runner_events WHERE tenant_id=%s AND kind='terminal'",(actors['a'].tenant_id,))==[]
    finally:
        service_database.rows('ALTER TABLE notification_messages DROP CONSTRAINT fixture_reject_runner')


def test_cancelled_and_settlement_changes_do_not_notify(notifications,actors,service_database):
    terminal(service_database,actors['a'],'cancelled')
    assert service_database.rows('SELECT 1 FROM notification_messages WHERE recipient_user_id=%s',(actors['a'].user_id,))==[]


def test_message_ref_does_not_grant_other_subject_access(notifications,actors,service_database):
    actor=actors['global'];terminal(service_database,actor)
    ref=service_database.rows('SELECT notification_ref FROM notification_messages WHERE recipient_user_id=%s',(actor.user_id,))[0]['notification_ref']
    assert notifications.resolve(Subject(actor.user_id,None),ref)['resource']['session_id']==actor.session_id
    for other in ('global_other','a','a_other','null_user'):
        user=actors[other]
        with pytest.raises(NotificationError): notifications.resolve(Subject(user.user_id,user.tenant_id),ref)


def test_api_rejects_invalid_subscription_without_echoing_credentials(notifications,actors,service_database):
    from src.notifications.api import create_router
    app=FastAPI();actor=actors['a']
    app.include_router(create_router(notifications.config,notifications,lambda _:dict(user_id=actor.user_id,tenant_id=actor.tenant_id,role='user')))
    client=TestClient(app)
    response=client.post('/api/notifications/targets',json={'channel':'web_push','provider':'web_push','installation_ref':'x'*32,
        'configuration':{'endpoint':'https://example.invalid','keys':{'p256dh':'secret-key','auth':'secret-auth'},'key_id':'v1','unknown':'never echo'}})
    assert response.status_code==422 and response.json()=={'error':'NOTIFICATION_INVALID_REQUEST'}
    assert 'secret' not in response.text
    assert client.get('/api/notifications/capabilities',headers={'X-Tenant-Id':actors['b'].tenant_id}).status_code==403
