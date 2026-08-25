"""
Excel → Markdown 转换

使用 markitdown 库将 Excel 内容转为 Markdown 表格，供大模型理解。
"""

import re
from pathlib import Path
from typing import Dict, Optional

from loguru import logger


def excel_to_markdown(file_path: str, sheet_name: Optional[str] = None,
                      max_rows: int = 100) -> Dict:
    """
    将 Excel 转为 Markdown 表格文本。

    Args:
        file_path: Excel 文件路径
        sheet_name: 指定 Sheet（默认全部）
        max_rows: 每个 Sheet 最大行数

    Returns:
        {"success": True, "markdown": "...", "sheet_count": N, "total_rows": N, "truncated": bool}
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    try:
        from markitdown import MarkItDown
        md_converter = MarkItDown()
        result = md_converter.convert(str(src))
        markdown = result.text_content or ""

        if not markdown.strip():
            return {"success": True, "markdown": "", "sheet_count": 0, "total_rows": 0, "truncated": False}

        # 如果指定了 sheet_name，尝试截取该 Sheet 的内容
        if sheet_name:
            markdown = _extract_sheet(markdown, sheet_name)

        # 按 max_rows 截断
        markdown, truncated, total_rows = _truncate_markdown(markdown, max_rows)

        sheet_count = _count_sheets(markdown)

        return {
            "success": True,
            "markdown": markdown,
            "sheet_count": sheet_count,
            "total_rows": total_rows,
            "truncated": truncated,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[ExcelToMd] 转换失败: {e}")
        return {"success": False, "error": f"Excel 转 Markdown 失败: {e}"}


def _extract_sheet(markdown: str, sheet_name: str) -> str:
    """从 markitdown 输出中提取指定 Sheet 的内容"""
    # markitdown 通常用 ## SheetName 格式分 sheet
    pattern = re.compile(
        rf"^##\s+{re.escape(sheet_name)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return markdown

    # 从匹配位置到下一个 ## 标题或文件末尾
    start = match.start()
    next_heading = re.compile(r"^##\s+", re.MULTILINE).search(markdown, match.end())
    end = next_heading.start() if next_heading else len(markdown)

    return markdown[start:end].strip()


def _truncate_markdown(markdown: str, max_rows: int):
    """截断 Markdown 表格到指定行数"""
    lines = markdown.split("\n")
    table_data_lines = 0
    total_rows = 0
    output_lines = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                in_table = True
                output_lines.append(line)
                continue

            # 跳过分隔行
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                output_lines.append(line)
                continue

            total_rows += 1
            if total_rows <= max_rows:
                output_lines.append(line)
            else:
                table_data_lines += 1
        else:
            in_table = False
            output_lines.append(line)

    truncated = table_data_lines > 0
    if truncated:
        output_lines.append(f"\n... (已截断，共 {total_rows} 行，显示前 {max_rows} 行)")

    return "\n".join(output_lines), truncated, total_rows


def _count_sheets(markdown: str) -> int:
    """统计 Markdown 中的 Sheet 数量"""
    return len(re.findall(r"^##\s+", markdown, re.MULTILINE))
