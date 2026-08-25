"""
PPT 文档解析器
"""

from typing import List
from loguru import logger
from . import BaseParser, ParseResult


class PPTParser(BaseParser):
    """PPT (.pptx) 文档解析器"""

    def supported_extensions(self) -> List[str]:
        return [".pptx"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"后端日志：开始解析 PPT 文档: {file_path}")
        try:
            from pptx import Presentation

            prs = Presentation(file_path)

            paragraphs = []
            total_slides = len(prs.slides)

            for i, slide in enumerate(prs.slides, 1):
                paragraphs.append(f"## 幻灯片 {i}")

                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for paragraph in shape.text_frame.paragraphs:
                            text = paragraph.text.strip()
                            if text:
                                paragraphs.append(text)

                    # 提取表格
                    if shape.has_table:
                        table = shape.table
                        for row in table.rows:
                            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                            if row_text:
                                paragraphs.append(" | ".join(row_text))

                paragraphs.append("")  # 幻灯片之间空行

            text = "\n".join(paragraphs)
            metadata = {
                "slide_count": total_slides,
                "char_count": len(text),
            }

            logger.info(f"后端日志：PPT 文档解析完成: {file_path}, 幻灯片数={total_slides}")
            return ParseResult(text=text, metadata=metadata)

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：PPT 文档解析失败: {file_path}, error: {e}")
            raise
