"""
Skill Session - Skill 执行期间的上下文隔离管理

当 use_skill 被调用时创建 SkillSession，跟踪 Skill 执行过程中的消息边界。
当 skill_complete 被调用或 Agent 直接回复用户时，压缩 Skill 过程中的中间消息。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SkillSession:
    """
    Skill 执行期间的上下文隔离管理
    
    用于跟踪 Skill 从加载到完成的整个生命周期，
    在完成时压缩中间消息，仅保留摘要。
    
    Attributes:
        skill_name: 技能名称
        start_index: use_skill 消息在 memory 中的位置
        message_count_before: Skill 开始前 memory 中的消息数量
        is_complete: Skill 是否已完成
    """
    
    skill_name: str
    start_index: int
    message_count_before: int
    is_complete: bool = False
