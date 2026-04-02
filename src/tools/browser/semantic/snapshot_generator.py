"""语义快照生成器

生成 LLM 可理解的页面语义结构表示。
"""

import asyncio
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger

from .element_classifier import ElementClassifier, ElementType
from .semantic_tagger import SemanticTagger
from .submenu_detector import SubmenuDetector, SubmenuSnapshot, MenuItem
from .ref_mapper import RefMapper, InteractiveElement
from .iframe_handler import IFrameHandler, get_page_iframes


@dataclass
class SnapshotCache:
    """快照缓存"""

    url: str
    content_hash: str
    snapshot_data: Dict[str, Any]
    timestamp: float
    element_count: int


@dataclass
class SemanticSnapshot:
    """语义快照数据类"""

    success: bool = True
    session_id: str = "default"
    url: str = ""
    title: str = ""
    mode: str = "interactive"
    regions: List[Dict[str, Any]] = field(default_factory=list)
    interactive_elements: List[Dict[str, Any]] = field(default_factory=list)
    submenu_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    iframe_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


class SemanticSnapshotGenerator:
    """语义快照生成器

    将 Playwright 页面转换为 LLM 可理解的语义结构。
    """

    # 区域标签优先级
    REGION_LABELS = {
        "nav": ["导航", "菜单", "主导航", "顶部导航"],
        "header": ["页头", "头部", "标题栏"],
        "aside": ["侧边栏", "侧栏", "边栏"],
        "main": ["主内容", "主要内容", "内容区"],
        "footer": ["页脚", "底部", "页脚信息"],
        "section": ["区域", "区块", "模块"],
    }

    def __init__(self, session_id: str = "default"):
        """初始化生成器

        Args:
            session_id: 浏览器会话 ID
        """
        self.session_id = session_id
        self.element_classifier = ElementClassifier()
        self.semantic_tagger = SemanticTagger()
        self.submenu_detector = SubmenuDetector()
        self.ref_mapper = RefMapper()
        self.iframe_handler = IFrameHandler()
        self._ref_counter = 0

        # 快照缓存（类级别共享）
        if not hasattr(SemanticSnapshotGenerator, '_cache'):
            SemanticSnapshotGenerator._cache = {}
        if not hasattr(SemanticSnapshotGenerator, '_cache_ttl'):
            SemanticSnapshotGenerator._cache_ttl = 300  # 5分钟 TTL

    def _get_page_content_hash(self, page) -> str:
        """获取页面内容哈希（用于缓存失效检测）

        Args:
            page: Playwright 页面对象

        Returns:
            str: 内容哈希
        """
        try:
            # 获取关键元素的变化来判断页面是否变化
            content = page.url
            # 简单哈希
            return hashlib.md5(content.encode()).hexdigest()[:16]
        except Exception:
            return ""

    def _is_cache_valid(self, url: str, content_hash: str) -> bool:
        """检查缓存是否有效

        Args:
            url: 页面 URL
            content_hash: 内容哈希

        Returns:
            bool: 缓存是否有效
        """
        cache_key = f"{self.session_id}:{url}"
        if cache_key not in self._cache:
            return False

        cache: SnapshotCache = self._cache[cache_key]

        # 检查哈希是否变化
        if cache.content_hash != content_hash:
            return False

        # 检查是否过期
        import time
        if time.time() - cache.timestamp > self._cache_ttl:
            return False

        return True

    def _store_cache(self, url: str, content_hash: str, snapshot: 'SemanticSnapshot') -> None:
        """存储快照到缓存

        Args:
            url: 页面 URL
            content_hash: 内容哈希
            snapshot: 语义快照
        """
        import time

        cache_key = f"{self.session_id}:{url}"
        cache = SnapshotCache(
            url=url,
            content_hash=content_hash,
            snapshot_data=snapshot.to_dict(),
            timestamp=time.time(),
            element_count=len(snapshot.interactive_elements),
        )
        self._cache[cache_key] = cache

        # 限制缓存数量
        if len(self._cache) > 100:
            # 清理最旧的缓存
            oldest_key = min(
                self._cache.keys(),
                key=lambda k: self._cache[k].timestamp
            )
            del self._cache[oldest_key]

    def _get_cached_snapshot(self, url: str) -> Optional[Dict[str, Any]]:
        """获取缓存的快照

        Args:
            url: 页面 URL

        Returns:
            Optional[Dict[str, Any]]: 缓存的快照数据或 None
        """
        cache_key = f"{self.session_id}:{url}"
        if cache_key in self._cache:
            return self._cache[cache_key].snapshot_data
        return None

    def invalidate_cache(self, url: Optional[str] = None) -> None:
        """使缓存失效

        Args:
            url: 如果提供，只清理该 URL 的缓存；否则清理所有缓存
        """
        if url:
            cache_key = f"{self.session_id}:{url}"
            if cache_key in self._cache:
                del self._cache[cache_key]
        else:
            self._cache.clear()

    def _generate_ref(self) -> str:
        """生成唯一的 ref"""
        self._ref_counter += 1
        return f"e{self._ref_counter}"

    async def generate(
        self,
        page,
        mode: str = "interactive",
        max_depth: int = 10,
        include_hidden: bool = False,
        use_cache: bool = True,
    ) -> SemanticSnapshot:
        """生成语义快照

        Args:
            page: Playwright 页面对象
            mode: 快照模式 (standard/interactive/compact)
            max_depth: DOM 遍历最大深度
            include_hidden: 是否包含隐藏元素
            use_cache: 是否使用缓存（默认 True）

        Returns:
            SemanticSnapshot: 语义快照
        """
        try:
            # 设置 page 引用到 ref_mapper，以便后续通过 data-ref 定位元素
            self.ref_mapper._page = page

            # 获取页面基本信息
            url = page.url
            title = await page.title()
            content_hash = self._get_page_content_hash(page)

            # 检查缓存
            if use_cache and self._is_cache_valid(url, content_hash):
                cached = self._get_cached_snapshot(url)
                if cached:
                    logger.info(f"使用缓存的语义快照: {url}")
                    cached.pop("from_cache", None)
                    return SemanticSnapshot(**cached)

            # 遍历 DOM 树，收集元素
            elements_data = await self._traverse_dom(
                page, mode, max_depth, include_hidden
            )

            # 构建交互元素列表
            interactive_elements = self._build_interactive_elements(elements_data)

            # 注入 data-ref 到 DOM 元素，以便后续操作时定位
            await self._inject_data_refs(page, interactive_elements)

            # 构建区域结构
            regions = self._build_regions(elements_data, mode)

            # 检测子菜单
            submenu_snapshots = await self._detect_submenus(
                page, interactive_elements
            )

            # 检测 iframe（Phase 3 新增）
            iframe_snapshots = await self._detect_iframes(page)

            # 构建快照（排除内部使用的 attrs 字段）
            clean_elements = [
                {k: v for k, v in elem.items() if k != "attrs"}
                for elem in interactive_elements
            ]

            snapshot = SemanticSnapshot(
                success=True,
                session_id=self.session_id,
                url=url,
                title=title,
                mode=mode,
                regions=regions,
                interactive_elements=clean_elements,
                submenu_snapshots=[asdict(s) for s in submenu_snapshots],
                iframe_snapshots=iframe_snapshots,
            )

            # 存储到缓存
            if use_cache:
                self._store_cache(url, content_hash, snapshot)

            logger.info(f"语义快照生成成功: {url}, 元素数: {len(interactive_elements)}")
            return snapshot

        except Exception as e:
            logger.error(f"语义快照生成失败: {e}")
            return SemanticSnapshot(
                success=False,
                session_id=self.session_id,
                error=str(e),
            )

    async def _traverse_dom(
        self,
        page,
        mode: str,
        max_depth: int,
        include_hidden: bool,
    ) -> List[Dict[str, Any]]:
        """遍历 DOM 树

        Args:
            page: Playwright 页面对象
            mode: 遍历模式
            max_depth: 最大深度
            include_hidden: 是否包含隐藏元素

        Returns:
            List[Dict[str, Any]]: 元素数据列表
        """
        try:
            # 使用 JavaScript 遍历 DOM
            elements_js = await page.evaluate("""
                (options) => {
                    const { maxDepth, includeHidden, mode } = options;
                    const elements = [];

                    // 可交互标签（完整列表）
                    const INTERACTIVE_TAGS = new Set([
                        'a', 'button', 'input', 'select', 'textarea', 'details', 'summary',
                        'area', 'option', 'optgroup', 'dialog',
                    ]);
                    const SKIP_TAGS = new Set(['script', 'style', 'noscript', 'meta', 'link', 'title', 'head', 'html', 'body', 'br', 'hr', 'wbr', 'svg', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'ellipse', 'g', 'defs', 'use', 'clippath', 'mask', 'symbol', 'marker', 'pattern', 'lineargradient', 'radialgradient', 'stop', 'filter', 'feblend', 'fecolormatrix', 'fecomponenttransfer', 'feflood', 'fegaussianblur', 'feimage', 'femerge', 'femergenode', 'feoffset', 'fepointlight', 'fespotlight', 'fetile', 'feturbulence', 'animate', 'animatetransform', 'animatemotion']);

                    // 可交互 role 列表
                    const INTERACTIVE_ROLES = new Set([
                        'button', 'link', 'checkbox', 'radio', 'tab', 'tablist',
                        'switch', 'slider', 'spinbutton', 'combobox', 'menuitem',
                        'menuitemcheckbox', 'menuitemradio', 'option', 'treeitem',
                        'textbox', 'searchbox', 'listbox', 'gridcell', 'grid',
                        'rowheader', 'columnheader', 'progressbar', 'scrollbar',
                        'separator', 'toolbar',
                    ]);

                    const isVisible = (el) => {
                        if (el.hidden) return false;
                        const style = window.getComputedStyle(el);
                        if (style.visibility === 'hidden') return false;
                        if (parseFloat(style.opacity) === 0) return false;
                        return true;
                    };

                    // 检查元素是否在可显示的容器中（display:none 的容器内元素不可见）
                    const isRendered = (el) => {
                        let current = el;
                        while (current && current !== document.body) {
                            const style = window.getComputedStyle(current);
                            if (style.display === 'none') return false;
                            current = current.parentElement;
                        }
                        return true;
                    };

                    // 容器元素标签（允许递归进入，即使自身尺寸为0）
                    const CONTAINER_TAGS = new Set([
                        'div', 'span', 'form', 'section', 'main', 'article',
                        'aside', 'header', 'footer', 'nav', 'ul', 'ol', 'li',
                        'table', 'tr', 'td', 'th', 'tbody', 'thead', 'tfoot',
                        'fieldset', 'label', 'p', 'h1', 'h2', 'h3', 'h4',
                        'h5', 'h6', 'details', 'summary', 'figure', 'figcaption',
                        'dl', 'dd', 'dt', 'blockquote', 'pre', 'code',
                        'dialog', 'menu', 'picture', 'video', 'audio',
                        'source', 'track', 'map', 'datalist', 'output',
                        'template', 'slot', 'blockquote', 'em', 'strong',
                        'small', 'mark', 'abbr', 'cite', 'q', 'dfn',
                        'var', 'samp', 'kbd', 'sub', 'sup', 'i', 'b',
                        'u', 's', 'ruby', 'rt', 'rp', 'bdi', 'bdo',
                        'wbr', 'data', 'time', 'ins', 'del',
                    ]);

                    const shouldSkip = (el) => {
                        const tag = el.tagName ? el.tagName.toLowerCase() : '';

                        // 交互元素：只检查 hidden/visibility/opacity + 是否在 display:none 容器中
                        if (INTERACTIVE_TAGS.has(tag) || INTERACTIVE_ROLES.has(el.getAttribute('role'))) {
                            if (!isVisible(el)) return true;
                            return !isRendered(el);
                        }
                        // contenteditable
                        if (el.getAttribute('contenteditable') === 'true' || el.isContentEditable) {
                            if (!isVisible(el)) return true;
                            return !isRendered(el);
                        }
                        // 容器元素：始终允许递归进入
                        if (CONTAINER_TAGS.has(tag)) {
                            return false;
                        }
                        // 其他元素：完整可见性检查
                        if (!isVisible(el)) return true;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none') return true;
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) return true;
                        return false;
                    };

                    // 提取元素文本（获取自身直接文本 + 子元素的文本摘要）
                    const getTextContent = (el) => {
                        // input 特殊处理：用 value 属性
                        if (el.tagName && el.tagName.toLowerCase() === 'input') {
                            const val = el.getAttribute('value');
                            if (val) return val;
                            // placeholder 作为后备文本来源
                            return el.getAttribute('placeholder') || '';
                        }
                        // 无子元素：直接取 textContent
                        if (el.children.length === 0) {
                            return el.textContent || '';
                        }
                        // 有子元素：取直接文本节点 + 第一个子元素的文本（覆盖按钮内的 span/img 场景）
                        let text = '';
                        for (const node of el.childNodes) {
                            if (node.nodeType === Node.TEXT_NODE) {
                                text += node.textContent;
                            }
                        }
                        // 如果直接文本为空，尝试取第一个有文本的子元素
                        if (!text.trim()) {
                            for (const child of el.children) {
                                const childText = child.textContent || '';
                                if (childText.trim()) {
                                    // 截取前50字符，避免过长
                                    text = childText.trim().substring(0, 50);
                                    break;
                                }
                            }
                        }
                        return text;
                    };

                    const traverse = (root, depth) => {
                        if (depth > maxDepth) return;

                        const children = root.children;
                        for (let i = 0; i < children.length; i++) {
                            const el = children[i];
                            const tagName = el.tagName ? el.tagName.toLowerCase() : '';

                            // 跳过不需要的标签
                            if (SKIP_TAGS.has(tagName)) continue;

                            // 获取属性
                            const attrs = {};
                            for (const attr of el.attributes) {
                                attrs[attr.name] = attr.value;
                            }

                            // 检查可见性
                            if (!includeHidden && shouldSkip(el)) continue;

                            // 跳过 type=hidden 的 input
                            if (tagName === 'input' && attrs['type'] === 'hidden') continue;

                            // 获取文本内容
                            const textContent = getTextContent(el);

                            // 判断是否可交互
                            const role = el.getAttribute('role');
                            const isInteractive = INTERACTIVE_TAGS.has(tagName) ||
                                INTERACTIVE_ROLES.has(role) ||
                                el.getAttribute('contenteditable') === 'true' ||
                                el.isContentEditable ||
                                el.getAttribute('tabindex') === '0';

                            // 判断是否有子菜单（更全面的检测）
                            const hasPopup = el.getAttribute('aria-haspopup') ||
                                el.getAttribute('aria-expanded') !== null ||
                                el.classList.contains('dropdown-toggle') ||
                                el.classList.contains('dropdown') ||
                                el.getAttribute('data-toggle') === 'dropdown' ||
                                el.getAttribute('data-bs-toggle') === 'dropdown' ||
                                (role === 'button' && el.getAttribute('data-target')) ||
                                el.querySelector('[role="menu"], .dropdown-menu, .submenu');

                            // 收集数据
                            elements.push({
                                tag: tagName,
                                attrs: attrs,
                                text: textContent.trim(),
                                depth: depth,
                                visible: isVisible(el),
                                isInteractive: isInteractive,
                                hasPopup: !!hasPopup,
                                role: role,
                                id: el.getAttribute('id'),
                                className: typeof el.className === 'string' ? el.className : '',
                            });

                            // 递归遍历子元素
                            if (el.children.length > 0 && depth < maxDepth) {
                                traverse(el, depth + 1);
                            }
                        }
                    };

                    // 从 body 开始遍历
                    traverse(document.body, 1);

                    return elements;
                }
            """, {"maxDepth": max_depth, "includeHidden": include_hidden, "mode": mode})

            return elements_js

        except Exception as e:
            logger.error(f"DOM 遍历失败: {e}")
            return []

    def _build_interactive_elements(
        self,
        elements_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """构建交互元素列表

        Args:
            elements_data: DOM 元素数据

        Returns:
            List[Dict[str, Any]]: 交互元素列表
        """
        interactive_elements = []
        self.ref_mapper.clear()
        self._ref_counter = 0

        for elem_data in elements_data:
            tag = elem_data.get("tag", "")
            attrs = elem_data.get("attrs", {})
            text = elem_data.get("text", "")
            is_interactive = elem_data.get("isInteractive", False)

            # 跳过不可交互元素
            if not is_interactive:
                continue

            # 生成 ref
            ref = self._generate_ref()

            # 生成语义标签
            label = self.semantic_tagger.generate_label(tag, attrs, text)

            # 跳过无标签元素
            if not label or label.startswith("<"):
                label = f"<{tag}>"

            # 生成 role
            role = self.semantic_tagger.generate_role(tag, attrs)

            # 获取 href（链接）
            href = attrs.get("href")

            # 获取 input type
            input_type = attrs.get("type", "")

            # 获取 disabled 状态
            disabled = attrs.get("disabled") is not None

            # 获取 has_popup
            has_popup = elem_data.get("hasPopup", False)

            # 构建元素数据
            elem_dict = {
                "ref": ref,
                "label": label,
                "tag": tag,
                "role": role,
                "href": href,
                "type": input_type if tag == "input" else None,
                "visible": elem_data.get("visible", True),
                "disabled": disabled,
                "has_popup": has_popup,
                "attrs": attrs,
            }

            interactive_elements.append(elem_dict)

            # 注册到 ref_mapper
            interactive_elem = InteractiveElement(
                ref=ref,
                label=label,
                tag=tag,
                role=role,
                href=href,
                input_type=input_type if tag == "input" else None,
                visible=elem_data.get("visible", True),
                disabled=disabled,
                has_popup=has_popup,
            )
            self.ref_mapper.register_element(interactive_elem)

        return interactive_elements

    async def _inject_data_refs(
        self,
        page,
        interactive_elements: List[Dict[str, Any]],
    ) -> None:
        """将 data-ref 属性注入到 DOM 元素上，以便后续通过 CSS 选择器定位

        Args:
            page: Playwright page
            interactive_elements: 交互元素列表（需要包含 attrs 字段）
        """
        # 构建定位信息，用于在 JS 中精确匹配元素
        locator_map = []
        for elem in interactive_elements:
            ref = elem.get("ref", "")
            tag = elem.get("tag", "")
            attrs = elem.get("attrs", {})
            if not ref or not tag:
                continue
            locator_map.append({
                "ref": ref,
                "tag": tag,
                "id": attrs.get("id", ""),
                "name": attrs.get("name", ""),
                "className": attrs.get("class", ""),
                "type": attrs.get("type", ""),
                "role": attrs.get("role", ""),
            })

        if not locator_map:
            return

        try:
            injected_count = await page.evaluate("""(locatorMap) => {
                let injected = 0;
                for (const item of locatorMap) {
                    // 优先用 ID 快速定位
                    if (item.id) {
                        const elById = document.getElementById(item.id);
                        if (elById && elById.tagName.toLowerCase() === item.tag.toLowerCase()) {
                            // 额外验证其他属性
                            if (item.type && elById.getAttribute('type') !== item.type) {
                                // ID 匹配但 type 不匹配，走通用流程
                            } else {
                                elById.setAttribute('data-ref', item.ref);
                                injected++;
                                continue;
                            }
                        }
                    }
                    // 通用流程：遍历同 tag 的所有元素
                    let candidates = document.querySelectorAll(item.tag);
                    for (const el of candidates) {
                        // 匹配 id
                        if (item.id && el.id !== item.id) continue;
                        // 匹配 name
                        if (item.name && el.getAttribute('name') !== item.name) continue;
                        // 匹配 type
                        if (item.type && el.getAttribute('type') !== item.type) continue;
                        // 匹配 role
                        if (item.role && el.getAttribute('role') !== item.role) continue;
                        // 匹配 className（部分匹配，因为 class 可能组合多个）
                        if (item.className) {
                            const elClasses = el.className.split(/\\s+/);
                            const requiredClasses = item.className.split(/\\s+/);
                            if (!requiredClasses.every(c => elClasses.includes(c))) continue;
                        }
                        // 匹配成功，注入 data-ref
                        el.setAttribute('data-ref', item.ref);
                        injected++;
                        break; // 每个 ref 只匹配第一个元素
                    }
                }
                return injected;
            }""", locator_map)
            logger.info(f"[data-ref] 注入完成: {injected_count}/{len(locator_map)} 个元素已注入 data-ref")
            if injected_count < len(locator_map):
                logger.warning(f"[data-ref] {len(locator_map) - injected_count} 个元素未能匹配到 DOM")
        except Exception as e:
            logger.warning(f"注入 data-ref 失败: {e}")

    def _build_regions(
        self,
        elements_data: List[Dict[str, Any]],
        mode: str,
    ) -> List[Dict[str, Any]]:
        """构建区域结构

        Args:
            elements_data: DOM 元素数据
            mode: 快照模式

        Returns:
            List[Dict[str, Any]]: 区域列表
        """
        if mode == "interactive":
            # 交互模式只返回精简区域信息
            return self._build_interactive_regions(elements_data)

        return self._build_standard_regions(elements_data)

    def _build_interactive_regions(
        self,
        elements_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """构建交互模式区域

        Args:
            elements_data: DOM 元素数据

        Returns:
            List[Dict[str, Any]]: 区域列表
        """
        regions = []
        current_nav = None
        current_form = None

        for elem_data in elements_data:
            tag = elem_data.get("tag", "")
            attrs = elem_data.get("attrs", {})
            is_interactive = elem_data.get("isInteractive", False)

            # 收集交互元素
            if is_interactive:
                # 使用 label 在 ref_mapper 中查找已有的 ref
                label = self.semantic_tagger.generate_label(tag, attrs, elem_data.get("text", ""))
                matched = self.ref_mapper.find_by_label(label)
                ref = matched[0].ref if matched else ""

                elem_dict = {
                    "type": "interactive",
                    "label": label,
                    "ref": ref,
                }

                # 尝试归类到区域
                role = attrs.get("role", "")
                if role in ("navigation", "menu", "menubar"):
                    elem_dict["region_type"] = "navigation"
                elif role == "form":
                    elem_dict["region_type"] = "form"

                regions.append(elem_dict)

        return regions[:20]  # 限制数量

    def _build_standard_regions(
        self,
        elements_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """构建标准模式区域

        Args:
            elements_data: DOM 元素数据

        Returns:
            List[Dict[str, Any]]: 区域列表
        """
        regions = []

        for elem_data in elements_data:
            tag = elem_data.get("tag", "")
            attrs = elem_data.get("attrs", {})
            role = attrs.get("role", "")

            # 只处理区域角色
            if role in ("navigation", "main", "banner", "contentinfo", "form", "region"):
                region_label = self._get_region_label(tag, role, attrs)
                regions.append({
                    "id": f"r{len(regions) + 1}",
                    "type": role,
                    "label": region_label,
                })

        return regions

    def _get_region_label(
        self,
        tag: str,
        role: str,
        attrs: Dict[str, Any],
    ) -> str:
        """获取区域标签

        Args:
            tag: HTML 标签
            role: ARIA role
            attrs: 元素属性

        Returns:
            str: 区域标签
        """
        # 尝试 aria-label
        aria_label = attrs.get("aria-label")
        if aria_label:
            return aria_label

        # 尝试 aria-labelledby
        aria_labelledby = attrs.get("aria-labelledby")
        if aria_labelledby:
            return f"<区域 {aria_labelledby}>"

        # 使用标准标签
        role_labels = {
            "navigation": "导航区域",
            "main": "主内容区",
            "banner": "头部区域",
            "contentinfo": "页脚区域",
            "form": "表单区域",
            "region": "区域",
        }

        return role_labels.get(role, f"<{tag}>")

    async def _detect_submenus(
        self,
        page,
        interactive_elements: List[Dict[str, Any]],
    ) -> List[SubmenuSnapshot]:
        """检测子菜单

        Args:
            page: Playwright 页面对象
            interactive_elements: 交互元素列表

        Returns:
            List[SubmenuSnapshot]: 子菜单快照列表
        """
        submenu_snapshots = []

        try:
            for elem_dict in interactive_elements:
                # 检查是否有子菜单
                if not elem_dict.get("has_popup", False):
                    continue

                ref = elem_dict.get("ref", "")
                label = elem_dict.get("label", "")

                # 获取元素句柄（通过属性定位，ref 是 Python 层面的，不是 DOM 属性）
                element = await self._find_element_by_attrs(page, elem_dict)

                if not element:
                    continue

                # 检测子菜单
                snapshot = await self.submenu_detector.detect_and_snapshot(
                    page, element, ref, label
                )

                if snapshot:
                    submenu_snapshots.append(snapshot)

                    # 注册子菜单项到 ref_mapper
                    for item in snapshot.items:
                        elem = InteractiveElement(
                            ref=item.ref,
                            label=item.label,
                            tag="a",
                            role="menuitem",
                            href=item.href,
                        )
                        self.ref_mapper.register_submenu_item(ref, elem)

        except Exception as e:
            logger.error(f"子菜单检测失败: {e}")

        return submenu_snapshots

    async def _detect_iframes(self, page) -> List[Dict[str, Any]]:
        """检测页面中的 iframe

        Args:
            page: Playwright 页面对象

        Returns:
            List[Dict[str, Any]]: iframe 信息列表
        """
        iframe_snapshots = []

        try:
            # 重置 iframe 处理器计数器
            self.iframe_handler._iframe_counter = 0

            # 检测 iframe
            iframes = await self.iframe_handler.detect_iframes(
                page,
                include_content=True,
                max_depth=2,
            )

            # 转换为字典
            iframe_snapshots = self.iframe_handler.iframes_to_dict(iframes)

            if iframe_snapshots:
                logger.info(f"检测到 {len(iframe_snapshots)} 个 iframe")

        except Exception as e:
            logger.error(f"iframe 检测失败: {e}")

        return iframe_snapshots

    async def _find_element_by_attrs(
        self,
        page,
        elem_dict: Dict[str, Any],
    ):
        """通过属性查找元素

        Args:
            page: Playwright 页面对象
            elem_dict: 元素数据

        Returns:
            找到的元素或 None
        """
        try:
            attrs = elem_dict.get("attrs", {})
            tag = elem_dict.get("tag", "")

            # 尝试 ID
            elem_id = attrs.get("id")
            if elem_id:
                element = await page.query_selector(f"#{elem_id}")
                if element:
                    return element

            # 尝试 class
            class_name = attrs.get("className", "")
            if class_name:
                # 取第一个类名
                first_class = class_name.split()[0] if class_name else ""
                if first_class:
                    element = await page.query_selector(f"{tag}.{first_class}")
                    if element:
                        return element

        except Exception:
            pass

        return None

    def get_ref_mapper(self) -> RefMapper:
        """获取 ref 映射器

        Returns:
            RefMapper: ref 映射器
        """
        return self.ref_mapper
