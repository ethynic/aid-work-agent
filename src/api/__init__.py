"""API modules load only when requested, without unrelated route side effects."""

from importlib import import_module

__all__ = ["auth", "session", "customer", "word"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    value = import_module("." + name, __name__)
    globals()[name] = value
    return value
