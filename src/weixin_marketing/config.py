"""weixin_marketing 配置入口（R42：configs/config.yaml weixin_marketing 节直读）

settings 为 Pydantic extra=allow + _AttrDict 动态挂载：yaml 顶层 weixin_marketing 节
自动转为属性对象；缺省（节不存在/键缺失）回落到本文件默认值——未配置时行为等价
enabled=false，线上默认零行为变化。

证据模式（R43）：evidence_real_mode=true 时真实证据校验器缺失即 fail-closed
（validate_evidence 一律 False）；默认 false 仅做结构绑定校验，不宣称真实证据验证。
"""

from dataclasses import dataclass, field
from typing import List, Optional

from src.config.settings import settings
from src.weixin_marketing.constants import (
    DEFAULT_DISPATCH_BATCH_SIZE,
    DEFAULT_MAX_BLOCKS,
    DEFAULT_MIN_INTERVAL_SECONDS,
    DEFAULT_QUOTA_ACCOUNT_LIMIT,
    DEFAULT_QUOTA_TARGET_LIMIT,
    DEFAULT_QUOTA_TASK_LIMIT,
    DEFAULT_QUOTA_TENANT_LIMIT,
    DEFAULT_QUOTA_WINDOW_SECONDS,
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
    tenant_allowlist: List[str] = field(default_factory=list)
    quotas: WeixinQuotaConfig = field(default_factory=WeixinQuotaConfig)
    # 调度闭环 tick 间隔（R44；scheduler/manager.py 注册 APScheduler interval job 用）
    time_scan_interval_seconds: int = 5
    dispatch_interval_seconds: int = 5
    permits_sweep_interval_seconds: int = 30
    runs_reclaim_interval_seconds: int = 60


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
    )


def tenant_allowed(config: Optional[WeixinMarketingConfig], tenant_id: str) -> bool:
    """租户白名单：空列表 = 不限制（仍受 enabled 总门控）；非空 = 命中才放行"""
    if config is None:
        config = get_weixin_marketing_config()
    if not config.tenant_allowlist:
        return True
    return tenant_id in config.tenant_allowlist
