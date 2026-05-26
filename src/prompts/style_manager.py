"""回复风格管理器"""

from pathlib import Path
from loguru import logger


class StyleManager:
    """管理回复风格文件的加载和解析"""

    STYLES_DIR = Path(__file__).parent / "styles"

    def __init__(self):
        self._styles: dict = {}  # style_id -> content
        self._load_all()

    def _load_all(self):
        """加载所有风格文件"""
        if not self.STYLES_DIR.exists():
            logger.warning(f"Styles directory not found: {self.STYLES_DIR}")
            return
        for f in self.STYLES_DIR.glob("*.md"):
            style_id = f.stem
            self._styles[style_id] = f.read_text(encoding="utf-8").strip()
            logger.info(f"Loaded reply style: {style_id}")

    def get_style(self, style_id: str) -> str | None:
        """获取风格内容，不存在返回 None"""
        return self._styles.get(style_id)

    def list_styles(self) -> list:
        """列出所有可用的风格 ID"""
        return list(self._styles.keys())

    def reload(self):
        """重新加载所有风格（热更新用）"""
        self._styles.clear()
        self._load_all()
