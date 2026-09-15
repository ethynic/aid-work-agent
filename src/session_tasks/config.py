"""session_tasks 配置（C1，计划 §4：默认关闭）。

两层读取（沿用 weixin_marketing 范式）：
- 进程快照 get_session_tasks_config()：调度节奏/租约等参数，改 yaml 重启生效；
- 授权门控热读 session_tasks_hot_gate()：enabled/tenant_allowlist 每调用 mtime+size
  缓存热读，文件缺失/损坏 fail-closed。覆盖路径：publish/resume/claim
  （新授权点）；renew/events/决策为迟到事实接纳路径不拦（设计 §8 在途回执
  不因旧 fence 被丢弃），control_task 放行以允许关闭后停止任务。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, NamedTuple, Optional

from .constants import DEFAULT_LEASE_SECONDS, DEFAULT_RENEW_SECONDS


@dataclass(frozen=True)
class SessionTasksConfig:
    enabled: bool = False
    tenant_allowlist: List[str] = field(default_factory=list)
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    renew_seconds: int = DEFAULT_RENEW_SECONDS  # 客户端续租节奏参考值，服务端不消费
    max_decisions_per_tenant: int = 2  # §13.5 DB 槽位（C3 已接线）
    events_max_records: int = 100
    events_max_bytes: int = 256 * 1024
    opening_enabled: bool = True
    # ----- C3 决策 worker（进程快照）-----
    decision_tick_seconds: int = 5
    decision_lease_seconds: int = 120
    decision_stale_seconds: int = 900
    decision_batch_limit: int = 5
    decision_model_max_tokens: int = 1000
    decision_reserve_units: float = 1.0
    execution_reserve_units: float = 0.0


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


def _settings_node(name: str) -> dict:
    """读取 settings 的额外配置节，兼容 dict 与 _AttrDict（yaml 动态挂载形态）。

    _AttrDict 是属性访问对象而非 dict 子类；只按 dict 判定会把已启用配置
    误读为关闭（设计评审 P1-1）。两种形态统一归一为 dict。
    """
    try:
        from src.config.settings import settings

        node = getattr(settings, name, None)
        if isinstance(node, dict):
            return node
        if node is not None and hasattr(node, "__dict__"):
            return {k: v for k, v in vars(node).items()}
    except Exception:  # noqa: BLE001 settings 不可用时按默认关闭
        pass
    return {}


def get_session_tasks_config() -> SessionTasksConfig:
    """进程快照配置（settings.session_tasks 为 yaml 动态挂载节，缺键回落默认）。"""
    node = _settings_node("session_tasks")
    return SessionTasksConfig(
        enabled=_as_bool(node.get("enabled"), False),
        tenant_allowlist=_as_list(node.get("tenant_allowlist")),
        lease_seconds=int(node.get("lease_seconds") or DEFAULT_LEASE_SECONDS),
        renew_seconds=int(node.get("renew_seconds") or DEFAULT_RENEW_SECONDS),
        max_decisions_per_tenant=int(node.get("max_decisions_per_tenant") or 2),
        events_max_records=int(node.get("events_max_records") or 100),
        events_max_bytes=int(node.get("events_max_bytes") or 256 * 1024),
        opening_enabled=_as_bool(node.get("opening_enabled"), True),
        decision_tick_seconds=int(node.get("decision_tick_seconds") or 5),
        decision_lease_seconds=int(node.get("decision_lease_seconds") or 120),
        decision_stale_seconds=int(node.get("decision_stale_seconds") or 900),
        decision_batch_limit=int(node.get("decision_batch_limit") or 5),
        decision_model_max_tokens=int(node.get("decision_model_max_tokens") or 1000),
        decision_reserve_units=float(node.get("decision_reserve_units") or 1.0),
        execution_reserve_units=float(node.get("execution_reserve_units") or 0.0),
    )


class _GateHotConfig(NamedTuple):
    enabled: bool
    tenant_allowlist: List[str]


_gate_lock = threading.Lock()
_gate_cache = None  # (path, mtime_ns, size) -> _GateHotConfig


def _gate_yaml_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "config.yaml"


def _parse_gate(path: Path) -> _GateHotConfig:
    try:
        from src.config.settings import load_yaml_config

        node = (load_yaml_config(path) or {}).get("session_tasks")
        node = node if isinstance(node, dict) else {}
        return _GateHotConfig(enabled=_as_bool(node.get("enabled"), False), tenant_allowlist=_as_list(node.get("tenant_allowlist")))
    except Exception:  # noqa: BLE001 fail-closed
        return _GateHotConfig(enabled=False, tenant_allowlist=[])


def session_tasks_hot_gate(config_path: Optional[str] = None) -> _GateHotConfig:
    """授权门控热读（发布/claim/授权路径每调用；mtime+size 缓存，语义同 weixin_marketing）。"""
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


def tenant_allowed(tenant_id: str) -> bool:
    """enabled + allowlist 联合判定（空 allowlist = 不限租户）。"""
    gate = session_tasks_hot_gate()
    if not gate.enabled:
        return False
    if not gate.tenant_allowlist:
        return True
    return tenant_id in gate.tenant_allowlist
