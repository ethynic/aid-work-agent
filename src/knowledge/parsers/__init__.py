"""
文档解析器基类和公共数据结构
"""

from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class ParseResult:
    """文档解析结果"""
    text: str                              # 提取的文本内容
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
    thumbnail_path: Optional[str] = None   # 缩略图路径（图片/视频）
    raw_text: Optional[str] = None        # 原始文本（OCR 结果等）


class BaseParser(ABC):
    """文档解析器基类"""

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """
        解析文档，提取文本和元数据

        Args:
            file_path: 文档路径

        Returns:
            ParseResult 对象
        """
        pass

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """支持的文件扩展名"""
        pass
