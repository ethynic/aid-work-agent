"""Prepared full application supervision with declared outer ASGI IO timing DI.

The held send models a slow network sink, not physical socket-buffer pressure.
Original service/Web middleware, fresh PG auth and real upstream are retained.
First-read cancellation holds original authorized RR, then joins the actual
thread/connection completion; cancellation of an await alone is not cleanup.
"""
import asyncio
from contextlib import contextmanager
import json
import threading
import time

import pytest

from src.services.agent_runner.api import create_app as service_app
from src.services.agent_runner.event_stream import ObserverAdmission, OBSERVER_LIMIT
from src.services.agent_runner.contracts import RunnerError
from src.saas.context import get_current_tenant_id, get_current_user_id

from .test_runner_event_auth import reader
from .test_runner_event_sse import event_apis
from .test_storage import storage

pytestmark = pytest.mark.integration


def scope_for(path, headers, head):
    return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
        'http_version': '1.1', 'method': 'GET', 'scheme': 'http', 'path': path,
        'raw_path': path.encode(), 'root_path': '', 'query_string': f'after_seq={head}'.encode(),
        'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        'client': ('127.0.0.1', 12345), 'server': ('127.0.0.1', 1)}


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['service', 'web'])
async def test_complete_actual_app_authorization_supervises_outer_held_headers_and_body(
        kind, storage, reader, event_apis, actors, service_database, monkeypatch):
    repository, _, submit = storage
    row, _ = submit(actors['a'])
    actor = actors['a']
    before = repository.get(row['runner_id'])
    if kind == 'service':
        manager, authorizer, credentials = reader
        config = authorizer.config.model_copy(update={'enabled': True})
        application = service_app(config=config, manager=manager)
        proof = credentials(actor)
        headers = {'X-AgentRunner-Service': proof['service_id'],
            'X-AgentRunner-Service-Token': proof['service_token'], 'Authorization': 'Bearer ' + actor.token}
        path = '/v1/runners/' + row['runner_id'] + '/events'
    else:
        from src.api import agent_runner_web
        config = agent_runner_web.settings.agent_runner.model_copy(update={
            'web_enabled': True, 'api_url': event_apis.urls[0], 'web_service_id': event_apis.service_id})
        monkeypatch.setattr(agent_runner_web.settings, 'agent_runner', config)
        monkeypatch.setenv('AGENT_RUNNER_WEB_SERVICE_TOKEN', event_apis._service_token)
        application = agent_runner_web.create_app()
        headers = {'Authorization': 'Bearer ' + actor.token}
        path = '/api/chat/runners/' + row['runner_id'] + '/events'
    token_row = service_database.rows('SELECT user_id,expires_at FROM tokens WHERE token=%s', (actor.token,))[0]
    async with application.router.lifespan_context(application):
        for stage in ('headers', 'body'):
            entered, release, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
            wrote_headers, wrote_body = [], []
            tenant_context_at_send, tenant_context_after_dispatch = [], []
            request_sent = False

            async def receive():
                nonlocal request_sent
                if not request_sent:
                    request_sent = True
                    return {'type': 'http.request', 'body': b'', 'more_body': False}
                await disconnected.wait()
                return {'type': 'http.disconnect'}

            async def send(message):
                if kind == 'web':
                    tenant_context_at_send.append((get_current_tenant_id() == actor.tenant_id,
                                                   get_current_user_id() == actor.user_id))
                held = (stage == 'headers' and message['type'] == 'http.response.start'
                    or stage == 'body' and message['type'] == 'http.response.body' and message.get('body'))
                if held:
                    entered.set()
                    await release.wait()
                if message['type'] == 'http.response.start':
                    wrote_headers.append(message['status'])
                elif message.get('body'):
                    wrote_body.append(len(message['body']))

            async def invoke_original_app():
                try:
                    await application(scope_for(path, headers, row['event_seq']), receive, send)
                finally:
                    # Read in the actual dispatch task, rather than infer its
                    # ContextVar cleanup from a different caller task.
                    if kind == 'web':
                        tenant_context_after_dispatch.append((get_current_tenant_id(), get_current_user_id()))

            operation = asyncio.create_task(invoke_original_app())
            try:
                await asyncio.wait_for(entered.wait(), 8)
                await asyncio.to_thread(service_database.rows, 'DELETE FROM tokens WHERE token=%s', (actor.token,))
                started = time.monotonic()
                # Service fresh auth is independent at1s; the thin Web proxy
                # relies on upstream fresh auth plus its own5s send bound,
                # not a second Web-local DAL authorization timer.
                bound = 4 if kind == 'service' else 7
                await asyncio.wait_for(asyncio.shield(operation), bound)
                assert time.monotonic() - started < bound
                assert not wrote_body
                assert wrote_headers == ([] if stage == 'headers' else [200])
                if kind == 'web':
                    assert tenant_context_at_send and all(all(value) for value in tenant_context_at_send)
                    assert tenant_context_after_dispatch == [(None, None)]
                assert repository.get(row['runner_id']) == before
                assert service_database.rows("SELECT count(*) AS count FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%%'") == [{'count': 0}]
            finally:
                release.set(); disconnected.set()
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                service_database.rows('INSERT INTO tokens(token,user_id,expires_at) VALUES (%s,%s,%s) ON CONFLICT(token) DO NOTHING',
                    (actor.token, token_row['user_id'], token_row['expires_at']))


