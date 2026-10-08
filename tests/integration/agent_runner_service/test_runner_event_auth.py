"""Prepared real Manager/PG auth contracts, not an SSE or channel transport.

The short-RR test delays only the original authorization return. It commits a
concurrent real mutation; no authorization result, token verifier or page is
replaced. Profiles are unused by the Manager's read-only entry point.
"""
from contextlib import contextmanager
from dataclasses import replace
import secrets
import uuid

import bcrypt
import pytest

from src.config.settings import AgentRunnerConfig, AgentRunnerPeerConfig
from src.core.cache_utils import CacheKeys, delete_cached
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import RunnerError, RunnerSubmit
from src.services.agent_runner.manager import RunnerManager
from src.services.agent_runner.repository import RunnerRepository

from .test_storage import storage

pytestmark = pytest.mark.integration


@pytest.fixture
def reader(service_database, actors):
    secret = secrets.token_urlsafe(32)
    config = AgentRunnerConfig(peers={'event-reader': AgentRunnerPeerConfig(
        token_hash=bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode(),
        sources=['chat', 'wecom_kf'])})
    authorizer = RunnerAuthorizer(config, service_database.connect)
    repository = RunnerRepository(service_database.connect)
    manager = RunnerManager(repository, authorizer, profiles=None)

    def credentials(actor=None, **extra):
        return {'service_id': 'event-reader', 'service_token': secret,
            **({'user_token': actor.token} if actor else {}), **extra}

    try:
        yield manager, authorizer, credentials
    finally:
        for actor in actors.values():
            delete_cached(CacheKeys.TOKEN, actor.token)


def rejected(manager, row, credentials, code, status):
    with pytest.raises(RunnerError) as caught:
        manager.read_events(row['runner_id'], credentials, 0)
    assert caught.value.code == code and caught.value.status == status


def test_real_manager_read_low_balance_and_warm_cache_cannot_override_token_revocation(
        storage, reader, actors, service_database):
    _, _, submit = storage
    manager, authorizer, credentials = reader
    actor = actors['a']
    row, _ = submit(actor)
    service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (actor.tenant_id,))
    assert manager.read_events(row['runner_id'], credentials(actor), 0)['events'][0]['kind'] == 'created'
    # The original opaque verifier really warmed its cache. A stale positive
    # alone must not authorize the authoritative short read transaction.
    assert authorizer.token_verifier(actor.token, auto_refresh=False) == actor.user_id
    service_database.rows('DELETE FROM tokens WHERE token=%s', (actor.token,))
    assert authorizer.token_verifier(actor.token, auto_refresh=False) == actor.user_id
    rejected(manager, row, credentials(actor), 'USER_UNAUTHORIZED', 401)
    assert service_database.rows('SELECT event_seq,attempt FROM agent_runners WHERE runner_id=%s',
        (row['runner_id'],)) == [{'event_seq': 1, 'attempt': 0}]


def test_real_manager_null_admin_selected_tenant_and_current_role_are_not_row_self_auth(
        storage, reader, actors, service_database):
    _, _, submit = storage
    manager, _, credentials = reader
    admin = actors['global']
    own, _ = submit(admin)
    session = 'event_admin_session_' + uuid.uuid4().hex
    service_database.rows('''INSERT INTO chat_sessions(session_id,user_id,tenant_id,title)
        VALUES (%s,%s,%s,'Event read fixture')''', (session, admin.user_id, actors['a'].tenant_id))
    scoped, _ = submit(replace(admin, tenant_id=actors['a'].tenant_id, session_id=session))
    assert manager.read_events(own['runner_id'], credentials(admin), 0)['head'] == 1
    assert manager.read_events(scoped['runner_id'], credentials(admin,
        target_tenant=actors['a'].tenant_id), 0)['head'] == 1
    rejected(manager, scoped, credentials(admin, target_tenant=actors['b'].tenant_id), 'SESSION_NOT_FOUND', 404)
    rejected(manager, own, credentials(actors['global_other']), 'SESSION_NOT_FOUND', 404)
    service_database.rows("UPDATE users SET role='user' WHERE user_id=%s", (admin.user_id,))
    rejected(manager, own, credentials(admin), 'TENANT_FORBIDDEN', 403)
    rejected(manager, scoped, credentials(admin, target_tenant=actors['a'].tenant_id), 'TENANT_FORBIDDEN', 403)


