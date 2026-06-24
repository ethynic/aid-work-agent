"""记忆系统模块"""

from .manager import MemoryManager
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .models import MemoryItem

# 注意：mid_term (ContextCompressionService) 不在此处 eager re-export。
# 原因：mid_term 依赖 src.db.models.ContextSummaryDB，而 src.db.models 通过
# src.core.cache_utils → src.core → src.core.agent → src.memory.short_term 这条链
# 反向依赖 src.memory。若在此处 eager import mid_term，会触发循环 import
# (ImportError: cannot import name 'ContextSummaryDB' from partially initialized module)。
# 使用方请直接 `from src.memory.mid_term import ContextCompressionService`。

__all__ = [
    "MemoryManager",
    "ShortTermMemory",
    "LongTermMemory",
    "MemoryItem",
]
