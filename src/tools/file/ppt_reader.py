"""
PPT文档处理工具

提供PowerPoint文件的读取功能，支持提取每页的文本内容
"""

from pathlib import Path
from typing import Any, Dict, List

from loguru import logger


class PPTReader:
    """PPT文档读取器"""

    def __init__(self):
        """初始化PPT读取器"""
        self.pptx = None
        self._init_pptx()

    def _init_pptx(self):
        """初始化python-pptx库"""
        try:
            from pptx import Presentation
            self.pptx = Presentation
            logger.info("python-pptx库初始化成功")
        except ImportError:
            logger.error("python-pptx库未安装，请运行: pip install python-pptx")
            self.pptx = None

    def read_ppt_document(self, file_path: str) -> Dict[str, Any]:
        """
        读取PPT文档内容

        Args:
            file_path: PPT文件路径

        Returns:
            包含文档内容的字典，每页内容独立返回
        """
        if self.pptx is None:
            return {
                "success": False,
                "error": "python-pptx库未安装，无法读取PPT文档"
            }

        try:
            path = Path(file_path)
            if not path.exists():
                return {
                    "success": False,
                    "error": f"文件不存在: {file_path}"
                }

            # 检查文件扩展名
            if path.suffix.lower() not in ['.pptx', '.ppt']:
                return {
                    "success": False,
                    "error": f"不支持的文件格式: {path.suffix}，仅支持.pptx和.ppt格式"
                }

            # 读取PPT文档
            prs = self.pptx(path)

            # 提取每页内容
            pages = self._extract_slides(prs)

            # 提取文档信息
            doc_info = self._extract_document_info(prs)

            # 合并所有内容用于兼容
            content_parts = []
            for page_info in pages:
                content_parts.append(f"=== 第{page_info['page']}页 ===\n{page_info['content']}")

            full_content = '\n\n'.join(content_parts)
            total_lines = len(full_content.splitlines())

            logger.info(f"成功读取PPT文档: {path} (页数: {len(pages)})")

            return {
                "success": True,
                "message": f"成功读取PPT文档，共{len(pages)}页",
                "file_path": str(path),
                "file_type": "pptx",
                "pages": pages,
                "page_count": len(pages),
                "document_info": doc_info,
                "content": full_content,
                "total_lines": total_lines
            }

        except Exception as e:
            logger.error(f"读取PPT文档失败: {e}")
            return {
                "success": False,
                "error": f"读取PPT文档失败: {str(e)}"
            }

    def _extract_slides(self, prs) -> List[Dict[str, Any]]:
        """
        提取PPT每页内容

        Args:
            prs: python-pptx的Presentation对象

        Returns:
            每页内容列表，格式为 [{"page": 1, "content": "..."}, ...]
        """
        pages = []

        for slide_num, slide in enumerate(prs.slides, start=1):
            page_content = []

            # 提取标题
            title = self._extract_slide_title(slide)
            if title:
                page_content.append(f"标题: {title}")

            # 提取文本内容
            text_content = self._extract_slide_text(slide)
            if text_content:
                page_content.append(f"正文: {text_content}")

            # 提取表格内容
            tables_content = self._extract_slide_tables(slide)
            if tables_content:
                page_content.append(f"表格: {tables_content}")

            # 组合页面内容
            content = '\n'.join(page_content) if page_content else ""

            pages.append({
                "page": slide_num,
                "content": content
            })

        return pages

    def _extract_slide_title(self, slide) -> str:
        """提取幻灯片标题"""
        if slide.shapes.title:
            return slide.shapes.title.text.strip()
        return ""

    def _extract_slide_text(self, slide) -> str:
        """提取幻灯片文本内容"""
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    # 过滤空段落
                    text = paragraph.text.strip()
                    if text and text != slide.shapes.title.text.strip():
                        texts.append(text)
        return '\n'.join(texts)

    def _extract_slide_tables(self, slide) -> str:
        """提取幻灯片表格内容"""
        tables_text = []
        for shape in slide.shapes:
            if shape.has_table:
                table = shape.table
                table_data = []
                for row in table.rows:
                    row_data = []
                    for cell in row.cells:
                        row_data.append(cell.text.strip())
                    table_data.append(" | ".join(row_data))

                if table_data:
                    tables_text.append('\n'.join(table_data))

        return '\n\n'.join(tables_text) if tables_text else ""

    def _extract_document_info(self, prs) -> Dict[str, Any]:
        """提取PPT文档元信息"""
        info = {}

        try:
            # 核心属性
            core_props = prs.core_properties
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

            # PPT特有属性
            info.update({
                "slide_count": len(prs.slides),
                "slide_width": prs.slide_width,
                "slide_height": prs.slide_height,
            })

        except Exception as e:
            logger.warning(f"提取文档信息失败: {e}")

        return info


def is_ppt_document(file_path: str) -> bool:
    """
    判断文件是否为PPT文档

    Args:
        file_path: 文件路径

    Returns:
        是否为PPT文档
    """
    path = Path(file_path)
    return path.suffix.lower() in ['.pptx', '.ppt']