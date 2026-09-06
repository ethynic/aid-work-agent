"""
长期记忆系统

基于 Markdown 文件的用户记忆存储，支持租户隔离。
文件路径: storage/tenants/{tenant_id}/memory/memory_{user_id}.md
（2026-09 前为 storage/memory/{tenant_id}/，旧文件在首次访问时自动迁移）
"""

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# 空模板
EMPTY_TEMPLATE = """# 用户记忆

> 最后更新: {date}
> 更新来源: 系统

"""


class LongTermMemory:
    """
    长期记忆文件存储层

    使用结构化 Markdown 文件持久化用户长期记忆。
    按 tenant_id 隔离不同租户的用户记忆文件。
    """

    def __init__(self, storage_dir: str = "storage/memory"):
        # storage_dir 仅用于定位历史遗留的旧记忆文件（首次访问自动迁移到
        # storage/tenants/{tenant_id}/memory/），新写入不再使用该目录
        self._storage_dir = Path(storage_dir)

    def get_memory(self, tenant_id: Optional[str], user_id: str) -> str:
        """
        读取用户记忆文件内容，不存在返回空模板。

        Args:
            tenant_id: 租户 ID，None 时使用 'default'
            user_id: 用户 ID
        """
        file_path = self._get_file_path(tenant_id, user_id)
        self._migrate_legacy_file(tenant_id, user_id, file_path)
        if file_path.exists():
            try:
                return file_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read memory file {file_path}: {e}")
                return self._render_empty_template()
        return self._render_empty_template()

    def save_memory(
        self,
        tenant_id: Optional[str],
        user_id: str,
        content: str,
        updated_by: str = "user",
    ) -> None:
        """
        保存用户记忆文件，更新元信息。

        Args:
            tenant_id: 租户 ID，None 时使用 'default'
            user_id: 用户 ID
            content: 记忆文件内容
            updated_by: 更新来源 ('user' 或 'system')
        """
        # 格式校验：必须包含一级标题
        if not content.strip().startswith("# 用户记忆"):
            raise ValueError("记忆文件必须以 '# 用户记忆' 开头")

        file_path = self._get_file_path(tenant_id, user_id)
        self._migrate_legacy_file(tenant_id, user_id, file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 更新元信息
        content = self._update_meta(content, updated_by)

        file_path.write_text(content, encoding="utf-8")
        logger.debug(f"Saved memory for user {user_id} (tenant={tenant_id}, by={updated_by})")

    def merge_memory(
        self,
        tenant_id: Optional[str],
        user_id: str,
        new_sections: Dict[str, List[str]],
    ) -> None:
        """
        增量合并新记忆到现有文件（系统自动总结用）。

        Args:
            tenant_id: 租户 ID
            user_id: 用户 ID
            new_sections: {分类标题: [条目列表]} 结构的新记忆
        """
        existing = self.get_memory(tenant_id, user_id)
        existing_sections = self._parse_sections(existing)

        for section_title, new_items in new_sections.items():
            if not new_items:
                continue

            if section_title in existing_sections:
                # 合并：检测冲突条目（以相同前缀开头的视为同一事项的更新）
                existing_items = existing_sections[section_title]
                merged = list(existing_items)

                for new_item in new_items:
                    # 提取条目的关键词前缀用于匹配冲突
                    new_prefix = self._extract_item_prefix(new_item)
                    replaced = False
                    for i, existing_item in enumerate(merged):
                        existing_prefix = self._extract_item_prefix(existing_item)
                        if new_prefix and existing_prefix and new_prefix == existing_prefix:
                            merged[i] = new_item
                            replaced = True
                            break
                    if not replaced:
                        merged.append(new_item)

                existing_sections[section_title] = merged
            else:
                existing_sections[section_title] = new_items

        # 重新组装文件
        content = self._assemble_content(existing_sections)
        self.save_memory(tenant_id, user_id, content, updated_by="system")

    def get_memory_sections(
        self, tenant_id: Optional[str], user_id: str
    ) -> Dict[str, List[str]]:
        """
        解析文件，返回 {分类标题: [条目列表]} 的结构。
        """
        content = self.get_memory(tenant_id, user_id)
        return self._parse_sections(content)

    def memory_exists(self, tenant_id: Optional[str], user_id: str) -> bool:
        """检查用户是否有记忆文件"""
        file_path = self._get_file_path(tenant_id, user_id)
        self._migrate_legacy_file(tenant_id, user_id, file_path)
        return file_path.exists()

    def get_reply_style(self, tenant_id: Optional[str], user_id: str) -> Optional[str]:
        """
        读取用户记忆中的回复风格设置。

        记忆文件中 "用户明确要求记住的事项" 分类下，
        格式为 "回复风格：{style_id}" 的条目会被识别。

        Returns:
            风格 ID 字符串，不存在返回 None
        """
        sections = self.get_memory_sections(tenant_id, user_id)
        # 在 "用户明确要求记住的事项" 和 "工作习惯" 中查找
        for section_name in ("用户明确要求记住的事项", "工作习惯"):
            items = sections.get(section_name, [])
            for item in items:
                match = re.match(r'^回复风格[：:]\s*(.+)', item)
                if match:
                    return match.group(1).strip()
        return None

    def set_reply_style(self, tenant_id: Optional[str], user_id: str, style_id: str) -> None:
        """
        写入用户记忆的回复风格设置。

        在 "用户明确要求记住的事项" 分类下添加/更新 "回复风格：{style_id}" 条目。
        """
        sections = self.get_memory_sections(tenant_id, user_id)
        section_name = "用户明确要求记住的事项"

        items = sections.get(section_name, [])
        new_items = []
        replaced = False
        for item in items:
            if re.match(r'^回复风格[：:]', item):
                new_items.append(f"回复风格：{style_id}")
                replaced = True
            else:
                new_items.append(item)

        if not replaced:
            new_items.append(f"回复风格：{style_id}")

        sections[section_name] = new_items
        content = self._assemble_content(sections)
        self.save_memory(tenant_id, user_id, content, updated_by="user")

    def get_memory_for_injection(
        self,
        tenant_id: Optional[str],
        user_id: str,
        max_tokens: int = 2000,
    ) -> str:
        """
        获取格式化的长期记忆内容用于注入 LLM 上下文。

        按优先级截断：
        1. 用户明确要求记住的事项
        2. 语言偏好
        3. 工作习惯
        4. 常用联系人
        5. 个人介绍
        6. 其他分类
        """
        sections = self.get_memory_sections(tenant_id, user_id)
        if not sections:
            return ""

        # 按优先级排序
        priority_order = [
            "用户明确要求记住的事项",
            "语言偏好",
            "工作习惯",
            "常用联系人",
            "个人介绍",
        ]

        sorted_sections = []
        for title in priority_order:
            if title in sections and sections[title]:
                sorted_sections.append((title, sections[title]))

        # 剩余分类追加
        for title, items in sections.items():
            if title not in priority_order and items:
                sorted_sections.append((title, items))

        # 组装并按 token 限制截断（粗略估算：1 中文字 ≈ 1 token）
        lines = []
        current_length = 0

        for title, items in sorted_sections:
            section_text = f"{title}：\n"
            for item in items:
                section_text += f"- {item}\n"

            section_length = len(section_text)
            if current_length + section_length > max_tokens:
                # 截断当前分类
                remaining = max_tokens - current_length
                if remaining > 50:
                    partial_items = []
                    partial_len = len(f"{title}：\n")
                    for item in items:
                        item_text = f"- {item}\n"
                        if partial_len + len(item_text) > remaining:
                            break
                        partial_items.append(item)
                        partial_len += len(item_text)
                    if partial_items:
                        lines.append(f"{title}：\n" + "\n".join(f"- {it}" for it in partial_items) + "\n")
                break

            lines.append(section_text)
            current_length += section_length

        if not lines:
            return ""

        return "[用户记忆]\n" + "".join(lines)

    def _get_file_path(self, tenant_id: Optional[str], user_id: str) -> Path:
        """
        获取记忆文件的完整路径（租户附件存储规范）。
        tenant_id 为 None 时使用 'default'。
        返回: storage/tenants/{tenant_id}/memory/memory_{user_id}.md
        """
        from src.core.storage import get_tenant_storage_dir, normalize_tenant_id
        tenant_dir = normalize_tenant_id(tenant_id) if tenant_id else "default"
        return Path(get_tenant_storage_dir(tenant_dir, "memory")) / f"memory_{user_id}.md"

    def _migrate_legacy_file(
        self, tenant_id: Optional[str], user_id: str, new_path: Path
    ) -> None:
        """历史记忆文件一次性迁移到新路径。

        旧路径候选（2026-09 前）：{storage_dir}/tenant_{tid}/（带前缀）、
        {storage_dir}/{tid}/。新文件已存在或迁移完成后跳过；并发下源文件
        消失视为另一线程已迁移，静默返回。
        """
        if new_path.exists():
            return
        tenant_dir = tenant_id if tenant_id else "default"
        filename = f"memory_{user_id}.md"
        candidates = [
            self._storage_dir / f"tenant_{tenant_dir}" / filename,
            self._storage_dir / tenant_dir / filename,
        ]
        for old_path in candidates:
            try:
                if old_path.exists():
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_path), str(new_path))
                    logger.info(f"[LongTermMemory] 旧记忆文件已迁移: {old_path} -> {new_path}")
                    return
            except FileNotFoundError:
                return
            except Exception as e:
                logger.warning(f"[LongTermMemory] 旧记忆文件迁移失败 {old_path}: {e}")
                return

    def _render_empty_template(self) -> str:
        return EMPTY_TEMPLATE.format(date=datetime.now().strftime("%Y-%m-%d"))

    def _update_meta(self, content: str, updated_by: str) -> str:
        """更新文件元信息行"""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d %H:%M")
        source = "用户编辑" if updated_by == "user" else "系统自动"

        lines = content.split("\n")
        new_lines = []
        meta_replaced = False

        for line in lines:
            if line.startswith("> 最后更新:"):
                new_lines.append(f"> 最后更新: {date_str}")
                meta_replaced = True
            elif line.startswith("> 更新来源:"):
                new_lines.append(f"> 更新来源: {source}")
            else:
                new_lines.append(line)

        if not meta_replaced:
            # 在第一行标题后插入元信息
            result = []
            inserted = False
            for line in new_lines:
                result.append(line)
                if not inserted and line.startswith("# 用户记忆"):
                    result.append("")
                    result.append(f"> 最后更新: {date_str}")
                    result.append(f"> 更新来源: {source}")
                    inserted = True
            new_lines = result

        return "\n".join(new_lines)

    def _parse_sections(self, content: str) -> Dict[str, List[str]]:
        """解析 MD 文件为 {分类标题: [条目列表]} 结构"""
        sections: Dict[str, List[str]] = {}
        current_section = None

        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                current_section = stripped[3:].strip()
                if current_section not in sections:
                    sections[current_section] = []
            elif stripped.startswith("- ") and current_section is not None:
                sections[current_section].append(stripped[2:])

        return sections

    def _assemble_content(self, sections: Dict[str, List[str]]) -> str:
        """将 sections 结构组装为完整的 MD 文件内容"""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        lines = [
            "# 用户记忆",
            "",
            f"> 最后更新: {date_str}",
            "> 更新来源: 系统",
            "",
        ]

        for section_title, items in sections.items():
            lines.append(f"## {section_title}")
            lines.append("")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

        return "\n".join(lines)

    def _extract_item_prefix(self, item: str) -> str:
        """
        提取条目的关键词前缀用于冲突检测。
        例如 "姓名：张三" -> "姓名"
              "合同金额超过50万需要走特批流程" -> ""
        """
        # 匹配 "key：value" 或 "key: value" 格式
        match = re.match(r'^([^：:]+)[：:]', item)
        if match:
            return match.group(1).strip()
        return ""
