"""Independent bounded-dispatch and legacy compatibility contract checks."""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.contracts import SendResult
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.repository import NotificationRepository


@pytest.mark.asyncio
async def test_dispatch_tick_failure_isolated_and_cleanup_still_runs():
    barrier = threading.Barrier(2)
    seen = []
    class Adapter:
        def __init__(self, failing):
            self.failing = failing
        def send(self, configuration, payload, ttl):
            barrier.wait(timeout=3)
            if self.failing:
                raise RuntimeError('external transport failure')
            return SendResult('accepted')
    repo = MagicMock(spec=NotificationRepository)
    repo.config = NotificationsConfig(concurrency=2, rate_per_second=100)
    claims = [dict(delivery_id=str(i)) for i in range(2)]
    repo.claim.return_value = claims
    repo.prepare_send.side_effect = lambda claim: (Adapter(claim['delivery_id'] == '0'), {}, {}, 20)
    repo.finish.side_effect = lambda claim, result: seen.append((claim['delivery_id'], result.outcome))
    dispatcher = NotificationDispatcher(repo)
    await dispatcher.tick()
    assert set(seen) == {('0', 'retryable'), ('1', 'accepted')}
    repo.claim.assert_called_once_with(dispatcher.owner, 2)
    repo.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_start_asserts_schema_and_spawns_worker():
    repo = MagicMock(spec=NotificationRepository); repo.config = NotificationsConfig()
    dispatcher = NotificationDispatcher(repo)
    try:
        await dispatcher.start()
        repo.assert_schema.assert_called_once()
        assert dispatcher._task is not None
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_schema_failure_prevents_dispatcher_worker_start():
    repo = MagicMock(spec=NotificationRepository); repo.config = NotificationsConfig()
    repo.assert_schema.side_effect = RuntimeError('schema incompatible')
    dispatcher = NotificationDispatcher(repo)
    with pytest.raises(RuntimeError, match='schema incompatible'):
        await dispatcher.start()
    assert dispatcher._task is None
    repo.expand.assert_not_called()


@pytest.mark.asyncio
async def test_start_stop_reuses_one_worker_and_can_restart():
    repo = MagicMock(spec=NotificationRepository); repo.config = NotificationsConfig()
    dispatcher = NotificationDispatcher(repo)
    entered = asyncio.Event()
    async def run():
        entered.set()
        await dispatcher._stop.wait()
    dispatcher._run = run
    await dispatcher.start()
    first = dispatcher._task
    await entered.wait()
    await dispatcher.start()
    assert dispatcher._task is first
    await dispatcher.stop()
    assert first.done() and dispatcher._task is None
    entered.clear()
    await dispatcher.start()
    await entered.wait()
    assert dispatcher._task is not first
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_legacy_email_webhook_public_contract_stays_compatible():
    from src.services.notification_service import NotificationChannel, NotificationMessage, NotificationService
    service = NotificationService()
    email, webhook = AsyncMock(return_value=True), AsyncMock(return_value=False)
    service.register_channel(NotificationChannel.EMAIL, email)
    service.register_channel(NotificationChannel.WEBHOOK, webhook)
    messages = [NotificationMessage('test', 'fixture', 'low', 'fixture', channel)
                for channel in (NotificationChannel.EMAIL, NotificationChannel.WEBHOOK)]
    assert await service.send_batch(messages) == [True, False]
    email.assert_awaited_once_with(messages[0])
    webhook.assert_awaited_once_with(messages[1])
