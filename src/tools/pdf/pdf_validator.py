"""PDF 质量验证模块。"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.pdf.pdf_inspector import inspect_pdf
from src.tools.pdf.pdf_renderer import render_pages


def validate_pdf(
    file_path: str,
    level: str = "structural",
    pages: Optional[List[int]] = None,
    render_dpi: int = 150,
    max_pages: int = 10,
) -> Dict[str, Any]:
    """验证 PDF 文件。level: basic | structural | visual。"""
    level = level or "structural"
    errors = []
    warnings = []

    inspected = inspect_pdf(file_path)
    if not inspected.get("success"):
        return {
            "success": False,
            "level": level,
            "errors": [inspected.get("error", "PDF检查失败")],
            "warnings": inspected.get("warnings", []),
            "inspection": inspected,
            "rendered_pages": [],
        }

    if inspected.get("page_count", 0) <= 0:
        errors.append("PDF页数为0")
    if inspected.get("file_size", 0) <= 0:
        errors.append("PDF文件大小为0")

    if level in ("structural", "visual"):
        pages_info = inspected.get("pages", [])
        if pages_info:
            first_size = (pages_info[0].get("width"), pages_info[0].get("height"))
            for page in pages_info:
                size = (page.get("width"), page.get("height"))
                if size != first_size:
                    warnings.append(f"第 {page.get('page')} 页尺寸与首页不一致")
                if page.get("text_chars", 0) == 0:
                    warnings.append(f"第 {page.get('page')} 页无可提取文本，可能是扫描件或空白页")

        for key in ("author", "creator", "producer"):
            value = inspected.get("metadata", {}).get(key)
            if value:
                warnings.append(f"PDF元数据包含 {key}: {value}")

    rendered_pages = []
    if level == "visual":
        rendered = render_pages(file_path, pages=pages, dpi=render_dpi, max_pages=max_pages)
        if not rendered.get("success"):
            errors.append(rendered.get("error") or "; ".join(rendered.get("errors", [])) or "PDF渲染失败")
        rendered_pages = rendered.get("pages", [])
        warnings.extend(_check_rendered_images(rendered_pages))

    return {
        "success": not errors,
        "level": level,
        "errors": errors,
        "warnings": warnings,
        "inspection": inspected,
        "rendered_pages": rendered_pages,
    }


def _check_rendered_images(rendered_pages: List[Dict[str, Any]]) -> List[str]:
    warnings = []
    for item in rendered_pages:
        image_path = item.get("image_path", "")
        path = Path(image_path)
        if not path.exists() or path.stat().st_size == 0:
            warnings.append(f"第 {item.get('page')} 页渲染图片为空")
            continue

        try:
            from PIL import Image, ImageStat

            with Image.open(path) as img:
                stat = ImageStat.Stat(img.convert("L"))
                mean = stat.mean[0]
                stddev = stat.stddev[0]
                if stddev < 1 and mean > 250:
                    warnings.append(f"第 {item.get('page')} 页渲染结果接近全白")
                elif stddev < 1 and mean < 5:
                    warnings.append(f"第 {item.get('page')} 页渲染结果接近全黑")
        except Exception:
            # Pillow 缺失或图片读取失败不阻断验证，文件存在性已检查。
            continue
    return warnings
