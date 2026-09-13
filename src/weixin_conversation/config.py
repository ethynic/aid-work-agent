"""weixin_conversation 场景配置（C1：骨架开关，默认关闭）。

场景 enabled/allowlist 与 session_tasks 一样按**热读**（mtime+size 缓存，文件
缺失/损坏 fail-closed）：关闭微信场景须立即阻止发布/分配/新决策等授权点，
迟到事实接纳不受影响（设计评审 P2-5）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, NamedTuple, Optional


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
