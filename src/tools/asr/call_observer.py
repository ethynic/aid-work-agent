"""Opt-in physical ASR boundary. No application, channel or database imports."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol


class AsrCallUnknown(RuntimeError):
    """A started operation has no trustworthy result; it must not be replayed."""


class AsrCallObserver(Protocol):
    async def before_post(self, *, audio_format: str, audio_size: int, sample_rate: int): ...
    async def known(self, *, success: bool, text: str, status: int): ...
    async def unknown(self, code: str): ...


_observer = ContextVar('asr_physical_observer', default=None)


def current_asr_observer():
    return _observer.get()


@contextmanager
def asr_call_observer(observer):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)
