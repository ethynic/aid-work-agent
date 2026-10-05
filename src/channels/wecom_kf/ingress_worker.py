"""Explicit KF pull-intent worker; it records received facts, never runs Agent."""

import argparse
import asyncio
import uuid

from loguru import logger

from .api_client import WeComKfApiClient
from .client_pool import KfClientPool
from .ingress_auth import KfIngressError, bounded_page, text
from .ingress_repository import KfIngressRepository


async def _drain(task):
    """Keep actual offloaded/IO work alive until its own scope has finished."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve its result/exception before propagating the caller cancel.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def _thread(function, *args, **kwargs):
    return await _drain(asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)))


async def accept_callback(tenant_id, config_id, query, body, *, repository=None):
    """Called before the old callback's body/query/debug logging branch."""
    repo = repository or KfIngressRepository()
    return await _thread(repo.accept_callback, tenant_id, config_id, query, body)


class KfIngressWorker:
    def __init__(self, config, repository=None, client_factory=WeComKfApiClient):
        self.config = config
        self.repository = repository or KfIngressRepository()
        self.client_factory = client_factory
        self.client_pool = KfClientPool(client_factory=client_factory,
            max_entries=config.client_pool_capacity,
            idle_ttl_seconds=config.client_pool_idle_seconds)
        self.worker_id = "kf-pull-" + str(uuid.uuid4())
        self.after = 0
        self.stopping = False
        self._active = set()
        self._runs = set()

    async def _verify_account(self, client, proof):
        # This is the original SDK account API, outside SQL transactions.
        # Exact config credentials are captured, never mutable adapter context.
        for offset in range(0, 1000, 100):
            result = await client.account_list(offset=offset, limit=100)
            if not isinstance(result, dict) or type(result.get("errcode")) is not int or result["errcode"] != 0:
                raise KfIngressError("KF_INGRESS_ACCOUNT_NOT_VERIFIED")
            accounts = result.get("account_list")
            if not isinstance(accounts, list) or len(accounts) > 100 or any(not isinstance(item, dict) for item in accounts):
                raise KfIngressError("KF_INGRESS_ACCOUNT_NOT_VERIFIED")
            ids = [text(item.get("open_kfid")) for item in accounts]
            if len(set(ids)) != len(ids):
                raise KfIngressError("KF_INGRESS_ACCOUNT_NOT_VERIFIED")
            if proof.open_kfid in ids:
                return
            if len(accounts) < 100:
                break
        raise KfIngressError("KF_INGRESS_ACCOUNT_NOT_VERIFIED")

    async def _pull(self, lease):
        account = await _thread(self.repository.current_account, lease.proof)
        pooled = await self.client_pool.acquire(account)
        failed = True
        try:
            client = pooled.client
            client.enable_ingress_mode(self.config.page_bytes)
            async with asyncio.timeout(90):
                if pooled.needs_verify:
                    # First use of a brand-new pool entry repeats the original
                    # platform-side account check; same-key reuse skips it.
                    await self._verify_account(client, lease.proof)
                    self.client_pool.mark_verified(pooled)
                if self.stopping:
                    raise KfIngressError("KF_INGRESS_STOPPED")
                result = await client.sync_msg(open_kfid=lease.proof.open_kfid,
                    cursor=lease.cursor, limit=self.config.page_limit, voice_format=1)
                page = bounded_page(result, lease.proof, limit=self.config.page_limit,
                                    max_bytes=self.config.page_bytes)
                if self.stopping:
                    raise KfIngressError("KF_INGRESS_STOPPED")
                value = await _thread(self.repository.commit_page, lease, page)
                failed = False
                return value
        finally:
            # A completed page returns its client for bounded same-key reuse;
            # any failure discards the entry so the next page cold-starts its
            # token and account check. Original client owns its actual HTTP
            # connection and token cache. Caller cancellation does not abandon
            # the owned close or release a lease early.
            await self.client_pool.release(pooled, failed=failed)

    async def _heartbeat(self, lease):
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            await _thread(self.repository.renew, lease, self.config.lease_seconds)

    async def run_once(self):
        task = asyncio.current_task()
        self._runs.add(task)
        try:
            return await self._run_once()
        finally:
            self._runs.discard(task)

    async def _run_once(self):
        if not self.config.enabled or self.stopping:
            return None
        lease, self.after = await _thread(self.repository.claim, self.worker_id,
            after=self.after, lease_seconds=self.config.lease_seconds)
        if lease is None:
            return None
        operation = asyncio.create_task(self._pull(lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        self._active.update((operation, heartbeat))
        try:
            done, _ = await asyncio.wait((operation, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # No stale commit is authorized. Cancel/drain the read before
                # physical lease cleanup; the repository rechecks its epoch.
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                heartbeat.result()
            return operation.result()
        except (KfIngressError, TimeoutError) as error:
            logger.warning("KF pull stopped: code={} type={}",
                           getattr(error, "code", "KF_INGRESS_PULL_TIMEOUT"), type(error).__name__)
            return None
        finally:
            # Every branch, including caller cancellation, completes original
            # HTTP/offloaded scopes before releasing the account lease.
            operation.cancel(); heartbeat.cancel()
            async def cleanup():
                await asyncio.gather(operation, heartbeat, return_exceptions=True)
                await _thread(self.repository.release, lease)
            try:
                await _drain(asyncio.create_task(cleanup()))
            finally:
                self._active.difference_update((operation, heartbeat))

    async def run(self, *, max_pages=0):
        processed = 0
        try:
            while not self.stopping and (not max_pages or processed < max_pages):
                value = await self.run_once()
                if value is not None:
                    processed += 1
                else:
                    await asyncio.sleep(self.config.poll_seconds)
        finally:
            await self.close()

    async def close(self):
        self.stopping = True
        # A running read is owned by run_once; stopping new dispatch and waiting
        # its bounded original scope ensures no thread/HTTP is silently dropped.
        if self._runs - {asyncio.current_task()}:
            await _drain(asyncio.create_task(self._wait_active()))
        # Retained pool entries (token cache plus HTTP pool) are worker-owned
        # process state; shutdown discards every entry and closes its client.
        await self.client_pool.close()

    async def _wait_active(self):
        await asyncio.gather(*tuple(self._runs - {asyncio.current_task()}), return_exceptions=True)


async def _main(arguments):
    import signal
    from src.config.settings import settings
    from src.db.database import init_postgres_pool, close_postgres_pool
    config = settings.agent_runner.wecom_kf
    if not config.enabled:
        raise KfIngressError("KF_INGRESS_DISABLED")
    await _thread(init_postgres_pool)
    worker = KfIngressWorker(config)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: setattr(worker, "stopping", True))
    try:
        await worker.run(max_pages=arguments.max_pages)
    finally:
        await _thread(close_postgres_pool)


def main():
    parser = argparse.ArgumentParser(description="Record one bounded KF ingress page per account lease")
    parser.add_argument("--max-pages", type=int, default=0)
    arguments = parser.parse_args()
    if arguments.max_pages < 0:
        parser.error("max-pages must be nonnegative")
    asyncio.run(_main(arguments))


if __name__ == "__main__":
    main()
