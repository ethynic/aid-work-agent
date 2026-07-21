"""
x-to-image 数据模型。

定义输入类型枚举、图片格式枚举，以及 XToImageInput / XToImageResult 两个核心 dataclass。
参见设计文档 §5.3。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class InputType(str, Enum):
    """支持的输入内容类型。"""
    TEXT = "text"          # 纯文本
    MARKDOWN = "markdown"  # Markdown
    HTML = "html"          # HTML 字符串或 .html 文件路径


class ImageFormat(str, Enum):
    """输出图片格式。"""
    PNG = "png"
    JPEG = "jpeg"


@dataclass
class XToImageInput:
    """x-to-image 转换输入。"""
    source: str                       # 文本/MD/HTML 内容，或 .html 文件绝对路径
    content_type: InputType
    width: int = 800                  # 渲染视窗宽度（px），默认 800
    output_name: Optional[str] = None  # 输出文件名（不含扩展名），默认用 uuid
    image_format: ImageFormat = ImageFormat.PNG
    max_height: int = 20_000          # 长图最大高度（px），超限截断
    max_file_size_mb: int = 10        # 长图最大体积（MB），超限转 JPEG/降质
    is_file_path: bool = False        # source 是否为文件路径（HTML 文件场景）
    extra: dict = field(default_factory=dict)  # 保留扩展（背景色/水印等）
    tenant_id: Optional[str] = None   # 提供时启用 HTML 图片 base64 内联（见 HtmlRenderer）
    user_id: Optional[str] = None     # 远程图片下载时附带


@dataclass
class XToImageResult:
    """x-to-image 转换结果。"""
    success: bool
    image_path: Optional[str] = None  # 最终单张长图的临时文件绝对路径
    width: int = 0
    height: int = 0
    file_size: int = 0
    truncated: bool = False           # 是否因超限被截断
    renderer: str = ""                # 实际使用的渲染器名
    error: Optional[str] = None
