"""元素分类器

对 DOM 元素进行类型分类，区分可交互元素、导航元素、表单元素等。
"""

from enum import Enum
from typing import Set


class ElementType(Enum):
    """元素类型枚举"""

    # 可交互元素（点击、输入）
    INTERACTIVE = "interactive"
    # 导航元素（菜单、导航栏）
    NAVIGATION = "navigation"
    # 表单元素（表单容器）
    FORM = "form"
    # 内容元素（段落、标题等）
    CONTENT = "content"
    # 媒体元素（图片、视频）
    MEDIA = "media"
    # 其他元素
    OTHER = "other"


class ElementClassifier:
    """元素类型分类器"""

    # 可交互元素标签
    INTERACTIVE_TAGS: Set[str] = {
        "a",
        "button",
        "input",
        "select",
        "textarea",
        "details",
        "summary",
        "label",
    }

    # 导航相关标签
    NAVIGATION_TAGS: Set[str] = {
        "nav",
        "header",
        "aside",
        "menu",
        "ul",
        "ol",
        "li",
    }

    # 表单相关标签
    FORM_TAGS: Set[str] = {
        "form",
        "fieldset",
        "legend",
        "datalist",
        "output",
    }

    # 内容标签
    CONTENT_TAGS: Set[str] = {
        "article",
        "section",
        "main",
        "div",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "code",
        "span",
    }

    # 媒体标签
    MEDIA_TAGS: Set[str] = {
        "img",
        "video",
        "audio",
        "canvas",
        "svg",
        "iframe",
    }

    # 列表项标签
    LIST_ITEM_TAGS: Set[str] = {"li", "dd", "dt"}

    @classmethod
    def classify(cls, tag_name: str) -> ElementType:
        """根据标签名分类元素类型

        Args:
            tag_name: HTML 标签名（小写）

        Returns:
            ElementType: 元素类型
        """
        tag = tag_name.lower()

        if tag in cls.INTERACTIVE_TAGS:
            return ElementType.INTERACTIVE
        elif tag in cls.NAVIGATION_TAGS:
            return ElementType.NAVIGATION
        elif tag in cls.FORM_TAGS:
            return ElementType.FORM
        elif tag in cls.MEDIA_TAGS:
            return ElementType.MEDIA
        elif tag in cls.CONTENT_TAGS:
            return ElementType.CONTENT
        else:
            return ElementType.OTHER

    @classmethod
    def is_interactive(cls, tag_name: str) -> bool:
        """判断元素是否可交互

        Args:
            tag_name: HTML 标签名

        Returns:
            bool: 是否可交互
        """
        return cls.classify(tag_name) == ElementType.INTERACTIVE

    @classmethod
    def is_navigation(cls, tag_name: str) -> bool:
        """判断元素是否属于导航类

        Args:
            tag_name: HTML 标签名

        Returns:
            bool: 是否为导航元素
        """
        return cls.classify(tag_name) == ElementType.NAVIGATION

    @classmethod
    def is_form_element(cls, tag_name: str) -> bool:
        """判断元素是否属于表单或表单容器

        Args:
            tag_name: HTML 标签名

        Returns:
            bool: 是否为表单元素
        """
        tag = tag_name.lower()
        return tag in cls.FORM_TAGS or tag in cls.INTERACTIVE_TAGS

    @classmethod
    def is_visible(cls, tag_name: str, attributes: dict) -> bool:
        """简单判断元素是否可见

        注意：这是简化版本，完整判断需要 CSS 计算样式

        Args:
            tag_name: HTML 标签名
            attributes: 元素属性字典

        Returns:
            bool: 是否可能可见
        """
        # 隐藏属性检查
        hidden = attributes.get("hidden")
        if hidden is not None:
            return False

        # type 属性检查
        input_type = attributes.get("type", "").lower()
        if input_type == "hidden":
            return False

        # aria-hidden 检查
        if attributes.get("aria-hidden") == "true":
            return False

        return True
