"""语义标签生成器

为 DOM 元素生成语义化的标签描述，便于 LLM 理解元素含义。
"""

import re
from typing import Optional, Dict, Any, List


class SemanticTagger:
    """生成元素的语义标签"""

    # 按钮/链接常用文本模式
    BUTTON_KEYWORDS = {
        "确认", "确定", "提交", "保存", "取消", "关闭", "删除", "编辑",
        "修改", "新建", "创建", "添加", "查询", "搜索", "登录", "注册",
        "下一步", "上一步", "返回", "退出", "下载", "上传", "刷新",
        "submit", "confirm", "cancel", "save", "delete", "edit", "create",
        "add", "search", "login", "register", "next", "prev", "back",
    }

    # 输入框标签模式
    INPUT_LABELS = {
        "用户名": ["用户名", "账号", "user", "username", "loginname"],
        "密码": ["密码", "口令", "password", "passwd", "pwd"],
        "邮箱": ["邮箱", "邮件", "email", "mail"],
        "手机": ["手机", "电话", "phone", "tel", "mobile"],
        "金额": ["金额", "钱", "数额", "amount", "money", "sum"],
        "日期": ["日期", "时间", "date", "time", "datetime"],
        "描述": ["描述", "备注", "说明", "原因", "description", "remark", "note"],
    }

    def __init__(self):
        self._aria_cache: Dict[str, str] = {}

    def generate_label(
        self,
        tag: str,
        attributes: Dict[str, Any],
        text_content: str = "",
    ) -> str:
        """生成元素语义标签

        优先级：aria-label > aria-labelledby > text content > placeholder > title > 自动生成

        Args:
            tag: 元素标签名
            attributes: 元素属性字典
            text_content: 元素的文本内容

        Returns:
            str: 语义标签
        """
        # 1. 尝试 aria-label
        aria_label = attributes.get("aria-label")
        if aria_label:
            label = aria_label.strip()
            if label:
                return label

        # 2. 尝试 aria-labelledby（需要页面上下文，这里简化处理）
        aria_labelledby = attributes.get("aria-labelledby")
        if aria_labelledby:
            # 存储 ID 以便后续解析
            label = self._resolve_aria_labelledby(aria_labelledby, attributes)
            if label:
                return label

        # 3. 尝试有意义的文本内容
        text = self._extract_meaningful_text(text_content)
        if text and len(text) <= 50:
            return text

        # 4. 尝试 placeholder
        placeholder = attributes.get("placeholder")
        if placeholder:
            return f"输入框: {placeholder}"

        # 5. 尝试 title 属性
        title = attributes.get("title")
        if title:
            return title

        # 6. 尝试 value 属性（按钮类）
        value = attributes.get("value")
        if value and tag.lower() in ("button", "submit", "reset"):
            return value.strip()

        # 7. 根据标签和属性自动生成
        return self._auto_generate_label(tag, attributes)

    def _resolve_aria_labelledby(
        self,
        aria_labelledby: str,
        attributes: Dict[str, Any],
    ) -> Optional[str]:
        """解析 aria-labelledby 引用

        注意：由于没有页面上下文，这里返回简化结果
        """
        # 如果是当前元素的 ID 直接返回
        element_id = attributes.get("id", "")
        if aria_labelledby == element_id:
            return attributes.get("aria-label") or ""

        # 存储以便后续处理
        self._aria_cache[aria_labelledby] = ""
        return None

    def _extract_meaningful_text(self, text_content: str) -> str:
        """提取有意义的文本内容

        Args:
            text_content: 原始文本内容

        Returns:
            str: 清理后的文本
        """
        if not text_content:
            return ""

        # 移除多余空白
        text = re.sub(r"\s+", " ", text_content)
        text = text.strip()

        # 移除特殊字符（保留中文、英文、数字、常见符号）
        text = re.sub(r"[^\w\u4e00-\u9fff\s\-_.,;:!?()（）【】《》]", "", text)

        return text.strip()

    def _auto_generate_label(self, tag: str, attributes: Dict[str, Any]) -> str:
        """根据标签和属性自动生成标签

        Args:
            tag: 标签名
            attributes: 属性字典

        Returns:
            str: 自动生成的标签
        """
        tag_lower = tag.lower()

        # input 特殊处理
        if tag_lower == "input":
            input_type = attributes.get("type", "text").lower()
            name = attributes.get("name", "")
            id_attr = attributes.get("id", "")

            # 检查常用字段名
            for label, keywords in self.INPUT_LABELS.items():
                for kw in keywords:
                    if kw.lower() in name.lower() or kw.lower() in id_attr.lower():
                        return label

            # 根据 type 生成
            type_labels = {
                "email": "邮箱",
                "password": "密码",
                "number": "数字",
                "tel": "电话",
                "url": "网址",
                "search": "搜索",
                "date": "日期",
                "time": "时间",
                "file": "文件",
            }
            if input_type in type_labels:
                return type_labels[input_type]

            return "输入框"

        # button/submit/reset
        if tag_lower in ("button", "submit", "reset"):
            # 检查 value
            value = attributes.get("value", "")
            if value:
                return value

            # 检查文本内容（如果之前没提取到）
            text = attributes.get("text", "")
            if text:
                return text

            # 根据 type 生成默认文本
            if tag_lower == "submit":
                return "提交"
            elif tag_lower == "reset":
                return "重置"
            return "按钮"

        # select
        if tag_lower == "select":
            name = attributes.get("name", "")
            id_attr = attributes.get("id", "")
            for label, keywords in self.INPUT_LABELS.items():
                for kw in keywords:
                    if kw.lower() in name.lower() or kw.lower() in id_attr.lower():
                        return label
            return "下拉选择"

        # textarea
        if tag_lower == "textarea":
            name = attributes.get("name", "")
            placeholder = attributes.get("placeholder", "")
            if placeholder:
                return f"输入框: {placeholder}"
            return "文本框"

        # a (链接)
        if tag_lower == "a":
            href = attributes.get("href", "")
            # 常见链接模式
            if "logout" in href.lower() or "退出" in href:
                return "退出"
            if "login" in href.lower() or "登录" in href:
                return "登录"
            if "register" in href.lower() or "注册" in href:
                return "注册"
            return "链接"

        # details/summary
        if tag_lower == "summary":
            return attributes.get("text", "详情")

        return f"<{tag}>"

    def generate_role(self, tag: str, attributes: Dict[str, Any]) -> Optional[str]:
        """生成 ARIA role

        Args:
            tag: 标签名
            attributes: 属性字典

        Returns:
            str: ARIA role 或 None
        """
        # 优先使用已有的 role
        role = attributes.get("role")
        if role:
            return role

        tag_lower = tag.lower()

        # 根据标签推断 role
        role_map = {
            "a": "link",
            "button": "button",
            "input": "textbox",
            "select": "listbox",
            "textarea": "textbox",
            "nav": "navigation",
            "article": "article",
            "main": "main",
            "aside": "complementary",
            "header": "banner",
            "footer": "contentinfo",
            "form": "form",
            "section": "region",
        }

        inferred_role = role_map.get(tag_lower)

        # 检查 aria-haspopup
        if tag_lower in ("button", "a") and attributes.get("aria-haspopup"):
            return "menuitem"

        return inferred_role

    def should_include(self, tag: str, attributes: Dict[str, Any]) -> bool:
        """判断元素是否应该包含在快照中

        Args:
            tag: 标签名
            attributes: 属性字典

        Returns:
            bool: 是否包含
        """
        # 跳过脚本和样式
        if tag.lower() in ("script", "style", "noscript"):
            return False

        # 跳过隐藏元素
        if attributes.get("hidden") is not None:
            return False

        if attributes.get("type", "").lower() == "hidden":
            return False

        if attributes.get("aria-hidden") == "true":
            return False

        # 跳过空的不可交互元素
        if tag.lower() in ("script", "style", "link", "meta", "title", "head", "html"):
            return False

        return True
