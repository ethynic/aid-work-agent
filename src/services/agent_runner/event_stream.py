"""Bounded observer transport over the original short, authorized event read."""

import asyncio
import json

from loguru import logger
from starlette.responses import Response

from .contracts import RunnerError, TERMINAL_STATUSES
from .event_contracts import cursor_state, nonnegative, stored_event


PAGE_LIMIT = 100
PAGE_BYTES = 49_152
FRAME_BYTES = 65_536
READ_SECONDS = 5
AUTH_SECONDS = 1
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15
SEND_SECONDS = 5
LIFETIME_SECONDS = 120
OBSERVER_LIMIT = 128


class ObserverAdmission:
    """A process-local finite connection count, unrelated to Runner ownership."""
    def __init__(self):
        self.active = 0

    def acquire(self):
        # Called without an await on the owning ASGI loop.
        if self.active >= OBSERVER_LIMIT:
            raise RunnerError('RUNNER_EVENT_OBSERVER_LIMIT', 429)
        self.active += 1

    def release(self):
        self.active -= 1


def parse_cursor(after_seq, last_event_id):
    if last_event_id is not None:
        if (not last_event_id or len(last_event_id) > 19
                or any(character < '0' or character > '9' for character in last_event_id)):
            raise RunnerError('INVALID_EVENT_CURSOR', 422)
        header = int(last_event_id)
        if header > 2**63 - 1 or after_seq is not None and header != after_seq:
            raise RunnerError('INVALID_EVENT_CURSOR', 422)
        return header
    return after_seq if after_seq is not None else 0


def event_frame(kind, payload, identifier):
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    frame = f'id: {identifier}\nevent: {kind}\ndata: {data}\n\n'.encode('utf-8')
    if len(frame) > FRAME_BYTES:
        raise RunnerError('RUNNER_EVENT_FRAME_TOO_LARGE', 502)
    return frame


def validate_page(page):
    if not isinstance(page, dict) or type(page.get('reset')) is not bool:
        raise RunnerError('RUNNER_EVENT_PAGE_INVALID', 502)
    head, floor, last = (nonnegative(page[key], key) for key in ('head', 'floor', 'last_seq'))
    state = page.get('state')
    if (not isinstance(state, dict) or type(state.get('version')) is not int
            or state['version'] != 1 or cursor_state(state) != state
            or not floor <= head or last > head or type(page.get('has_more')) is not bool
            or not isinstance(page.get('events'), list) or len(page['events']) > PAGE_LIMIT):
        raise RunnerError('RUNNER_EVENT_PAGE_INVALID', 502)


def reset_frame(page):
    # Never serialize a large reset.runner snapshot. GET remains authoritative.
    return event_frame('reset', {'reset': True, 'query_required': True,
        'head': page['head'], 'floor': page['floor'], 'last_seq': page['head'],
        'state': page['state']}, page['head'])


class ObserverReads:
    """Retain real offloaded reads until their connection scopes have exited."""
    def __init__(self):
        self.pending = set()

    async def page(self, manager, runner_id, credentials, cursor, **limits):
        task = asyncio.create_task(asyncio.to_thread(
            manager.read_events, runner_id, credentials, cursor, **limits))
        self.pending.add(task)

        def completed(done):
            self.pending.discard(done)
            # A timed-out observer may no longer await its original result.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
        try:
            page = await asyncio.wait_for(asyncio.shield(task), READ_SECONDS)
        except asyncio.TimeoutError:
            raise RunnerError('RUNNER_STORAGE_UNAVAILABLE', 503) from None
        validate_page(page)
        return page

    async def close(self):
        if not self.pending:
            return
        drain = asyncio.gather(*tuple(self.pending), return_exceptions=True)
        cancelled = False
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                # Cancellation cannot stop a thread or return its SQL
                # connection. Retain the permit until the actual read ends.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


