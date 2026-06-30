"""PPT 工具运行时依赖探测。"""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


PYTHON_PACKAGES = {
    "pptx": "python-pptx",
    "playwright": "playwright",
}


COMMANDS = {
    "node": "node",
    "npm": "npm",
    "soffice": "libreoffice",
    "libreoffice": "libreoffice_alt",
    "pdftoppm": "poppler_pdftoppm",
}


def get_capabilities(renderer_dir: Path | None = None) -> Dict[str, Any]:
    """返回 PPT 工具依赖可用性报告。"""
    base_dir = renderer_dir or Path(__file__).parent / "renderer-node"
    packages = {
        display_name: {
            "available": importlib.util.find_spec(module_name) is not None,
            "module": module_name,
        }
        for module_name, display_name in PYTHON_PACKAGES.items()
    }

    commands = {}
    for command, display_name in COMMANDS.items():
        path = shutil.which(command)
        commands[display_name] = {
            "available": bool(path),
            "command": command,
            "path": path or "",
            "version": command_version(command) if path else "",
        }

    node_renderer = probe_node_renderer(base_dir, commands)
    html_export = probe_playwright(packages["playwright"]["available"])
    return {
        "success": True,
        "packages": packages,
        "commands": commands,
        "node_renderer": node_renderer,
        "html_export": html_export,
        "render_validation": _select_render_validation(commands),
    }


def probe_node_renderer(renderer_dir: Path, commands: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """探测 Node 渲染器及其依赖是否就绪。"""
    commands = commands or {
        "node": {"available": bool(shutil.which("node"))},
        "npm": {"available": bool(shutil.which("npm"))},
    }
    package_json = renderer_dir / "package.json"
    pptxgenjs_dir = renderer_dir / "node_modules" / "pptxgenjs"

    if not commands.get("node", {}).get("available"):
        return {
            "available": False,
            "renderer_dir": str(renderer_dir),
            "reason": "Node.js 未安装或不在 PATH 中",
        }
    if not package_json.exists():
        return {
            "available": False,
            "renderer_dir": str(renderer_dir),
            "reason": "Node 渲染器工程尚未安装",
        }
    if not pptxgenjs_dir.exists():
        return {
            "available": False,
            "renderer_dir": str(renderer_dir),
            "reason": "Node 渲染器依赖未安装，请在 renderer-node 目录执行 npm install",
        }

    return {
        "available": True,
        "renderer_dir": str(renderer_dir),
        "reason": "",
    }


def command_version(command: str) -> str:
    """尽力获取命令版本，失败时返回空字符串。"""
    path = shutil.which(command)
    if not path:
        return ""

    version_args = {
        "node": ["--version"],
        "npm": ["--version"],
        "soffice": ["--version"],
        "libreoffice": ["--version"],
        "pdftoppm": ["-v"],
    }.get(command, ["--version"])

    try:
        result = subprocess.run(
            [path, *version_args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except Exception:
        return ""


def probe_playwright(package_available: bool) -> Dict[str, Any]:
    """探测 Playwright Python 包和 Chromium 运行时是否可用。"""
    if not package_available:
        return {
            "available": False,
            "browser": "chromium",
            "reason": "Playwright Python 包未安装",
        }

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable_path = Path(playwright.chromium.executable_path)
            if executable_path.is_file():
                return {
                    "available": True,
                    "browser": "chromium",
                    "reason": "",
                }
    except Exception:
        pass

    return {
        "available": False,
        "browser": "chromium",
        "reason": "Playwright Chromium 未安装或不可用",
    }


def _select_render_validation(commands: Dict[str, Any]) -> Dict[str, Any]:
    if commands.get("libreoffice", {}).get("available"):
        return {"available": True, "tool": "libreoffice", "reason": ""}
    if commands.get("libreoffice_alt", {}).get("available"):
        return {"available": True, "tool": "libreoffice", "reason": ""}
    if commands.get("poppler_pdftoppm", {}).get("available"):
        return {"available": True, "tool": "poppler", "reason": ""}
    return {
        "available": False,
        "tool": "none",
        "reason": "未找到 LibreOffice 或 Poppler 渲染验证工具",
    }
