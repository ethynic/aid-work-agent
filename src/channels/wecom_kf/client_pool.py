"""Bounded in-process reuse of original KF SDK clients, keyed per account proof.

Each entry keeps one original client (with its token cache and HTTP pool) for
one (account_id, config_version) key while the current config still carries the
exact corp_id/secret the entry was built from. This is a process-bounded cache
only: no cross-process or durable token sharing is claimed or implemented.

Entries are discarded on key/credential mismatch, page failure (including the
SDK's errcode=-1 HTTP-level failures, for which the client keeps its token),
capacity pressure, idle TTL expiry, and worker close.
"""
import asyncio
import time
from dataclasses import dataclass, field

from .ingress_auth import KfIngressError


async def _drain(task):
    """Keep an owned close alive until its own scope has finished."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve failures without letting them replace caller cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


@dataclass
class _PoolEntry:
    key: tuple
    corp_id: str
    secret: str = field(repr=False)
    client: object = field(repr=False)
    last_used: float
    verified: bool = False
    in_use: bool = True


class PooledClient:
    """One acquire handle; stored credentials never leave the pool entry."""

    __slots__ = ('entry',)

    def __init__(self, entry):
        self.entry = entry

    @property
    def client(self):
        return self.entry.client

    @property
    def needs_verify(self):
        """Only the first page of a brand-new entry repeats the account check."""
        return not self.entry.verified


class KfClientPool:
    """Reuse original clients per (account_id, config_version), bounded."""

    def __init__(self, *, client_factory, max_entries=16, idle_ttl_seconds=300.0,
                 clock=time.monotonic):
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("KF_INGRESS_POOL_CAPACITY_INVALID")
        if type(idle_ttl_seconds) not in (int, float) or not 0 < idle_ttl_seconds <= 86400:
            raise ValueError("KF_INGRESS_POOL_TTL_INVALID")
        self.client_factory = client_factory
        self.max_entries = max_entries
        self.idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self._entries = {}
        self._closed = False

    async def acquire(self, account):
        """Take the entry for a fresh current-account row from the repository."""
        if self._closed:
            raise KfIngressError("KF_INGRESS_POOL_CLOSED")
        await self._sweep()
        proof = account.proof
        key = (proof.account_id, proof.config_version)
        entry = self._entries.get(key)
        if entry is not None and (entry.corp_id != proof.corp_id or entry.secret != account.secret):
            # Hard credential check: config_version gates reuse, but a changed
            # secret/corp_id under an unchanged updated_at must never reuse it.
            await self._discard(entry)
            entry = None
        if entry is None:
            await self._make_room()
            entry = _PoolEntry(key, proof.corp_id, account.secret,
                               self.client_factory(proof.corp_id, account.secret),
                               self._clock())
            self._entries[key] = entry
        entry.in_use = True
        entry.last_used = self._clock()
        return PooledClient(entry)

    def mark_verified(self, pooled):
        """The entry's first full platform account check has succeeded."""
        pooled.entry.verified = True

    async def release(self, pooled, *, failed):
        """Return an entry for bounded reuse, or discard it after page failure."""
        entry = pooled.entry
        entry.in_use = False
        if not failed and self._entries.get(entry.key) is entry:
            entry.last_used = self._clock()
            await self._sweep()
        else:
            # A failed page leaves token/HTTP state unproven (the SDK keeps its
            # cached token on errcode=-1 HTTP-level failures), so drop the entry.
            await self._discard(entry)

    async def close(self):
        """Worker shutdown discards every entry and closes its client."""
        self._closed = True
        entries = tuple(self._entries.values())
        self._entries.clear()
        if not entries:
            return
        clients = [entry.client for entry in entries]

        async def close_all():
            results = await asyncio.gather(*(client.close() for client in clients),
                                           return_exceptions=True)
            failures = [result for result in results
                        if isinstance(result, BaseException)
                        and not isinstance(result, asyncio.CancelledError)]
            if failures:
                raise failures[0]

        await _drain(asyncio.create_task(close_all()))

    async def _sweep(self):
        now = self._clock()
        expired = [entry for entry in self._entries.values()
                   if not entry.in_use and now - entry.last_used > self.idle_ttl_seconds]
        for entry in expired:
            await self._discard(entry)

    async def _make_room(self):
        while len(self._entries) >= self.max_entries:
            idle = [entry for entry in self._entries.values() if not entry.in_use]
            candidates = idle or list(self._entries.values())
            victim = min(candidates, key=lambda entry: entry.last_used)
            del self._entries[victim.key]
            if not victim.in_use:
                await self._close_client(victim.client)
            # An in-use victim is only detached from the pool dict; its page
            # still owns the client and its release() closes it. The dict size,
            # not the use count, is the bound.

    async def _discard(self, entry):
        if self._entries.get(entry.key) is entry:
            del self._entries[entry.key]
        await self._close_client(entry.client)

    async def _close_client(self, client):
        await _drain(asyncio.create_task(client.close()))
