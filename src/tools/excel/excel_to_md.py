"""
Excel → Markdown 转换

使用 markitdown 库将 Excel 内容转为 Markdown 表格，供大模型理解。
"""

import re
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

# to_md 整簿转换守门阈值（2026-11-12 agent2 压测：1M 行 Excel 全量加载顶死 1GiB cgroup）。
# openpyxl 全量内存 ≈ cells × ~200B，50 万格约 100MB，为同容器其它执行体留余量。
TO_MD_MAX_CELLS = 500_000


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
        guard = _guard_too_large(src)
        if guard is not None:
            return guard

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


def _probe_sheet(ws) -> tuple:
    """探测单个 sheet 的真实规模，返回 (rows, cols, bounds_confirmed)。

    声明维度在正常范围内直接采信；膨胀时流式探测真实边界（参照
    excel_reader._bounded_dimensions，额外区分「确认边界」与「触顶未确认」——
    后者意味着数据密集大文件，必须按超限处理，防止真大文件在扫描上限处被放行）。
    """
    from src.tools.file.excel_reader import MAX_NORMAL_ROWS, STREAM_EMPTY_TAIL, STREAM_MAX_SCAN_ROWS

    declared_rows = ws.max_row or 0
    declared_cols = ws.max_column or 0
    # 声明维度缺失（如 write_only 生成的文件无 dimension，read_only 下 max_row=None）
    # 时不能当小文件放行——0 < r 的下界把缺失/0 推入流式探测分支
    if 0 < declared_rows <= MAX_NORMAL_ROWS and 0 < declared_cols <= MAX_NORMAL_ROWS:
        return declared_rows, declared_cols, True

    true_rows = 0
    true_cols = 0
    empty_streak = 0
    confirmed = False
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        col_idx = 0
        has_data = False
        for v in row:
            col_idx += 1
            if v is not None and v != "":
                has_data = True
                if col_idx > true_cols:
                    true_cols = col_idx
        if has_data:
            true_rows = row_idx
            empty_streak = 0
        elif true_rows:
            empty_streak += 1
            if empty_streak >= STREAM_EMPTY_TAIL:
                confirmed = True
                break
        if row_idx >= STREAM_MAX_SCAN_ROWS:
            break
    else:
        # iter_rows 自然扫完且未确认空尾 → 行数不超过扫描上限，边界可信
        confirmed = True
    return max(true_rows, 1), max(true_cols, 1), confirmed


def _estimate_scale(src: Path) -> tuple:
    """估算整簿规模，返回 (rows, cols, cells, bounds_confirmed)。

    探测异常（非 xlsx 等不受 openpyxl 管控的格式）时 fail-open 放行，
    由后续 MarkItDown 转换按原有行为处理。
    """
    import openpyxl

    wb = openpyxl.load_workbook(str(src), read_only=True, data_only=True)
    total_rows = 0
    total_cols = 0
    confirmed = True
    try:
        for ws in wb.worksheets:
            rows, cols, ws_confirmed = _probe_sheet(ws)
            total_rows += rows
            total_cols += cols
            if not ws_confirmed:
                confirmed = False
    finally:
        wb.close()
    return total_rows, total_cols, total_rows * total_cols, confirmed


def _guard_too_large(src: Path) -> Optional[Dict]:
    """to_md 转换前规模守门：超阈值 fail-fast，引导 read + range 分块。

    不建议改用 sheet_name——_extract_sheet 是 convert 后截取，整簿加载照旧，不省内存。
    """
    try:
        rows, cols, cells, confirmed = _estimate_scale(src)
    except Exception as e:
        logger.debug(f"[ExcelToMd] 规模探测跳过（{type(e).__name__}: {e}），按原路径转换")
        return None

    if confirmed and cells <= TO_MD_MAX_CELLS:
        return None

    if not confirmed:
        scale_desc = f"数据密集且超过流式探测上限（实际规模不低于约 {rows} 行 × {cols} 列）"
    else:
        scale_desc = f"约 {rows} 行 × {cols} 列（{cells} 单元格）"
    return {
        "success": False,
        "error": (
            f"Excel 整簿规模{scale_desc}，超出 to_md 整簿转换上限（{TO_MD_MAX_CELLS} 单元格），"
            "整簿加载会导致内存超限。请改用 read 操作按 range 分块读取"
            "（如 A1:C2000 逐段推进，单次最多返回 500 行有效数据）。"
        ),
        "rows_est": rows,
        "cols_est": cols,
        "cells_est": cells,
    }


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
