"""
Excel 文档解析器
"""

from typing import List
from loguru import logger
from . import BaseParser, ParseResult


class ExcelParser(BaseParser):
    """Excel (.xlsx) 文档解析器"""

    def supported_extensions(self) -> List[str]:
        return [".xlsx"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"后端日志：开始解析 Excel 文档: {file_path}")
        try:
            from openpyxl import load_workbook

            wb = load_workbook(file_path, read_only=True, data_only=True)

            paragraphs = []
            total_sheets = len(wb.sheetnames)

            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                paragraphs.append(f"## 工作表: {sheet_name}")

                for row in sheet.iter_rows(values_only=True):
                    row_text = []
                    for cell in row:
                        if cell is not None:
                            row_text.append(str(cell).strip())
                    if row_text:
                        paragraphs.append(" | ".join(row_text))

                paragraphs.append("")  # 工作表之间空行

            wb.close()

            text = "\n".join(paragraphs)
            metadata = {
                "sheet_count": total_sheets,
                "sheets": wb.sheetnames if hasattr(wb, 'sheetnames') else [],
                "char_count": len(text),
            }

            logger.info(f"后端日志：Excel 文档解析完成: {file_path}, 工作表数={total_sheets}")
            return ParseResult(text=text, metadata=metadata)

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：Excel 文档解析失败: {file_path}, error: {e}")
            raise
