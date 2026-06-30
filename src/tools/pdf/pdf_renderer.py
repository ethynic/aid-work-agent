"""PDF 页面渲染模块。"""

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.pdf.pdf_lib import PdfFileHandler


def render_pages(
    file_path: str,
    pages: Optional[List[int]] = None,
    dpi: int = 150,
    max_pages: int = 10,
    output_dir: Optional[str] = None,
    renderer: str = "auto",
) -> Dict[str, Any]:
    """将 PDF 页面渲染为 PNG。pages 使用 1-based 页码。"""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    out_dir = _prepare_output_dir(path, output_dir)
    selected_pages = _select_pages(path, pages, max_pages)
    if not selected_pages:
        return {"success": False, "error": "没有可渲染的页面"}

    if renderer in ("auto", "poppler") and shutil.which("pdftoppm"):
        result = _render_with_poppler(path, out_dir, selected_pages, dpi)
        if result.get("success") or renderer == "poppler":
            return result

    return _render_with_pymupdf(path, out_dir, selected_pages, dpi)


def _prepare_output_dir(path: Path, output_dir: Optional[str]) -> Path:
    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = (
            PdfFileHandler.get_session_dir()
            / "pdf_validation"
            / path.stem
            / uuid.uuid4().hex[:8]
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _select_pages(path: Path, pages: Optional[List[int]], max_pages: int) -> List[int]:
    import fitz

    doc = fitz.open(str(path))
    page_count = doc.page_count
    doc.close()

    if pages:
        selected = []
        for page in pages:
            try:
                p = int(page)
            except (TypeError, ValueError):
                continue
            if 1 <= p <= page_count and p not in selected:
                selected.append(p)
        return selected[:max_pages]

    if page_count <= max_pages:
        return list(range(1, page_count + 1))

    selected = [1, 2, 3, page_count]
    return sorted({p for p in selected if 1 <= p <= page_count})[:max_pages]


def _render_with_poppler(path: Path, out_dir: Path, pages: List[int], dpi: int) -> Dict[str, Any]:
    rendered = []
    errors = []
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return {"success": False, "error": "pdftoppm 未安装"}

    for page in pages:
        prefix = out_dir / f"page-{page:03d}"
        cmd = [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            str(path),
            str(prefix),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            expected = out_dir / f"page-{page:03d}-{page}.png"
            fallback = out_dir / f"page-{page:03d}-1.png"
            png_path = expected if expected.exists() else fallback
            if result.returncode == 0 and png_path.exists():
                rendered.append({"page": page, "image_path": str(png_path.absolute())})
            else:
                errors.append(f"第 {page} 页渲染失败: {(result.stderr or '').strip()[:200]}")
        except Exception as e:
            errors.append(f"第 {page} 页渲染异常: {e}")

    return {
        "success": bool(rendered) and not errors,
        "renderer": "poppler",
        "pages": rendered,
        "count": len(rendered),
        "errors": errors,
    }


def _render_with_pymupdf(path: Path, out_dir: Path, pages: List[int], dpi: int) -> Dict[str, Any]:
    try:
        import fitz

        rendered = []
        scale = dpi / 72
        doc = fitz.open(str(path))
        for page_num in pages:
            page = doc[page_num - 1]
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            out_path = out_dir / f"page-{page_num:03d}.png"
            pix.save(str(out_path))
            rendered.append({"page": page_num, "image_path": str(out_path.absolute())})
        doc.close()
        return {
            "success": True,
            "renderer": "pymupdf",
            "pages": rendered,
            "count": len(rendered),
            "errors": [],
        }
    except Exception as e:
        logger.error(f"[PdfRenderer] PyMuPDF 渲染失败: {e}", exc_info=True)
        return {"success": False, "renderer": "pymupdf", "error": f"PDF渲染失败: {e}"}
