"""回复风格管理器"""

import time
from pathlib import Path
from loguru import logger


# 全局单例
style_manager = None


class StyleManager:
    """管理回复风格的加载和解析（数据库优先，磁盘文件 fallback）"""

    STYLES_DIR = Path(__file__).parent / "styles"
    CACHE_TTL = 30  # 秒

    def __init__(self):
        self._styles: dict = {}           # (tenant_id, style_id) -> content
        self._system_styles: dict = {}    # style_id -> content（系统内置）
        self._disk_styles: dict = {}      # style_id -> content（磁盘 fallback）
        self._last_load_time: float = 0
        self._db_loaded: bool = False
        self._load_disk_files()

    def _load_disk_files(self):
        """加载磁盘风格文件（fallback）"""
        if not self.STYLES_DIR.exists():
            return
        for f in self.STYLES_DIR.glob("*.md"):
            style_id = f.stem
            self._disk_styles[style_id] = f.read_text(encoding="utf-8").strip()
            logger.info(f"Loaded reply style from disk: {style_id}")

    def _load_from_db(self):
        """从数据库加载所有激活的风格"""
        try:
            from src.saas.db.reply_style_db import ReplyStyleDB, SYSTEM_TENANT
            styles = ReplyStyleDB.list_all_active()
            for s in styles:
                key = (s["tenant_id"], s["style_id"])
                self._styles[key] = s["content"]
                if s["tenant_id"] == SYSTEM_TENANT:
                    self._system_styles[s["style_id"]] = s["content"]
            self._db_loaded = True
            logger.info(f"Loaded {len(styles)} reply styles from database")
        except Exception as e:
            if not self._db_loaded:
                logger.warning(f"Failed to load reply styles from database, using disk fallback: {e}")
                # 数据库不可用时，将磁盘文件作为 fallback
                for style_id, content in self._disk_styles.items():
                    self._system_styles[style_id] = content
            else:
                logger.warning(f"Failed to refresh reply styles from database: {e}")
        self._last_load_time = time.time()

    def _maybe_refresh_cache(self):
        """检查缓存 TTL，过期则重新加载"""
        if not self._db_loaded:
            self._load_from_db()
            return
        if time.time() - self._last_load_time > self.CACHE_TTL:
            self._styles.clear()
            self._system_styles.clear()
            self._load_from_db()

    def get_style(self, style_id: str, tenant_id: str = None) -> str | None:
        """获取风格内容，支持租户隔离"""
        self._maybe_refresh_cache()

        # 1. 查租户自定义风格
        if tenant_id:
            content = self._styles.get((tenant_id, style_id))
            if content:
                return content

        # 2. 查系统内置风格
        content = self._system_styles.get(style_id)
        if content:
            return content

        # 3. 查磁盘 fallback
        return self._disk_styles.get(style_id)

    def list_styles(self, tenant_id: str = None) -> list:
        """列出可用的风格 ID"""
        self._maybe_refresh_cache()

        style_ids = set()
        # 系统内置
        style_ids.update(self._system_styles.keys())
        # 租户自定义
        if tenant_id:
            for (tid, sid) in self._styles:
                if tid == tenant_id:
                    style_ids.add(sid)
        else:
            for (tid, sid) in self._styles:
                style_ids.add(sid)
        # 磁盘 fallback
        style_ids.update(self._disk_styles.keys())

        return list(style_ids)

    def reload(self):
        """热更新：重新从数据库加载"""
        self._styles.clear()
        self._system_styles.clear()
        self._load_disk_files()
        self._load_from_db()

    def seed_system_styles(self):
        """将磁盘风格文件作为系统内置风格种子数据写入数据库"""
        if not self._disk_styles:
            return

        try:
            from src.saas.db.reply_style_db import ReplyStyleDB, SYSTEM_TENANT
            for style_id, content in self._disk_styles.items():
                if not ReplyStyleDB.exists(style_id, SYSTEM_TENANT):
                    ReplyStyleDB.create(
                        style_id=style_id,
                        tenant_id=SYSTEM_TENANT,
                        name=self._get_style_name(style_id),
                        content=content,
                        description=self._get_style_description(style_id),
                    )
                    logger.info(f"Seeded system reply style: {style_id}")
        except Exception as e:
            logger.warning(f"Failed to seed system styles: {e}")

    def _get_style_name(self, style_id: str) -> str:
        """根据 style_id 生成默认显示名称"""
        names = {
            "human-like": "拟人风格",
            "professional": "专业助手",
            "concise": "简洁风格",
            "detailed": "详尽风格",
        }
        return names.get(style_id, style_id)

    def _get_style_description(self, style_id: str) -> str:
        """根据 style_id 生成默认描述"""
        descriptions = {
            "human-like": "像真人同事一样对话，隐藏 AI 工作过程",
        }
        return descriptions.get(style_id, "")


def get_style_manager() -> StyleManager:
    """获取全局 StyleManager 单例"""
    global style_manager
    if style_manager is None:
        style_manager = StyleManager()
    return style_manager
