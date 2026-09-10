"""weixin_marketing 配置入口（R42：configs/config.yaml weixin_marketing 节直读）

settings 为 Pydantic extra=allow + _AttrDict 动态挂载：yaml 顶层 weixin_marketing 节
自动转为属性对象；缺省（节不存在/键缺失）回落到本文件默认值——未配置时行为等价
enabled=false，线上默认零行为变化。**settings 是进程 import 时快照**：tick 节奏/
配额等消费点改 yaml 需重启生效；授权门控（enabled/tenant_allowlist）例外——经
get_hot_gate_config 每调用热读（mtime 缓存，见其 docstring 选型说明）。

证据模式（R43）：evidence_real_mode=true 时真实证据校验器缺失即 fail-closed
（validate_evidence 一律 False）；默认 false 仅做结构绑定校验，不宣称真实证据验证。
"""

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, NamedTuple, Optional

from src.config.settings import settings
from src.weixin_marketing.constants import (
    DEFAULT_ASSET_MAX_BYTES,
    DEFAULT_ASSET_MAX_PIXELS,
    DEFAULT_ASSETS_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_ASSETS_ORPHAN_SCAN_ENABLED,
    DEFAULT_ASSETS_ORPHAN_SCAN_INTERVAL_SECONDS,
    DEFAULT_DATA_RETENTION_DAYS,
    DEFAULT_DISPATCH_BATCH_SIZE,
    DEFAULT_MAX_BLOCKS,
    DEFAULT_MIN_INTERVAL_SECONDS,
    DEFAULT_QUOTA_ACCOUNT_LIMIT,
    DEFAULT_QUOTA_TARGET_LIMIT,
    DEFAULT_QUOTA_TASK_LIMIT,
    DEFAULT_QUOTA_TENANT_LIMIT,
    DEFAULT_QUOTA_WINDOW_SECONDS,
    DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_RETENTION_DAYS,
)


@dataclass(frozen=True)
class WeixinQuotaConfig:
    """微信发送配额（映射 desktop_automation quota scopes，R41）"""

    window_seconds: int = DEFAULT_QUOTA_WINDOW_SECONDS
    tenant_limit: int = DEFAULT_QUOTA_TENANT_LIMIT
    task_limit: int = DEFAULT_QUOTA_TASK_LIMIT
    target_limit: int = DEFAULT_QUOTA_TARGET_LIMIT
    account_limit: int = DEFAULT_QUOTA_ACCOUNT_LIMIT


