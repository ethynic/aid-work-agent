"""
文档解析器测试
"""

import os
import unittest
import asyncio
from pathlib import Path

from src.knowledge.parsers.word_parser import WordParser
from src.knowledge.parsers.excel_parser import ExcelParser
from src.knowledge.parsers.ppt_parser import PPTParser
from src.knowledge.parsers.pdf_parser import PDFParser
from src.knowledge.parsers.parser_factory import parser_factory


class TestParsers(unittest.TestCase):
    """文档解析器测试基类"""

    @classmethod
    def setUpClass(cls):
        cls.test_files_dir = Path(__file__).parent.parent / "test_uploads"

    def test_parser_factory_get_parser(self):
        """测试解析器工厂"""
        # Word 解析器
        parser = parser_factory.get_parser("test.docx")
        self.assertIsInstance(parser, WordParser)

        # Excel 解析器
        parser = parser_factory.get_parser("test.xlsx")
        self.assertIsInstance(parser, ExcelParser)

        # PPT 解析器
        parser = parser_factory.get_parser("test.pptx")
        self.assertIsInstance(parser, PPTParser)

        # PDF 解析器
        parser = parser_factory.get_parser("test.pdf")
        self.assertIsInstance(parser, PDFParser)

        # 不支持的格式
        parser = parser_factory.get_parser("test.xyz")
        self.assertIsNone(parser)


class TestWordParser(unittest.TestCase):
    """Word 解析器测试"""

    @classmethod
    def setUpClass(cls):
        cls.parser = WordParser()
        cls.test_files_dir = Path(__file__).parent.parent / "test_uploads"

    def test_supported_extensions(self):
        """测试支持的扩展名"""
        exts = self.parser.supported_extensions()
        self.assertIn(".docx", exts)

    def test_parse_missing_file(self):
        """异常路径：文件不存在"""
        async def run():
            with self.assertRaises(Exception):
                await self.parser.parse("non_existent_file.docx")

        asyncio.run(run())


class TestExcelParser(unittest.TestCase):
    """Excel 解析器测试"""

    def setUp(self):
        self.parser = ExcelParser()

    def test_supported_extensions(self):
        """测试支持的扩展名"""
        exts = self.parser.supported_extensions()
        self.assertIn(".xlsx", exts)


class TestPPTParser(unittest.TestCase):
    """PPT 解析器测试"""

    def setUp(self):
        self.parser = PPTParser()

    def test_supported_extensions(self):
        """测试支持的扩展名"""
        exts = self.parser.supported_extensions()
        self.assertIn(".pptx", exts)


class TestPDFParser(unittest.TestCase):
    """PDF 解析器测试"""

    def setUp(self):
        self.parser = PDFParser()

    def test_supported_extensions(self):
        """测试支持的扩展名"""
        exts = self.parser.supported_extensions()
        self.assertIn(".pdf", exts)


if __name__ == "__main__":
    unittest.main()
