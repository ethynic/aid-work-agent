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
        max_depth: int = 6,
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
            # 获取页面基本信息
            url = page.url
            title = await page.title()
            content_hash = self._get_page_content_hash(page)

            # 检查缓存
            if use_cache and self._is_cache_valid(url, content_hash):
                cached = self._get_cached_snapshot(url)
                if cached:
                    logger.info(f"使用缓存的语义快照: {url}")
                    cached["from_cache"] = True
                    return SemanticSnapshot(**cached)

            # 遍历 DOM 树，收集元素
            elements_data = await self._traverse_dom(
                page, mode, max_depth, include_hidden
            )

            # 构建交互元素列表
            interactive_elements = self._build_interactive_elements(elements_data)

            # 构建区域结构
            regions = self._build_regions(elements_data, mode)

            # 检测子菜单
            submenu_snapshots = await self._detect_submenus(
                page, interactive_elements
            )

            # 检测 iframe（Phase 3 新增）
            iframe_snapshots = await self._detect_iframes(page)

            # 构建快照
            snapshot = SemanticSnapshot(
                success=True,
                session_id=self.session_id,
                url=url,
                title=title,
                mode=mode,
                regions=regions,
                interactive_elements=interactive_elements,
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

                    const INTERACTIVE_TAGS = ['a', 'button', 'input', 'select', 'textarea', 'details'];
                    const SKIP_TAGS = ['script', 'style', 'noscript', 'meta', 'link', 'title', 'head', 'html', 'body'];

                    const isVisible = (el) => {
                        if (el.hidden) return false;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none') return false;
                        if (style.visibility === 'hidden') return false;
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) return false;
                        return true;
                    };

                    const traverse = (root, depth) => {
                        if (depth > maxDepth) return;

                        const children = root.children;
                        for (let i = 0; i < children.length; i++) {
                            const el = children[i];
                            const tagName = el.tagName.toLowerCase();

                            // 跳过不需要的标签
                            if (SKIP_TAGS.includes(tagName)) continue;

                            // 获取属性
                            const attrs = {};
                            for (const attr of el.attributes) {
                                attrs[attr.name] = attr.value;
                            }

                            // 检查可见性
                            const visible = isVisible(el);
                            if (!includeHidden && !visible) continue;

                            // 获取文本内容
                            let textContent = '';
                            if (el.children.length === 0) {
                                textContent = el.textContent || '';
                            } else {
                                // 只获取直接子文本节点
                                for (const node of el.childNodes) {
                                    if (node.nodeType === Node.TEXT_NODE) {
                                        textContent += node.textContent;
                                    }
                                }
                            }

                            // 判断是否可交互
                            const isInteractive = INTERACTIVE_TAGS.includes(tagName) ||
                                el.getAttribute('role') === 'button' ||
                                el.getAttribute('role') === 'link' ||
                                el.getAttribute('contenteditable') === 'true';

                            // 判断是否有子菜单
                            const hasPopup = el.getAttribute('aria-haspopup') ||
                                el.classList.contains('dropdown-toggle') ||
                                el.classList.contains('dropdown');

                            // 收集数据
                            elements.push({
                                tag: tagName,
                                attrs: attrs,
                                text: textContent.trim(),
                                depth: depth,
                                visible: visible,
                                isInteractive: isInteractive,
                                hasPopup: hasPopup,
                                role: el.getAttribute('role'),
                                id: el.getAttribute('id'),
                                className: el.className,
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
                # 生成 ref
                ref = self._generate_ref()

                # 获取 ref_mapper 中的 ref
                mapper_refs = self.ref_mapper.get_all_refs()
                if mapper_refs:
                    ref = mapper_refs[-1] if mapper_refs else ref

                elem_dict = {
                    "type": "interactive",
                    "label": self.semantic_tagger.generate_label(tag, attrs, elem_data.get("text", "")),
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

                # 获取元素句柄
                element = await page.query_selector(f"[ref='{ref}']")
                if not element:
                    # 尝试其他方式定位
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