@pytest.mark.asyncio
async def test_cancelled_first_app_read_joins_original_rr_thread_and_returns_all_admission_slots(
        storage, reader, actors, service_database, monkeypatch):
    repository, _, submit = storage
    row, _ = submit(actors['a'])
    manager, authorizer, credentials = reader
    entered, release, connection_closed = threading.Event(), threading.Event(), threading.Event()
    admissions, release_calls = [], []
    original_acquire, original_release = ObserverAdmission.acquire, ObserverAdmission.release

    def observed_acquire(admission):
        result = original_acquire(admission)
        if admission not in admissions:
            admissions.append(admission)
        return result

    def observed_release(admission):
        result = original_release(admission)
        release_calls.append(admission)
        return result

    monkeypatch.setattr(ObserverAdmission, 'acquire', observed_acquire)
    monkeypatch.setattr(ObserverAdmission, 'release', observed_release)
    original_auth = authorizer.authorize_read_in_tx
    original_factory = repository.connection_factory
    active = 0

    def held_auth(*args, **kwargs):
        result = original_auth(*args, **kwargs)
        entered.set()
        assert release.wait(8), 'Fixture must release original RR thread'
        return result

    @contextmanager
    def tracked():
        nonlocal active
        active += 1
        try:
            with original_factory() as connection:
                yield connection
        finally:
            active -= 1
            # Set only AFTER the original factory actually exits/closes.
            connection_closed.set()

    monkeypatch.setattr(authorizer, 'authorize_read_in_tx', held_auth)
    monkeypatch.setattr(manager.repository, 'connection_factory', tracked)
    application = service_app(config=authorizer.config.model_copy(update={'enabled': True}), manager=manager)
    proof = credentials(actors['a'])
    headers = {'Authorization': 'Bearer ' + actors['a'].token,
        'X-AgentRunner-Service': proof['service_id'], 'X-AgentRunner-Service-Token': proof['service_token']}
    sent = []
    disconnected = asyncio.Event()

    async def receive():
        await disconnected.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        sent.append(message['type'])

    operation = asyncio.create_task(application(scope_for('/v1/runners/' + row['runner_id'] + '/events', headers, 1), receive, send))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert active == 1 and len(admissions) == 1 and admissions[0].active == 1
        operation.cancel()
        await asyncio.sleep(.05)
        operation.cancel()  # A second real cancellation during first-read cleanup.
        await asyncio.sleep(.05)
        assert active == 1 and not connection_closed.is_set()
        assert admissions[0].active == 1 and not release_calls
        release.set()
        assert await asyncio.to_thread(connection_closed.wait, 5)
        await asyncio.wait_for(asyncio.gather(operation, return_exceptions=True), 5)
        assert active == 0 and not sent  # No body without a successful HTTP start.
        assert admissions[0].active == 0 and release_calls == [admissions[0]]
        assert repository.get(row['runner_id']) == row
        assert service_database.rows("SELECT count(*) AS count FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%%'") == [{'count': 0}]
        # Exact original counter contract, not empirical HTTP capacity proof.
        admission = ObserverAdmission()
        for _ in range(OBSERVER_LIMIT):
            admission.acquire()
        with pytest.raises(RunnerError) as caught:
            admission.acquire()
        assert caught.value.code == 'RUNNER_EVENT_OBSERVER_LIMIT' and caught.value.status == 429
        for _ in range(OBSERVER_LIMIT):
            admission.release()
        assert admission.active == 0
    finally:
        release.set(); disconnected.set()
        if not operation.done():
            operation.cancel()
        await asyncio.wait_for(asyncio.gather(operation, return_exceptions=True), 6)
        assert await asyncio.to_thread(connection_closed.wait, 5)



