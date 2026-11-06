"""Browser工具模块

## 已废弃（2026-11-06）

`browser_automation`（BrowserAutomationTool）已退出工具自动目录，不再注册、
发现或提供给新任务（Runner 执行槽隔离计划 Phase 1）。类保留供恢复链路与
生命周期测试引用；包级工厂 create_browser_tools 已删除（browser_tool.py 内
同名旧版函数保留兼容，长期无调用方）。

## 旧工具（保留兼容）

以下工具长期未注册（catalog=False），仅保留类型兼容：
- BrowserOpenTool, BrowserSnapshotTool, BrowserClickTool, BrowserFillTool 等
"""

# ============================================
# 已废弃工具：统一浏览器自动化（仅类型保留，不进目录）
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


__all__ = [
    # 已废弃工具（仅类型保留，不进目录）
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
]
