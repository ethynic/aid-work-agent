"""
PDF 生成模块

使用 Pandoc + WeasyPrint 生成 PDF（支持 Markdown/HTML/DOCX → PDF）。
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from src.tools.pdf.pdf_lib import PdfFileHandler


def _find_pandoc() -> str:
    """查找 Pandoc 可执行文件路径。"""
    # 复用 Word 工具已有的查找逻辑
    try:
        from src.tools.word.md_to_word import _find_pandoc as word_find_pandoc
        return word_find_pandoc()
    except (ImportError, AttributeError):
        pass

    pandoc_path = os.environ.get("PANDOC_PATH", "")
    if pandoc_path and Path(pandoc_path).exists():
        return pandoc_path

    import shutil
    found = shutil.which("pandoc")
    if found:
        return found

    raise FileNotFoundError("Pandoc 未安装或不在 PATH 中")


def _get_default_css() -> str:
    """获取内置 CSS 样式文件路径。"""
    css_path = os.path.join(os.path.dirname(__file__), "default.css")
    return css_path if Path(css_path).exists() else ""


def md_to_pdf(md_text: str, output_name: Optional[str] = None,
              css: Optional[str] = None, title: str = "") -> Dict[str, Any]:
    """Markdown → PDF，通过 Pandoc + WeasyPrint。

    Args:
        md_text: Markdown 文本内容
        output_name: 输出文件名
        css: 自定义 CSS 文件路径
        title: 文档标题

    Returns:
        {"success": True, "file_path": str, "file_size": int}
    """
    # 复用 Word 工具的 markdown 预处理逻辑
    try:
        from src.tools.word.md_to_word import normalize_markdown
        md_text = normalize_markdown(md_text)
    except ImportError:
        pass

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.md")
            output_path = os.path.join(tmpdir, "output.pdf")

            Path(input_path).write_text(md_text, encoding="utf-8")

            cmd = [
                _find_pandoc(),
                input_path, "-o", output_path,
                "-f", "markdown+pipe_tables+raw_html+autolink_bare_uris",
                "--wrap=none",
            ]

            # 尝试使用 WeasyPrint 引擎
            try:
                import weasyprint  # noqa: F401
                cmd.extend(["--pdf-engine=weasyprint"])
            except ImportError:
                pass

            if title:
                cmd.extend(["-V", f"title={title}"])

            # CSS 样式
            css_file = css or _get_default_css()
            if css_file and Path(css_file).exists():
                cmd.extend(["--css", css_file])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if not Path(output_path).exists():
                error_detail = result.stderr[:500] if result.stderr else "未知错误"
                logger.error(f"[PdfWriter] md_to_pdf 失败: {error_detail}")
                return {
                    "success": False,
                    "error": "PDF 生成失败",
                    "debug": error_detail,
                }

            # 保存到会话目录
            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            return save_result

    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "PDF 生成超时（120秒）"}
    except Exception as e:
        logger.error(f"[PdfWriter] md_to_pdf 异常: {e}", exc_info=True)
        return {"success": False, "error": f"生成PDF失败: {e}"}


def html_to_pdf(html_text: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """HTML → PDF。

    优先使用 Pandoc 路径，失败时回退到 WeasyPrint 直接调用。
    """
    try:
        return _html_to_pdf_via_pandoc(html_text, output_name)
    except Exception:
        return _html_to_pdf_via_weasyprint(html_text, output_name)


def _html_to_pdf_via_pandoc(html_text: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """通过 Pandoc 将 HTML 转为 PDF。"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.html")
            output_path = os.path.join(tmpdir, "output.pdf")

            Path(input_path).write_text(html_text, encoding="utf-8")

            cmd = [_find_pandoc(), input_path, "-o", output_path]

            try:
                import weasyprint  # noqa: F401
                cmd.append("--pdf-engine=weasyprint")
            except ImportError:
                pass

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if not Path(output_path).exists():
                error_detail = result.stderr[:500] if result.stderr else "未知错误"
                raise RuntimeError(f"Pandoc HTML→PDF 失败: {error_detail}")

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            return save_result

    except FileNotFoundError as e:
        raise RuntimeError(str(e))


def _html_to_pdf_via_weasyprint(html_text: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """直接使用 WeasyPrint 将 HTML 转为 PDF（Pandoc 路径的备选）。"""
    try:
        from weasyprint import HTML

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.pdf")

            HTML(string=html_text).write_pdf(output_path)

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            return save_result

    except ImportError:
        return {"success": False, "error": "WeasyPrint 未安装，无法生成 PDF"}
    except Exception as e:
        logger.error(f"[PdfWriter] WeasyPrint 转换失败: {e}", exc_info=True)
        return {"success": False, "error": f"HTML转PDF失败: {e}"}


def docx_to_pdf(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """Word → PDF。

    优先使用 LibreOffice（效果最佳），回退到 Pandoc。
    """
    file_path = PdfFileHandler.resolve_path(file_path)
    if not Path(file_path).exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    # 尝试 LibreOffice 路径
    result = _docx_to_pdf_via_libreoffice(file_path, output_name)
    if result.get("success"):
        return result

    # 回退到 Pandoc 路径
    result = _docx_to_pdf_via_pandoc(file_path, output_name)
    if result.get("success"):
        return result

    return {"success": False, "error": "Word转PDF失败：LibreOffice 和 Pandoc 均不可用或转换失败"}


def _docx_to_pdf_via_libreoffice(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """通过 LibreOffice 将 DOCX 转为 PDF。"""
    import shutil

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return {"success": False, "error": "LibreOffice 未安装"}

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                soffice, "--headless", "--convert-to", "pdf",
                "--outdir", tmpdir, file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            # LibreOffice 输出文件名与输入文件名相同，扩展名改为 .pdf
            pdf_name = Path(file_path).stem + ".pdf"
            output_path = os.path.join(tmpdir, pdf_name)

            if not Path(output_path).exists():
                return {"success": False, "error": "LibreOffice 转换失败", "debug": result.stderr[:500]}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or pdf_name,
            )
            save_result["success"] = True
            return save_result

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "LibreOffice 转换超时"}
    except Exception as e:
        logger.error(f"[PdfWriter] LibreOffice 转换失败: {e}", exc_info=True)
        return {"success": False, "error": f"LibreOffice 转换失败: {e}"}


def _docx_to_pdf_via_pandoc(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """通过 Pandoc 将 DOCX 转为 PDF。"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.pdf")

            cmd = [_find_pandoc(), file_path, "-o", output_path]

            try:
                import weasyprint  # noqa: F401
                cmd.append("--pdf-engine=weasyprint")
            except ImportError:
                pass

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if not Path(output_path).exists():
                return {"success": False, "error": "Pandoc 转换失败", "debug": result.stderr[:500]}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            return save_result

    except Exception as e:
        logger.error(f"[PdfWriter] Pandoc DOCX→PDF 失败: {e}", exc_info=True)
        return {"success": False, "error": f"Pandoc 转换失败: {e}"}
