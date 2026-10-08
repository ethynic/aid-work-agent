"""Optional local-operation lifecycle port, installed by an execution owner.

This module does not select tools, devices, retry branches, prices or storage.
Legacy callers see no owner and keep their existing invocation service.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol


class LocalLifecycle(Protocol):
    async def authorize_operation(self): ...
    async def bind_invocation(self, *, branch, ordinal, tool_name, device, arguments,
                              provider_key=None, deadline_at=None,
                              authorization_epoch=None, execution_lane='standard'): ...
    async def saved_phase(self, *, branch, ordinal): ...
    async def saved_domain(self, *, branch, ordinal): ...
    async def start_domain(self, *, branch, ordinal, intent): ...
    async def commit_domain(self, *, branch, ordinal, intent, writer): ...
    async def commit_postprocess(self, *, branch, ordinal, intent, writer): ...
    async def observe_domain(self, *, branch, ordinal, intent, writer): ...
    async def model_phase(self, *, branch, ordinal, intent, invoke, purpose='llm'): ...
    # Persist an observed result only. Domain effects (fees/records/logs)
    # require the execution owner's cursor-only domain transaction, not a
    # separately committed DAL call followed by this observation.
    async def complete_phase(self, *, branch, ordinal, intent, result): ...


@dataclass(frozen=True)
class LocalOperationScope:
    owner: LocalLifecycle
    branch: str
    ordinal: int

    def __post_init__(self):
        if not isinstance(self.branch,str) or not self.branch or type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError('LOCAL_PHASE_INVALID')


_current = ContextVar('local_operation_lifecycle',default=None)


def current_local_operation():
    return _current.get()


@contextmanager
def local_operation_scope(owner, *, branch='main', ordinal=0):
    token = _current.set(LocalOperationScope(owner,branch,ordinal))
    try:
        yield
    finally:
        _current.reset(token)
