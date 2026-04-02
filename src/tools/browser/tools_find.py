"""browser_find 工具

通过自然语言描述查找页面元素。
"""

from typing import Any, Dict, List, Optional
from loguru import logger

from src.tools.base import BaseTool
from src.tools.browser.browser_tool import _browser_sessions
from src.tools.browser.semantic import NaturalMatcher
from src.tools.browser.tools_snapshot import get_ref_mapper


class BrowserFindTool(BaseTool):
    """根据语义描述查找元素工具"""

    name = "browser_find"
    description = """根据语义描述查找页面元素。通过自然语言描述目标特征，自动在页面中寻找匹配元素。

    **重要**：建议先调用 browser_snapshot 获取当前页面的语义快照！

    适用于：
    - 不知道具体 ref，根据描述查找
    - 动态页面，ref 可能变化
    - 需要在当前视口或整页中搜索

    返回结果包含:
    - ref: 匹配到的元素引用
    - label: 元素标签
    - tag: 元素标签名
    - role: ARIA 角色
    - confidence: 匹配置信度
    - alternatives: 其他可能的匹配选项

    注意：此工具只查找元素，不执行操作。需要配合 browser_click 等工具使用。"""

    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "元素的语义描述，如'登录按钮'、'报销金额输入框'、'财务管理菜单'",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "scope": {
                "type": "string",
                "enum": ["viewport", "page"],
                "description": "搜索范围：viewport只在当前视口，page在整页搜索（默认page）",
                "default": "page",
            },
            "type_filter": {
                "type": "string",
                "enum": ["button", "input", "link", "select", "all"],
                "description": "元素类型过滤：button按钮，input输入框，link链接，select下拉框，all全部（默认all）",
                "default": "all",
            },
        },
        "required": ["description"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行查找操作

        Args:
            description: 语义描述
            session_id: 会话ID
            scope: 搜索范围
            type_filter: 类型过滤

        Returns:
            执行结果
        """
        description = kwargs.get("description", "")
        session_id = kwargs.get("session_id", "default")
        scope = kwargs.get("scope", "page")
        type_filter = kwargs.get("type_filter", "all")

        if not description:
            return {
                "success": False,
                "error": "必须提供 description",
            }

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用 browser_open 打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用 browser_open 打开网页",
                }

            # 获取 ref_mapper
            ref_mapper = get_ref_mapper(session_id)
            if not ref_mapper:
                return {
                    "success": False,
                    "error": "未找到语义快照，请先调用 browser_snapshot",
                }

            # 使用自然语言匹配
            matcher = NaturalMatcher(ref_mapper)
            match_result = matcher.match_click(description)

            if not match_result.success:
                return {
                    "success": True,  # 工具执行成功，只是没找到
                    "message": "未找到匹配元素",
                    "description": description,
                    "alternatives": [],
                }

            # 获取元素详情
            element = ref_mapper.get_by_ref(match_result.ref)
            if not element:
                return {
                    "success": False,
                    "error": f"元素 ref={match_result.ref} 定位失败",
                }

            # 构建返回结果
            result = {
                "success": True,
                "message": f"找到匹配元素: {match_result.label}",
                "ref": match_result.ref,
                "label": match_result.label,
                "tag": element.tag,
                "role": element.role,
                "href": element.href,
                "visible": element.visible,
                "confidence": match_result.confidence,
                "alternatives": [
                    {
                        "ref": alt["ref"],
                        "label": alt["label"],
                        "confidence": alt["confidence"],
                    }
                    for alt in match_result.alternatives
                ],
            }

            # 根据 type_filter 过滤
            if type_filter != "all":
                if not self._matches_filter(element, type_filter):
                    return {
                        "success": True,
                        "message": f"找到元素但类型不匹配 '{type_filter}'",
                        "ref": match_result.ref,
                        "label": match_result.label,
                        "tag": element.tag,
                        "filtered": True,
                        "alternatives": result["alternatives"],
                    }

            logger.info(f"元素查找成功: '{description}' -> ref={match_result.ref}, confidence={match_result.confidence}")

            return result

        except Exception as e:
            logger.error(f"查找元素失败: {e}")
            return {
                "success": False,
                "error": f"查找元素失败: {str(e)}",
            }

    def _matches_filter(self, element, filter_type: str) -> bool:
        """检查元素是否匹配类型过滤

        Args:
            element: 元素
            filter_type: 过滤类型

        Returns:
            bool: 是否匹配
        """
        tag = element.tag.lower()

        if filter_type == "button":
            return tag in ("button", "a") or element.role == "button"
        elif filter_type == "input":
            return tag in ("input", "textarea")
        elif filter_type == "link":
            return tag == "a" and element.href
        elif filter_type == "select":
            return tag == "select"

        return True


class BrowserFindAllTool(BaseTool):
    """查找所有匹配元素工具"""

    name = "browser_find_all"
    description = """查找所有匹配语义描述的元素。

    **重要**：建议先调用 browser_snapshot 获取当前页面的语义快照！

    与 browser_find 不同的是，此工具返回所有匹配的元素（按置信度排序），
    而不只是第一个最佳匹配。

    返回结果是一个元素列表。"""

    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "元素的语义描述",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "limit": {
                "type": "integer",
                "description": "返回元素数量限制，默认10",
                "default": 10,
            },
        },
        "required": ["description"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行查找操作

        Args:
            description: 语义描述
            session_id: 会话ID
            limit: 返回数量限制

        Returns:
            执行结果
        """
        description = kwargs.get("description", "")
        session_id = kwargs.get("session_id", "default")
        limit = kwargs.get("limit", 10)

        if not description:
            return {
                "success": False,
                "error": "必须提供 description",
            }

        try:
            # 获取浏览器会话
            if session_id not in _browser_sessions:
                return {
                    "success": False,
                    "error": f"会话 {session_id} 不存在，请先使用 browser_open 打开网页",
                }

            session = _browser_sessions[session_id]

            if not session.is_running():
                return {
                    "success": False,
                    "error": "浏览器未运行，请先使用 browser_open 打开网页",
                }

            # 获取 ref_mapper
            ref_mapper = get_ref_mapper(session_id)
            if not ref_mapper:
                return {
                    "success": False,
                    "error": "未找到语义快照，请先调用 browser_snapshot",
                }

            # 使用自然语言匹配获取所有匹配元素
            matcher = NaturalMatcher(ref_mapper)
            match_result = matcher.match_click(description)

            # 获取所有元素
            all_elements = ref_mapper.get_all_elements()

            # 计算每个元素的匹配置信度
            candidates = []
            normalized_desc = matcher._normalize(description)

            for elem in all_elements:
                confidence = matcher._calculate_click_confidence(normalized_desc, elem)
                if confidence > 0.3:
                    candidates.append({
                        "ref": elem.ref,
                        "label": elem.label,
                        "tag": elem.tag,
                        "role": elem.role,
                        "href": elem.href,
                        "visible": elem.visible,
                        "confidence": confidence,
                    })

            # 按置信度排序
            candidates.sort(key=lambda x: x["confidence"], reverse=True)

            # 限制返回数量
            candidates = candidates[:limit]

            if not candidates:
                return {
                    "success": True,
                    "message": "未找到匹配元素",
                    "description": description,
                    "elements": [],
                }

            return {
                "success": True,
                "message": f"找到 {len(candidates)} 个匹配元素",
                "description": description,
                "count": len(candidates),
                "elements": candidates,
            }

        except Exception as e:
            logger.error(f"查找元素失败: {e}")
            return {
                "success": False,
                "error": f"查找元素失败: {str(e)}",
            }
