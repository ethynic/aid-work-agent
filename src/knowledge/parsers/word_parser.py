"""
Word 文档解析器
"""

from typing import List
from loguru import logger
from . import BaseParser, ParseResult


class WordParser(BaseParser):
    """Word (.docx) 文档解析器"""

    def supported_extensions(self) -> List[str]:
        return [".docx"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"后端日志：开始解析 Word 文档: {file_path}")
        try:
            from docx import Document

            doc = Document(file_path)

            # 提取所有段落文本
            paragraphs = []
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    paragraphs.append(text)

            # 提取表格文本
            for table in doc.tables:
                for row in table.rows:
                    row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if row_text:
                        paragraphs.append(" | ".join(row_text))

            text = "\n".join(paragraphs)

            if not text:
                logger.warning(f"后端日志：Word 文档内容为空: {file_path}")

            metadata = {
                "paragraph_count": len(doc.paragraphs),
                "table_count": len(doc.tables),
                "char_count": len(text),
            }

            logger.info(f"后端日志：Word 文档解析完成: {file_path}, 字符数={len(text)}")
            return ParseResult(text=text, metadata=metadata)

        except Exception as e:
            logger.error(f"后端日志：Word 文档解析失败: {file_path}, error: {e}", exc_info=True)
            raise