@dataclass(frozen=True)
class WeixinMarketingConfig:
    """weixin_marketing 节冻结视图（get_weixin_marketing_config 每次现读）"""

    enabled: bool = False
    time_triggers_enabled: bool = True
    event_triggers_enabled: bool = False
    images_enabled: bool = False
    evidence_real_mode: bool = False
    max_blocks: int = DEFAULT_MAX_BLOCKS
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS
    dispatch_batch_size: int = DEFAULT_DISPATCH_BATCH_SIZE
    retention_days: int = DEFAULT_RETENTION_DAYS
    # P4-A 素材通道（R57）：上传约束与过期清理节奏
    asset_max_bytes: int = DEFAULT_ASSET_MAX_BYTES
    asset_max_pixels: int = DEFAULT_ASSET_MAX_PIXELS
    assets_cleanup_interval_seconds: int = DEFAULT_ASSETS_CLEANUP_INTERVAL_SECONDS
    # P5 R59③：payloads/occurrences 保留期清理与磁盘孤儿素材扫描（默认关）
    data_retention_days: int = DEFAULT_DATA_RETENTION_DAYS
    retention_cleanup_interval_seconds: int = DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS
    assets_orphan_scan_enabled: bool = DEFAULT_ASSETS_ORPHAN_SCAN_ENABLED
    assets_orphan_scan_interval_seconds: int = DEFAULT_ASSETS_ORPHAN_SCAN_INTERVAL_SECONDS
    tenant_allowlist: List[str] = field(default_factory=list)
    quotas: WeixinQuotaConfig = field(default_factory=WeixinQuotaConfig)
    # 调度闭环 tick 间隔（R44；scheduler/manager.py 注册 APScheduler interval job 用）
    time_scan_interval_seconds: int = 5
    dispatch_interval_seconds: int = 5
    permits_sweep_interval_seconds: int = 30
    runs_reclaim_interval_seconds: int = 60
    # P4-B 事件闭环（R57）：匹配 worker tick 与 webhook 接纳约束
    event_match_interval_seconds: int = 5
    event_match_batch_size: int = 100
    webhook_rate_limit_per_minute: int = 120
    webhook_max_body_bytes: int = 256 * 1024
    webhook_key_rotate_window_seconds: int = 900


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def get_weixin_marketing_config() -> WeixinMarketingConfig:
    """从 settings.weixin_marketing（yaml extra 节）读取；缺键回落默认值"""
    node = getattr(settings, "weixin_marketing", None)
    if node is None:
        return WeixinMarketingConfig()
    getter = node.get if isinstance(node, dict) else (lambda k, d=None: getattr(node, k, d))
    quotas_node = getter("quotas", None)
    q_getter = (
        (lambda k, d=None: quotas_node.get(k, d)) if isinstance(quotas_node, dict)
        else (lambda k, d=None: getattr(quotas_node, k, d))
    )
    quotas = WeixinQuotaConfig(
        window_seconds=_as_int(q_getter("window_seconds", None), DEFAULT_QUOTA_WINDOW_SECONDS),
        tenant_limit=_as_int(q_getter("tenant_limit", None), DEFAULT_QUOTA_TENANT_LIMIT),
        task_limit=_as_int(q_getter("task_limit", None), DEFAULT_QUOTA_TASK_LIMIT),
        target_limit=_as_int(q_getter("target_limit", None), DEFAULT_QUOTA_TARGET_LIMIT),
        account_limit=_as_int(q_getter("account_limit", None), DEFAULT_QUOTA_ACCOUNT_LIMIT),
    )
    return WeixinMarketingConfig(
        enabled=_as_bool(getter("enabled", None), False),
        time_triggers_enabled=_as_bool(getter("time_triggers_enabled", None), True),
        event_triggers_enabled=_as_bool(getter("event_triggers_enabled", None), False),
        images_enabled=_as_bool(getter("images_enabled", None), False),
        evidence_real_mode=_as_bool(getter("evidence_real_mode", None), False),
        max_blocks=_as_int(getter("max_blocks", None), DEFAULT_MAX_BLOCKS),
        min_interval_seconds=_as_int(getter("max_interval_frequency", None), DEFAULT_MIN_INTERVAL_SECONDS),
        dispatch_batch_size=_as_int(getter("dispatch_batch_size", None), DEFAULT_DISPATCH_BATCH_SIZE),
        retention_days=_as_int(getter("retention_days", None), DEFAULT_RETENTION_DAYS),
        asset_max_bytes=_as_int(getter("asset_max_bytes", None), DEFAULT_ASSET_MAX_BYTES),
        asset_max_pixels=_as_int(getter("asset_max_pixels", None), DEFAULT_ASSET_MAX_PIXELS),
        assets_cleanup_interval_seconds=_as_int(
            getter("assets_cleanup_interval_seconds", None),
            DEFAULT_ASSETS_CLEANUP_INTERVAL_SECONDS,
        ),
        data_retention_days=_as_int(
            getter("data_retention_days", None), DEFAULT_DATA_RETENTION_DAYS
        ),
        retention_cleanup_interval_seconds=_as_int(
            getter("retention_cleanup_interval_seconds", None),
            DEFAULT_RETENTION_CLEANUP_INTERVAL_SECONDS,
        ),
        assets_orphan_scan_enabled=_as_bool(
            getter("assets_orphan_scan_enabled", None),
            DEFAULT_ASSETS_ORPHAN_SCAN_ENABLED,
        ),
        assets_orphan_scan_interval_seconds=_as_int(
            getter("assets_orphan_scan_interval_seconds", None),
            DEFAULT_ASSETS_ORPHAN_SCAN_INTERVAL_SECONDS,
        ),
        tenant_allowlist=_as_list(getter("tenant_allowlist", None)),
        quotas=quotas,
        time_scan_interval_seconds=_as_int(
            getter("time_scan_interval_seconds", None), 5
        ),
        dispatch_interval_seconds=_as_int(
            getter("dispatch_interval_seconds", None), 5
        ),
        permits_sweep_interval_seconds=_as_int(
            getter("permits_sweep_interval_seconds", None), 30
        ),
        runs_reclaim_interval_seconds=_as_int(
            getter("runs_reclaim_interval_seconds", None), 60
        ),
        event_match_interval_seconds=_as_int(
            getter("event_match_interval_seconds", None), 5
        ),
        event_match_batch_size=_as_int(getter("event_match_batch_size", None), 100),
        webhook_rate_limit_per_minute=_as_int(
            getter("webhook_rate_limit_per_minute", None), 120
        ),
        webhook_max_body_bytes=_as_int(getter("webhook_max_body_bytes", None), 256 * 1024),
        webhook_key_rotate_window_seconds=_as_int(
            getter("webhook_key_rotate_window_seconds", None), 900
        ),
    )


