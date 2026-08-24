"""browser_get_path / browser_backtrack 工具

获取操作路径历史和状态回溯。
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger
from src.tools._helpers import sanitize_error
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.browser.session import _browser_sessions


class BrowserGetPathInput(BaseModel):
    """获取操作路径参数"""
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    format: Optional[str] = Field("text", description="输出格式：text文本格式，json为JSON格式")


class BrowserBacktrackInput(BaseModel):
    """回溯页面状态参数"""
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    steps: Optional[int] = Field(1, description="回溯的步数，默认为1")


@dataclass
class PathStep:
    """操作步骤"""

    step_number: int
    action: str
    ref: str
    label: str
    result: str
    url: str
    timestamp: str


class PathTracker:
    """路径追踪器

    记录浏览器操作历史，支持状态回溯。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.history: List[PathStep] = []
        self._step_counter = 0

    def record_step(
        self,
        action: str,
        ref: str,
        label: str,
        result: str,
        url: str,
    ) -> PathStep:
        """记录操作步骤

        Args:
            action: 操作类型（click, fill, select, snapshot, open 等）
            ref: 元素 ref
            label: 元素标签
            result: 操作结果
            url: 当前 URL

        Returns:
            PathStep: 创建的步骤
        """
        self._step_counter += 1
        step = PathStep(
            step_number=self._step_counter,
            action=action,
            ref=ref,
            label=label,
            result=result,
            url=url,
            timestamp=datetime.now().isoformat(),
        )
        self.history.append(step)
        return step

    def get_history(
        self,
        format: str = "text",
    ) -> str:
        """获取操作历史

        Args:
            format: 输出格式 (text/json)

        Returns:
            str: 格式化的历史记录
        """
        if format == "json":
            return json.dumps(
                [asdict(step) for step in self.history],
                ensure_ascii=False,
                indent=2,
            )

        # text 格式
        if not self.history:
            return "暂无操作历史"

        lines = ["# 浏览器操作路径历史", ""]
        for step in self.history:
            lines.append(
                f"{step.step_number}. [{step.action}] {step.label} ({step.ref})"
            )
            lines.append(f"   结果: {step.result}")
            lines.append(f"   URL: {step.url}")
            lines.append(f"   时间: {step.timestamp}")
            lines.append("")

        return "\n".join(lines)

    def backtrack(self, steps: int = 1) -> List[str]:
        """回溯操作

        Args:
            steps: 回溯步数

        Returns:
            List[str]: 需要重新访问的 URL 列表
        """
        if steps > len(self.history):
            steps = len(self.history)

        # 移除最后 steps 步
        removed = self.history[-steps:]
        self.history = self.history[:-steps]

        # 返回需要重新访问的 URL
        urls = []
        for step in reversed(removed):
            if step.url and step.url not in urls:
                urls.append(step.url)

        return urls

    def clear(self) -> None:
        """清空历史"""
        self.history.clear()
        self._step_counter = 0


# 全局路径追踪器存储
_path_trackers: Dict[str, PathTracker] = {}


def get_path_tracker(session_id: str) -> PathTracker:
    """获取或创建路径追踪器

    Args:
        session_id: 会话ID

    Returns:
        PathTracker: 路径追踪器
    """
    if session_id not in _path_trackers:
        _path_trackers[session_id] = PathTracker(session_id)
    return _path_trackers[session_id]


class BrowserGetPathTool(BaseTool):
    """获取操作路径历史工具"""

    # 旧版浏览器工具，保留兼容：不进自动目录（agent 只注册统一入口 browser_automation）
    catalog = False
    name = "browser_get_path"
    description = """获取当前的浏览器操作路径历史。

    返回格式化的操作历史记录，包含：
    - 操作步骤编号
    - 操作类型（click, fill, select, snapshot, open 等）
    - 操作目标（元素标签和 ref）
    - 操作结果
    - 当前 URL
    - 操作时间

    用于了解如何到达当前位置，或进行问题排查。"""

    display_name = "获取操作路径"
    category = "browser"
    InputModel = BrowserGetPathInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行获取路径操作

        Args:
            session_id: 会话ID
            format: 输出格式

        Returns:
            执行结果
        """
        session_id = kwargs.get("session_id", "default")
        format_type = kwargs.get("format", "text")

        try:
            tracker = get_path_tracker(session_id)
            history = tracker.get_history(format=format_type)

            return {
                "success": True,
                "session_id": session_id,
                "format": format_type,
                "step_count": len(tracker.history),
                "history": history,
            }

        except Exception as e:
            logger.error("获取路径历史失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="获取路径历史失败"),
            }


class BrowserBacktrackTool(BaseTool):
    """回溯操作工具"""

    # 旧版浏览器工具，保留兼容：不进自动目录（agent 只注册统一入口 browser_automation）
    catalog = False
    name = "browser_backtrack"
    description = """回溯到之前的页面状态。

    如果当前操作导致页面异常，可以回退到之前的状态。
    注意：此工具只能回退操作历史，不会自动恢复页面内容，
    需要配合 browser_open 重新打开之前的页面。

    返回需要重新访问的 URL 列表。"""

    display_name = "回溯页面状态"
    category = "browser"
    InputModel = BrowserBacktrackInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行回溯操作

        Args:
            session_id: 会话ID
            steps: 回溯步数

        Returns:
            执行结果
        """
        session_id = kwargs.get("session_id", "default")
        steps = kwargs.get("steps", 1)

        try:
            tracker = get_path_tracker(session_id)

            if not tracker.history:
                return {
                    "success": True,
                    "message": "暂无操作历史，无需回溯",
                    "steps": steps,
                    "remaining_steps": 0,
                    "urls_to_restore": [],
                }

            urls = tracker.backtrack(steps=steps)

            return {
                "success": True,
                "message": f"已回溯 {min(steps, len(tracker.history) + 1)} 步",
                "steps": steps,
                "remaining_steps": len(tracker.history),
                "urls_to_restore": urls,
            }

        except Exception as e:
            logger.error("回溯操作失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="回溯操作失败"),
            }


def record_browser_action(
    session_id: str,
    action: str,
    ref: str,
    label: str,
    result: str,
    url: str,
) -> None:
    """记录浏览器操作（供其他工具调用）

    Args:
        session_id: 会话ID
        action: 操作类型
        ref: 元素 ref
        label: 元素标签
        result: 操作结果
        url: 当前 URL
    """
    try:
        tracker = get_path_tracker(session_id)
        tracker.record_step(action, ref, label, result, url)
    except Exception as e:
        logger.warning(f"记录浏览器操作失败: {e}")
