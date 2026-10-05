from __future__ import annotations
import re
from typing import Optional
from loguru import logger
from src.config.settings import settings
from src.models.user import User

class RememberCoordinator:
    def __init__(self, tenant_id, style_manager):
        self._get_effective_tenant_id = lambda: tenant_id
        self.style_manager = style_manager
    async def _handle_remember_intent(self, user_input: str, user: Optional[User]) -> None:
        """
        检测用户"记住"意图，将内容写入长期记忆文件。

        支持的表达方式：帮我记住...、记住...、以后记住...、记一下...
        """
        if not settings.memory.long_term.enabled or not user:
            return

        # 检测"记住"类意图
        remember_patterns = [
            r'^帮我记住[：:\s]*(.+)',
            r'^记住[：:\s]*(.+)',
            r'^以后记住[：:\s]*(.+)',
            r'^记一下[：:\s]*(.+)',
            r'^请记住[：:\s]*(.+)',
            r'^帮我记[：:\s]*(.+)',
        ]

        content_to_remember = None
        for pattern in remember_patterns:
            match = re.match(pattern, user_input.strip(), re.IGNORECASE)
            if match:
                content_to_remember = match.group(1).strip()
                break

        if not content_to_remember:
            return

        try:
            tenant_id = self._get_effective_tenant_id()
            from src.memory.long_term import LongTermMemory
            ltm = LongTermMemory(storage_dir=settings.memory.long_term.storage_dir)

            # 检测回复风格设定意图
            style_match = re.match(
                r'^回复风格[是为用]\s*(.+)',
                content_to_remember,
                re.IGNORECASE,
            )
            if not style_match:
                style_match = re.match(
                    r'^(.+?)风格(?:回复|回答|交流)?',
                    content_to_remember,
                    re.IGNORECASE,
                )
            if style_match:
                raw_style = style_match.group(1).strip()
                # 模糊匹配风格 ID
                tenant_id_for_style = self._get_effective_tenant_id()
                available = self.style_manager.list_styles(tenant_id_for_style)
                matched_id = self._fuzzy_match_style(raw_style, available)
                if matched_id:
                    ltm.set_reply_style(tenant_id=tenant_id, user_id=user.user_id, style_id=matched_id)
                    logger.info(f"User {user.user_id} set reply style to: {matched_id}")
                    return
                else:
                    logger.warning(f"User tried to set unknown reply style: {raw_style}, available: {available}")

            ltm.merge_memory(
                tenant_id=tenant_id,
                user_id=user.user_id,
                new_sections={
                    "用户明确要求记住的事项": [content_to_remember]
                },
            )
            logger.info(f"Remembered for user {user.user_id}: {content_to_remember[:50]}")
        except Exception as e:
            logger.warning(f"Failed to save 'remember' intent: {e}")

    def _fuzzy_match_style(self, raw: str, available: list) -> Optional[str]:
        """模糊匹配风格 ID：精确匹配 > 包含匹配"""
        raw_lower = raw.lower().strip()
        # 精确匹配
        if raw_lower in available:
            return raw_lower
        # 包含匹配（用户说"拟人"匹配 "human-like"）
        style_aliases = {
            "拟人": "human-like",
            "拟人化": "human-like",
            "像人": "human-like",
            "像真人": "human-like",
            "专业": "professional",
            "极简": "concise",
            "简洁": "concise",
            "详尽": "detailed",
            "详细": "detailed",
        }
        for alias, style_id in style_aliases.items():
            if alias in raw_lower:
                if style_id in available:
                    return style_id
        return None

