"""
PDF 工具核心库

提供共享的基础功能：
- 文件操作（PdfFileHandler）
- 路径解析
- PDF 类型判断
"""

import uuid
from pathlib import Path
from typing import Any, Dict, Optional


class PdfFileHandler:
    """PDF 文件操作管理"""

    @staticmethod
    def save_temp(source_path: str, file_name: Optional[str] = None,
                  output_dir: Optional[str] = None) -> Dict[str, Any]:
        """将 PDF 文件保存到会话上传目录。"""
        src = Path(source_path)
        if not src.exists():
            return {"success": False, "error": f"源文件不存在: {source_path}"}

        if output_dir:
            save_dir = Path(output_dir)
        else:
            save_dir = PdfFileHandler.get_session_dir()

        save_dir.mkdir(parents=True, exist_ok=True)

        if not file_name:
            file_name = f"document_{uuid.uuid4().hex[:8]}.pdf"
        elif not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"

        output_path = save_dir / file_name
        import shutil
        shutil.copy2(str(src), str(output_path))

        file_size = output_path.stat().st_size
        return {
            "file_path": str(output_path.absolute()),
            "file_size": file_size,
        }

    @staticmethod
    def get_session_dir() -> Path:
        """获取当前用户会话的文件存储目录。"""
        try:
            from src.main import _get_tenant_upload_dir
            return _get_tenant_upload_dir()
        except ImportError:
            import tempfile
            return Path(tempfile.mkdtemp(prefix="pdf_"))

    @staticmethod
    def resolve_path(file_path: str) -> str:
        """解析文件路径（支持相对路径）"""
        p = Path(file_path)
        if p.exists():
            return str(p.absolute())
        try:
            from src.config.settings import settings
            uploads = Path(settings.storage.uploads_dir) / file_path
            if uploads.exists():
                return str(uploads.absolute())
        except (ImportError, AttributeError):
            pass
        return str(p.absolute())

    @staticmethod
    def get_page_count(file_path: str) -> int:
        """快速获取 PDF 页数"""
        import fitz
        doc = fitz.open(file_path)
        count = doc.page_count
        doc.close()
        return count

    @staticmethod
    def is_text_pdf(file_path: str, sample_pages: int = 3) -> bool:
        """判断 PDF 是文字型还是扫描件（采样前几页）"""
        import fitz
        doc = fitz.open(file_path)
        total_chars = 0
        for i in range(min(sample_pages, doc.page_count)):
            page = doc[i]
            total_chars += len(page.get_text("text").strip())
        doc.close()
        return total_chars > sample_pages * 50
