"""SaaS 数据模型"""

from .enums import (
    TenantStatus,
    SubscriptionStatus,
    PaymentStatus,
    AgentInstanceStatus,
    UserRole,
    PlanType,
)
from .tenant import TenantCreate, TenantUpdate, TenantResponse, TenantAdminCreate, TenantAdminResponse
from .subscription import SubscriptionCreate, SubscriptionResponse, PlanInfo
from .agent_instance import InstanceCreate, InstanceUpdate, InstanceResponse
from .channel_config import ChannelConfigCreate, ChannelConfigResponse
from .usage import UsageSummary, UserUsageDetail

__all__ = [
    # 枚举
    "TenantStatus",
    "SubscriptionStatus",
    "PaymentStatus",
    "AgentInstanceStatus",
    "UserRole",
    "PlanType",
    # 模型
    "TenantCreate", "TenantUpdate", "TenantResponse",
    "TenantAdminCreate", "TenantAdminResponse",
    "SubscriptionCreate", "SubscriptionResponse", "PlanInfo",
    "InstanceCreate", "InstanceUpdate", "InstanceResponse",
    "ChannelConfigCreate", "ChannelConfigResponse",
    "UsageSummary", "UserUsageDetail",
]
