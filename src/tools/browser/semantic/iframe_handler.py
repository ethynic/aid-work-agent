"""iframe 嵌套处理

处理页面中的 iframe 嵌套内容，支持跨域 iframe 的内容提取。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from loguru import logger


@dataclass
class IFrameInfo:
    """iframe 信息"""

    ref: str
    src: Optional[str] = None
    name: Optional[str] = None
    title: Optional[str] = None
    width: int = 0
    height: int = 0
    visible: bool = True
    sandboxed: bool = False
    # 如果是同源 iframe，存储其内容
    content_elements: List[Dict[str, Any]] = field(default_factory=list)
    # 嵌套的子 iframe
    nested_iframes: List['IFrameInfo'] = field(default_factory=list)


class IFrameHandler:
    """iframe 处理器

    处理页面中的 iframe 元素，支持：
    - 检测页面中的所有 iframe
    - 提取 iframe 元信息
    - 对于同源 iframe，递归提取其内部元素
    - 在快照中标记 iframe 的位置和内容
    """

    IFRAME_SELECTORS = [
        "iframe",
        "[role='iframe']",
        "frame",
    ]

    def __init__(self):
        self._iframe_counter = 0

    def _generate_iframe_ref(self) -> str:
        """生成唯一的 iframe ref"""
        self._iframe_counter += 1
        return f"iframe{self._iframe_counter}"

    async def detect_iframes(
        self,
        page,
        include_content: bool = True,
        max_depth: int = 2,
    ) -> List[IFrameInfo]:
        """检测页面中的所有 iframe

        Args:
            page: Playwright 页面对象
            include_content: 是否提取同源 iframe 的内容
            max_depth: 最大递归深度

        Returns:
            List[IFrameInfo]: iframe 信息列表
        """
        iframes = []

        try:
            # 获取主页面中的所有 iframe
            iframe_elements = await page.query_selector_all(",".join(self.IFRAME_SELECTORS))

            for iframe_elem in iframe_elements:
                iframe_info = await self._extract_iframe_info(iframe_elem, page, include_content, max_depth, 0)
                if iframe_info:
                    iframes.append(iframe_info)

        except Exception as e:
            logger.error(f"检测 iframe 失败: {e}")

        return iframes

    async def _extract_iframe_info(
        self,
        iframe_element,
        parent_page,
        include_content: bool,
        max_depth: int,
        current_depth: int,
    ) -> Optional[IFrameInfo]:
        """提取单个 iframe 的信息

        Args:
            iframe_element: iframe 元素
            parent_page: 父页面对象
            include_content: 是否提取内容
            max_depth: 最大深度
            current_depth: 当前深度

        Returns:
            IFrameInfo 或 None
        """
        try:
            # 获取基本信息
            tag = await iframe_element.tag_name()
            if tag.lower() != "iframe":
                return None

            # 获取属性
            attrs = {}
            for attr in ["src", "name", "id", "title", "sandbox", "allow", "allowfullscreen"]:
                value = await iframe_element.get_attribute(attr)
                if value:
                    attrs[attr] = value

            # 检查可见性
            visible = await iframe_element.is_visible()
            if not visible:
                # 可选：跳过不可见的 iframe
                return None

            # 获取尺寸
            try:
                box = await iframe_element.bounding_box()
                width = int(box["width"]) if box else 0
                height = int(box["height"]) if box else 0
            except Exception:
                width = 0
                height = 0

            # 检查是否沙箱化（跨域）
            sandboxed = "sandbox" in attrs or not attrs.get("src", "").startswith("http")

            # 生成 ref
            iframe_ref = self._generate_iframe_ref()

            iframe_info = IFrameInfo(
                ref=iframe_ref,
                src=attrs.get("src"),
                name=attrs.get("name"),
                title=attrs.get("title"),
                width=width,
                height=height,
                visible=visible,
                sandboxed=sandboxed,
            )

            # 尝试获取同源 iframe 的内容
            if include_content and not sandboxed and current_depth < max_depth:
                try:
                    # 尝试切换到 iframe 并提取内容
                    frame_content = await self._extract_frame_content(
                        parent_page, iframe_element, iframe_ref, max_depth, current_depth
                    )
                    if frame_content:
                        iframe_info.content_elements = frame_content.get("elements", [])
                        iframe_info.nested_iframes = frame_content.get("iframes", [])
                except Exception as e:
                    logger.debug(f"无法提取 iframe 内容（可能跨域）: {e}")

            return iframe_info

        except Exception as e:
            logger.error(f"提取 iframe 信息失败: {e}")
            return None

    async def _extract_frame_content(
        self,
        page,
        iframe_element,
        parent_ref: str,
        max_depth: int,
        current_depth: int,
    ) -> Optional[Dict[str, Any]]:
        """提取同源 iframe 的内容

        Args:
            page: 父页面对象
            iframe_element: iframe 元素
            parent_ref: 父 iframe ref
            max_depth: 最大深度
            current_depth: 当前深度

        Returns:
            Dict 包含 elements 和 iframes
        """
        try:
            # 尝试使用 content_frame 获取 iframe 的 frame 对象
            frame = await iframe_element.content_frame()
            if not frame:
                return None

            # 在 frame 中执行 DOM 遍历
            elements_data = await frame.evaluate("""
                () => {
                    const elements = [];
                    const INTERACTIVE_TAGS = ['a', 'button', 'input', 'select', 'textarea', 'details'];

                    const isVisible = (el) => {
                        if (el.hidden) return false;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none') return false;
                        if (style.visibility === 'hidden') return false;
                        return true;
                    };

                    const traverse = (root, depth, parentRef) => {
                        if (depth > 6) return;

                        const children = root.children;
                        for (let i = 0; i < children.length; i++) {
                            const el = children[i];
                            const tagName = el.tagName.toLowerCase();

                            // 获取文本内容
                            let textContent = '';
                            if (el.children.length === 0) {
                                textContent = el.textContent || '';
                            } else {
                                for (const node of el.childNodes) {
                                    if (node.nodeType === Node.TEXT_NODE) {
                                        textContent += node.textContent;
                                    }
                                }
                            }

                            // 判断是否可交互
                            const isInteractive = INTERACTIVE_TAGS.includes(tagName) ||
                                el.getAttribute('role') === 'button' ||
                                el.getAttribute('role') === 'link';

                            // 收集数据
                            if (isInteractive || tagName === 'iframe') {
                                elements.push({
                                    ref: parentRef + '-e' + elements.length,
                                    tag: tagName,
                                    text: textContent.trim().substring(0, 100),
                                    visible: isVisible(el),
                                    role: el.getAttribute('role'),
                                    href: el.getAttribute('href'),
                                    type: el.getAttribute('type'),
                                    isIframe: tagName === 'iframe',
                                });
                            }

                            // 递归遍历
                            if (el.children.length > 0 && depth < 6) {
                                traverse(el, depth + 1, parentRef);
                            }
                        }
                    };

                    if (document.body) {
                        traverse(document.body, 1, arguments[0]);
                    }

                    return elements;
                }
            """, parent_ref)

            # 递归检测嵌套 iframe
            nested_iframes = []
            if current_depth < max_depth:
                nested_iframe_elements = await frame.query_selector_all("iframe")
                for nested_iframe in nested_iframe_elements:
                    nested_info = await self._extract_iframe_info(
                        nested_iframe, frame, True, max_depth, current_depth + 1
                    )
                    if nested_info:
                        nested_iframes.append(nested_info)

            return {
                "elements": elements_data or [],
                "iframes": nested_iframes,
            }

        except Exception as e:
            logger.debug(f"提取 frame 内容失败: {e}")
            return None

    def iframes_to_dict(self, iframes: List[IFrameInfo]) -> List[Dict[str, Any]]:
        """将 IFrameInfo 列表转换为字典

        Args:
            iframes: iframe 信息列表

        Returns:
            字典列表
        """
        result = []
        for iframe in iframes:
            iframe_dict = {
                "ref": iframe.ref,
                "src": iframe.src,
                "name": iframe.name,
                "title": iframe.title,
                "width": iframe.width,
                "height": iframe.height,
                "visible": iframe.visible,
                "sandboxed": iframe.sandboxed,
                "element_count": len(iframe.content_elements),
            }

            if iframe.content_elements:
                iframe_dict["content_elements"] = iframe.content_elements

            if iframe.nested_iframes:
                iframe_dict["nested_iframes"] = self.iframes_to_dict(iframe.nested_iframes)

            result.append(iframe_dict)

        return result


async def get_page_iframes(page, include_content: bool = True) -> List[Dict[str, Any]]:
    """获取页面中所有 iframe 的快捷函数

    Args:
        page: Playwright 页面对象
        include_content: 是否提取同源 iframe 的内容

    Returns:
        iframe 信息列表
    """
    handler = IFrameHandler()
    iframes = await handler.detect_iframes(page, include_content)
    return handler.iframes_to_dict(iframes)