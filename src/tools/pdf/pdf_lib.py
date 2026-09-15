"""
PDF 工具核心库

提供共享的基础功能：
- 文件操作（PdfFileHandler）
- 路径解析
- PDF 类型判断
"""

import uuid
import re
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

        file_name = PdfFileHandler.sanitize_pdf_filename(file_name)

        output_path = save_dir / file_name
        if output_path.exists():
            output_path = save_dir / f"{output_path.stem}_{uuid.uuid4().hex[:8]}{output_path.suffix}"

        import shutil
        shutil.copy2(str(src), str(output_path))

        file_size = output_path.stat().st_size
        return {
            "file_path": str(output_path.absolute()),
            "file_size": file_size,
            "display_name": output_path.name,
        }

    @staticmethod
    def sanitize_pdf_filename(file_name: Optional[str] = None) -> str:
        """生成安全的 PDF 文件名，禁止路径穿越并自动补齐 .pdf 后缀。"""
        if not file_name:
            return f"document_{uuid.uuid4().hex[:8]}.pdf"

        name = Path(str(file_name)).name.strip()
        if not name:
            name = f"document_{uuid.uuid4().hex[:8]}"

        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        name = re.sub(r"\s+", " ", name).strip(" .")
        if not name:
            name = f"document_{uuid.uuid4().hex[:8]}"

        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return name

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
        """解析文件路径（支持相对路径或 file_id）

        查找顺序：
        1. 原路径直接命中（含绝对路径）
        2. Redis 元数据命中（file_id -> uploaded_file:{file_id}.path，最可靠）
        3. 磁盘全场景扫描兜底（storage/tenants/{tenant}/{scene}/，命中后回写 Redis 自愈）
        """
        p = Path(file_path)
        # 防路径穿越：含 .. 的相对路径不得进行 exists 检查或路径拼接
        # （Path.exists() 和 Path()/.. 都会自动 resolve 后命中项目外系统文件）
        if not p.is_absolute() and ".." in p.parts:
            return str(p.absolute())
        if p.exists():
            return str(p.absolute())

        # Redis 元数据 + 磁盘全场景扫描兜底：file_id 上传时写了 uploaded_file:{file_id}
        # 元数据；元数据丢失（迁移/过期）时按文件名主干全场景扫描磁盘并自愈回写
        try:
            from src.core.storage import resolve_uploaded_file_path
            resolved = resolve_uploaded_file_path(file_path)
            if resolved:
                return resolved
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
