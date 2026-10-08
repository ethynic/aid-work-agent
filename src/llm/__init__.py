"""Transport implementations load only when explicitly requested."""

__all__ = ['LLMGateway','BaseLLMProvider']


def __getattr__(name):
    if name == 'LLMGateway':
        from .gateway import LLMGateway
        return LLMGateway
    if name == 'BaseLLMProvider':
        from .providers.base import BaseLLMProvider
        return BaseLLMProvider
    raise AttributeError(name)
