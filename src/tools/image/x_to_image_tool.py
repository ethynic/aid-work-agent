"""
x-to-image 内容转图片工具 — Agent 入口

将文本、Markdown 或 HTML 渲染为一张纵向长图(PNG)，写入临时目录并返回临时文件路径。
工具为 src/services/x_to_image 服务的薄包装，遵循 v1.1 设计：
仅返回临时文件路径 + 元信息，不注册下载、不返回 download_url。
"""

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.tools.base import BaseTool

# 注意：src.services.x_to_image 的导入放到 execute() 内部做函数级延迟导入，
# 与 pdf_process_tool.py 一致，避免在模块顶层触发 src.services.__init__ 链
# （后者会拉起 db/config/core/agent，造成循环导入）。


class XToImageInputModel(BaseModel):
    content: str = Field(
        ...,
        description="待转换的内容：纯文本、Markdown 或 HTML"
    )
    content_type: str = Field(
        "markdown",
        description="内容类型：text / markdown / html"
    )
    is_file_path: bool = Field(
        False,
        description="content 是否为本地 .html 文件路径"
    )
    width: int = Field(
        800,
        description="图片宽度(px)，默认800，建议400-1200"
    )
    output_name: Optional[str] = Field(
        None,
        description="输出文件名(不含扩展名)，默认自动生成"
    )


class XToImageTool(BaseTool):
    name = "x_to_image"
    display_name = "内容转图片"
    category = "image"
    description = (
        "将文本、Markdown 或 HTML 转换为一张长图(PNG)。"
        "适用于需要把富文本/表格/样式化内容以图片形式呈现给用户的场景。"
        "返回临时图片文件路径及尺寸信息。"
    )
    InputModel = XToImageInputModel

    async def execute(self, **kwargs) -> Dict[str, Any]:
        # 函数级延迟导入：避免顶层导入触发 src.services.__init__ 链导致的循环导入
        from src.services.x_to_image import (
            x_to_image_service,
            XToImageInput,
            InputType,
        )

        content: str = kwargs.get("content")
        content_type: str = (kwargs.get("content_type") or "markdown").strip().lower()
        is_file_path: bool = bool(kwargs.get("is_file_path", False))
        width: int = kwargs.get("width") or 800
        output_name: Optional[str] = kwargs.get("output_name")

        # 防御性收口：把 width 钳制到合理区间，避免 LLM 误传超大值
        # （如 999999）导致 Playwright 创建超大视窗造成资源滥用。
        # 设计 §3.2 N5：渲染参数有合理默认值/上限。建议区间 400-1200，留余量放宽到 200-4000。
        try:
            width = max(200, min(int(width), 4000))
        except (TypeError, ValueError):
            width = 800

        # 1. 安全地把 content_type 字符串解析为 InputType 枚举，非法值直接返回失败，不崩溃
        try:
            resolved_type = InputType(content_type)
        except ValueError:
            return {
                "success": False,
                "error": f"不支持的内容类型: {content_type}, 可选: text/markdown/html",
            }

        # 2. 构造服务输入
        inp = XToImageInput(
            source=content,
            content_type=resolved_type,
            width=width,
            output_name=output_name,
            is_file_path=is_file_path,
        )

        # 3. 调用服务
        try:
            result = await x_to_image_service.convert(inp)
        except Exception as e:
            return {"success": False, "error": f"内容转图片失败: {e}"}

        # 4. 服务层失败：透传错误
        if not result.success:
            return {"success": False, "error": result.error}

        # 5. 成功：仅返回临时文件路径 + 元信息（v1.1 设计：不注册下载/不返回 download_url）
        return {
            "success": True,
            "image_path": result.image_path,
            "image_name": Path(result.image_path).name,
            "file_size": result.file_size,
            "image_width": result.width,
            "image_height": result.height,
            "truncated": result.truncated,
            "renderer": result.renderer,
        }
