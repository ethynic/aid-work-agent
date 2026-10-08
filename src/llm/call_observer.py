"""Optional request-scoped observation port at a physical provider-call boundary.

The model client owns transport; the application supplies durable observation.
No service, database, billing or identity implementation is imported here.
"""

from contextvars import ContextVar
from contextlib import contextmanager
from typing import Protocol


class CallObserver(Protocol):
    async def call(self, method, *, provider, model, kwargs, owner, purpose): ...
    def call_sync(self, method, *, provider, model, kwargs, owner, purpose, normalize): ...


_observer = ContextVar('provider_call_observer', default=None)
_purpose = ContextVar('provider_call_purpose', default=None)


@contextmanager
def provider_call_purpose(purpose):
    """Application-only classification; never read from model/request arguments."""
    token = _purpose.set(purpose)
    try:
        yield
    finally:
        _purpose.reset(token)


def install_call_observer(observer):
    return _observer.set(observer)


def reset_call_observer(token):
    _observer.reset(token)


def prepare_child_environment(environment, context=None):
    observer = _observer.get()
    prepare = getattr(observer,'prepare_environment',None)
    if prepare is not None:
        prepare(environment,context)


def observe_child_exit(returncode):
    observer = _observer.get()
    check = getattr(observer,'child_exit',None)
    if check is not None:
        check(returncode)


def prepare_child_process(environment):
    callback = getattr(_observer.get(),'prepare_process',None)
    if callback is not None:
        callback(environment)


def register_child_process(process_id, environment):
    callback = getattr(_observer.get(),'register_process',None)
    if callback is not None:
        callback(process_id,environment)


def release_child_process(environment):
    callback = getattr(_observer.get(),'release_process',None)
    if callback is not None:
        callback(environment)


async def observed_call(method, *, provider, model, kwargs, owner='llm', purpose='llm'):
    observer = _observer.get()
    if observer is None:
        return await method(**kwargs)
    return await observer.call(method, provider=provider, model=model, kwargs=kwargs,
                               owner=owner, purpose=_purpose.get() if owner=='llm' and _purpose.get() else purpose)


def observed_call_sync(method, *, provider, model, kwargs, normalize, owner='llm', purpose='llm'):
    observer = _observer.get()
    if observer is None:
        return method(**kwargs)
    return observer.call_sync(method, provider=provider, model=model, kwargs=kwargs,
                              normalize=normalize, owner=owner,
                              purpose=_purpose.get() if owner=='llm' and _purpose.get() else purpose)
