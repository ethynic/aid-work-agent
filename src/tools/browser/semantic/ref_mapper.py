"""ref 映射器

管理语义快照中的元素 ref 引用，提供 ref 到 Playwright 元素的映射。
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass


@dataclass
class InteractiveElement:
    """可交互元素"""

    ref: str
    label: str
    tag: str
    role: Optional[str] = None
    href: Optional[str] = None
    input_type: Optional[str] = None
    visible: bool = True
    disabled: bool = False
    has_popup: bool = False
    element_handle: Any = None  # Playwright ElementHandle


class RefMapper:
    """ref 映射器，管理元素引用和 Playwright 句柄的映射"""

    def __init__(self):
        # ref -> InteractiveElement
        self._ref_map: Dict[str, InteractiveElement] = {}
        # submenu ref -> (parent_ref, InteractiveElement)
        self._submenu_map: Dict[str, InteractiveElement] = {}

    def register_element(self, element: InteractiveElement) -> None:
        """注册一个可交互元素

        Args:
            element: 可交互元素
        """
        self._ref_map[element.ref] = element

    def register_submenu_item(
        self,
        parent_ref: str,
        element: InteractiveElement,
    ) -> None:
        """注册一个子菜单项

        Args:
            parent_ref: 父元素 ref
            element: 子菜单项元素
        """
        self._submenu_map[element.ref] = element

    def get_by_ref(self, ref: str) -> Optional[InteractiveElement]:
        """通过 ref 获取元素

        Args:
            ref: 元素 ref

        Returns:
            InteractiveElement 或 None
        """
        # 先检查主映射表
        if ref in self._ref_map:
            return self._ref_map[ref]

        # 再检查子菜单映射表
        if ref in self._submenu_map:
            return self._submenu_map[ref]

        return None

    def get_handle(self, ref: str):
        """通过 ref 获取 Playwright 元素句柄

        Args:
            ref: 元素 ref

        Returns:
            Playwright ElementHandle 或 None
        """
        element = self.get_by_ref(ref)
        if element:
            return element.element_handle
        return None

    def find_by_label(self, label: str, exact: bool = False) -> List[InteractiveElement]:
        """通过标签查找元素

        Args:
            label: 标签文本
            exact: 是否精确匹配

        Returns:
            匹配的元素列表
        """
        results = []
        label_lower = label.lower()

        for element in self._ref_map.values():
            element_label_lower = element.label.lower()

            if exact:
                if element_label_lower == label_lower:
                    results.append(element)
            else:
                if label_lower in element_label_lower or element_label_lower in label_lower:
                    results.append(element)

        return results

    def find_by_tag(self, tag: str) -> List[InteractiveElement]:
        """通过标签名查找元素

        Args:
            tag: HTML 标签名

        Returns:
            匹配的元素列表
        """
        tag_lower = tag.lower()
        return [
            elem for elem in self._ref_map.values()
            if elem.tag.lower() == tag_lower
        ]

    def find_interactive_by_tag(self, tag: str) -> List[InteractiveElement]:
        """查找指定标签的可交互元素

        Args:
            tag: HTML 标签名

        Returns:
            匹配的元素列表
        """
        return self.find_by_tag(tag)

    def find_buttons(self) -> List[InteractiveElement]:
        """查找所有按钮元素

        Returns:
            按钮元素列表
        """
        return [
            elem for elem in self._ref_map.values()
            if elem.tag.lower() in ("button", "a")
            or elem.role == "button"
        ]

    def find_inputs(self) -> List[InteractiveElement]:
        """查找所有输入元素

        Returns:
            输入元素列表
        """
        return [
            elem for elem in self._ref_map.values()
            if elem.tag.lower() in ("input", "textarea", "select")
        ]

    def find_links(self) -> List[InteractiveElement]:
        """查找所有链接元素

        Returns:
            链接元素列表
        """
        return [
            elem for elem in self._ref_map.values()
            if elem.tag.lower() == "a" and elem.href
        ]

    def clear(self) -> None:
        """清空所有映射"""
        self._ref_map.clear()
        self._submenu_map.clear()

    def get_all_refs(self) -> List[str]:
        """获取所有 ref 列表

        Returns:
            ref 列表
        """
        return list(self._ref_map.keys())

    def get_all_elements(self) -> List[InteractiveElement]:
        """获取所有元素

        Returns:
            元素列表
        """
        return list(self._ref_map.values())

    def from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """从快照数据初始化映射

        Args:
            snapshot: 语义快照字典
        """
        self.clear()

        # 从 interactive_elements 加载
        for elem_data in snapshot.get("interactive_elements", []):
            element = InteractiveElement(
                ref=elem_data.get("ref", ""),
                label=elem_data.get("label", ""),
                tag=elem_data.get("tag", ""),
                role=elem_data.get("role"),
                href=elem_data.get("href"),
                input_type=elem_data.get("type"),
                visible=elem_data.get("visible", True),
                disabled=elem_data.get("disabled", False),
                has_popup=elem_data.get("has_popup", False),
            )
            self.register_element(element)

        # 从 submenu_snapshots 加载子菜单项
        for submenu in snapshot.get("submenu_snapshots", []):
            parent_ref = submenu.get("trigger_ref", "")
            for item_data in submenu.get("items", []):
                element = InteractiveElement(
                    ref=item_data.get("ref", ""),
                    label=item_data.get("label", ""),
                    tag="a",  # 菜单项通常为链接
                    role="menuitem",
                    href=item_data.get("href"),
                )
                self.register_submenu_item(parent_ref, element)
