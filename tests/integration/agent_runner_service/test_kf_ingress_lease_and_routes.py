"""Prepared real lock/clock and exact legacy session-scope contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message

pytestmark = pytest.mark.integration


def test_original_page_session_lock_crossing_actual_lease_expiry_rolls_back_route_inbox_and_cursor(kf_scope):
    """Real PG row-lock timing; no altered timestamps/clock/ownership result."""
    from src.channels.wecom_kf.ingress_auth import KfIngressError, bounded_page
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    scope = kf_scope
    repository = KfIngressRepository(scope.database.connect)
    seed_intent(scope, repository)
    lease, _ = repository.claim('kf_lock_owner_' + scope.marker, lease_seconds=5)
    assert lease is not None
    page = bounded_page({'errcode': 0, 'has_more': 0, 'next_cursor': 'expired_not_committed',
        'msg_list': [text_message(scope, 'lock_msg_' + scope.marker)]}, lease.proof)
    initial = scope.rows('SELECT lease_until>clock_timestamp() AS live FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert initial['live'] is True
    # Original repository has LOCAL lock_timeout=3s. Age the real minimum-5s
    # lease until less than 2s remain, so the actual row-lock wait crosses lease
    # expiry without first hitting an unrelated SQL lock timeout.
    aged_deadline = time.monotonic() + 5
    while time.monotonic() < aged_deadline:
        remaining = scope.rows("""SELECT EXTRACT(EPOCH FROM (lease_until-clock_timestamp())) AS remaining
            FROM wecom_kf_account_sync WHERE config_id=%s""", (scope.config_id,))[0]['remaining']
        if remaining <= 2:
            break
        time.sleep(.025)
    assert 0 < remaining <= 2
    with scope.database.connect() as locked, locked.cursor() as cursor:
        cursor.execute('SELECT session_id FROM channel_sessions WHERE session_id=%s FOR UPDATE', (scope.legacy_sid,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(repository.commit_page, lease, page)
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    count = scope.rows("""SELECT count(*) AS n FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                        AND wait_event_type='Lock' AND query LIKE 'SELECT * FROM channel_sessions%%'""")[0]['n']
                    if count:
                        break
                    time.sleep(.02)
                assert count > 0 and not future.done()
                expiry_deadline = time.monotonic() + 6
                while time.monotonic() < expiry_deadline:
                    expired = scope.rows('SELECT lease_until<=clock_timestamp() AS expired FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]['expired']
                    if expired:
                        break
                    time.sleep(.04)
                assert expired is True and not future.done()
            finally:
                locked.rollback()  # Always release before joining original SQL thread.
            with pytest.raises(KfIngressError) as rejected:
                future.result(timeout=5)
            assert rejected.value.code == 'KF_INGRESS_LEASE_LOST'
    assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []
    assert scope.rows('SELECT 1 FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,)) == []
    row = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
    assert row['cursor'] == '' and row['completed_generation'] == 0 and row['requested_generation'] == 1
    assert row['claim_epoch'] == lease.epoch and row['worker_id'] == lease.worker_id
    repository.release(lease)
    assert scope.rows('SELECT worker_id,lease_until FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,)) == [{'worker_id': None, 'lease_until': None}]


def test_original_route_same_cursor_keeps_shared_sid_null_user_and_refuses_ambiguous_or_foreign_session(kf_scope):
    from src.channels.wecom_kf.channel_session_repository import find_or_bind_in_tx
    from src.channels.wecom_kf.ingress_auth import KfIngressError, current_config_in_tx, bounded_page
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    scope = kf_scope
    second_config = 'kf_other_config_' + scope.marker
    third_config = 'kf_ambiguous_config_' + scope.marker
    duplicate_sid = 'kf_ambiguous_sid_' + scope.marker
    try:
        with scope.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO tenant_channel_configs(config_id,tenant_id,channel_type,name,config,
                verified,subagent_type,updated_at) SELECT %s,tenant_id,channel_type,name,config,
                verified,subagent_type,clock_timestamp() FROM tenant_channel_configs WHERE config_id=%s""",
                (second_config, scope.config_id))
            first = current_config_in_tx(cursor, scope.tenant_id, scope.config_id, scope.open_kfid, lock=True).proof
            second = current_config_in_tx(cursor, scope.tenant_id, second_config, scope.open_kfid, lock=True).proof
            original = find_or_bind_in_tx(cursor, first, scope.actor_id)
            shared = find_or_bind_in_tx(cursor, second, scope.actor_id)
            assert original['session_id'] == shared['session_id'] == scope.legacy_sid
            assert original['route_id'] != shared['route_id']
            assert original['user_id'] is None and shared['user_id'] is None
            assert original['legacy_shared'] is True and shared['legacy_shared'] is True
            assert first.raw_profile == second.raw_profile == '' and first.profile_id == second.profile_id == 'main'
        repository = KfIngressRepository(scope.database.connect)

        def fresh_proof():
            with scope.database.connect() as connection, connection.cursor() as cursor:
                return current_config_in_tx(cursor, scope.tenant_id, scope.config_id,
                    scope.open_kfid, lock=True).proof

        def page(lease, message_id, next_cursor):
            return bounded_page({'errcode': 0, 'has_more': 0, 'next_cursor': next_cursor,
                'msg_list': [text_message(scope, message_id)]}, lease.proof)

        # Original received facts retain their source audit version, while a
        # fresh current config authorizes reads and future lease acquisition.
        first_id = 'original_audit_' + scope.marker
        seed_intent(scope, repository)
        initial_lease, _ = repository.claim('kf_original_' + scope.marker)
        assert initial_lease is not None
        assert repository.commit_page(initial_lease, page(initial_lease, first_id, 'audit_saved'))['received'] == 1
        first_inbox = repository.read_received(first, 'sync', first_id)
        route_audit = scope.rows('SELECT * FROM channel_session_routes WHERE route_id=%s', (original['route_id'],))[0]
        assert first_inbox['config_version'] == initial_lease.proof.config_version
        seed_intent(scope, repository)
        old_lease, _ = repository.claim('kf_before_cosmetic_' + scope.marker)
        assert old_lease is not None and old_lease.generation == 2
        scope.rows("""UPDATE tenant_channel_configs SET name='Fictional cosmetic rename',
            updated_at=clock_timestamp() WHERE config_id=%s""", (scope.config_id,))
        cosmetic = fresh_proof()
        assert cosmetic.config_version != old_lease.proof.config_version
        with pytest.raises(KfIngressError) as stale:
            repository.commit_page(old_lease, page(old_lease, 'stale_' + scope.marker, 'must_not_save'))
        assert stale.value.code == 'KF_INGRESS_CONFIG_CHANGED'
        repository.release(old_lease)
        assert repository.read_received(cosmetic, 'sync', first_id) == first_inbox
        assert scope.rows('SELECT * FROM channel_session_routes WHERE route_id=%s', (original['route_id'],))[0] == route_audit
        fresh_lease, _ = repository.claim('kf_after_cosmetic_' + scope.marker)
        assert fresh_lease is not None and fresh_lease.epoch > old_lease.epoch
        assert fresh_lease.generation == 2 and fresh_lease.cursor == 'audit_saved'
        assert fresh_lease.proof.config_version == cosmetic.config_version
        assert repository.commit_page(fresh_lease, page(fresh_lease, 'cosmetic_' + scope.marker, 'cosmetic_saved'))['received'] == 1
        assert scope.rows('SELECT count(*) AS n FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))[0]['n'] == 2

        # Literal main and the original empty selector have one canonical scope;
        # neither received inbox nor route/session audit is rewritten.
        scope.rows("""UPDATE tenant_channel_configs SET
            config=jsonb_set(config::jsonb,'{kf_account,0,subagent_type}','"main"')::text,
            updated_at=clock_timestamp() WHERE config_id=%s""", (scope.config_id,))
        literal_main = fresh_proof()
        assert literal_main.raw_profile == 'main' and literal_main.profile_id == first.profile_id
        assert repository.read_received(literal_main, 'sync', first_id) == first_inbox
        seed_intent(scope, repository)
        main_lease, _ = repository.claim('kf_literal_main_' + scope.marker)
        assert main_lease is not None and main_lease.cursor == 'cosmetic_saved' and main_lease.generation == 3
        assert repository.commit_page(main_lease, page(main_lease, 'main_' + scope.marker, 'main_saved'))['received'] == 1
        assert scope.rows('SELECT * FROM channel_session_routes WHERE route_id=%s', (original['route_id'],))[0] == route_audit
        original_account = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
        assert original_account['raw_profile'] == '' and original_account['profile_id'] == 'main'
        assert original_account['requested_generation'] == original_account['completed_generation'] == 3

        # A real different profile cannot retarget the original account's
        # pending generation, historical route, or original nullable-user SID.
        seed_intent(scope, repository)
        scope.rows("""UPDATE tenant_channel_configs SET
            config=jsonb_set(config::jsonb,'{kf_account,0,subagent_type}','"different_profile"')::text,
            updated_at=clock_timestamp() WHERE config_id=%s""", (scope.config_id,))
        changed = fresh_proof()
        with pytest.raises(KfIngressError) as conflict:
            seed_intent(scope, repository)
        assert conflict.value.code == 'KF_INGRESS_ACCOUNT_CONFLICT'
        rejected_lease, _ = repository.claim('kf_must_not_retarget_' + scope.marker)
        assert rejected_lease is None
        with pytest.raises(KfIngressError) as changed_read:
            repository.read_received(changed, 'sync', first_id)
        assert changed_read.value.code == 'KF_INGRESS_ROUTE_INVALID'
        blocked = scope.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (scope.config_id,))[0]
        assert blocked['verification_code'] == 'KF_INGRESS_ACCOUNT_CHANGED'
        assert blocked['cursor'] == 'main_saved' and blocked['requested_generation'] == 4 and blocked['completed_generation'] == 3
        assert blocked['raw_profile'] == '' and blocked['profile_id'] == 'main' and blocked['worker_id'] is None
        assert scope.rows('SELECT count(*) AS n FROM channel_session_routes WHERE config_id=%s', (scope.config_id,))[0]['n'] == 1
        scope.rows("""UPDATE tenant_channel_configs SET
            config=jsonb_set(config::jsonb,'{kf_account,0,subagent_type}','"main"')::text,
            updated_at=clock_timestamp() WHERE config_id=%s""", (scope.config_id,))
        first = fresh_proof()
        assert repository.read_received(first, 'sync', first_id) == first_inbox
        scope.rows('DELETE FROM channel_session_routes WHERE route_id=%s', (original['route_id'],))
        with pytest.raises(KfIngressError) as absent:
            repository.read_received(first, 'sync', first_id)
        assert absent.value.code == 'KF_INGRESS_ROUTE_UNAVAILABLE'
        assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (scope.config_id,)) == []
        # Explicit authorized binding is separate from the pure received getter.
        with scope.database.connect() as connection, connection.cursor() as cursor:
            rebound = find_or_bind_in_tx(cursor, first, scope.actor_id)
            assert rebound['session_id'] == scope.legacy_sid and rebound['user_id'] is None
        # Actual original session's account field is altered by a scoped SQL DI.
        scope.rows('UPDATE channel_sessions SET channel_chat_id=%s WHERE session_id=%s', ('foreign_fictional_open', scope.legacy_sid))
        with scope.database.connect() as connection, connection.cursor() as cursor:
            with pytest.raises(KfIngressError) as foreign:
                find_or_bind_in_tx(cursor, first, scope.actor_id)
            assert foreign.value.code == 'KF_INGRESS_SESSION_LOST'
        scope.rows('UPDATE channel_sessions SET channel_chat_id=%s WHERE session_id=%s', (scope.open_kfid, scope.legacy_sid))
        with scope.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO tenant_channel_configs(config_id,tenant_id,channel_type,name,config,
                verified,updated_at) SELECT %s,tenant_id,channel_type,name,config,verified,clock_timestamp()
                FROM tenant_channel_configs WHERE config_id=%s""", (third_config, scope.config_id))
            cursor.execute("""INSERT INTO channel_sessions(session_id,tenant_id,channel_type,channel_user_id,
                subagent_id,channel_chat_id,user_id) VALUES(%s,%s,'wecom_kf',%s,'main',%s,NULL)""",
                (duplicate_sid, scope.tenant_id, scope.actor_id, scope.open_kfid))
        with scope.database.connect() as connection, connection.cursor() as cursor:
            third = current_config_in_tx(cursor, scope.tenant_id, third_config, scope.open_kfid, lock=True).proof
            with pytest.raises(KfIngressError) as ambiguous:
                find_or_bind_in_tx(cursor, third, scope.actor_id)
            assert ambiguous.value.code == 'KF_INGRESS_SESSION_AMBIGUOUS'
        assert scope.rows('SELECT 1 FROM channel_session_routes WHERE config_id=%s', (third_config,)) == []
        assert scope.rows('SELECT content FROM channel_messages WHERE session_id=%s', (scope.legacy_sid,)) == [{'content': 'Original fictional history'}]
    finally:
        scope.rows('DELETE FROM channel_session_routes WHERE config_id=ANY(%s)', ([second_config, third_config],))
        scope.rows('DELETE FROM channel_sessions WHERE session_id=%s', (duplicate_sid,))
        scope.rows('DELETE FROM tenant_channel_configs WHERE config_id=ANY(%s)', ([second_config, third_config],))
