"""Agent 请求级扩展上下文。

HTTP、渠道等可信入口可在进入 Agent 前把领域参数转换为通用的提示词增强和
工具请求数据。Agent 只负责传递，不感知具体业务字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Tuple


def freeze_request_data(value: Any, _active_ids: set[int] | None = None) -> Any:
    """深度复制并冻结 JSON 风格的请求数据，避免调用方后续修改。"""
    if _active_ids is None:
        _active_ids = set()
    is_container = isinstance(value, (Mapping, list, tuple, set, frozenset))
    value_id = id(value)
    if is_container:
        if value_id in _active_ids:
            raise ValueError("request_data 不支持循环引用")
        _active_ids.add(value_id)
    try:
        return _freeze_request_data_value(value, _active_ids)
    finally:
        if is_container:
            _active_ids.remove(value_id)


def _freeze_request_data_value(value: Any, active_ids: set[int]) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("request_data 的 mapping key 必须是字符串")
        return MappingProxyType({
            key: freeze_request_data(item, active_ids)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(freeze_request_data(item, active_ids) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_request_data(item, active_ids) for item in value)
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    raise TypeError(f"请求上下文仅支持 JSON 风格数据，收到: {type(value).__name__}")


def _empty_request_data() -> Mapping[str, Any]:
    return MappingProxyType({})


def freeze_request_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """冻结请求数据根对象，并拒绝非 mapping 的误用。"""
    if not isinstance(value, Mapping):
        raise TypeError("request_data 必须是 mapping")
    return freeze_request_data(value)


@dataclass(frozen=True)
class AgentRequestContext:
    """单次 Agent 调用携带的不可变通用上下文。"""

    prompt_augmentations: Tuple[str, ...] = ()
    request_data: Mapping[str, Any] = field(default_factory=_empty_request_data)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prompt_augmentations",
            tuple(str(item) for item in self.prompt_augmentations),
        )
        object.__setattr__(self, "request_data", freeze_request_mapping(self.request_data))
