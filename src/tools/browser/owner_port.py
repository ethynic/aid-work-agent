"""Optional original-execution owner for the existing Browser runtime.

Only the application runtime installs this port. Tool arguments cannot select
an owner, endpoint, lease or completion authority; legacy calls have no port.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol


class BrowserOwnerFailure(RuntimeError):
    authoritative_storage_failure = True


def propagate_owner_failure(error):
    if getattr(error, 'authoritative_storage_failure', False):
        raise error


class BrowserExecutionOwner(Protocol):
    @property
    def runner_id(self) -> str: ...
    async def bind_run(self, manager, record): ...
    async def activate_run(self, manager, record): ...
    async def bind_wait(self, manager, record, assistance, suspension): ...
    async def authorize_agent_action(self): ...
    async def renew_browser_owner(self, tenant_id, run_id): ...
    async def record_state(self, tenant_id, run_id, state, **facts): ...
    async def close_browser_owner(self, tenant_id, run_id, state): ...
    async def record_completion(self, assistance, *, completed_by_human): ...
    async def authorize_human_completion(self, assistance_id): ...
    async def sample_human_completion(self, assistance_id, page_ops): ...
    def record_owner_failure(self, error): ...


_current_owner = ContextVar('browser_execution_owner', default=None)
_agent_action_owner = ContextVar('browser_agent_action_owner', default=None)
_human_action_guard = ContextVar('browser_human_action_guard', default=None)
_resource_close_guard = ContextVar('browser_resource_close_guard', default=None)


class HumanActionRejected(RuntimeError):
    """A current human permission denial before IPC; never an owner failure."""

    def __init__(self, code='HUMAN_ACTION_REJECTED'):
        self.code = code
        super().__init__(code)


class HumanActionOperation:
    """One original IPC command's guard and finite dispatch watermarks."""

    def __init__(self, guard):
        self.guard = guard
        self.write_started = False
        self.frame_written = False

    async def __call__(self):
        await self.guard()


def current_human_action_guard():
    return _human_action_guard.get()


def current_resource_close_guard():
    return _resource_close_guard.get()


@contextmanager
def browser_resource_close_scope(guard):
    token=_resource_close_guard.set(HumanActionOperation(guard))
    try:
        yield
    finally:
        _resource_close_guard.reset(token)


@contextmanager
def browser_human_action_scope(guard):
    token = _human_action_guard.set(guard)
    try:
        yield
    finally:
        _human_action_guard.reset(token)


def current_browser_execution_owner():
    return _current_owner.get()


def current_browser_agent_action_owner():
    return _agent_action_owner.get()


@contextmanager
def browser_agent_action_scope(owner):
    token = _agent_action_owner.set(owner)
    try:
        yield
    finally:
        _agent_action_owner.reset(token)


@contextmanager
def browser_execution_owner_scope(owner):
    token = _current_owner.set(owner)
    try:
        yield
    finally:
        _current_owner.reset(token)
