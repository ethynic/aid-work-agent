"""Run-scoped accounting for provider-reported LLM token usage."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Callable, Mapping


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.call_count += other.call_count

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


UsageRecorder = Callable[[str, str, TokenUsage], None]
_recorder: ContextVar[UsageRecorder | None] = ContextVar("llm_usage_recorder", default=None)
_association: ContextVar[str] = ContextVar("llm_usage_association", default="")
_stage: ContextVar[str] = ContextVar("llm_usage_stage", default="llm")


def install_usage_recorder(recorder: UsageRecorder | None):
    return _recorder.set(recorder)


def reset_usage_recorder(token) -> None:
    _recorder.reset(token)


def set_usage_context(*, association: str | None = None, stage: str | None = None):
    tokens = []
    if association is not None:
        tokens.append((_association, _association.set(association)))
    if stage is not None:
        tokens.append((_stage, _stage.set(stage)))
    return tokens


def reset_usage_context(tokens) -> None:
    for variable, token in reversed(tokens):
        variable.reset(token)


def normalize_usage(usage: object) -> TokenUsage | None:
    if not isinstance(usage, Mapping):
        return None

    def read(primary: str, alias: str | None = None, *, default: int | None = None):
        name = primary if primary in usage else alias if alias and alias in usage else None
        if name is None:
            return default
        value = usage[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        return value

    prompt = read("prompt_tokens", "input_tokens")
    cached = read("cached_tokens", "cached_input_tokens", default=0)
    completion = read("completion_tokens", "output_tokens")
    reported_total = read("total_tokens", default=0)
    call_count = read("call_count", default=1)
    if (
        prompt is None
        or cached is None
        or completion is None
        or reported_total is None
        or call_count is None
        or call_count < 1
        or cached > prompt
        or (prompt == 0 and cached == 0 and completion == 0 and reported_total == 0)
    ):
        return None
    return TokenUsage(
        input_tokens=prompt,
        cached_input_tokens=cached,
        output_tokens=completion,
        # Provider prompt/input already includes cached tokens. Recompute total
        # from input + output so cached input is never counted twice.
        total_tokens=prompt + completion,
        call_count=call_count,
    )


def record_usage(usage: object) -> TokenUsage | None:
    normalized = normalize_usage(usage)
    recorder = _recorder.get()
    if normalized is not None and recorder is not None:
        recorder(_association.get(), _stage.get(), normalized)
    return normalized


def record_response_usage(response: object) -> TokenUsage | None:
    return record_usage(response.get("usage") if isinstance(response, Mapping) else None)
