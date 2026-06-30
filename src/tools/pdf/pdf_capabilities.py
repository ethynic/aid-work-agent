"""PDF 工具运行时依赖探测。"""

import importlib.util
import shutil
import subprocess
from typing import Any, Dict


PYTHON_PACKAGES = {
    "fitz": "PyMuPDF",
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "fpdf": "fpdf2",
    "pymupdf4llm": "PyMuPDF4LLM",
    "reportlab": "reportlab",
}

COMMANDS = {
    "pdftoppm": "poppler_pdftoppm",
    "pdfinfo": "poppler_pdfinfo",
    "soffice": "libreoffice",
    "libreoffice": "libreoffice_alt",
    "pandoc": "pandoc",
}


def get_capabilities() -> Dict[str, Any]:
    """返回 PDF 工具依赖可用性报告。"""
    packages = {}
    for module_name, display_name in PYTHON_PACKAGES.items():
        packages[display_name] = {
            "available": importlib.util.find_spec(module_name) is not None,
            "module": module_name,
        }

    commands = {}
    for command, display_name in COMMANDS.items():
        path = shutil.which(command)
        commands[display_name] = {
            "available": bool(path),
            "command": command,
            "path": path or "",
        }

    return {
        "success": True,
        "packages": packages,
        "commands": commands,
        "renderer": _select_renderer(commands),
    }


def _select_renderer(commands: Dict[str, Any]) -> str:
    if commands.get("poppler_pdftoppm", {}).get("available"):
        return "poppler"
    if importlib.util.find_spec("fitz") is not None:
        return "pymupdf"
    return "none"


def command_version(command: str) -> str:
    """尽力获取命令版本，失败时返回空字符串。"""
    path = shutil.which(command)
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except Exception:
        return ""
