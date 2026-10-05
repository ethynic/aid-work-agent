"""Prepared real-PG ingress transactions; localhost replaces provider IO only."""
import asyncio
import json
import secrets
import threading

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage

pytestmark = pytest.mark.integration


def test_callback_during_original_http_pull_retains_new_generation_and_reuses_original_cursor(kf_scope):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    release = threading.Event()
    first = PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'first_committed',
        'msg_list': [text_message(scope, 'msg_one_' + scope.marker)]}, release=release)
    scope.peer.script(first, PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'second_committed',
        'msg_list': [text_message(scope, 'msg_two_' + scope.marker, send_time=101)]}))
    config = type(settings.agent_runner.wecom_kf)(enabled=True)

    async def original_pull():
        worker = KfIngressWorker(config, repository, scope.original_client)
        operation = asyncio.create_task(worker.run_once())
        try:
            assert await asyncio.to_thread(first.arrived.wait, 4)
            # Actual callback same repository/crypto/config, while HTTP is in flight.
            accepted = await asyncio.to_thread(seed_intent, scope, repository)
            assert accepted['requested_generation'] == 2
            pending = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
            assert pending['completed_generation'] == 0 and pending['claim_epoch'] == 1
            release.set()
            assert await asyncio.wait_for(operation, 6) == {'received': 1, 'has_more': False}
            first_done = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
            assert first_done['requested_generation'] == 2 and first_done['completed_generation'] == 1
            assert first_done['cursor'] == 'first_committed'
            # Bounded keyset reaches the end, then the next poll wraps naturally.
            assert await worker.run_once() is None
            assert await worker.run_once() == {'received': 1, 'has_more': False}
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
            await worker.close()

    asyncio.run(original_pull())
    done = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert done['completed_generation'] == done['requested_generation'] == 2
    assert done['cursor'] == 'second_committed' and done['lease_until'] is None and done['worker_id'] is None
    assert len(scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))) == 2
    assert len(scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,))) == 1
    sync = [call for call in scope.peer.calls if call['path'] == '/cgi-bin/kf/sync_msg']
    assert [call['cursor'] for call in sync] == ['', 'first_committed']
    assert all(call['account_matches'] and call['credential_valid'] for call in sync)
    assert not scope.peer.errors


def test_late_original_account_update_sql_failure_rolls_back_inbox_route_and_cursor(kf_scope):
    """Real SQL fault DI at page commit, not a substituted repository result."""
    from psycopg2.errors import RaiseException
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.config.settings import settings
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    function = 'kf_late_fault_' + scope.marker
    trigger = 'kf_late_trigger_' + scope.marker
    # Identifiers are solely uuid.hex. No secret is interpolated into SQL.
    scope.rows(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'KF_TEST_LATE_PAGE_FAILURE'; END $$")
    scope.rows(f"CREATE TRIGGER {trigger} BEFORE UPDATE ON wecom_kf_account_sync FOR EACH ROW WHEN (NEW.cursor IS DISTINCT FROM OLD.cursor) EXECUTE FUNCTION {function}()")
    scope.peer.script(PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'must_not_commit',
        'msg_list': [text_message(scope, 'msg_one_' + scope.marker)]}))

    async def original_pull():
        worker = KfIngressWorker(type(settings.agent_runner.wecom_kf)(enabled=True), repository, scope.original_client)
        try:
            with pytest.raises(RaiseException):
                await worker.run_once()
        finally:
            await worker.close()

    try:
        asyncio.run(original_pull())
        account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
        assert account['cursor'] == '' and account['completed_generation'] == 0 and account['requested_generation'] == 1
        assert account['worker_id'] is None and account['lease_until'] is None
        assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
        assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []
        assert scope.rows('SELECT content FROM channel_messages WHERE session_id=%s', (scope.legacy_sid,)) == [
            {'content': 'Original fictional history'}]
        assert not scope.peer.errors
    finally:
        scope.rows(f'DROP TRIGGER IF EXISTS {trigger} ON wecom_kf_account_sync')
        scope.rows(f'DROP FUNCTION IF EXISTS {function}()')


