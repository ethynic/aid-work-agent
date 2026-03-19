"""
Word文档处理工具

提供Word文档的读取功能，支持提取文本、表格等内容
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class WordReader:
    """Word文档读取器"""

    def __init__(self):
        """初始化Word读取器"""
        self.docx = None
        self._init_docx()

    def _init_docx(self):
        """初始化python-docx库"""
        try:
            from docx import Document
            self.docx = Document
            logger.info("python-docx库初始化成功")
        except ImportError:
            logger.error("python-docx库未安装，请运行: pip install python-docx")
            self.docx = None

    def read_word_document(self, file_path: str) -> Dict[str, Any]:
        """
        读取Word文档内容

        Args:
            file_path: Word文档路径

        Returns:
            包含文档内容的字典
        """
        if self.docx is None:
            return {
                "success": False,
                "error": "python-docx库未安装，无法读取Word文档"
            }

        try:
            path = Path(file_path)
            if not path.exists():
                return {
                    "success": False,
                    "error": f"文件不存在: {file_path}"
                }

            # 检查文件扩展名
            if path.suffix.lower() not in ['.docx', '.doc']:
                return {
                    "success": False,
                    "error": f"不支持的文件格式: {path.suffix}，仅支持.docx和.doc格式"
                }

            # 检查.doc格式（需要转换）
            if path.suffix.lower() == '.doc':
                return {
                    "success": False,
                    "error": "目前仅支持.docx格式，请将.doc文件转换为.docx格式"
                }

            # 读取Word文档
            doc = self.docx(path)

            # 提取段落文本
            paragraphs = self._extract_paragraphs(doc)

            # 提取表格内容
            tables = self._extract_tables(doc)

            # 提取文档信息
            doc_info = self._extract_document_info(doc)

            # 合并所有内容
            content_parts = []
            content_parts.append("=== 文档段落 ===\n")
            content_parts.append('\n'.join(paragraphs))

            if tables:
                content_parts.append("\n\n=== 文档表格 ===\n")
                for i, table in enumerate(tables, 1):
                    content_parts.append(f"\n表格 {i}:\n{table}")

            full_content = ''.join(content_parts)
            total_lines = len(full_content.splitlines())

            logger.info(f"成功读取Word文档: {path} (段落: {len(paragraphs)}, 表格: {len(tables)})")

            return {
                "success": True,
                "message": "成功读取Word文档",
                "file_path": str(path),
                "file_type": "docx",
                "paragraphs": paragraphs,
                "paragraph_count": len(paragraphs),
                "tables": tables,
                "table_count": len(tables),
                "document_info": doc_info,
                "content": full_content,
                "total_lines": total_lines
            }

        except Exception as e:
            logger.error(f"读取Word文档失败: {e}")
            return {
                "success": False,
                "error": f"读取Word文档失败: {str(e)}"
            }

    def _extract_paragraphs(self, doc) -> List[str]:
        """
        提取文档段落

        Args:
            doc: python-docx的Document对象

        Returns:
            段落文本列表
        """
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():  # 过滤空段落
                paragraphs.append(para.text.strip())
        return paragraphs

    def _extract_tables(self, doc) -> List[str]:
        """
        提取文档表格

        Args:
            doc: python-docx的Document对象

        Returns:
            表格文本列表（每个表格格式化为字符串）
        """
        tables = []
        for table in doc.tables:
            table_text = []
            # 提取表头
            if table.rows:
                headers = []
                for cell in table.rows[0].cells:
                    headers.append(cell.text.strip())
                table_text.append(" | ".join(headers))
                table_text.append("-" * len(" | ".join(headers)))

                # 提取表格数据（跳过表头）
                for row in table.rows[1:]:
                    row_data = []
                    for cell in row.cells:
                        row_data.append(cell.text.strip())
                    table_text.append(" | ".join(row_data))

            tables.append('\n'.join(table_text))
        return tables

    def _extract_document_info(self, doc) -> Dict[str, Any]:
        """
        提取文档元信息

        Args:
            doc: python-docx的Document对象

        Returns:
            文档信息字典
        """
        info = {}

        try:
            # 核心属性
            core_props = doc.core_properties
            if core_props:
                info.update({
                    "title": core_props.title or "",
                    "author": core_props.author or "",
                    "subject": core_props.subject or "",
                    "created": str(core_props.created) if core_props.created else "",
                    "modified": str(core_props.modified) if core_props.modified else "",
                    "last_modified_by": core_props.last_modified_by or "",
                    "keywords": core_props.keywords or "",
                    "comments": core_props.comments or "",
                })
        except Exception as e:
            logger.warning(f"提取文档信息失败: {e}")

        return info


def is_word_document(file_path: str) -> bool:
    """
    判断文件是否为Word文档

    Args:
        file_path: 文件路径

    Returns:
        是否为Word文档
    """
    path = Path(file_path)
    return path.suffix.lower() in ['.docx', '.doc']