def tenant_allowed(config: Optional[WeixinMarketingConfig], tenant_id: str) -> bool:
    """租户白名单：空列表 = 不限制（仍受 enabled 总门控）；非空 = 命中才放行"""
    if config is None:
        config = get_weixin_marketing_config()
    if not config.tenant_allowlist:
        return True
    return tenant_id in config.tenant_allowlist


# ==================== 授权门控热读（P5 增量复核：真热读，非 settings 快照）====================


class GateHotConfig(NamedTuple):
    """授权门控热读结果（仅 enabled/tenant_allowlist 两个门控键）"""

    enabled: bool
    tenant_allowlist: List[str]


def _gate_yaml_path() -> Path:
    """门控热读的 yaml 路径（与 settings.load_yaml_config 缺省同源：仓库 configs/config.yaml）"""
    return Path(__file__).resolve().parents[2] / "configs" / "config.yaml"


_gate_lock = threading.Lock()
# (path_str, mtime_ns, size) -> GateHotConfig；进程内缓存，mtime+size 变则重建
_gate_cache = None


def _parse_gate_from_yaml(path: Path) -> GateHotConfig:
    """直接解析 yaml 的 weixin_marketing 门控键（与 load_yaml_config 同一解析链，
    含 env 替换；文件缺失/不可解析 → enabled=False fail-closed）"""
    try:
        from src.config.settings import load_yaml_config

        node = (load_yaml_config(path) or {}).get("weixin_marketing")
        node = node if isinstance(node, dict) else {}
        return GateHotConfig(
            enabled=_as_bool(node.get("enabled"), False),
            tenant_allowlist=_as_list(node.get("tenant_allowlist")),
        )
    except Exception:  # noqa: BLE001 yaml 损坏/IO 异常：门控 fail-closed（拒新授权）
        return GateHotConfig(enabled=False, tenant_allowlist=[])


def get_hot_gate_config(config_path: Optional[str] = None) -> GateHotConfig:
    """授权门控每调用热读（P5 增量复核必修①）。

    选型：mtime+size 轻量缓存而非每调用 create_settings()——后者为 yaml 全解析 +
    数十项 env 覆盖链 + Pydantic Settings 全量构造，逐许可调用直读开销不可接受；
    热路径仅一次 os.stat（微秒级），文件变化后下一次调用即见新值（免重启），
    未变时零解析开销。与「直接重读」的等价性由缓存键 (path, mtime_ns, size) 保证：
    仅同一文件在**同一纳秒内**完成两次写且 size 恰不变时可能读到旧值——常见文件
    系统 mtime_ns 粒度与运维编辑节奏下不可达（等价性测试固化：变更后/未变时
    热读结果均与 fresh 解析一致）。

    失败语义：文件不可达/损坏 → enabled=False（fail-closed 拒新授权）。
    仅授权门控路径使用（adapters.authorize_operation）；tick/配额等消费点仍读
    进程内 settings 快照（改 yaml 重启生效），避免全局面性能影响。
    """
    global _gate_cache
    path = Path(config_path) if config_path else _gate_yaml_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return GateHotConfig(enabled=False, tenant_allowlist=[])
    with _gate_lock:
        if _gate_cache is not None and _gate_cache[0] == key:
            return _gate_cache[1]
        parsed = _parse_gate_from_yaml(path)
        _gate_cache = (key, parsed)
        return parsed
