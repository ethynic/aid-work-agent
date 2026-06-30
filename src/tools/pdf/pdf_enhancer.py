"""PDF 企业文档增强能力。"""

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.pdf.pdf_lib import PdfFileHandler


def clean_metadata(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """清理 PDF 元数据。"""
    try:
        from pypdf import PdfReader, PdfWriter

        path = PdfFileHandler.resolve_path(file_path)
        if not Path(path).exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        reader = PdfReader(path)
        if reader.is_encrypted:
            return {"success": False, "error": "PDF已加密，无法清理元数据"}

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({})

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "clean_metadata.pdf"
            with open(output_path, "wb") as f:
                writer.write(f)

            result = PdfFileHandler.save_temp(
                str(output_path),
                file_name=output_name or f"{Path(path).stem}_clean_metadata.pdf",
            )
            result["success"] = True
            return result
    except Exception as e:
        logger.error(f"[PdfEnhancer] 元数据清理失败: {e}", exc_info=True)
        return {"success": False, "error": f"清理PDF元数据失败: {e}"}


def protect_pdf(
    file_path: str,
    password: str,
    output_name: Optional[str] = None,
) -> Dict[str, Any]:
    """添加 PDF 打开密码。注意：返回值不包含明文密码。"""
    if not password:
        return {"success": False, "error": "protect 操作需要 password 参数"}

    try:
        from pypdf import PdfReader, PdfWriter

        path = PdfFileHandler.resolve_path(file_path)
        if not Path(path).exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        reader = PdfReader(path)
        if reader.is_encrypted:
            return {"success": False, "error": "PDF已加密，无法重复加密"}

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(user_password=password)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "protected.pdf"
            with open(output_path, "wb") as f:
                writer.write(f)

            result = PdfFileHandler.save_temp(
                str(output_path),
                file_name=output_name or f"{Path(path).stem}_protected.pdf",
            )
            result["success"] = True
            result["protected"] = True
            return result
    except Exception as e:
        logger.error(f"[PdfEnhancer] PDF加密失败: {e}", exc_info=True)
        return {"success": False, "error": f"PDF加密失败: {e}"}


def add_watermark(
    file_path: str,
    text: str,
    output_name: Optional[str] = None,
    opacity: float = 0.18,
    font_size: int = 42,
    rotate: int = 0,
) -> Dict[str, Any]:
    """添加文字水印。"""
    if not text:
        return {"success": False, "error": "add_watermark 操作需要 text 参数"}

    try:
        import fitz

        path = PdfFileHandler.resolve_path(file_path)
        if not Path(path).exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        doc = fitz.open(path)
        if doc.is_encrypted:
            doc.close()
            return {"success": False, "error": "PDF已加密，无法添加水印"}

        warnings = []
        normalized_rotate = _normalize_text_rotation(rotate)
        if normalized_rotate != rotate:
            warnings.append("PyMuPDF 文字水印仅支持 0/90/180/270 度旋转，已按 0 度处理")

        for page in doc:
            rect = page.rect
            page.insert_text(
                (rect.width * 0.22, rect.height * 0.55),
                text,
                fontsize=font_size,
                rotate=normalized_rotate,
                color=(0.65, 0.65, 0.65),
                fill_opacity=max(0.0, min(float(opacity), 1.0)),
                overlay=True,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "watermarked.pdf"
            doc.save(str(output_path), garbage=4, deflate=True)
            page_count = doc.page_count
            doc.close()

            result = PdfFileHandler.save_temp(
                str(output_path),
                file_name=output_name or f"{Path(path).stem}_watermarked.pdf",
            )
            result["success"] = True
            result["page_count"] = page_count
            if warnings:
                result["warnings"] = warnings
            return result
    except Exception as e:
        logger.error(f"[PdfEnhancer] 添加水印失败: {e}", exc_info=True)
        return {"success": False, "error": f"添加PDF水印失败: {e}"}


def rotate_pages(
    file_path: str,
    rotation: int,
    pages: Optional[List[int]] = None,
    output_name: Optional[str] = None,
) -> Dict[str, Any]:
    """旋转 PDF 页面。pages 使用 1-based 页码；None 表示全部页面。"""
    if rotation not in (90, 180, 270, -90, -180, -270):
        return {"success": False, "error": "rotate 操作的 rotation 只能是 90、180、270 或对应负数"}

    try:
        import fitz

        path = PdfFileHandler.resolve_path(file_path)
        if not Path(path).exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        doc = fitz.open(path)
        if doc.is_encrypted:
            doc.close()
            return {"success": False, "error": "PDF已加密，无法旋转页面"}

        selected = _valid_user_pages(pages, doc.page_count)
        if pages and not selected:
            doc.close()
            return {"success": False, "error": "rotate 操作没有有效页码"}
        if not selected:
            selected = list(range(1, doc.page_count + 1))

        normalized_rotation = rotation % 360
        for page_num in selected:
            page = doc[page_num - 1]
            page.set_rotation((page.rotation + normalized_rotation) % 360)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "rotated.pdf"
            doc.save(str(output_path), garbage=4, deflate=True)
            doc.close()

            result = PdfFileHandler.save_temp(
                str(output_path),
                file_name=output_name or f"{Path(path).stem}_rotated.pdf",
            )
            result["success"] = True
            result["rotated_pages"] = selected
            result["rotation"] = rotation
            return result
    except Exception as e:
        logger.error(f"[PdfEnhancer] 页面旋转失败: {e}", exc_info=True)
        return {"success": False, "error": f"旋转PDF页面失败: {e}"}


def compress_pdf(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """压缩 PDF。当前使用 PyMuPDF 的 garbage/deflate 保存策略。"""
    try:
        import fitz

        path = PdfFileHandler.resolve_path(file_path)
        if not Path(path).exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        original_size = Path(path).stat().st_size
        doc = fitz.open(path)
        if doc.is_encrypted:
            doc.close()
            return {"success": False, "error": "PDF已加密，无法压缩"}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "compressed.pdf"
            doc.save(str(output_path), garbage=4, deflate=True, clean=True)
            doc.close()

            result = PdfFileHandler.save_temp(
                str(output_path),
                file_name=output_name or f"{Path(path).stem}_compressed.pdf",
            )
            result["success"] = True
            result["original_size"] = original_size
            result["compressed_size"] = result.get("file_size", 0)
            if result["compressed_size"] >= original_size:
                result["warnings"] = ["压缩后文件未变小，可能原 PDF 已压缩"]
            return result
    except Exception as e:
        logger.error(f"[PdfEnhancer] PDF压缩失败: {e}", exc_info=True)
        return {"success": False, "error": f"压缩PDF失败: {e}"}


def extract_images(
    file_path: str,
    pages: Optional[List[int]] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """提取 PDF 内嵌图片。pages 使用 1-based 页码。"""
    try:
        import fitz

        path = PdfFileHandler.resolve_path(file_path)
        if not Path(path).exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        doc = fitz.open(path)
        if doc.is_encrypted:
            doc.close()
            return {"success": False, "error": "PDF已加密，无法提取图片"}

        selected = _valid_user_pages(pages, doc.page_count)
        if pages and not selected:
            doc.close()
            return {"success": False, "error": "extract_images 操作没有有效页码"}
        if not selected:
            selected = list(range(1, doc.page_count + 1))

        save_dir = Path(output_dir) if output_dir else PdfFileHandler.get_session_dir() / "pdf_images" / Path(path).stem
        save_dir.mkdir(parents=True, exist_ok=True)

        images = []
        for page_num in selected:
            page = doc[page_num - 1]
            for image_index, image in enumerate(page.get_images(full=True), start=1):
                xref = image[0]
                extracted = doc.extract_image(xref)
                ext = extracted.get("ext", "png")
                image_name = f"{Path(path).stem}_p{page_num:03d}_{image_index:02d}.{ext}"
                image_path = save_dir / image_name
                image_path.write_bytes(extracted.get("image", b""))
                images.append({
                    "page": page_num,
                    "image_index": image_index,
                    "file_path": str(image_path.absolute()),
                    "file_size": image_path.stat().st_size,
                })

        doc.close()
        return {
            "success": True,
            "images": images,
            "count": len(images),
            "warnings": [] if images else ["未提取到内嵌图片"],
        }
    except Exception as e:
        logger.error(f"[PdfEnhancer] 提取图片失败: {e}", exc_info=True)
        return {"success": False, "error": f"提取PDF图片失败: {e}"}


def _valid_user_pages(pages: Optional[List[int]], page_count: int) -> List[int]:
    if not pages:
        return []

    valid = []
    for page in pages:
        try:
            p = int(page)
        except (TypeError, ValueError):
            continue
        if 1 <= p <= page_count and p not in valid:
            valid.append(p)
    return valid


def _normalize_text_rotation(rotation: int) -> int:
    """PyMuPDF insert_text only accepts quarter-turn rotations."""
    try:
        value = int(rotation)
    except (TypeError, ValueError):
        return 0
    normalized = value % 360
    if normalized in (0, 90, 180, 270):
        return normalized
    return 0
