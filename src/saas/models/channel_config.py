"""渠道配置相关数据模型"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


class ChannelConfigCreate(BaseModel):
    """创建渠道配置请求"""
    channel_type: str = Field(..., description="渠道类型：wecom/dingtalk/feishu")
    config: dict = Field(..., description="渠道凭证配置（JSON）")

    class Config:
        # 允许额外字段以适应不同渠道的配置
        extra = "allow"


class ChannelConfigResponse(BaseModel):
    """渠道配置响应"""
    config_id: str
    tenant_id: str
    channel_type: str
    config: dict  # 脱敏后的配置
    verified: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
