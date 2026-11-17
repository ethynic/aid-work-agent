"""微信云端自动回复场景配置。

微信消息采集现仅在本机运行。旧部署配置里的 enabled 不再授予云端自动回复权限。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, NamedTuple, Optional

CLOUD_REPLY_RETIRED = True


class _ScenarioGate(NamedTuple):
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


def _parse_gate(path: Path) -> _ScenarioGate:
    try:
        from src.config.settings import load_yaml_config

        node = (load_yaml_config(path) or {}).get("weixin_conversation")
        node = node if isinstance(node, dict) else {}
        return _ScenarioGate(enabled=_as_bool(node.get("enabled"), False),
                             tenant_allowlist=_as_list(node.get("tenant_allowlist")))
    except Exception:  # noqa: BLE001 fail-closed
        return _ScenarioGate(enabled=False, tenant_allowlist=[])


_gate_lock = threading.Lock()
_gate_cache = None  # (path, mtime_ns, size) -> _ScenarioGate


def _gate_yaml_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "config.yaml"


def scenario_enabled(tenant_id: str, config_path: Optional[str] = None) -> bool:
    """场景门控热读（每调用；缓存键 mtime+size，语义同 session_tasks_hot_gate）。

    enabled=false 或租户不在 allowlist → False（fail-closed）。
    """
    if CLOUD_REPLY_RETIRED:
        return False
    global _gate_cache
    path = Path(config_path) if config_path else _gate_yaml_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    with _gate_lock:
        if _gate_cache is not None and _gate_cache[0] == key:
            gate = _gate_cache[1]
        else:
            gate = _parse_gate(path)
            _gate_cache = (key, gate)
    if not gate.enabled:
        return False
    if not gate.tenant_allowlist:
        return True
    return tenant_id in gate.tenant_allowlist


def scenario_enabled_gate(config_path: Optional[str] = None) -> bool:
    """租户无关的场景总门控（注册/调度接线用：仅看 enabled，不看 allowlist）。"""
    if CLOUD_REPLY_RETIRED:
        return False
    global _gate_cache
    path = Path(config_path) if config_path else _gate_yaml_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    with _gate_lock:
        if _gate_cache is not None and _gate_cache[0] == key:
            gate = _gate_cache[1]
        else:
            gate = _parse_gate(path)
            _gate_cache = (key, gate)
    return gate.enabled
