"""
Word 转 Markdown

基于 markitdown 库，将 Word 文档转为 Markdown 文本。
"""

from pathlib import Path
from typing import Dict, Any


def convert(file_path: str) -> str:
    """将 .docx 文件转为 Markdown 文本"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(str(path))
    return result.text_content


def convert_to_md_file(file_path: str, output_path: str = None) -> str:
    """转换并保存为 .md 文件"""
    md_text = convert(file_path)
    if output_path is None:
        output_path = str(Path(file_path).with_suffix(".md"))
    Path(output_path).write_text(md_text, encoding="utf-8")
    return output_path
