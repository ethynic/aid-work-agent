"""商机状态机

严格遵循设计文档 §7.6：
    new → contacted
    contacted → qualified | invalid
    qualified → converted
    invalid / converted → 终态

说明：本状态机按设计文档的线性管道解读实现。
若后续需要「new → invalid」等快捷失效路径（如雷达判定为垃圾/风险立即标记），
应在本表显式扩展，并在状态图文档同步更新。
"""

from src.social_media.outbound.enums import LeadStatus


class InvalidLeadTransition(ValueError):
    """非法状态转换。携带 (current, target) 供调用方定位问题。"""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(
            f"非法商机状态转换：{current!r} → {target!r}（不允许的路径）"
        )


# 合法转换表：当前状态 → 允许进入的目标状态集合
# 注意：无效(invalid)和已转化(converted)为终态，无出口。
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    LeadStatus.NEW.value: {LeadStatus.CONTACTED.value},
    LeadStatus.CONTACTED.value: {LeadStatus.QUALIFIED.value, LeadStatus.INVALID.value},
    LeadStatus.QUALIFIED.value: {LeadStatus.CONVERTED.value},
    LeadStatus.INVALID.value: set(),
    LeadStatus.CONVERTED.value: set(),
}


def can_transition(current: str, target: str) -> bool:
    """判断 ``current → target`` 是否为合法转换。

    参数支持传入任意字符串；非已知状态返回 False（保守拒绝）。
    """
    if current == target:
        # 相同状态不算转换，不允许 no-op「转换」
        return False
    return target in ALLOWED_TRANSITIONS.get(current, set())
