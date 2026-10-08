"""Source admission values and ports. No platform or transport implementation."""

import hashlib
import json
import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from src.core.agent_engine.contracts import CheckpointFailure
from .contracts import RunnerError


@dataclass(frozen=True)
class SourceLocator:
    source: str
    account_id: str
    namespace: str
    message_id: str

    def __post_init__(self):
        if self.source not in {'wecom_kf','feishu','dingtalk'}:
            raise RunnerError('SOURCE_INPUT_UNSUPPORTED', 409)
        if any(not isinstance(v,str) or not v or len(v.encode())>256 or '\x00' in v
               for v in (self.account_id,self.namespace,self.message_id)):
            raise RunnerError('INVALID_SOURCE_RECEIPT', 422)

    def value(self):
        return dict(source=self.source,account_id=self.account_id,
                    namespace=self.namespace,message_id=self.message_id)

    @property
    def stable_key(self):
        return 'source_' + hashlib.sha256(json.dumps(self.value(),sort_keys=True,
            separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class SourceBatch:
    """An ordered bounded receipt set, not a caller supplied source proof."""
    members: tuple[SourceLocator, ...]

    def __post_init__(self):
        if (not isinstance(self.members, tuple) or not 1 <= len(self.members) <= 32
                or any(not isinstance(member, SourceLocator) for member in self.members)
                or len({member.stable_key for member in self.members}) != len(self.members)
                or len({member.source for member in self.members}) != 1):
            raise RunnerError('INVALID_SOURCE_BATCH', 422)

    @property
    def stable_key(self):
        return 'batch_' + hashlib.sha256(json.dumps(
            [member.value() for member in self.members], sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()

    def value(self):
        return [member.value() for member in self.members]


class LocalPreparationFailed(RuntimeError):
    """A completed non-paid local/media preflight has a known failure."""


class SourceUnavailable(CheckpointFailure):
    """Lost source authority preserves the original execution and resources."""
    authoritative_storage_failure = True
    public_verification = '渠道来源或当前服务状态尚待核对，任务与原消息已保留。'


@dataclass(frozen=True)
class PreparedSource:
    locator: SourceLocator
    provenance: dict = field(repr=False)
    observed_at: object = field(repr=False)


@dataclass(frozen=True)
class PreparationCandidate:
    fact: dict = field(repr=False)
    recognition: str | None = field(repr=False)
    existing: dict | None = field(repr=False)


class SourceReceiptPort(Protocol):
    async def prepare(self, locator, service_id): ...
    def authorize_in_tx(self, cursor, locator, service_id, *, prepared=None): ...
    def authorize_row_in_tx(self, cursor, row, credentials=None, *, execute=False): ...
    def requires_receipt(self, source, service_id, *, purpose, row=None): ...
    def link_in_tx(self, cursor, locator, input_ref): ...
    async def prepare_inputs(self, row, attempt, dispatch_check): ...
    async def prepare_accepted(self, locator, service_id): ...


async def source_offload(function,*args,**kwargs):
    """A cancelled HTTP observer cannot abandon its actual owned SQL thread."""
    task=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
    cancelled=False
    while not task.done():
        try: await asyncio.shield(task)
        except asyncio.CancelledError: cancelled=True
        except Exception: break
    if cancelled:
        task.exception()
        raise asyncio.CancelledError
    return task.result()