def test_original_private_welcome_lifecycle_and_unsupported_facts_do_not_stall_following_text(kf_scope, monkeypatch):
    from src.channels.wecom_kf.ingress_auth import bounded_page
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.config.settings import settings
    from src.core import secret_crypto
    scope = kf_scope
    monkeypatch.setenv('APP_SECRET_KEY', secrets.token_urlsafe(40))
    monkeypatch.setattr(settings, 'app', settings.app.copy(update={'secret_key': ''}))
    monkeypatch.setattr(secret_crypto, '_fernet', None)  # Fresh original primitive, no crypto replacement.
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    lease, _ = repository.claim('kf_contract_' + scope.marker)
    assert lease is not None
    grant = secrets.token_urlsafe(24)
    unsupported = dict(text_message(scope, 'emoji_' + scope.marker), msgtype='emoji', emoji={'type': 1})
    unsupported.pop('text')
    event = dict(text_message(scope, 'enter_' + scope.marker, send_time=101), msgtype='event',
        event={'event_type': 'enter_session', 'scene': 'fictional_scene', 'welcome_code': grant,
            'external_userid': scope.actor_id, 'open_kfid': scope.open_kfid})
    event.pop('text')
    service = dict(text_message(scope, 'servicer_' + scope.marker, send_time=103), origin=4,
        servicer_userid='fictional_servicer')
    recall = dict(text_message(scope, 'recall_' + scope.marker, send_time=104), msgtype='event',
        event={'event_type': 'user_recall_msg', 'recall_msgid': 'text_' + scope.marker,
            'external_userid': scope.actor_id, 'open_kfid': scope.open_kfid})
    recall.pop('text')
    messages = [unsupported, event, text_message(scope, 'text_' + scope.marker, send_time=102), service, recall]
    original = bounded_page({'errcode': 0, 'has_more': 0, 'next_cursor': 'all_received', 'msg_list': messages}, lease.proof)
    assert repository.commit_page(lease, original) == {'received': 5, 'has_more': False}
    query, body = scope.material.encrypted_callback(scope.open_kfid, event='enter_session',
        fields={'ExternalUserID': scope.actor_id, 'Scene': 'callback_scene', 'Code': grant})
    repository.accept_callback(scope.tenant_id, scope.config_id, query, body)
    repository.accept_callback(scope.tenant_id, scope.config_id, query, body)
    status_query, status_body = scope.material.encrypted_callback(scope.open_kfid, event='change_type',
        fields={'ExternalUserID': scope.actor_id, 'ChangeType': 'session_status_change', 'ServiceState': '3'})
    repository.accept_callback(scope.tenant_id, scope.config_id, status_query, status_body)
    facts = scope.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s ORDER BY send_time,message_id', (scope.config_id,))
    assert len(facts) == 7 and sum(fact['namespace'] == 'callback' for fact in facts) == 2
    assert sum(fact['namespace'] == 'sync' for fact in facts) == 5
    recalls = [fact for fact in facts if fact['payload'].get('event', {}).get('event_type') == 'user_recall_msg']
    assert len(recalls) == 1 and recalls[0]['payload']['event']['recall_msgid'] == 'text_' + scope.marker
    services = [fact for fact in facts if fact['origin'] == 4]
    assert len(services) == 1 and services[0]['payload']['servicer_userid'] == 'fictional_servicer'
    statuses = [fact for fact in facts if fact['payload'].get('event', {}).get('event_type') == 'change_type']
    assert len(statuses) == 1 and statuses[0]['payload']['event']['change_type'] == 'session_status_change'
    unsupported_fact = next(fact for fact in facts if fact['message_type'] == 'emoji')
    assert unsupported_fact['payload']['unsupported'] is True
    entered = [fact for fact in facts if fact['capability_ciphertext']]
    assert len(entered) == 2
    scenes = {fact['payload']['event']['scene'] for fact in entered}
    assert scenes == {'fictional_scene', 'callback_scene'}
    for fact in facts:
        exposed = grant in json.dumps(fact['payload'])
        assert exposed is False
    for fact in entered:
        private_matches = json.loads(secret_crypto.decrypt_secret(fact['capability_ciphertext'])) == {'welcome_code': grant}
        is_ciphertext = secret_crypto.looks_like_ciphertext(fact['capability_ciphertext'])
        assert private_matches is True and is_ciphertext is True
    last = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert last['cursor'] == 'all_received' and last['completed_generation'] == 1 and last['requested_generation'] == 4
    assert scope.rows('SELECT count(*) AS n FROM channel_messages WHERE session_id=%s', (scope.legacy_sid,))[0]['n'] == 1
    monkeypatch.delenv('APP_SECRET_KEY', raising=False)
    monkeypatch.delenv('RPA_SECRET_KEY', raising=False)
    monkeypatch.setattr(secret_crypto, '_fernet', None)
    # Missing deployment master key is an actual primitive failure. Failed
    # lifecycle authorization must not publish a new account generation/fact.
    with pytest.raises(RuntimeError):
        repository.accept_callback(scope.tenant_id, scope.config_id, query, body)
    after_missing_key = scope.rows('SELECT requested_generation FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert after_missing_key['requested_generation'] == 4
    assert len(scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))) == 7
