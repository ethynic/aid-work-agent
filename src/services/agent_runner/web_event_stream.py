"""Thin bounded SSE proxy; it holds no Runner repository or execution task."""

from .event_stream import FRAME_BYTES, ObserverResponse
from .web_client import close_event_transport


class WebEventResponse(ObserverResponse):
    def __init__(self, response, client, admission):
        super().__init__(admission)
        self.response, self.client = response, client

    async def pump(self, send):
        # One transport chunk only; never buffer the cumulative response or
        # accumulate pages while the downstream observer is slow.
        async for chunk in self.response.aiter_raw():
            if len(chunk) > FRAME_BYTES:
                return
            await send(chunk)

    async def close(self):
        await close_event_transport(self.response, self.client)
