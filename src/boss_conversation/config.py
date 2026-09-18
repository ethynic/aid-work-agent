"""boss_conversation 场景配置（B1.3 骨架，默认关闭）。

场景 enabled/allowlist 与微信场景一样按**热读**（mtime+size 缓存，文件缺失/
损坏 fail-closed）：关闭 BOSS 场景须立即阻止注册/发布/分配/新决策等授权点。
进程快照 get_boss_conversation_config() 供组合根与诊断读取（改 yaml 重启生效；
门控判定一律走热读，不读快照）。

设计 §5.1：config.yaml `boss_conversation:` 节点含 enabled/tenant_allowlist
（敏感词表/resume 字段白名单随 B2 增补），热读门控照抄微信范式
（weixin_conversation/config.py）；双门控复用通用层 session_tasks.enabled 机制。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, NamedTuple, Optional


class BossConversationConfig(NamedTuple):
    """进程快照（组合根/诊断用；门控判定走热读函数，不读本快照）。"""

    enabled: bool = False
    tenant_allowlist: List[str] = []


class _GateHotConfig(NamedTuple):
    enabled: bool
    tenant_allowlist: List[str]


def _as_bool(value, default: bool = False) -> bool:  # noqa: ANN001
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _as_list(value) -> List[str]:  # noqa: ANN001
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _settings_node() -> dict:
    """读取 settings 的 boss_conversation 节，兼容 dict 与 _AttrDict 动态挂载形态
    （同 session_tasks/config._settings_node：只按 dict 判定会把已启用配置误读为
    关闭；两种形态统一归一为 dict）。settings 不可用时按默认关闭。"""
    try:
        from src.config.settings import settings

        node = getattr(settings, "boss_conversation", None)
        if isinstance(node, dict):
            return node
        if node is not None and hasattr(node, "__dict__"):
            return {k: v for k, v in vars(node).items()}
    except Exception:  # noqa: BLE001 fail-closed
        pass
    return {}


def get_boss_conversation_config() -> BossConversationConfig:
    """进程快照配置（settings.boss_conversation 为 yaml 动态挂载节，缺键回落默认关闭）。"""
    node = _settings_node()
    return BossConversationConfig(
        enabled=_as_bool(node.get("enabled"), False),
        tenant_allowlist=_as_list(node.get("tenant_allowlist")),
    )


def _parse_gate(path: Path) -> _GateHotConfig:
    try:
        from src.config.settings import load_yaml_config

        node = (load_yaml_config(path) or {}).get("boss_conversation")
        node = node if isinstance(node, dict) else {}
        return _GateHotConfig(enabled=_as_bool(node.get("enabled"), False),
                              tenant_allowlist=_as_list(node.get("tenant_allowlist")))
    except Exception:  # noqa: BLE001 fail-closed
        return _GateHotConfig(enabled=False, tenant_allowlist=[])


_gate_lock = threading.Lock()
_gate_cache = None  # (path, mtime_ns, size) -> _GateHotConfig


def _gate_yaml_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "config.yaml"


def _read_gate(config_path: Optional[str]) -> _GateHotConfig:
    """热读（mtime+size 缓存；文件缺失/解析失败 fail-closed 关闭）。"""
    global _gate_cache
    path = Path(config_path) if config_path else _gate_yaml_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return _GateHotConfig(enabled=False, tenant_allowlist=[])
    with _gate_lock:
        if _gate_cache is not None and _gate_cache[0] == key:
            return _gate_cache[1]
        parsed = _parse_gate(path)
        _gate_cache = (key, parsed)
        return parsed


def scenario_enabled_gate(config_path: Optional[str] = None) -> bool:
    """租户无关的场景总门控（注册/调度接线用：仅看 enabled，不看 allowlist）。

    boss_conversation.enabled 默认 false（含文件缺失/损坏），fail-closed。
    """
    return _read_gate(config_path).enabled


def tenant_allowed(tenant_id: str, config_path: Optional[str] = None) -> bool:
    """enabled + tenant_allowlist 热读联合判定（空 allowlist = 不限租户）。"""
    gate = _read_gate(config_path)
    if not gate.enabled:
        return False
    if not gate.tenant_allowlist:
        return True
    return tenant_id in gate.tenant_allowlist
