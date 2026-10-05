"""Real WebSocket wire observations, with credential-safe failure messages.

This helper performs external transport only. It never supplies a Browser
frame, ticket result, auth result, runtime owner or repository response.
"""
from contextlib import asynccontextmanager
import asyncio
import json
from urllib.parse import quote, urlsplit, urlunsplit


class ObservationTransportFailure(AssertionError):
    def __init__(self, kind, status=None):
        self.kind, self.status = kind, status
        super().__init__(f'Browser observation transport: {kind}, status={status}')


class ObservationWire:
    def __init__(self, connection):
        self.connection = connection

    async def receive(self, timeout=5):
        from websockets.exceptions import ConnectionClosed
        try:
            return await asyncio.wait_for(self.connection.recv(), timeout)
        except ConnectionClosed as error:
            received = getattr(error, 'rcvd', None)
            raise ObservationTransportFailure('ConnectionClosed',
                                               getattr(received, 'code', None)) from None
        except (TimeoutError, OSError) as error:
            raise ObservationTransportFailure(type(error).__name__) from None

    async def frame(self, timeout=10, *, after_seq=0):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            assert remaining > 0, 'No original Browser frame observed before deadline'
            value = await self.receive(remaining)
            assert isinstance(value, str), 'JPEG arrived without original frame metadata'
            try:
                metadata = json.loads(value)
            except (ValueError, TypeError):
                raise ObservationTransportFailure('InvalidFrameMetadata') from None
            if metadata.get('type') == 'heartbeat':
                continue
            assert metadata.get('type') == 'frame', 'Unexpected Browser observation message type'
            for field in ('seq', 'width', 'height'):
                assert type(metadata.get(field)) is int and metadata[field] > 0
            jpeg = await self.receive(max(0.1, deadline - asyncio.get_running_loop().time()))
            assert isinstance(jpeg, bytes) and jpeg.startswith(b'\xff\xd8'), 'Original frame is not JPEG'
            if metadata['seq'] <= after_seq:
                continue
            return metadata, jpeg


@asynccontextmanager
async def observation_socket(api_url, run_id, ticket):
    """One real socket; its detach does not send an API cancel or control."""
    import websockets
    parts = urlsplit(api_url)
    assert parts.scheme == 'http' and parts.hostname == '127.0.0.1'
    path = '/api/browser/runs/' + quote(run_id, safe='') + '/view_ws'
    uri = urlunsplit(parts._replace(scheme='ws', path=path,
                                   query='ticket=' + quote(ticket, safe=''), fragment=''))
    connection = None
    try:
        try:
            connection = await websockets.connect(uri, open_timeout=5, close_timeout=3,
                                                  max_size=4 * 1024 * 1024)
        except Exception as error:
            response = getattr(error, 'response', None)
            status = getattr(response, 'status_code', getattr(error, 'status_code', None))
            raise ObservationTransportFailure(type(error).__name__, status) from None
        yield ObservationWire(connection)
    finally:
        if connection is not None:
            await connection.close()
