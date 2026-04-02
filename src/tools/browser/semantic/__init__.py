"""语义快照模块

包含语义快照生成、元素分类、语义标签生成、子菜单检测、iframe处理等功能。
"""

from .snapshot_generator import SemanticSnapshotGenerator, SemanticSnapshot
from .element_classifier import ElementClassifier, ElementType
from .semantic_tagger import SemanticTagger
from .submenu_detector import SubmenuDetector, SubmenuSnapshot, MenuItem
from .ref_mapper import RefMapper
from .natural_matcher import NaturalMatcher, MatchResult
from .iframe_handler import IFrameHandler, IFrameInfo, get_page_iframes

__all__ = [
    "SemanticSnapshotGenerator",
    "SemanticSnapshot",
    "ElementClassifier",
    "ElementType",
    "SemanticTagger",
    "SubmenuDetector",
    "SubmenuSnapshot",
    "MenuItem",
    "RefMapper",
    "NaturalMatcher",
    "MatchResult",
    "IFrameHandler",
    "IFrameInfo",
    "get_page_iframes",
]
