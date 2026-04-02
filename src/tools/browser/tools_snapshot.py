"""browser_snapshot 工具

获取页面语义快照，返回 LLM 可理解的页面结构。
"""

from typing import Any, Dict, Optional, List
from loguru import logger

from src.tools.base import BaseTool
from src.tools.browser.browser_tool import _browser_sessions
from src.tools.browser.semantic import SemanticSnapshotGenerator
from src.tools.browser.tools_path import record_browser_action


class BrowserSnapshotTool(BaseTool):
    """获取页面语义快照工具"""

    name = "browser_snapshot"
    description = """获取当前页面的语义快照，返回结构化的页面表示。

    快照包含页面上所有可交互元素的语义信息，便于大模型理解页面结构并定位元素。

    **必须首先调用**：在 browser_open 后、进行任何操作前，必须先调用此工具获取快照。

    适用于：
    - 了解页面整体布局和功能区域
    - 定位需要点击或填写的元素
    - 发现折叠菜单中的隐藏内容

    **使用流程**：
    1. browser_open 打开网页
    2. browser_snapshot 获取语义快照
    3. 分析快照中的 interactive_elements 和 submenu_snapshots
    4. 使用 browser_click/fill/select 等工具操作元素"""

    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "mode": {
                "type": "string",
                "enum": ["standard", "interactive", "compact"],
                "description": "快照模式：standard标准模式，interactive交互模式(仅显示可交互元素，推荐)，compact紧凑模式",
                "default": "interactive",
            },
            "max_depth": {
                "type": "integer",
                "description": "DOM遍历最大深度，默认6",
                "default": 6,
            },
            "include_hidden": {
                "type": "boolean",
                "description": "是否包含隐藏元素，默认false",
                "default": False,
            },
        },
        "required": [],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行获取语义快照操作

        Args:
            session_id: 会话ID
            mode: 快照模式
            max_depth: 最大深度
            include_hidden: 是否包含隐藏元素

        Returns:
            执行结果
        """
        session_id = kwargs.get("session_id", "default")
        mode = kwargs.get("mode", "interactive")
        max_depth = kwargs.get("max_depth", 6)
        include_hidden = kwargs.get("include_hidden", False)

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用 browser_open 打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用 browser_open 打开网页",
                }

            # 创建生成器
            generator = SemanticSnapshotGenerator(session_id=session_id)

            # 生成快照
            snapshot = await generator.generate(
                page=session.page,
                mode=mode,
                max_depth=max_depth,
                include_hidden=include_hidden,
            )

            if not snapshot.success:
                return {
                    "success": False,
                    "error": f"快照生成失败: {snapshot.error}",
                }

            # 返回结果
            result = snapshot.to_dict()

            # 添加 ref_mapper 引用（存储在会话中供后续工具使用）
            self._store_ref_mapper(session_id, generator.get_ref_mapper())

            # 记录快照操作到 PathTracker
            record_browser_action(
                session_id=session_id,
                action="snapshot",
                ref="",
                label=f"获取快照 ({mode}模式)",
                result=f"成功, {len(snapshot.interactive_elements)} 个元素",
                url=snapshot.url,
            )

            logger.info(f"语义快照生成成功: {snapshot.url}, 元素数: {len(snapshot.interactive_elements)}")

            return result

        except Exception as e:
            logger.error(f"获取语义快照失败: {e}")
            return {
                "success": False,
                "error": f"获取语义快照失败: {str(e)}",
            }

    def _store_ref_mapper(self, session_id: str, ref_mapper) -> None:
        """存储 ref_mapper 到会话

        Args:
            session_id: 会话ID
            ref_mapper: RefMapper 实例
        """
        _ref_mappers[session_id] = ref_mapper


# 全局 ref_mapper 存储（用于工具间共享）
_ref_mappers: Dict[str, Any] = {}


def store_ref_mapper(session_id: str, ref_mapper) -> None:
    """存储 ref_mapper

    Args:
        session_id: 会话ID
        ref_mapper: RefMapper 实例
    """
    _ref_mappers[session_id] = ref_mapper


def get_ref_mapper(session_id: str):
    """获取 ref_mapper

    Args:
        session_id: 会话ID

    Returns:
        RefMapper 或 None
    """
    return _ref_mappers.get(session_id)
