"""
用户模型

管理用户信息和权限
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class UserRole(str, Enum):
    """用户角色"""
    EMPLOYEE = "employee"
    MANAGER = "manager"
    ADMIN = "admin"


class User(BaseModel):
    """
    用户模型
    
    存储用户的基本信息和权限
    """
    user_id: str = Field(..., description="用户唯一ID")
    name: str = Field(..., description="用户姓名")
    role: UserRole = Field(default=UserRole.EMPLOYEE, description="用户角色")
    department_id: Optional[str] = Field(None, description="部门ID")
    department_name: Optional[str] = Field(None, description="部门名称")
    avatar: Optional[str] = Field(None, description="头像URL")
    channel_user_id: Optional[str] = Field(None, description="渠道用户ID")
    channel_type: Optional[str] = Field(None, description="渠道类型")
    permissions: List[str] = Field(default_factory=list, description="权限列表")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    
    class Config:
        use_enum_values = True
    
    def has_permission(self, permission: str) -> bool:
        """
        检查用户是否有指定权限
        
        Args:
            permission: 权限名称
        
        Returns:
            是否有权限
        """
        # 管理员拥有所有权限
        if self.role == UserRole.ADMIN:
            return True
        
        return permission in self.permissions
    
    def get_default_permissions(self) -> List[str]:
        """
        获取角色默认权限
        
        Returns:
            权限列表
        """
        role_permissions = {
            UserRole.EMPLOYEE: [
                "email_send",
                "email_read",
                "ocr_image",
                "doc_summarize",
                "web_search",
            ],
            UserRole.MANAGER: [
                "email_send",
                "email_read",
                "ocr_image",
                "ocr_pdf",
                "doc_summarize",
                "doc_translate",
                "web_search",
                "data_query",
                "data_export",
            ],
            UserRole.ADMIN: [
                "email_send",
                "email_read",
                "email_search",
                "ocr_image",
                "ocr_pdf",
                "ocr_handwriting",
                "doc_summarize",
                "doc_translate",
                "doc_format",
                "web_search",
                "kb_search",
                "data_query",
                "data_export",
                "chart_generator",
                "report_generator",
                "system_settings",
                "user_management",
            ],
        }
        return role_permissions.get(self.role, [])
    
    def to_dict(self) -> dict:
        """
        转换为字典
        
        Returns:
            字典表示
        """
        return {
            "user_id": self.user_id,
            "name": self.name,
            "role": self.role,
            "department_id": self.department_id,
            "department_name": self.department_name,
            "avatar": self.avatar,
            "channel_user_id": self.channel_user_id,
            "channel_type": self.channel_type,
            "permissions": self.permissions,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "User":
        """
        从字典创建用户
        
        Args:
            data: 字典数据
        
        Returns:
            User实例
        """
        if isinstance(data.get("role"), str):
            data["role"] = UserRole(data["role"])
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)
