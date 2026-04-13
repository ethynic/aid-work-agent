"""智能体实例相关数据模型"""

from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class InstanceCreate(BaseModel):
    """创建实例请求"""
    subagent_type: str = Field(..., description="子智能体类型（对应 subagents 目录名）")
    display_name: str = Field(..., min_length=1, max_length=50, description="实例显示名称")
    subscription_id: Optional[str] = Field(None, description="关联的订阅 ID（创建时自动生成）")
    config: Optional[dict] = Field(None, description="实例自定义配置")
    bound_channel_type: Optional[str] = Field(None, description="绑定的渠道类型：wecom/dingtalk/feishu")
    allowed_skills: Optional[List[str]] = Field(None, description="允许的 Skill 列表")


class InstanceUpdate(BaseModel):
    """更新实例请求"""
    display_name: Optional[str] = Field(None, max_length=50, description="显示名称")
    config: Optional[dict] = Field(None, description="自定义配置")
    bound_channel_type: Optional[str] = Field(None, description="绑定的渠道类型")
    allowed_skills: Optional[List[str]] = Field(None, description="允许的 Skill 列表")


class InstanceResponse(BaseModel):
    """实例响应"""
    instance_id: str
    tenant_id: str
    subscription_id: Optional[str] = None
    subagent_type: str
    display_name: str
    status: str  # running/stopped
    config: Optional[dict] = None
    bound_channel_type: Optional[str] = None
    allowed_skills: Optional[List[str]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
