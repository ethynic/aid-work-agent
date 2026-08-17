"""
PDF 文档解析器
"""

from typing import List
from loguru import logger
from . import BaseParser, ParseResult


class PDFParser(BaseParser):
    """PDF 文档解析器"""

    def supported_extensions(self) -> List[str]:
        return [".pdf"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"后端日志：开始解析 PDF 文档: {file_path}")
        try:
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            total_pages = len(reader.pages)

            paragraphs = []
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text()
                if text:
                    paragraphs.append(f"## 第 {i} 页")
                    paragraphs.append(text.strip())
                    paragraphs.append("")

            text = "\n".join(paragraphs)
            metadata = {
                "page_count": total_pages,
                "char_count": len(text),
            }

            logger.info(f"后端日志：PDF 文档解析完成: {file_path}, 页数={total_pages}")
            return ParseResult(text=text, metadata=metadata)

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：PDF 文档解析失败: {file_path}, error: {e}")
            raise
