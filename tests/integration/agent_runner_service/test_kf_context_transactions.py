"""Prepared original Context SQL transactions; no mutable-source execution."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time

import psycopg2
import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text
from .kf_admission_peer import StateReply

pytestmark = pytest.mark.integration


def test_actual_route_session_inbox_lock_wait_rechecks_fresh_clock_and_configuration(
        context_receipts, monkeypatch):
    """Original SDK timestamp ages under scheduling DI, then a real SQL wait.

    The transparent classify-entry gate delays only the first genuine
    observation until DB age >=8.7s. The actual session lock wait is below the
    original 3s lock timeout; release follows DB clock crossing 10s. Neither
    clock, observation, permissions nor SQL results are rewritten. This is
    not a claim that one SQL lock can block for the entire ten-second window.
    """
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    c, s = context_receipts, context_receipts.scope
    locator = c.receive(human_text(s, 'clock_wait_' + s.marker,
        'Fictional expired context observation'))[0]
    original = c.receipt(locator)
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}),
        StateReply({'errcode': 0, 'service_state': 3})]
    repository = ContextRepository(s.database.connect)
    original_classify = repository.classify
    entered = threading.Event()
    observation = {}

    def observe_original_classify(actual_locator, actual_observation):
        if not entered.is_set():
            observation['actual'] = actual_observation
            # Timing DI before the original classified transaction, retaining
            # its real SDK timestamp. PG itself supplies every age sample.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                age = s.rows('SELECT extract(epoch FROM clock_timestamp()-%s) AS age',
                    (actual_observation.observed_at,))[0]['age']
                if age >= 8.7:
                    break
                time.sleep(0.05)
            assert 8.7 <= age < 9.5
            observation['sql_entered_at'] = time.monotonic()
            entered.set()
        return original_classify(actual_locator, actual_observation)
    monkeypatch.setattr(repository, 'classify', observe_original_classify)

    async def one():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=c.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()

    with s.database.connect() as holder, holder.cursor() as cursor:
        cursor.execute('SELECT session_id FROM channel_sessions WHERE session_id=%s '
            'AND tenant_id=%s FOR UPDATE', (s.legacy_sid, s.tenant_id))
        assert cursor.fetchone()['session_id'] == s.legacy_sid
        cursor.execute('SELECT pg_backend_pid() AS pid')
        holder_pid = cursor.fetchone()['pid']
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(lambda: asyncio.run(one()))
            try:
                assert entered.wait(12)
                actual = observation['actual']
                assert actual is not None and actual.state == 3
                # Confirm a real waiter is blocked by our owned holder, rather
                # than treating a Python timeout or held SDK IO as a SQL lock.
                deadline = observation['sql_entered_at'] + 2.5
                while time.monotonic() < deadline:
                    blocked = s.rows('SELECT count(*) AS n FROM pg_stat_activity '
                        'WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))',
                        (holder_pid,))[0]['n']
                    if blocked:
                        break
                    time.sleep(0.05)
                assert blocked >= 1 and not pending.done()
                # Exact DB clock crosses the unchanged 10s freshness budget
                # while the actual waiter remains within its own SQL budget.
                deadline = observation['sql_entered_at'] + 2.5
                while time.monotonic() < deadline:
                    age = s.rows('SELECT extract(epoch FROM clock_timestamp()-%s) AS age',
                        (actual.observed_at,))[0]['age']
                    if age > 10:
                        break
                    time.sleep(0.05)
                assert age > 10 and not pending.done()
                assert time.monotonic() - observation['sql_entered_at'] < 3
                holder.commit()
                with pytest.raises(KfIngressError) as captured:
                    pending.result(timeout=8)
                assert captured.value.code == 'KF_CONTEXT_OBSERVATION_EXPIRED'
            finally:
                holder.rollback()
                if not pending.done():
                    pending.result(timeout=8)
    assert c.receipt(locator) == original
    assert len(c.stored_history()) == 1
    for table in ('wecom_kf_receipt_classifications', 'wecom_kf_context_consumptions',
            'wecom_kf_context_task_intents'):
        assert s.rows('SELECT 1 FROM ' + table + ' WHERE account_id=%s',
            (c.account['account_id'],)) == []
    # The genuine new original SDK observation succeeds after our lock release.
    result = asyncio.run(one())
    assert result['disposition'] == 'persisted'
    assert len(c.stored_history()) == 2
    assert len(c.platform.calls) == 2 and not c.platform.errors

    # Distinct same-node version fence: an actual CFG lock delays the original
    # classify transaction, then a committed cosmetic version change makes its
    # otherwise fresh original SDK observation stale. No config/proof row is
    # replaced by a returned fixture object.
    config_locator=c.receive(human_text(s,'config_wait_'+s.marker,
        'Fictional context observation with changed configuration'))[0]
    original_config=c.stored_config()
    config_inbox=c.receipt(config_locator)
    config_entered=threading.Event()
    c.platform.states.extend([StateReply({'errcode':0,'service_state':3}),
        StateReply({'errcode':0,'service_state':3})])

    def observe_config_wait(actual_locator,actual_observation):
        if actual_locator==config_locator:
            observation['config_observation']=actual_observation
            config_entered.set()
        return original_classify(actual_locator,actual_observation)

    monkeypatch.setattr(repository,'classify',observe_config_wait)
    try:
        with s.database.connect() as holder,holder.cursor() as cursor:
            cursor.execute('SELECT config_id FROM tenant_channel_configs WHERE config_id=%s '
                'AND tenant_id=%s FOR UPDATE',(s.config_id,s.tenant_id))
            assert cursor.fetchone()['config_id']==s.config_id
            cursor.execute('SELECT pg_backend_pid() AS pid')
            holder_pid=cursor.fetchone()['pid']
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(lambda:asyncio.run(one()))
                try:
                    assert config_entered.wait(5)
                    assert observation['config_observation'].config_version==original_config['updated_at']
                    deadline=time.monotonic()+2
                    blocked=0
                    while time.monotonic()<deadline:
                        blocked=s.rows('SELECT count(*) AS n FROM pg_stat_activity '
                            'WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))',
                            (holder_pid,))[0]['n']
                        if blocked:
                            break
                        time.sleep(0.02)
                    assert blocked>=1 and not pending.done()
                    cursor.execute('UPDATE tenant_channel_configs SET updated_at=clock_timestamp() '
                        'WHERE config_id=%s AND tenant_id=%s',(s.config_id,s.tenant_id))
                    holder.commit()
                    with pytest.raises(KfIngressError) as rejected:
                        pending.result(timeout=8)
                    assert rejected.value.code=='KF_CONTEXT_OBSERVATION_EXPIRED'
                finally:
                    holder.rollback()
                    if not pending.done():
                        pending.result(timeout=8)
        assert c.receipt(config_locator)==config_inbox
        assert len(c.stored_history())==2
        for table in ('wecom_kf_receipt_classifications','wecom_kf_context_consumptions',
                'wecom_kf_context_task_intents'):
            assert s.rows('SELECT 1 FROM '+table+' WHERE account_id=%s AND namespace=%s AND message_id=%s',
                (config_locator.account_id,config_locator.namespace,config_locator.message_id))==[]
    finally:
        c.restore_config(original_config)
    assert asyncio.run(one())['disposition']=='persisted'
    assert len(c.stored_history())==3
    assert len(c.platform.calls)==4 and not c.platform.errors


def test_original_sql_failure_rolls_back_history_consumption_and_pending_recap_intents(context_receipts):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    c, s = context_receipts, context_receipts.scope
    locator = c.receive(human_text(s, 'context_sql_failure_' + s.marker,
        'Fictional atomic context text'))[0]
    before = c.receipt(locator)
    session_before = s.rows('SELECT * FROM channel_sessions WHERE session_id=%s',
        (s.legacy_sid,))
    history_before = c.stored_history()
    repository = ContextRepository(s.database.connect)
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}),
        StateReply({'errcode': 0, 'service_state': 3})]
    function = 'reject_context_intent_' + s.marker
    trigger = 'reject_context_intent_' + s.marker
    # Identifier names are our fresh lowercase/hex IDs; account data remains
    # a parameter via set_config, never interpolated into SQL or a log.
    with s.database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('CREATE FUNCTION ' + function + "() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.account_id=current_setting('test.context_account',true) THEN "
            "RAISE EXCEPTION 'FICTIONAL_CONTEXT_INTENT_SQL_FAILURE'; END IF; RETURN NEW; END $$")
        cursor.execute('CREATE TRIGGER ' + trigger + ' BEFORE INSERT ON '
            'wecom_kf_context_task_intents FOR EACH ROW EXECUTE FUNCTION ' + function + '()')

    from contextlib import contextmanager
    @contextmanager
    def fault_connection():
        with s.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('test.context_account',%s,true)",
                    (c.account['account_id'],))
            yield connection

    async def one(repo):
        worker = ContextWorker(c.config.wecom_kf, repository=repo,
            client_factory=c.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()

    try:
        with pytest.raises(psycopg2.errors.RaiseException):
            asyncio.run(one(ContextRepository(fault_connection)))
        assert c.receipt(locator) == before
        assert c.stored_history() == history_before
        assert s.rows('SELECT * FROM channel_sessions WHERE session_id=%s',
            (s.legacy_sid,)) == session_before
        # Classification is an earlier durable stage. A history/intent SQL
        # failure must not erase reliable human ownership and permit later AI.
        fixed = s.rows('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s',
            (c.account['account_id'],))
        assert len(fixed) == 1 and fixed[0]['classification'] == 'human'
        assert fixed[0]['observed_state'] == 3 and fixed[0]['payload_digest'] == before['payload_digest']
        for table in ('wecom_kf_context_consumptions', 'wecom_kf_context_task_intents'):
            assert s.rows('SELECT 1 FROM ' + table + ' WHERE account_id=%s',
                (c.account['account_id'],)) == []
    finally:
        with s.database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER IF EXISTS ' + trigger + ' ON wecom_kf_context_task_intents')
            cursor.execute('DROP FUNCTION IF EXISTS ' + function + '()')
    result = asyncio.run(one(repository))
    assert result['disposition'] == 'persisted'
    assert len(c.stored_history()) == 2
    assert s.rows('SELECT disposition FROM wecom_kf_context_consumptions WHERE account_id=%s',
        (c.account['account_id'],)) == [{'disposition': 'persisted'}]
    assert len(s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
        (c.account['account_id'],))) == 2
    assert s.rows('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s',
        (c.account['account_id'],)) == fixed
    assert len(c.platform.calls) == 1 and not c.platform.errors
