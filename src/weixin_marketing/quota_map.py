"""微信发送配额 → desktop_automation quota scopes 映射（R41）

微信发送配额由场景映射 task/target/account scope（加 tenant 层），底座
quota_buckets 在许可事务内原子预留（R9/R19 固定顺序逐层加锁）。
scope_id 用场景前缀命名，避免与其他场景同 scope_type 撞桶。
"""

from typing import List, Optional

from src.desktop_automation.adapters import QuotaScopeSpec
from src.weixin_marketing.config import WeixinMarketingConfig, get_weixin_marketing_config


def tenant_scope(tenant_id: str, config: Optional[WeixinMarketingConfig] = None) -> QuotaScopeSpec:
    config = config or get_weixin_marketing_config()
    return QuotaScopeSpec(
        scope_type="tenant",
        scope_id=f"wxm:{tenant_id}",
        limit_count=config.quotas.tenant_limit,
        window_seconds=config.quotas.window_seconds,
    )


def task_scope(scenario_key: str, task_ref: str, config: Optional[WeixinMarketingConfig] = None) -> QuotaScopeSpec:
    config = config or get_weixin_marketing_config()
    return QuotaScopeSpec(
        scope_type="task",
        scope_id=f"wxm:{scenario_key}:{task_ref}",
        limit_count=config.quotas.task_limit,
        window_seconds=config.quotas.window_seconds,
    )


def target_scope(group_binding_id: str, config: Optional[WeixinMarketingConfig] = None) -> QuotaScopeSpec:
    config = config or get_weixin_marketing_config()
    return QuotaScopeSpec(
        scope_type="target",
        scope_id=f"wxm:gb:{group_binding_id}",
        limit_count=config.quotas.target_limit,
        window_seconds=config.quotas.window_seconds,
    )


def account_scope(account_binding_id: str, config: Optional[WeixinMarketingConfig] = None) -> QuotaScopeSpec:
    config = config or get_weixin_marketing_config()
    return QuotaScopeSpec(
        scope_type="account",
        scope_id=f"wxm:ab:{account_binding_id}",
        limit_count=config.quotas.account_limit,
        window_seconds=config.quotas.window_seconds,
    )


def build_quota_scopes(
    *,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    group_binding_id: str,
    account_binding_id: Optional[str],
    config: Optional[WeixinMarketingConfig] = None,
) -> List[QuotaScopeSpec]:
    """一次微信发送动作的完整额度层级（tenant/task/target/account）。

    account_binding_id 缺失（群绑定未关联账号）时降级为三层——预留语义仍完整，
    账号层限流在补齐关联后生效。
    """
    scopes = [
        tenant_scope(tenant_id, config),
        task_scope(scenario_key, task_ref, config),
        target_scope(group_binding_id, config),
    ]
    if account_binding_id:
        scopes.append(account_scope(account_binding_id, config))
    return scopes
