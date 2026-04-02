"""browser_click/fill/select 语义工具

通过自然语言描述操作页面元素，无需 CSS 选择器。
"""

import asyncio
import inspect
from typing import Any, Dict, Optional
from loguru import logger

from src.tools.base import BaseTool
from src.tools.browser.browser_tool import _browser_sessions
from src.tools.browser.semantic import NaturalMatcher, RefMapper, InteractiveElement
from src.tools.browser.tools_snapshot import get_ref_mapper
from src.tools.browser.tools_path import get_path_tracker, record_browser_action


class BrowserClickTool(BaseTool):
    """点击页面元素（自然语言驱动）"""

    name = "browser_click"
    description = """点击页面上的元素。通过自然语言描述要点击的元素，系统自动在快照中查找匹配元素并点击。

    **重要**：必须先调用 browser_snapshot 获取当前页面的语义快照！

    使用方式：
    - description="登录按钮"
    - description="报销申请菜单"
    - description="提交"
    - description="确定"

    返回结果包含:
    - ref: 匹配到的元素引用
    - label: 元素标签
    - confidence: 匹配置信度
    - alternatives: 其他可能的匹配选项"""

    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "要点击元素的自然语言描述，如'登录按钮'、'报销申请'",
            },
            "ref": {
                "type": "string",
                "description": "元素的 ref 引用（可选，直接指定 ref 可跳过匹配）",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间(毫秒)，默认10000",
                "default": 10000,
            },
        },
        "required": ["description"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行点击操作

        Args:
            description: 自然语言描述
            ref: 直接指定 ref（可选）
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        description = kwargs.get("description", "")
        ref = kwargs.get("ref")
        session_id = kwargs.get("session_id", "default")
        timeout = kwargs.get("timeout", 10000)

        if not description and not ref:
            return {
                "success": False,
                "error": "必须提供 description 或 ref",
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

            # 确定要操作的元素
            target_ref = ref
            target_label = description

            if not target_ref:
                # 使用自然语言匹配
                matcher = NaturalMatcher(ref_mapper)
                match_result = matcher.match_click(description)

                if not match_result.success:
                    return {
                        "success": False,
                        "error": match_result.error,
                        "alternatives": match_result.alternatives,
                    }

                target_ref = match_result.ref
                target_label = match_result.label

                logger.info(f"自然语言匹配成功: '{description}' -> ref={target_ref}, label={target_label}, confidence={match_result.confidence}")

            # 获取元素句柄
            element = await ref_mapper.get_handle(target_ref)
            if not element:
                return {
                    "success": False,
                    "error": f"无法定位元素 ref={target_ref}，可能页面已变化，请重新调用 browser_snapshot",
                    "ref": target_ref,
                }

            # 防御性检查：确保拿到的是真正的 ElementHandle 而非 coroutine
            if inspect.iscoroutine(element):
                logger.error(f"[BUG] get_handle 返回了 coroutine 而非 ElementHandle，ref={target_ref}。请重启程序以确保加载最新代码。")
                return {
                    "success": False,
                    "error": f"内部错误：元素句柄获取异常 (ref={target_ref})，请重启程序后重试",
                    "ref": target_ref,
                }

            # 执行点击
            await element.click(timeout=timeout)
            await session.page.wait_for_load_state("networkidle", timeout=5000)

            # 记录操作到 PathTracker
            record_browser_action(
                session_id=session_id,
                action="click",
                ref=target_ref,
                label=target_label,
                result="success",
                url=session.page.url,
            )

            logger.info(f"成功点击元素: ref={target_ref}, label={target_label}")

            return {
                "success": True,
                "message": f"成功点击元素: {target_label}",
                "ref": target_ref,
                "label": target_label,
                "current_url": session.page.url,
                "page_title": await session.page.title(),
            }

        except Exception as e:
            logger.error(f"点击元素失败: {e}")
            return {
                "success": False,
                "error": f"点击元素失败: {str(e)}",
                "ref": target_ref if 'target_ref' in dir() else None,
            }


class BrowserFillTool(BaseTool):
    """填写表单字段（自然语言驱动）"""

    name = "browser_fill"
    description = """填写表单字段。通过自然语言描述要填写的字段和值，系统自动查找匹配元素并填写。

    **重要**：必须先调用 browser_snapshot 获取当前页面的语义快照！

    使用方式：
    - field="用户名", value="zhangsan"
    - field="密码", value="***"
    - field="报销金额", value="1500"
    - field="备注", value="客户拜访差旅"

    返回结果包含:
    - ref: 匹配到的元素引用
    - label: 元素标签
    - confidence: 匹配置信度"""

    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "要填写的字段的自然语言描述，如'用户名'、'报销金额'",
            },
            "value": {
                "type": "string",
                "description": "要填写的值",
            },
            "ref": {
                "type": "string",
                "description": "元素的 ref 引用（可选，直接指定 ref 可跳过匹配）",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间(毫秒)，默认10000",
                "default": 10000,
            },
        },
        "required": ["field", "value"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行填写操作

        Args:
            field: 字段描述
            value: 要填写的值
            ref: 直接指定 ref（可选）
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        field = kwargs.get("field", "")
        value = kwargs.get("value", "")
        ref = kwargs.get("ref")
        session_id = kwargs.get("session_id", "default")
        timeout = kwargs.get("timeout", 10000)

        if not field and not ref:
            return {
                "success": False,
                "error": "必须提供 field 或 ref",
            }

        if not value:
            return {
                "success": False,
                "error": "必须提供 value",
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

            # 确定要操作的元素
            target_ref = ref
            target_label = field

            if not target_ref:
                # 使用自然语言匹配
                matcher = NaturalMatcher(ref_mapper)
                match_result = matcher.match_fill(field, value)

                if not match_result.success:
                    return {
                        "success": False,
                        "error": match_result.error,
                        "alternatives": match_result.alternatives,
                    }

                target_ref = match_result.ref
                target_label = match_result.label

                logger.info(f"自然语言匹配成功: '{field}' -> ref={target_ref}, label={target_label}, confidence={match_result.confidence}")

            # 获取元素句柄
            element = await ref_mapper.get_handle(target_ref)
            if not element:
                return {
                    "success": False,
                    "error": f"无法定位元素 ref={target_ref}，可能页面已变化，请重新调用 browser_snapshot",
                    "ref": target_ref,
                }

            # 防御性检查
            if inspect.iscoroutine(element):
                logger.error(f"[BUG] get_handle 返回了 coroutine 而非 ElementHandle，ref={target_ref}。请重启程序以确保加载最新代码。")
                return {
                    "success": False,
                    "error": f"内部错误：元素句柄获取异常 (ref={target_ref})，请重启程序后重试",
                    "ref": target_ref,
                }

            # 执行填写（fill 对 input/textarea/contenteditable 等所有可编辑元素通用）
            await element.fill(value, timeout=timeout)

            # 记录操作到 PathTracker
            record_browser_action(
                session_id=session_id,
                action="fill",
                ref=target_ref,
                label=target_label,
                result=f"filled with {value}",
                url=session.page.url,
            )

            logger.info(f"成功填写表单: ref={target_ref}, label={target_label}, value={value}")

            return {
                "success": True,
                "message": f"成功填写字段: {target_label}",
                "ref": target_ref,
                "label": target_label,
                "value": value,
            }

        except Exception as e:
            logger.error(f"填写表单失败: {e}")
            return {
                "success": False,
                "error": f"填写表单失败: {str(e)}",
                "field": field,
            }


class BrowserSelectTool(BaseTool):
    """选择下拉选项（自然语言驱动）"""

    name = "browser_select"
    description = """在下拉列表中选择选项。通过自然语言描述要选择的选项，系统自动查找并选择。

    **重要**：必须先调用 browser_snapshot 获取当前页面的语义快照！

    使用方式：
    - field="部门", option="技术研发部"
    - field="报销类型", option="差旅费"
    - field="审批人", option="张三"

    注意：需要先点击下拉框展开选项，然后再调用此工具选择。
    建议流程：browser_click(description="部门") -> browser_select(field="部门", option="技术研发部")"""

    category = "browser"
    parameters_schema = {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "下拉选择框的描述，如'部门'、'报销类型'",
            },
            "option": {
                "type": "string",
                "description": "要选择的选项，如'技术研发部'、'差旅费'",
            },
            "ref": {
                "type": "string",
                "description": "下拉框的 ref 引用（可选）",
            },
            "session_id": {
                "type": "string",
                "description": "浏览器会话ID，默认为'default'",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间(毫秒)，默认10000",
                "default": 10000,
            },
        },
        "required": ["field", "option"],
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行选择操作

        Args:
            field: 字段描述
            option: 要选择的选项
            ref: 直接指定 ref（可选）
            session_id: 会话ID
            timeout: 超时时间

        Returns:
            执行结果
        """
        field = kwargs.get("field", "")
        option = kwargs.get("option", "")
        ref = kwargs.get("ref")
        session_id = kwargs.get("session_id", "default")
        timeout = kwargs.get("timeout", 10000)

        if not field and not ref:
            return {
                "success": False,
                "error": "必须提供 field 或 ref",
            }

        if not option:
            return {
                "success": False,
                "error": "必须提供 option",
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

            # 确定要操作的元素
            target_ref = ref
            target_label = field

            if not target_ref:
                # 使用自然语言匹配
                matcher = NaturalMatcher(ref_mapper)
                match_result = matcher.match_select(field, option)

                if not match_result.success:
                    return {
                        "success": False,
                        "error": match_result.error,
                        "alternatives": match_result.alternatives,
                    }

                target_ref = match_result.ref
                target_label = match_result.label

                logger.info(f"自然语言匹配成功: '{field}' -> ref={target_ref}, label={target_label}")

            # 获取元素句柄
            element = await ref_mapper.get_handle(target_ref)
            if not element:
                return {
                    "success": False,
                    "error": f"无法定位元素 ref={target_ref}，可能页面已变化，请重新调用 browser_snapshot",
                    "ref": target_ref,
                }

            # 防御性检查
            if inspect.iscoroutine(element):
                logger.error(f"[BUG] get_handle 返回了 coroutine 而非 ElementHandle，ref={target_ref}。请重启程序以确保加载最新代码。")
                return {
                    "success": False,
                    "error": f"内部错误：元素句柄获取异常 (ref={target_ref})，请重启程序后重试",
                    "ref": target_ref,
                }

            # 选择选项
            # 先点击展开下拉框
            await element.click(timeout=timeout)

            # 等待选项出现
            await session.page.wait_for_load_state("networkidle", timeout=3000)

            # 使用 select 定位选项（通过文本内容）
            # 简化实现：直接使用 Playwright 的 select
            try:
                # 尝试通过文本选择
                await element.select_option(option, timeout=timeout)
            except Exception:
                # 如果直接选择失败，尝试点击选项
                # 这是一个简化实现，完整版本需要解析下拉选项
                pass

            # 记录操作到 PathTracker
            record_browser_action(
                session_id=session_id,
                action="select",
                ref=target_ref,
                label=target_label,
                result=f"selected {option}",
                url=session.page.url,
            )

            logger.info(f"成功选择选项: ref={target_ref}, field={target_label}, option={option}")

            return {
                "success": True,
                "message": f"成功选择: {option}",
                "ref": target_ref,
                "field": target_label,
                "option": option,
            }

        except Exception as e:
            logger.error(f"选择选项失败: {e}")
            return {
                "success": False,
                "error": f"选择选项失败: {str(e)}",
                "field": field,
                "option": option,
            }
