"""租户相关数据模型"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ============== 租户 ==============

class TenantCreate(BaseModel):
    """创建租户请求"""
    company_name: str = Field(..., min_length=1, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")
    plan: str = Field("basic", description="套餐：basic/standard/premium")
    max_instances: Optional[int] = Field(None, description="最大实例数，None 使用默认值")
    max_users: Optional[int] = Field(None, description="最大用户数，None 使用默认值")


class TenantUpdate(BaseModel):
    """更新租户请求"""
    company_name: Optional[str] = Field(None, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")
    plan: Optional[str] = Field(None, description="套餐：basic/standard/premium")
    status: Optional[int | str] = Field(None, description="状态：active(1)/suspended(0)/deactivated(-1)")
    max_instances: Optional[int] = Field(None, description="最大实例数")
    max_users: Optional[int] = Field(None, description="最大用户数")


class TenantResponse(BaseModel):
    """租户响应"""
    tenant_id: str
    company_name: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    status: str
    plan: str
    max_instances: int
    max_users: int
    settings: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ============== 租户管理员 ==============

class TenantAdminCreate(BaseModel):
    """创建管理员请求"""
    phone: str = Field(..., min_length=11, max_length=11, description="手机号")
    name: Optional[str] = Field(None, max_length=50, description="管理员姓名")
    role: str = Field("admin", description="角色：owner/admin/viewer")


class TenantAdminLoginRequest(BaseModel):
    """管理员登录请求（手机号+验证码）"""
    phone: str = Field(..., min_length=11, max_length=11, description="手机号")
    code: str = Field(..., min_length=4, max_length=6, description="短信验证码")


class TenantAdminResponse(BaseModel):
    """管理员响应"""
    admin_id: str
    tenant_id: str
    phone: str
    name: Optional[str] = None
    role: str
    sso_provider: Optional[str] = None
    created_at: Optional[str] = None
