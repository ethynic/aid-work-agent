"""巡检商机模块（outbound）。

包含两个子系统：
- 商机池数据层（B1）：3 张业务表 + Repository
  - bs_outbound_leads              商机主表（原文加密、意向分、状态、去重指纹）
  - bs_outbound_lead_interactions  商机互动/跟进记录
  - bs_outbound_outreach_actions   我方接触动作审计
- 托管登录态子系统（B0.5）：bs_outbound_account_sessions + account_session_store（加密 cookie 读写）

本包仅提供数据层（enums / state_machine / db / repository / account_session_store），
不包含副作用初始化；表初始化函数 ``init_outbound_tables`` /
``init_outbound_account_sessions_table`` 由调用方显式调用，
不挂在包顶层 import 链上（遵循 backend_dev.md「包初始化副作用规范」）。
"""

from src.social_media.outbound.enums import (
    LeadStatus,
    LeadSourceType,
    InteractionType,
    OutreachActionType,
    OutreachExecutionStatus,
)
from src.social_media.outbound.state_machine import (
    can_transition,
    InvalidLeadTransition,
)

__all__ = [
    "LeadStatus",
    "LeadSourceType",
    "InteractionType",
    "OutreachActionType",
    "OutreachExecutionStatus",
    "can_transition",
    "InvalidLeadTransition",
]
