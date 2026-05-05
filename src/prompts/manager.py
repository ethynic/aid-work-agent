"""
提示词管理器 — 加载 .md 模板文件并渲染系统提示词
"""

from pathlib import Path
from typing import Dict

from loguru import logger

from .renderer import render_template

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptManager:
    def __init__(self):
        self._cache: Dict[str, str] = {}

    def load_template(self, name: str) -> str:
        if name not in self._cache:
            path = _TEMPLATES_DIR / name
            if not path.exists():
                raise FileNotFoundError(f"Prompt template not found: {path}")
            self._cache[name] = path.read_text(encoding="utf-8")
        return self._cache[name]

    def render(self, template_name: str, variables: Dict[str, str]) -> str:
        template = self.load_template(template_name)
        return render_template(template, variables)

    def clear_cache(self) -> None:
        self._cache.clear()
