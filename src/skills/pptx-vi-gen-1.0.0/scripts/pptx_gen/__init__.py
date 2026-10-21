# pptx_gen — 模板 VI 提取 + AI 内容重新创作的 PPT 生成库（仅路线二）
from .vi import extract_vi
from .pipeline import generate_from_vi

__all__ = ["extract_vi", "generate_from_vi"]
