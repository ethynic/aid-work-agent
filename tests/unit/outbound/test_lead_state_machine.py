"""商机状态机单测

校验状态机语义（设计文档 §7.6）：
- 合法路径：new → contacted → qualified|invalid，qualified → converted
- 终态：invalid / converted 无出口
- no-op（相同状态）拒绝
- 未知状态保守拒绝
"""

import pytest

from src.social_media.outbound.enums import LeadStatus
from src.social_media.outbound.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidLeadTransition,
    can_transition,
)


# ============================================================
# 合法转换（设计文档明确路径）
# ============================================================


def test_new_to_contacted_allowed():
    """new 必须能进入 contacted（雷达捕获后的标准入口动作）。"""
    assert can_transition("new", "contacted") is True


def test_contacted_to_qualified_allowed():
    """contacted → qualified（确认有效需求）。"""
    assert can_transition("contacted", "qualified") is True


def test_contacted_to_invalid_allowed():
    """contacted → invalid（接触后发现不相关/低质）。"""
    assert can_transition("contacted", "invalid") is True


def test_qualified_to_converted_allowed():
    """qualified → converted（最终转化）。"""
    assert can_transition("qualified", "converted") is True


# ============================================================
# 非法转换
# ============================================================


def test_new_cannot_skip_to_qualified():
    """new 不能跳过 contacted 直接到 qualified（防止跳过接触动作审计）。"""
    assert can_transition("new", "qualified") is False


def test_new_cannot_skip_to_converted():
    """new 不能直接 converted（必须走完接触→合格→转化全链路）。"""
    assert can_transition("new", "converted") is False


def test_invalid_is_terminal():
    """invalid 是终态，任何出口都拒绝。"""
    for target in ("new", "contacted", "qualified", "converted"):
        assert can_transition("invalid", target) is False


def test_converted_is_terminal():
    """converted 是终态，任何出口都拒绝（转化后不再回退）。"""
    for target in ("new", "contacted", "qualified", "invalid"):
        assert can_transition("converted", target) is False


def test_revert_back_to_new_rejected():
    """状态机不允许回退到 new（防止通过状态回退绕过审计）。"""
    assert can_transition("contacted", "new") is False
    assert can_transition("qualified", "contacted") is False
    assert can_transition("qualified", "new") is False


# ============================================================
# no-op 与未知状态
# ============================================================


def test_same_status_no_op_rejected():
    """相同状态不算转换，应拒绝（防止「伪转换」掩盖真实状态变化）。"""
    for status in ("new", "contacted", "qualified", "invalid", "converted"):
        assert can_transition(status, status) is False


def test_unknown_status_rejected():
    """未知状态保守拒绝（防止拼写错误绕过状态机）。"""
    assert can_transition("new", "deleted") is False
    assert can_transition("pending", "contacted") is False
    assert can_transition("NEW", "contacted") is False  # 大小写敏感


# ============================================================
# 状态机完整性：所有 LeadStatus 枚举值都在 ALLOWED_TRANSITIONS 中
# ============================================================


def test_all_lead_status_enum_values_have_transition_table_entry():
    """LeadStatus 枚举新增值时必须同步更新 ALLOWED_TRANSITIONS。

    本测试锁定：新增状态忘了登记转换表会立刻失败（Rule 6：测试验证意图）。
    """
    enum_values = {s.value for s in LeadStatus}
    table_keys = set(ALLOWED_TRANSITIONS.keys())
    assert enum_values == table_keys, (
        f"LeadStatus 与 ALLOWED_TRANSITIONS 键不一致："
        f"enum-only={enum_values - table_keys}, table-only={table_keys - enum_values}"
    )


# ============================================================
# 异常类
# ============================================================


def test_invalid_lead_transition_carries_current_and_target():
    """异常对象必须携带 current/target，供 API 层返回有意义的错误信息。"""
    err = InvalidLeadTransition("new", "converted")
    assert err.current == "new"
    assert err.target == "converted"
    assert "new" in str(err)
    assert "converted" in str(err)
