"""
租户上下文管理

使用 ContextVar 在请求生命周期内传递 tenant_id 和 user_id。
中间件设置，业务代码读取。
"""

from contextvars import ContextVar
from typing import Optional

# 当前租户 ID（企业租户有值，公共用户为 None）
current_tenant_id: ContextVar[Optional[str]] = ContextVar("current_tenant_id", default=None)

# 当前用户 ID
current_user_id: ContextVar[Optional[str]] = ContextVar("current_user_id", default=None)


def set_tenant_context(tenant_id: Optional[str], user_id: Optional[str] = None):
    """设置当前租户上下文"""
    current_tenant_id.set(tenant_id)
    current_user_id.set(user_id)


def clear_tenant_context():
    """清除租户上下文"""
    current_tenant_id.set(None)
    current_user_id.set(None)


def get_current_tenant_id() -> Optional[str]:
    """获取当前租户 ID"""
    return current_tenant_id.get()


def get_current_user_id() -> Optional[str]:
    """获取当前用户 ID"""
    return current_user_id.get()