def test_real_manager_channel_actor_proof_and_current_internal_user_binding(
        reader, actors, service_database):
    manager, authorizer, credentials = reader
    owner = actors['a']
    session = 'event_channel_' + uuid.uuid4().hex
    service_database.rows('''INSERT INTO channel_sessions
        (session_id,tenant_id,channel_type,channel_user_id,channel_chat_id,user_id,subagent_id)
        VALUES (%s,%s,'wecom_kf','event-platform-user','event-platform-chat',%s,'main')''',
        (session, owner.tenant_id, owner.user_id))
    request = RunnerSubmit(client_request_id=uuid.uuid4().hex,
        session={'kind': 'channel', 'session_id': session}, source='wecom_kf',
        channel_user_id='event-platform-user', channel_chat_id='event-platform-chat', text='Event channel fixture')
    proof = credentials(actor_source='wecom_kf', actor_user='event-platform-user', actor_chat='event-platform-chat')
    principal = authorizer.authorize(request, execute=False, **proof)
    row, _ = manager.repository.submit(principal, request, 'event-channel-fixture')
    assert manager.read_events(row['runner_id'], proof, 0)['head'] == 1
    rejected(manager, row, credentials(actor_source='wecom_kf'), 'CHANNEL_ACTOR_REQUIRED', 403)
    rejected(manager, row, {**proof, 'actor_user': 'foreign-platform-user'}, 'CHANNEL_ACTOR_FORBIDDEN', 403)
    service_database.rows('UPDATE channel_sessions SET user_id=%s WHERE session_id=%s',
        (actors['a_other'].user_id, session))
    rejected(manager, row, proof, 'RUNNER_NOT_FOUND', 404)


def test_real_short_rr_auth_and_page_share_snapshot_without_holding_writer_lock(
        storage, reader, actors, service_database, monkeypatch):
    repository, _, submit = storage
    manager, authorizer, credentials = reader
    actor = actors['a']
    row, principal = submit(actor)
    original_factory = manager.repository.connection_factory
    active = 0
    observed = []

    @contextmanager
    def tracked_connection():
        nonlocal active
        with original_factory() as connection:
            active += 1
            try:
                yield connection
            finally:
                active -= 1

    original_verifier = authorizer.token_verifier
    original_service = authorizer.verify_service

    def service(*args, **kwargs):
        assert active == 0
        return original_service(*args, **kwargs)

    def verifier(*args, **kwargs):
        assert active == 0
        return original_verifier(*args, **kwargs)

    original_auth = authorizer.authorize_read_in_tx

    def observe_authorization(cursor, *args, **kwargs):
        assert active == 1
        cursor.execute('SHOW transaction_isolation')
        assert cursor.fetchone()['transaction_isolation'] == 'repeatable read'
        result = original_auth(cursor, *args, **kwargs)
        # Both original writes commit while this RR read is open, proving no
        # Runner row lock is retained. Bound SQL timeout fails deterministically
        # if an accidental FOR UPDATE read is introduced.
        with service_database.connect() as connection, connection.cursor() as other:
            other.execute("SET LOCAL lock_timeout='2s'")
            other.execute("UPDATE users SET status='suspended' WHERE user_id=%s", (actor.user_id,))
        repository.cancel(principal, row['runner_id'])
        observed.append(True)
        return result

    monkeypatch.setattr(manager.repository, 'connection_factory', tracked_connection)
    monkeypatch.setattr(authorizer, 'verify_service', service)
    monkeypatch.setattr(authorizer, 'token_verifier', verifier)
    monkeypatch.setattr(authorizer, 'authorize_read_in_tx', observe_authorization)
    consistent = manager.read_events(row['runner_id'], credentials(actor), 0)
    assert observed == [True] and active == 0
    assert consistent['head'] == 1 and len(consistent['events']) == 1
    assert consistent['events'][0]['status'] == 'queued'
    current = repository.get(row['runner_id'])
    assert current['status'] == 'cancelled' and current['event_seq'] == 2
    monkeypatch.setattr(authorizer, 'authorize_read_in_tx', original_auth)
    rejected(manager, row, credentials(actor), 'USER_UNAUTHORIZED', 401)
