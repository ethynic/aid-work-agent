"""Browser工具模块

包含传统工具（已标记deprecated）和新的语义快照驱动工具。

## 新工具（语义快照驱动）

| 工具 | 描述 |
|------|------|
| BrowserSnapshotTool | 获取页面语义快照（必须首先调用） |
| BrowserClickTool | 自然语言点击元素 |
| BrowserFillTool | 自然语言填写表单 |
| BrowserSelectTool | 自然语言选择选项 |
| BrowserFindTool | 语义查找元素 |
| BrowserGetPathTool | 获取操作路径历史 |
| BrowserBacktrackTool | 回溯操作 |

## 旧工具（deprecated）

以下工具已标记为 deprecated，将在 Phase 2 删除：
- BrowserClickTool (CSS版) -> 使用语义版 BrowserClickTool
- BrowserFillTool (CSS版) -> 使用语义版 BrowserFillTool

## 使用流程

```python
# 1. 打开网页
await BrowserOpenTool().execute(url="https://example.com")

# 2. 获取语义快照（必须）
await BrowserSnapshotTool().execute()

# 3. 使用语义工具操作
await BrowserClickTool().execute(description="登录按钮")
await BrowserFillTool().execute(field="用户名", value="zhangsan")
```
"""

# ============================================
# 传统工具（已标记 deprecated）
# ============================================
from .browser_tool import (
    BrowserSession,
    get_browser_session,
    close_browser_session,
    BrowserOpenTool,
    BrowserGetContentTool,
    BrowserNavigateTool,
    BrowserCloseTool,
    BrowserScreenshotTool,
    create_browser_tools as _create_legacy_tools,
)

# 为了兼容性，保留旧的 Click/Fill（会在 Phase 2 删除）
# 这些工具会在 Phase 2 被真正的语义工具替代
from .browser_tool import (
    BrowserClickTool as _LegacyBrowserClickTool,
    BrowserFillTool as _LegacyBrowserFillTool,
)

# ============================================
# 新工具（语义快照驱动）
# ============================================
from .tools_snapshot import (
    BrowserSnapshotTool,
    get_ref_mapper,
    store_ref_mapper,
)

from .tools_semantic import (
    BrowserClickTool,  # 语义驱动版
    BrowserFillTool,   # 语义驱动版
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
    """创建所有浏览器工具列表

    Returns:
        工具列表
    """
    return [
        # 基础工具
        BrowserOpenTool(),
        BrowserGetContentTool(),
        BrowserNavigateTool(),
        BrowserCloseTool(),
        BrowserScreenshotTool(),
        # 语义快照驱动工具（新版）
        BrowserSnapshotTool(),
        BrowserClickTool(),  # 语义驱动版
        BrowserFillTool(),   # 语义驱动版
        BrowserSelectTool(),
        BrowserFindTool(),
        BrowserFindAllTool(),
        BrowserGetPathTool(),
        BrowserBacktrackTool(),
    ]


__all__ = [
    # 会话管理
    "BrowserSession",
    "get_browser_session",
    "close_browser_session",
    # 基础工具
    "BrowserOpenTool",
    "BrowserGetContentTool",
    "BrowserNavigateTool",
    "BrowserCloseTool",
    "BrowserScreenshotTool",
    # 语义快照工具
    "BrowserSnapshotTool",
    "get_ref_mapper",
    "store_ref_mapper",
    # 语义操作工具
    "BrowserClickTool",  # 语义驱动版
    "BrowserFillTool",   # 语义驱动版
    "BrowserSelectTool",
    # 查找工具
    "BrowserFindTool",
    "BrowserFindAllTool",
    # 路径工具
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
