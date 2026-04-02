"""子菜单检测器

检测并展开折叠菜单，获取隐藏的菜单项。
支持动态子菜单检测（MutationObserver）和智能等待策略。
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Callable, Awaitable
import asyncio
import re


@dataclass
class MenuItem:
    """菜单项"""

    ref: str
    label: str
    action: str = "click"
    visible: bool = True
    href: Optional[str] = None
    disabled: bool = False


@dataclass
class SubmenuSnapshot:
    """子菜单快照"""

    trigger_ref: str
    trigger_label: str
    items: List[MenuItem]


class SubmenuDetector:
    """检测并处理折叠菜单/子菜单

    支持：
    - 传统 hover/focus/click 触发检测
    - MutationObserver 动态子菜单检测
    - 智能等待策略（替代固定 sleep）
    """

    # 子菜单触发元素的选择器模式
    SUBMENU_TRIGGERS = [
        "[aria-haspopup='true']",
        "[data-toggle='dropdown']",
        ".dropdown-toggle",
        "[role='menuitem']",
        "[role='menuitemcheckbox']",
        "[role='menuitemradio']",
        ".nav-item.dropdown",
        ".dropdown .dropdown-toggle",
    ]

    # 子菜单面板选择器模式
    SUBMENU_PANELS = [
        "[role='menu']",
        "[role='menubar']",
        ".dropdown-menu",
        ".nav-dropdown",
        ".submenu",
        "[data-dropdown-menu]",
        ".dropdown-menu-nav",
    ]

    def __init__(self):
        self._parent_ref: str = ""
        self._observer_callback: Optional[Callable[[List[Dict]], Awaitable[None]]] = None
        self._pending_submenus: Dict[str, asyncio.Event] = {}

    def set_observer_callback(self, callback: Callable[[List[Dict]], Awaitable[None]]) -> None:
        """设置 MutationObserver 回调函数

        Args:
            callback: 当检测到子菜单出现时的回调函数
        """
        self._observer_callback = callback

    async def wait_for_submenu_appearance(
        self,
        page,
        trigger_element,
        timeout: float = 2.0,
    ) -> Optional[Any]:
        """智能等待子菜单出现（替代固定 sleep）

        使用 MutationObserver 监听 DOM 变化，当子菜单出现时立即返回。

        Args:
            page: Playwright 页面对象
            trigger_element: 触发元素
            timeout: 超时时间（秒）

        Returns:
            子菜单元素或 None
        """
        try:
            # 创建事件用于同步
            menu_appeared = asyncio.Event()

            # 查找关联的菜单面板 ID
            menu_id = await trigger_element.get_attribute("aria-controls") or \
                      await trigger_element.get_attribute("data-target")

            # 通过 JavaScript 注入 MutationObserver
            if menu_id:
                # 监听特定 ID 的元素出现
                menu_found = await page.evaluate("""
                    (menuId) => {
                        return new Promise((resolve) => {
                            // 检查是否已存在
                            const existing = document.getElementById(menuId);
                            if (existing && (existing.offsetParent !== null || existing.style.display !== 'none')) {
                                resolve(true);
                                return;
                            }

                            // 创建 MutationObserver
                            const observer = new MutationObserver((mutations) => {
                                for (const mutation of mutations) {
                                    if (mutation.type === 'childList') {
                                        for (const node of mutation.addedNodes) {
                                            if (node.nodeType === Node.ELEMENT_NODE) {
                                                if (node.id === menuId ||
                                                    (node.children && Array.from(node.children).some(c => c.id === menuId))) {
                                                    observer.disconnect();
                                                    resolve(true);
                                                    return;
                                                }
                                            }
                                        }
                                    }
                                    if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                                        const el = mutation.target;
                                        if (el.id === menuId && el.style.display !== 'none') {
                                            observer.disconnect();
                                            resolve(true);
                                            return;
                                        }
                                    }
                                }
                            });

                            // 观察整个文档
                            observer.observe(document.body, {
                                childList: true,
                                subtree: true,
                                attributes: true,
                                attributeFilter: ['style', 'class', 'display']
                            });

                            // 超时处理
                            setTimeout(() => {
                                observer.disconnect();
                                resolve(false);
                            }, arguments[1] * 1000);
                        });
                    }
                """, menu_id, timeout)

                if menu_found:
                    # 返回菜单元素
                    return await page.query_selector(f"#{menu_id}")

            # 如果没有特定 ID，监听任何新增的菜单类元素
            menu_found = await page.evaluate("""
                () => {
                    return new Promise((resolve) => {
                        const startTime = Date.now();
                        const timeout = arguments[0] * 1000;

                        // 检查现有菜单
                        const existingMenus = document.querySelectorAll('.dropdown-menu, .dropdown-menu-nav, [role="menu"], .submenu, .nav-dropdown');
                        for (const menu of existingMenus) {
                            const style = window.getComputedStyle(menu);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                resolve(true);
                                return;
                            }
                        }

                        // 创建 MutationObserver
                        const observer = new MutationObserver((mutations) => {
                            if (Date.now() - startTime > timeout) {
                                observer.disconnect();
                                resolve(false);
                                return;
                            }

                            for (const mutation of mutations) {
                                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                                    for (const node of mutation.addedNodes) {
                                        if (node.nodeType === Node.ELEMENT_NODE) {
                                            const el = node;
                                            if (el.matches && (
                                                el.matches('.dropdown-menu, .dropdown-menu-nav, [role="menu"], .submenu, .nav-dropdown') ||
                                                el.querySelector('.dropdown-menu, .dropdown-menu-nav, [role="menu"]')
                                            )) {
                                                observer.disconnect();
                                                resolve(true);
                                                return;
                                            }
                                        }
                                    }
                                }
                            }
                        });

                        observer.observe(document.body, {
                            childList: true,
                            subtree: true
                        });

                        setTimeout(() => {
                            observer.disconnect();
                            resolve(false);
                        }, timeout);
                    });
                }
            """, timeout)

            if menu_found:
                # 返回第一个可见的菜单
                return await page.query_selector(".dropdown-menu:not([style*='display: none']), [role='menu']:not([style*='display: none'])")

        except Exception as e:
            pass

        return None

    async def detect_and_snapshot(
        self,
        page,
        element,
        element_ref: str,
        element_label: str,
    ) -> Optional[SubmenuSnapshot]:
        """检测元素是否有子菜单，如有则展开并获取快照

        Args:
            page: Playwright 页面对象
            element: Playwright 元素对象
            element_ref: 元素的 ref 引用
            element_label: 元素的语义标签

        Returns:
            SubmenuSnapshot: 子菜单快照，如果无子菜单则返回 None
        """
        try:
            # 检查是否有子菜单特征
            has_submenu = await self._has_submenu(element)
            if not has_submenu:
                return None

            # 记录父 ref
            self._parent_ref = element_ref

            # 尝试多种方式触发子菜单
            items = await self._get_menu_items_via_hover(page, element, element_ref)
            if not items:
                items = await self._get_menu_items_via_focus(page, element, element_ref)
            if not items:
                items = await self._get_menu_items_via_click(page, element, element_ref)

            if items:
                return SubmenuSnapshot(
                    trigger_ref=element_ref,
                    trigger_label=element_label,
                    items=items,
                )

        except Exception:
            pass

        return None

    async def _has_submenu(self, element) -> bool:
        """检查元素是否有子菜单特征

        Args:
            element: Playwright 元素对象

        Returns:
            bool: 是否有子菜单
        """
        try:
            # 检查 aria-haspopup
            has_popup = await element.get_attribute("aria-haspopup")
            if has_popup in ("true", "menu", "listbox", "tree", "grid"):
                return True

            # 检查 class 中的常见子菜单类名
            class_attr = await element.get_attribute("class") or ""
            if any(kw in class_attr.lower() for kw in ["dropdown", "submenu", "has-submenu"]):
                return True

            # 检查 data 属性
            data_toggle = await element.get_attribute("data-toggle")
            if data_toggle in ("dropdown", "submenu"):
                return True

        except Exception:
            pass

        return False

    async def _get_menu_items_via_hover(
        self,
        page,
        element,
        parent_ref: str,
    ) -> List[MenuItem]:
        """通过悬停触发子菜单并获取菜单项

        Args:
            page: Playwright 页面对象
            element: 触发元素
            parent_ref: 父元素 ref

        Returns:
            List[MenuItem]: 菜单项列表
        """
        try:
            # 悬停触发
            await element.hover()

            # 智能等待子菜单出现（替代固定 sleep）
            menu_panel = await self.wait_for_submenu_appearance(page, element, timeout=1.5)

            if not menu_panel:
                # 回退到传统方式：查找已存在的菜单面板
                menu_panel = await self._find_menu_panel(page, element)

            if menu_panel:
                items = await self._extract_menu_items_from_panel(menu_panel, parent_ref)
                if items:
                    return items

        except Exception:
            pass

        return []

    async def _get_menu_items_via_focus(
        self,
        page,
        element,
        parent_ref: str,
    ) -> List[MenuItem]:
        """通过聚焦触发子菜单并获取菜单项

        Args:
            page: Playwright 页面对象
            element: 触发元素
            parent_ref: 父元素 ref

        Returns:
            List[MenuItem]: 菜单项列表
        """
        try:
            # 聚焦触发
            await element.focus()

            # 智能等待子菜单出现
            menu_panel = await self.wait_for_submenu_appearance(page, element, timeout=1.0)

            if not menu_panel:
                menu_panel = await self._find_menu_panel(page, element)

            if menu_panel:
                items = await self._extract_menu_items_from_panel(menu_panel, parent_ref)
                if items:
                    return items

        except Exception:
            pass

        return []

    async def _get_menu_items_via_click(
        self,
        page,
        element,
        parent_ref: str,
    ) -> List[MenuItem]:
        """通过点击触发子菜单并获取菜单项

        Args:
            page: Playwright 页面对象
            element: 触发元素
            parent_ref: 父元素 ref

        Returns:
            List[MenuItem]: 菜单项列表
        """
        try:
            # 点击触发
            await element.click()

            # 智能等待子菜单出现
            menu_panel = await self.wait_for_submenu_appearance(page, element, timeout=1.0)

            if not menu_panel:
                menu_panel = await self._find_menu_panel(page, element)

            if menu_panel:
                items = await self._extract_menu_items_from_panel(menu_panel, parent_ref)
                if items:
                    return items

        except Exception:
            pass

        return []

    async def _find_menu_panel(self, page, trigger_element) -> Optional[Any]:
        """查找与触发元素关联的菜单面板

        Args:
            page: Playwright 页面对象
            trigger_element: 触发元素

        Returns:
            菜单面板元素或 None
        """
        try:
            # 方式1：通过 aria-controls 查找
            aria_controls = await trigger_element.get_attribute("aria-controls")
            if aria_controls:
                panel = await page.query_selector(f"#{aria_controls}")
                if panel:
                    return panel

            # 方式2：通过 aria-owns 查找
            aria_owns = await trigger_element.get_attribute("aria-owns")
            if aria_owns:
                panel = await page.query_selector(f"#{aria_owns}")
                if panel:
                    return panel

            # 方式3：通过 data-target 查找
            data_target = await trigger_element.get_attribute("data-target")
            if data_target:
                panel = await page.query_selector(data_target)
                if panel:
                    return panel

            # 方式4：查找相邻的菜单元素（通过 JS 生成唯一选择器）
            panel_selector = await page.evaluate(""" [element] => {
                const trigger = element;

                // 查找下一个兄弟元素
                let sibling = trigger.nextElementSibling;
                while (sibling) {
                    if (sibling.matches('[role="menu"], [role="menubar"], .dropdown-menu, .submenu, .nav-dropdown')) {
                        // 生成唯一选择器
                        if (sibling.id) return '#' + CSS.escape(sibling.id);
                        return sibling.tagName.toLowerCase() + '.' + [...sibling.classList].map(c => CSS.escape(c)).join('.');
                    }
                    sibling = sibling.nextElementSibling;
                }

                // 查找父元素的子菜单
                const parent = trigger.closest('[role="menu"], .dropdown, .nav-item');
                if (parent) {
                    const menu = parent.querySelector('[role="menu"], .dropdown-menu, .submenu');
                    if (menu) {
                        if (menu.id) return '#' + CSS.escape(menu.id);
                        return menu.tagName.toLowerCase() + '.' + [...menu.classList].map(c => CSS.escape(c)).join('.');
                    }
                }

                return null;
            } """, trigger_element)

            if panel_selector:
                return await page.query_selector(panel_selector)

        except Exception:
            pass

        return None

    async def _extract_menu_items_from_panel(
        self,
        panel,
        parent_ref: str,
    ) -> List[MenuItem]:
        """从菜单面板中提取菜单项

        Args:
            panel: 菜单面板元素
            parent_ref: 父元素 ref

        Returns:
            List[MenuItem]: 菜单项列表
        """
        items = []
        try:
            # 查询所有菜单项选择器
            menu_itemSelectors = [
                "[role='menuitem']",
                "[role='menuitemcheckbox']",
                "[role='menuitemradio']",
                "a",
                "button",
                ".dropdown-item",
                ".nav-link",
            ]

            all_items = []
            for selector in menu_itemSelectors:
                try:
                    elements = await panel.query_selector_all(selector)
                    for elem in elements:
                        all_items.append(elem)
                except Exception:
                    pass

            # 去重
            seen = set()
            unique_items = []
            for item in all_items:
                try:
                    tag = await item.tag_name
                    text = await item.inner_text()
                    key = (tag, text)
                    if key not in seen:
                        seen.add(key)
                        unique_items.append(item)
                except Exception:
                    pass

            # 提取菜单项信息
            for idx, item in enumerate(unique_items):
                try:
                    tag = await item.tag_name
                    text = (await item.inner_text()).strip()
                    href = await item.get_attribute("href")
                    disabled = await item.get_attribute("disabled")
                    is_hidden = await item.is_hidden()

                    # 跳过隐藏或禁用的项
                    if is_hidden or disabled:
                        continue

                    # 生成 ref
                    ref = f"{parent_ref}-{idx + 1}"

                    # 确定标签
                    if not text:
                        # 尝试 aria-label
                        aria_label = await item.get_attribute("aria-label")
                        if aria_label:
                            label = aria_label
                        else:
                            # 尝试 title
                            title = await item.get_attribute("title")
                            label = title if title else f"<{tag}>"
                    else:
                        label = text

                    items.append(MenuItem(
                        ref=ref,
                        label=label,
                        action="click",
                        visible=not is_hidden,
                        href=href if href else None,
                        disabled=disabled is not None,
                    ))

                except Exception:
                    continue

        except Exception:
            pass

        return items

    async def detect_all_submenus(
        self,
        page,
        interactive_elements: List[Dict[str, Any]],
    ) -> List[SubmenuSnapshot]:
        """检测页面上所有可展开的子菜单

        Args:
            page: Playwright 页面对象
            interactive_elements: 交互元素列表（包含 ref 和 element 对象）

        Returns:
            List[SubmenuSnapshot]: 所有子菜单快照列表
        """
        snapshots = []

        for elem_info in interactive_elements:
            ref = elem_info.get("ref", "")
            label = elem_info.get("label", "")
            element = elem_info.get("element")

            if not element or not ref:
                continue

            snapshot = await self.detect_and_snapshot(
                page, element, ref, label
            )
            if snapshot:
                snapshots.append(snapshot)

        return snapshots