class ObserverResponse(Response):
    """Explicit ASGI supervision keeps auth independent of a blocked send."""
    def __init__(self, admission):
        self.status_code, self.media_type, self.background = 200, 'text/event-stream', None
        self.init_headers({'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        self.admission = admission

    async def pump(self, send):
        raise NotImplementedError

    async def authorization(self):
        await asyncio.Future()

    async def close(self):
        pass

    async def __call__(self, scope, receive, send):
        tasks = []
        disconnected = False
        started = False

        async def disconnect():
            nonlocal disconnected
            while True:
                if (await receive())['type'] == 'http.disconnect':
                    disconnected = True
                    return

        async def bounded_send(body):
            await asyncio.wait_for(send({'type': 'http.response.body', 'body': body,
                                        'more_body': True}), SEND_SECONDS)

        async def stream():
            nonlocal started
            await asyncio.wait_for(send({'type': 'http.response.start', 'status': 200,
                                        'headers': self.raw_headers}), SEND_SECONDS)
            started = True
            await self.pump(bounded_send)

        try:
            # Auth and detach are supervised even while HTTPstart is blocked.
            tasks = [asyncio.create_task(stream()),
                     asyncio.create_task(self.authorization()), asyncio.create_task(disconnect())]
            done, _ = await asyncio.wait(tasks, timeout=LIFETIME_SECONDS,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (RunnerError, asyncio.TimeoutError, ConnectionError):
            # Headers are already committed. End only this observer; the next
            # fresh query/reconnect reports its normal safe HTTP error.
            pass
        except Exception as error:
            logger.warning('Runner event observer ended kind={}', type(error).__name__)
        finally:
            async def cleanup():
                # The owner of cleanup is shielded as a whole: a second
                # request cancellation cannot skip real reads or permit release.
                try:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await self.close()
                finally:
                    try:
                        if started and not disconnected:
                            try:
                                await asyncio.wait_for(send({'type': 'http.response.body',
                                    'body': b'', 'more_body': False}), 1)
                            except (Exception, asyncio.CancelledError):
                                pass
                    finally:
                        self.admission.release()

            closing = asyncio.create_task(cleanup())
            cancelled = False
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    cancelled = True
            closing.result()
            if cancelled:
                raise asyncio.CancelledError


class RunnerEventResponse(ObserverResponse):
    def __init__(self, manager, runner_id, credentials, cursor, first_page, admission, reads):
        super().__init__(admission)
        self.manager, self.runner_id, self.credentials = manager, runner_id, credentials
        self.cursor, self.first_page, self.reads = cursor, first_page, reads

    async def read(self, *, limit=PAGE_LIMIT, max_bytes=PAGE_BYTES):
        # The connection is returned before this coroutine sends or waits.
        return await self.reads.page(self.manager, self.runner_id, self.credentials,
                                     self.cursor, limit=limit, max_bytes=max_bytes)

    async def close(self):
        await self.reads.close()

    async def authorization(self):
        while True:
            await asyncio.sleep(AUTH_SECONDS)
            await self.read(limit=1, max_bytes=1024)

    async def pump(self, send):
        page, first = self.first_page, True
        last_send = asyncio.get_running_loop().time()
        while True:
            if page['reset'] or first and not page['events']:
                await send(reset_frame(page))
                self.cursor = page['head']
                last_send = asyncio.get_running_loop().time()
            else:
                for item in page['events']:
                    event = stored_event({'runner_id': self.runner_id, 'seq': item['seq'],
                                          'kind': item['kind'], 'payload': item})
                    if event['seq'] != self.cursor + 1:
                        raise RunnerError('RUNNER_EVENT_PAGE_INVALID', 502)
                    await send(event_frame(event['kind'], event, event['seq']))
                    self.cursor = event['seq']
                    last_send = asyncio.get_running_loop().time()
            first = False
            if (self.cursor == page['head'] and page['state']['status'] in TERMINAL_STATUSES
                    and page['state']['settlement_status'] == 'settled'):
                return
            if not page['has_more']:
                await asyncio.sleep(POLL_SECONDS)
                if asyncio.get_running_loop().time() - last_send >= HEARTBEAT_SECONDS:
                    await send(b': heartbeat\n\n')
                    last_send = asyncio.get_running_loop().time()
            page = await self.read()


async def open_runner_events(manager, runner_id, credentials, after_seq, admission):
    # Admission covers authentication and the real first offloaded read too.
    admission.acquire()
    reads, transferred = ObserverReads(), False
    try:
        page = await reads.page(manager, runner_id, credentials, after_seq,
                                limit=PAGE_LIMIT, max_bytes=PAGE_BYTES)
        observer = RunnerEventResponse(manager, runner_id, credentials, after_seq,
                                       page, admission, reads)
        transferred = True
        return observer
    finally:
        if not transferred:
            try:
                await reads.close()
            finally:
                admission.release()
