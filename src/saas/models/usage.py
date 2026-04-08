"""使用报告相关数据模型"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class UsageSummary(BaseModel):
    """用量汇总"""
    tenant_id: str
    period: str  # day/week/month
    total_tokens: int = 0
    total_sessions: int = 0
    active_users: int = 0
    date: str  # 统计日期


class UserUsageDetail(BaseModel):
    """用户用量明细"""
    user_id: str
    username: Optional[str] = None
    total_tokens: int = 0
    total_sessions: int = 0
    last_active: Optional[str] = None


class TokenTrendItem(BaseModel):
    """Token 用量趋势数据点"""
    date: str
    tokens: int
    sessions: int


class UsageReportResponse(BaseModel):
    """用量报告响应"""
    tenant_id: str
    period: str
    start_date: str
    end_date: str
    total_tokens: int = 0
    total_sessions: int = 0
    active_users: int = 0
    trend: List[TokenTrendItem] = []
    user_details: List[UserUsageDetail] = []
