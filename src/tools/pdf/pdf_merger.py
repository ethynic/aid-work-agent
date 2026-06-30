"""
PDF 合并/拆分/页面提取模块

使用 PyMuPDF 实现合并、拆分和页面提取操作。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.pdf.pdf_lib import PdfFileHandler


def merge_pdfs(file_paths: List[str], output_name: Optional[str] = None) -> Dict[str, Any]:
    """合并多个 PDF 文件为一个。

    Args:
        file_paths: PDF 文件路径列表
        output_name: 输出文件名

    Returns:
        {"success": True, "file_path": str, "file_size": int, "page_count": int}
    """
    import fitz

    if not file_paths or len(file_paths) < 2:
        return {"success": False, "error": "合并操作需要至少 2 个 PDF 文件"}

    try:
        merged = fitz.open()
        total_pages = 0

        for path in file_paths:
            resolved = PdfFileHandler.resolve_path(path)
            if not Path(resolved).exists():
                merged.close()
                return {"success": False, "error": f"文件不存在: {path}"}
            doc = fitz.open(resolved)
            merged.insert_pdf(doc)
            total_pages += doc.page_count
            doc.close()

        # 保存到临时文件
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_output = str(Path(tmpdir) / "merged.pdf")
            merged.save(temp_output)
            merged.close()

            save_result = PdfFileHandler.save_temp(
                source_path=temp_output,
                file_name=output_name or "merged.pdf",
            )
            save_result["success"] = True
            save_result["page_count"] = total_pages
            save_result["source_count"] = len(file_paths)
            return save_result

    except Exception as e:
        logger.error(f"[PdfMerger] 合并失败: {e}", exc_info=True)
        return {"success": False, "error": f"合并PDF失败: {e}"}


def split_pdf(file_path: str, ranges: List[str],
              output_name: Optional[str] = None) -> Dict[str, Any]:
    """按页码范围拆分 PDF。

    Args:
        file_path: PDF 文件路径
        ranges: 页码范围列表，如 ["1-3", "5-7"]（页码从1开始）
        output_name: 输出文件名前缀

    Returns:
        {"success": True, "files": list, "count": int}
    """
    import fitz

    file_path = PdfFileHandler.resolve_path(file_path)
    if not Path(file_path).exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    try:
        doc = fitz.open(file_path)
        base_name = output_name or Path(file_path).stem
        output_files = []
        warnings = []

        for range_str in ranges:
            parts = range_str.split("-")
            if len(parts) != 2:
                warnings.append(f"忽略无效页码范围: {range_str}")
                continue
            try:
                start = int(parts[0].strip()) - 1  # 转为 0-indexed
                end = int(parts[1].strip())  # end 是闭区间，fitz 用法中 end 独占
            except ValueError:
                warnings.append(f"忽略无效页码范围: {range_str}")
                continue

            start = max(0, start)
            end = min(end, doc.page_count)
            if start >= end:
                warnings.append(f"忽略越界或空页码范围: {range_str}")
                continue

            new_doc = fitz.open()
            try:
                new_doc.insert_pdf(doc, from_page=start, to_page=end - 1)

                # 保存
                import tempfile
                with tempfile.TemporaryDirectory() as tmpdir:
                    part_name = f"{base_name}_p{start+1}-{end}.pdf"
                    temp_path = str(Path(tmpdir) / part_name)
                    new_doc.save(temp_path)

                    save_result = PdfFileHandler.save_temp(
                        source_path=temp_path,
                        file_name=part_name,
                    )
                    output_files.append(save_result)
            finally:
                new_doc.close()

        doc.close()

        if not output_files:
            return {
                "success": False,
                "error": "拆分PDF失败：没有有效的页码范围",
                "warnings": warnings,
            }

        return {
            "success": True,
            "files": output_files,
            "count": len(output_files),
            "warnings": warnings,
        }
    except Exception as e:
        logger.error(f"[PdfMerger] 拆分失败: {e}", exc_info=True)
        return {"success": False, "error": f"拆分PDF失败: {e}"}


def extract_pages(file_path: str, pages: List[int],
                  output_name: Optional[str] = None) -> Dict[str, Any]:
    """提取 PDF 的指定页面为独立文件。

    Args:
        file_path: PDF 文件路径
        pages: 页码列表（从1开始），如 [1, 3, 5]
        output_name: 输出文件名

    Returns:
        {"success": True, "file_path": str, "file_size": int, "page_count": int}
    """
    import fitz

    file_path = PdfFileHandler.resolve_path(file_path)
    if not Path(file_path).exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    try:
        doc = fitz.open(file_path)
        new_doc = fitz.open()
        valid_pages = []

        for page_num in pages:
            idx = page_num - 1  # 转为 0-indexed
            if 0 <= idx < doc.page_count:
                new_doc.insert_pdf(doc, from_page=idx, to_page=idx)
                valid_pages.append(page_num)

        if not valid_pages:
            new_doc.close()
            doc.close()
            return {"success": False, "error": "提取页面失败：没有有效的页码"}

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            part_name = output_name or f"{Path(file_path).stem}_extracted.pdf"
            temp_path = str(Path(tmpdir) / part_name)
            new_doc.save(temp_path)
            new_doc.close()
            doc.close()

            save_result = PdfFileHandler.save_temp(
            source_path=temp_path,
            file_name=part_name,
        )
        save_result["success"] = True
        save_result["extracted_pages"] = valid_pages
        save_result["page_count"] = len(valid_pages)
        return save_result

    except Exception as e:
        logger.error(f"[PdfMerger] 页面提取失败: {e}", exc_info=True)
        return {"success": False, "error": f"提取页面失败: {e}"}
