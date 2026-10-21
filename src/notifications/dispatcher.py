"""Bounded background-runner lifecycle. No Gunicorn jobs or network DB locks."""
import asyncio
import time
import uuid
from loguru import logger

from .contracts import SendResult
from .service import notification_repository


class NotificationDispatcher:
    def __init__(self, repository=None):
        self.repository = repository or notification_repository()
        self.config = self.repository.config
        self.owner = 'notification_dispatcher_'+uuid.uuid4().hex
        self._stop = asyncio.Event()
        self._task = None
        self._rate_lock = asyncio.Lock()
        self._next_send = 0.0

    async def start(self):
        if self._task is not None:
            return
        await asyncio.to_thread(self.repository.assert_schema)
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            task,self._task = self._task,None
            # Send transport has a finite timeout. An unresolved send remains
            # lease-fenced and is recovered; shutdown never resets accepted work.
            try:
                await asyncio.wait_for(asyncio.shield(task),timeout=60)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def _send(self, claim):
        async with self._rate_lock:
            delay = self._next_send-time.monotonic()
            if delay>0:
                await asyncio.sleep(delay)
            self._next_send = time.monotonic()+1/self.config.rate_per_second
        if self._stop.is_set():
            return
        prepared = await asyncio.to_thread(self.repository.prepare_send,claim)
        if prepared is None:
            return
        adapter,configuration,payload,ttl = prepared
        try:
            result = await asyncio.to_thread(adapter.send,configuration,payload,ttl)
        except Exception:
            result = SendResult('retryable','adapter_failure')
        await asyncio.to_thread(self.repository.finish,claim,result)
        logger.info('notification delivery id={} outcome={} error_class={}',
                    claim['delivery_id'],result.outcome,result.error_class)

    async def tick(self):
        await asyncio.to_thread(self.repository.expand)
        claims = await asyncio.to_thread(self.repository.claim,self.owner,self.config.concurrency)
        outcomes = await asyncio.gather(*(self._send(claim) for claim in claims),return_exceptions=True)
        if any(isinstance(value,BaseException) for value in outcomes):
            logger.error('notification delivery bookkeeping failure')
        await asyncio.to_thread(self.repository.cleanup)

    async def _run(self):
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.opt(exception=True).error('notification dispatcher failure')
            try:
                await asyncio.wait_for(self._stop.wait(),timeout=self.config.poll_seconds)
            except asyncio.TimeoutError:
                pass
