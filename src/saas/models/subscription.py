"""订阅与计费相关数据模型"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class PlanInfo(BaseModel):
    """套餐信息"""
    name: str = Field(..., description="套餐名称：basic/standard/premium")
    display_name: str = Field(..., description="显示名称")
    price: float = Field(..., description="月费（元）")
    token_quota: int = Field(..., description="Token 配额（-1 表示不限量）")
    max_instances: int = Field(..., description="最大实例数")
    max_users: int = Field(..., description="最大用户数")


# 预定义套餐
AVAILABLE_PLANS: dict[str, PlanInfo] = {
    "basic": PlanInfo(
        name="basic",
        display_name="基础版",
        price=2000,
        token_quota=200_000_000,  # 2 亿 token
        max_instances=3,
        max_users=30,
    ),
    "standard": PlanInfo(
        name="standard",
        display_name="标准版",
        price=5000,
        token_quota=500_000_000,
        max_instances=10,
        max_users=100,
    ),
    "premium": PlanInfo(
        name="premium",
        display_name="旗舰版",
        price=10000,
        token_quota=-1,  # 不限量
        max_instances=50,
        max_users=500,
    ),
}


class SubscriptionCreate(BaseModel):
    """创建订阅请求"""
    tenant_id: str = Field(..., description="租户 ID")
    plan: str = Field("basic", description="套餐名称")
    subagent_type: Optional[str] = Field(None, description="关联的子智能体类型")
    billing_cycle: str = Field("monthly", description="计费周期：monthly/yearly")


class SubscriptionResponse(BaseModel):
    """订阅响应"""
    subscription_id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    subagent_type: Optional[str] = None
    billing_cycle: str
    unit_price: float
    token_quota: int
    tokens_used: int = 0
    status: str
    payment_status: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None


class TokenUsageResponse(BaseModel):
    """Token 用量响应"""
    subscription_id: str
    token_quota: int
    tokens_used: int
    tokens_remaining: int
    usage_percent: float
    status: str
