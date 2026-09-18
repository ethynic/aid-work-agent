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
    initial_admin_name: Optional[str] = Field(None, max_length=50, description="初始管理员姓名")
    initial_admin_phone: Optional[str] = Field(None, max_length=11, description="初始管理员手机号")
    plan: str = Field("basic", description="套餐：basic/standard/premium")
    max_instances: Optional[int] = Field(None, description="最大实例数，None 使用默认值")
    max_users: Optional[int] = Field(None, description="最大用户数，None 使用默认值")
    tenant_code: str = Field(..., pattern='^[A-Za-z0-9]{4,8}$', description='租户代码（4-8位字母数字，不区分大小写）')
    tenant_type: str = Field("test", description="租户类型：real=真实租户（真实金额充值）/ test=测试/演示租户（虚拟充值）")
    expire_at: Optional[str] = Field(None, description="到期日期（YYYY-MM-DD，空表示永久有效）")


class TenantUpdate(BaseModel):
    """更新租户请求"""
    company_name: Optional[str] = Field(None, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")
    initial_admin_name: Optional[str] = Field(None, max_length=50, description="初始管理员姓名")
    initial_admin_phone: Optional[str] = Field(None, max_length=11, description="初始管理员手机号")
    plan: Optional[str] = Field(None, description="套餐：basic/standard/premium")
    status: Optional[str] = Field(None, description="状态：active/suspended/deactivated")
    tenant_type: Optional[str] = Field(None, description="租户类型：real=真实租户（真实金额充值）/ test=测试/演示租户（虚拟充值）")
    max_instances: Optional[int] = Field(None, description="最大实例数")
    max_users: Optional[int] = Field(None, description="最大用户数")
    tenant_code: Optional[str] = Field(None, pattern='^[A-Za-z0-9]{4,8}$', description='租户代码（4-8位字母数字，不区分大小写）')
    expire_at: Optional[str] = Field(None, description="到期日期（YYYY-MM-DD，空表示永久有效）")
    logo_file_id: Optional[str] = Field(None, description="租户 Logo 文件 ID，传 null 清空")


class TenantResponse(BaseModel):
    """租户响应"""
    tenant_id: str
    company_name: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    initial_admin_name: Optional[str] = None
    initial_admin_phone: Optional[str] = None
    status: str
    plan: str
    tenant_type: str = "test"
    max_instances: int
    max_users: int
    expire_at: Optional[str] = None
    tenant_code: Optional[str] = None
    settings: Optional[dict] = None
    logo_file_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


