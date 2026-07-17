"""Browser工具模块

## 新工具（推荐）

| 工具 | 描述 |
|------|------|
| BrowserAutomationTool | 统一浏览器自动化工具，一个工具完成所有浏览器操作 |

## 旧工具（保留兼容）

以下工具已保留用于兼容性，新代码应使用 BrowserAutomationTool：
- BrowserOpenTool, BrowserSnapshotTool, BrowserClickTool, BrowserFillTool 等
"""

# ============================================
# 新工具：统一浏览器自动化
# ============================================
from .automation_tool import BrowserAutomationTool

# ============================================
# 会话管理
# ============================================
from .session import (
    BrowserSession,
    get_browser_session,
    close_browser_session,
    close_all_owned_browser_runs,
    has_browser_session,
)

# ============================================
# 旧工具（保留兼容）
# ============================================
from .browser_tool import (
    BrowserOpenTool,
    BrowserGetContentTool,
    BrowserNavigateTool,
    BrowserCloseTool,
    BrowserScreenshotTool,
    create_browser_tools as _create_legacy_tools,
)
from .browser_tool import (
    BrowserClickTool as _LegacyBrowserClickTool,
    BrowserFillTool as _LegacyBrowserFillTool,
)
from .tools_snapshot import (
    BrowserSnapshotTool,
    get_ref_mapper,
    store_ref_mapper,
)
from .tools_semantic import (
    BrowserClickTool,
    BrowserFillTool,
    BrowserSelectTool,
)
from .tools_find import (
    BrowserFindTool,
    BrowserFindAllTool,
)
from .tools_path import (
    BrowserGetPathTool,
    BrowserBacktrackTool,
    PathTracker,
    get_path_tracker,
    record_browser_action,
)

# ============================================
# 语义快照模块
# ============================================
from .semantic import (
    SemanticSnapshotGenerator,
    SemanticSnapshot,
    ElementClassifier,
    ElementType,
    SemanticTagger,
    SubmenuDetector,
    SubmenuSnapshot,
    MenuItem,
    RefMapper,
    InteractiveElement,
    NaturalMatcher,
    MatchResult,
    IFrameHandler,
    IFrameInfo,
    get_page_iframes,
)


def create_browser_tools() -> list:
    """创建浏览器工具列表

    Returns:
        工具列表
    """
    return [
        # 新版统一工具
        BrowserAutomationTool(),
    ]


__all__ = [
    # 新工具
    "BrowserAutomationTool",
    # 会话管理
    "BrowserSession",
    "get_browser_session",
    "close_browser_session",
    "close_all_owned_browser_runs",
    "has_browser_session",
    # 旧工具（兼容）
    "BrowserOpenTool",
    "BrowserGetContentTool",
    "BrowserNavigateTool",
    "BrowserCloseTool",
    "BrowserScreenshotTool",
    "BrowserSnapshotTool",
    "get_ref_mapper",
    "store_ref_mapper",
    "BrowserClickTool",
    "BrowserFillTool",
    "BrowserSelectTool",
    "BrowserFindTool",
    "BrowserFindAllTool",
    "BrowserGetPathTool",
    "BrowserBacktrackTool",
    "PathTracker",
    "get_path_tracker",
    "record_browser_action",
    # 语义模块
    "SemanticSnapshotGenerator",
    "SemanticSnapshot",
    "ElementClassifier",
    "ElementType",
    "SemanticTagger",
    "SubmenuDetector",
    "SubmenuSnapshot",
    "MenuItem",
    "RefMapper",
    "InteractiveElement",
    "NaturalMatcher",
    "MatchResult",
    "IFrameHandler",
    "IFrameInfo",
    "get_page_iframes",
    # 工厂函数
    "create_browser_tools",
]
