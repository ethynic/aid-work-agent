"""Prepared native ingress authority using actual current config and crypto."""
import asyncio
import copy
import json
import time

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message

pytestmark = pytest.mark.integration


def test_original_native_callback_http_requires_current_encrypted_account_and_committed_intent(kf_scope, actors, monkeypatch):
    """Actual FastAPI route/ASGI HTTP and real PG, not an HTTP socket timing test."""
    from fastapi import FastAPI
    import httpx
    from loguru import logger
    from src.config.settings import settings
    from src.saas.api.channel_routes import router
    scope = kf_scope
    monkeypatch.setattr(settings.agent_runner.wecom_kf, 'enabled', True)
    app = FastAPI()
    app.include_router(router, prefix='/api/channels')
    path = '/api/channels/t/' + scope.tenant_id + '/wecom_kf/callback/' + scope.config_id
    logs = []
    sink = logger.add(lambda record: logs.append(str(record)), level='DEBUG')

    async def actual_requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local-fixture') as client:
            query, body = scope.material.encrypted_callback(scope.open_kfid)
            invalid = [(dict(query, msg_signature='0' * 40), body),
                ({}, b'<xml><Event>kf_msg_or_event</Event></xml>'),
                scope.material.encrypted_callback(scope.open_kfid, receiver='foreign_fictional_corp')]
            for parameters, data in invalid:
                response = await client.post(path, params=parameters, content=data)
                assert response.status_code in (400, 403)
                assert response.json()['success'] is False
                assert scope.rows('SELECT 1 FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,)) == []
            oversized = await client.post(path, params=query, content=b'X' * 65537)
            assert oversized.status_code == 413
            other_path = '/api/channels/t/' + actors['b'].tenant_id + '/wecom_kf/callback/' + scope.config_id
            crossed = await client.post(other_path, params=query, content=body)
            assert crossed.status_code == 403 and crossed.json()['debug'] == 'KF_INGRESS_CONFIG_UNAVAILABLE'
            # A legitimate first notification may be delayed; original four-part
            # signature uses an actual old timestamp, not a patched production clock.
            delayed_query, delayed_body = scope.material.encrypted_callback(
                scope.open_kfid, timestamp=int(time.time()) - 900)
            accepted = await client.post(path, params=delayed_query, content=delayed_body)
            assert accepted.status_code == 200 and accepted.text == 'success'
            # HTTP success is only read after original transaction has committed.
            stored = scope.rows('SELECT requested_generation FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))
            assert stored == [{'requested_generation': 1}]
            duplicate = await client.post(path, params=query, content=body)
            assert duplicate.status_code == 200 and duplicate.text == 'success'
            stored = scope.rows('SELECT requested_generation FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))
            assert stored == [{'requested_generation': 2}]
            scope.rows('UPDATE tenant_channel_configs SET verified=0,updated_at=clock_timestamp() WHERE config_id=%s', (scope.config_id,))
            denied = await client.post(path, params=query, content=body)
            assert denied.status_code == 403 and denied.json()['debug'] == 'KF_INGRESS_CONFIG_UNAVAILABLE'
            assert scope.rows('SELECT requested_generation FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,)) == stored
            scope.rows("""UPDATE tenant_channel_configs SET verified=1,
                config=jsonb_set(config::jsonb,'{encoding_aes_key}','null')::text,
                updated_at=clock_timestamp() WHERE config_id=%s""", (scope.config_id,))
            missing_key = await client.post(path, params=query, content=body)
            assert missing_key.status_code == 400 and missing_key.json()['debug'] == 'KF_INGRESS_INVALID_FIELD'
            assert scope.rows('SELECT requested_generation FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,)) == stored

    try:
        asyncio.run(actual_requests())
        leaked = any(value in '\n'.join(logs) for value in (
            scope.material.token, scope.material.encoding_aes_key, scope.material.pull_token, scope.peer.secret))
        assert leaked is False
        assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
        assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []
        assert not scope.peer.calls  # Callback never needs external network for first ACK.
    finally:
        logger.remove(sink)


def test_full_account_actor_strict_page_failures_and_stalled_cursor_cannot_overwrite_original_facts(kf_scope):
    from src.channels.wecom_kf.ingress_auth import KfIngressError, bounded_page
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    lease, _ = repository.claim('kf_page_' + scope.marker)
    assert lease is not None
    original_message = text_message(scope, 'stable_provider_' + scope.marker)
    normal = {'errcode': 0, 'has_more': 0, 'next_cursor': 'saved_first', 'msg_list': [original_message]}
    malformed = []
    missing = copy.deepcopy(normal); missing.pop('errcode'); malformed.append(missing)
    malformed.append(dict(normal, errcode=True))
    malformed.append(dict(normal, has_more=True))
    malformed.append(dict(normal, msg_list=[dict(original_message, send_time=2**63)]))
    malformed.append(dict(normal, msg_list=[dict(original_message, send_time=True)]))
    event = dict(original_message, msgtype='event', event={'event_type': 'enter_session',
        'external_userid': scope.actor_id, 'open_kfid': 'foreign_open'})
    event.pop('text')
    malformed.append(dict(normal, msg_list=[event]))
    event = dict(event, event=dict(event['event'], open_kfid=scope.open_kfid, external_userid='different_actor'))
    malformed.append(dict(normal, msg_list=[event]))
    malformed.append(dict(normal, msg_list=[dict(original_message, text={'content': 'X' * 65537})]))
    try:
        for value in malformed:
            with pytest.raises(KfIngressError):
                bounded_page(value, lease.proof)
            assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
        with pytest.raises(KfIngressError) as stopped:
            repository.commit_page(lease, bounded_page(dict(normal, has_more=1, next_cursor=''), lease.proof))
        # Empty cursor is codec-invalid; unchanged nonempty cursor tested below.
        assert stopped.value.code in ('KF_INGRESS_INVALID_FIELD', 'KF_INGRESS_CURSOR_STALLED')
        assert repository.commit_page(lease, bounded_page(normal, lease.proof)) == {'received': 1, 'has_more': False}
    finally:
        repository.release(lease)
    seed_intent(scope, repository)
    next_lease, _ = repository.claim('kf_page_next_' + scope.marker)
    assert next_lease is not None and next_lease.cursor == 'saved_first'
    try:
        with pytest.raises(KfIngressError) as stalled:
            repository.commit_page(next_lease, bounded_page(dict(normal, has_more=1, next_cursor='saved_first'), next_lease.proof))
        assert stalled.value.code == 'KF_INGRESS_CURSOR_STALLED'
        conflict = copy.deepcopy(normal)
        conflict['msg_list'][0]['text']['content'] = 'Conflicting provider fact'
        with pytest.raises(KfIngressError) as rejected:
            repository.commit_page(next_lease, bounded_page(conflict, next_lease.proof))
        assert rejected.value.code == 'KF_INGRESS_MESSAGE_CONFLICT'
        assert scope.rows('SELECT payload FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))[0]['payload']['text']['content'] == 'Fictional incoming text'
        account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
        assert account['cursor'] == 'saved_first' and account['completed_generation'] == 1 and account['requested_generation'] == 2
        # Same original fact on a later authorized page is idempotent, not a new message/history/action.
        assert repository.commit_page(next_lease, bounded_page(normal, next_lease.proof)) == {'received': 0, 'has_more': False}
    finally:
        repository.release(next_lease)
    assert len(scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))) == 1
    assert scope.rows('SELECT count(*) AS n FROM channel_messages WHERE session_id=%s', (scope.legacy_sid,))[0]['n'] == 1