@pytest.mark.asyncio
async def test_original_full_service_read_real_table_lock_timeout_returns_connection_and_permit(
        storage, reader, actors, service_database, monkeypatch):
    """Real PG lock timeout; full ASGI app output, not physical socket pressure."""
    repository, _, submit = storage
    row, _ = submit(actors['a'])
    manager, authorizer, credentials = reader
    original_factory = manager.repository.connection_factory
    original_acquire, original_release = ObserverAdmission.acquire, ObserverAdmission.release
    active, closed = 0, 0
    admissions, releases = [], []
    original_read = manager.read_events
    read_errors = []

    def observed_read(*args, **kwargs):
        try:
            return original_read(*args, **kwargs)
        except Exception as error:
            read_errors.append((type(error).__name__, getattr(error, 'pgcode', None)))
            raise

    @contextmanager
    def tracked():
        nonlocal active, closed
        active += 1
        try:
            with original_factory() as connection:
                yield connection
        finally:
            active -= 1
            closed += 1

    def acquire(admission):
        result = original_acquire(admission)
        admissions.append(admission)
        return result

    def release(admission):
        result = original_release(admission)
        releases.append(admission)
        return result

    monkeypatch.setattr(manager.repository, 'connection_factory', tracked)
    monkeypatch.setattr(manager, 'read_events', observed_read)
    monkeypatch.setattr(ObserverAdmission, 'acquire', acquire)
    monkeypatch.setattr(ObserverAdmission, 'release', release)
    application = service_app(config=authorizer.config.model_copy(update={'enabled': True}), manager=manager)
    proof = credentials(actors['a'])
    headers = {'Authorization': 'Bearer ' + actors['a'].token,
        'X-AgentRunner-Service': proof['service_id'], 'X-AgentRunner-Service-Token': proof['service_token']}

    async def invoke(*, detach_after_first_frame=False):
        messages, disconnected = [], asyncio.Event()
        sent_request = False

        async def receive():
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            messages.append(message)
            if detach_after_first_frame and message['type'] == 'http.response.body' and message.get('body'):
                disconnected.set()

        await asyncio.wait_for(application(scope_for('/v1/runners/' + row['runner_id'] + '/events', headers, 0), receive, send), 5)
        return messages

    # This second real connection retains an actual ACCESS EXCLUSIVE table
    # lock. The original event read must wait and hit its local lock budget.
    with service_database.connect() as holder, holder.cursor() as cursor:
        cursor.execute('LOCK TABLE agent_runners IN ACCESS EXCLUSIVE MODE')
        try:
            began = time.monotonic()
            messages = await invoke()
            elapsed = time.monotonic() - began
            assert .8 <= elapsed < 5
            assert [message['status'] for message in messages if message['type'] == 'http.response.start'] == [500]
            response = json.loads(b''.join(message.get('body', b'') for message in messages))
            assert response['error'] == 'RUNNER_STORAGE_UNAVAILABLE'
            assert read_errors == [('LockNotAvailable', '55P03')]
            assert active == 0 and closed == 1
            assert len(admissions) == 1 and admissions[0].active == 0 and releases == admissions
        finally:
            holder.rollback()
    assert repository.get(row['runner_id']) == row
    messages = await invoke(detach_after_first_frame=True)
    assert [message['status'] for message in messages if message['type'] == 'http.response.start'] == [200]
    assert any(b'event: created\n' in message.get('body', b'') for message in messages)
    assert active == 0 and closed >= 2 and all(admission.active == 0 for admission in admissions)
    assert releases == admissions
    assert repository.get(row['runner_id']) == row
    assert service_database.rows("SELECT count(*) AS count FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%%'") == [{'count': 0}]
