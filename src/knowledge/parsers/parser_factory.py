"""
解析器工厂 - 根据文件类型选择合适的解析器
"""

from typing import Dict, Optional
from pathlib import Path
from loguru import logger

from . import BaseParser
from .word_parser import WordParser
from .excel_parser import ExcelParser
from .ppt_parser import PPTParser
from .pdf_parser import PDFParser


class ParserFactory:
    """解析器工厂"""

    def __init__(self):
        self._parsers: Dict[str, BaseParser] = {}
        self._register_default_parsers()

    def _register_default_parsers(self):
        """注册默认解析器"""
        parsers = [
            WordParser(),
            ExcelParser(),
            PPTParser(),
            PDFParser(),
        ]
        for parser in parsers:
            for ext in parser.supported_extensions():
                self._parsers[ext.lower()] = parser

    def get_parser(self, file_path: str) -> Optional[BaseParser]:
        """根据文件路径获取合适的解析器"""
        ext = Path(file_path).suffix.lower()
        parser = self._parsers.get(ext)
        if not parser:
            logger.warning(f"后端日志：没有找到支持 {ext} 格式的解析器: {file_path}")
        return parser

    def register_parser(self, extension: str, parser: BaseParser):
        """注册自定义解析器"""
        self._parsers[extension.lower()] = parser
        logger.info(f"后端日志：注册解析器: {extension} -> {parser.__class__.__name__}")


# 全局单例
parser_factory = ParserFactory()
