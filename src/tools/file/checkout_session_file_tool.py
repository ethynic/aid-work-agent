"""按需检出会话历史文件的控制工具（checkout_session_file）。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class CheckoutSessionFileInput(BaseModel):
    """按需检出会话文件入参。"""

    file_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="会话文件清单中的 file_id（如 file_1fba1c4a843e）。",
    )


class CheckoutSessionFileTool(BaseTool):
    """将当前会话较早轮次的文件（用户上传或助手生成）按需检出到当前工作区。"""

    name = "checkout_session_file"
    description = (
        "按需检出当前会话中较早轮次的文件（包括用户上传的附件和助手此前生成的 Word/PDF/Excel 等文档），"
        "返回可直接供 skill_execute / read / cp 等工具使用的本地绝对路径。"
        "仅可检出本会话文件清单中列出的 file_id。"
    )
    display_name = "检出会话文件"
    category = "file"
    InputModel = CheckoutSessionFileInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        fid = (tool_args or {}).get("file_id") or ""
        return f"{self.display_name}「{fid}」" if fid else self.display_name

    def get_usage_guide(self) -> str:
        return (
            "当用户要求基于本会话之前上传或之前生成的文档继续处理（如转格式、修改、提取内容）时，"
            "先从「本会话已有文件清单」中找到对应 `file_id`，调用 `checkout_session_file(file_id=...)` "
            "获取真实磁盘路径后再执行后续工具。"
        )

    async def execute(
        self,
        file_id: str = "",
        _workspace_dir: Optional[str] = None,
        _session_kind: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        from src.services.files.session_file_checkout import checkout_session_file
        from src.tools.context import current_tool_execution_context

        ctx = current_tool_execution_context()
        if ctx is None or not ctx.session_id:
            return {"success": False, "error": "当前执行缺少会话上下文，无法检出会话文件"}
        workspace_dir = _workspace_dir or ctx.skill_workdir
        if not workspace_dir:
            return {"success": False, "error": "当前执行未初始化工作区，无法检出会话文件"}

        return await asyncio.to_thread(
            checkout_session_file,
            file_id,
            workspace_dir=workspace_dir,
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            session_kind=_session_kind,
            user_id=ctx.user_id,
        )
