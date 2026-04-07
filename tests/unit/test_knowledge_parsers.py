"""
文档解析器测试
"""

import pytest

pytestmark = pytest.mark.skills

from src.knowledge.parsers.word_parser import WordParser
from src.knowledge.parsers.excel_parser import ExcelParser
from src.knowledge.parsers.ppt_parser import PPTParser
from src.knowledge.parsers.pdf_parser import PDFParser
from src.knowledge.parsers.parser_factory import parser_factory


class TestParserFactory:
    """解析器工厂测试"""

    def test_get_word_parser(self):
        parser = parser_factory.get_parser("test.docx")
        assert isinstance(parser, WordParser)

    def test_get_excel_parser(self):
        parser = parser_factory.get_parser("test.xlsx")
        assert isinstance(parser, ExcelParser)

    def test_get_ppt_parser(self):
        parser = parser_factory.get_parser("test.pptx")
        assert isinstance(parser, PPTParser)

    def test_get_pdf_parser(self):
        parser = parser_factory.get_parser("test.pdf")
        assert isinstance(parser, PDFParser)

    def test_unsupported_format_returns_none(self):
        parser = parser_factory.get_parser("test.xyz")
        assert parser is None


class TestWordParser:
    """Word 解析器测试"""

    def test_supported_extensions(self):
        parser = WordParser()
        assert ".docx" in parser.supported_extensions()

    @pytest.mark.asyncio
    async def test_parse_missing_file(self):
        parser = WordParser()
        with pytest.raises(Exception):
            await parser.parse("non_existent_file.docx")


class TestExcelParser:
    """Excel 解析器测试"""

    def test_supported_extensions(self):
        parser = ExcelParser()
        assert ".xlsx" in parser.supported_extensions()


class TestPPTParser:
    """PPT 解析器测试"""

    def test_supported_extensions(self):
        parser = PPTParser()
        assert ".pptx" in parser.supported_extensions()


class TestPDFParser:
    """PDF 解析器测试"""

    def test_supported_extensions(self):
        parser = PDFParser()
        assert ".pdf" in parser.supported_extensions()
