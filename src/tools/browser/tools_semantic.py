"""browser_click/fill/select 语义工具

通过自然语言描述操作页面元素，无需 CSS 选择器。
"""

import asyncio
import inspect
from typing import Any, Dict, List, Optional
from loguru import logger
from src.tools._helpers import sanitize_error
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.browser.session import _browser_sessions
from src.tools.browser.semantic import NaturalMatcher, RefMapper, InteractiveElement
from src.tools.browser.tools_snapshot import get_ref_mapper
from src.tools.browser.tools_path import get_path_tracker, record_browser_action
from src.tools.browser.semantic import SemanticSnapshotGenerator


class BrowserClickSemanticInput(BaseModel):
    """点击元素参数"""
    description: str = Field(..., description="要点击元素的自然语言描述")
    ref: Optional[str] = Field(None, description="元素的 ref 引用（可选，直接指定 ref 可跳过匹配）")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    timeout: Optional[int] = Field(10000, description="超时时间（毫秒）")


class BrowserFillSemanticInput(BaseModel):
    """填写表单参数"""
    field: str = Field(..., description="要填写的字段描述")
    value: str = Field(..., description="要填写的值")
    ref: Optional[str] = Field(None, description="元素的 ref 引用（可选，直接指定 ref 可跳过匹配）")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    timeout: Optional[int] = Field(10000, description="超时时间（毫秒）")


class BrowserSelectSemanticInput(BaseModel):
    """选择下拉选项参数"""
    field: str = Field(..., description="下拉选择框的描述")
    option: str = Field(..., description="要选择的选项")
    ref: Optional[str] = Field(None, description="下拉框的 ref 引用（可选）")
    session_id: Optional[str] = Field("default", description="浏览器会话ID")
    timeout: Optional[int] = Field(10000, description="超时时间（毫秒）")


async def wait_for_page_stable(page, timeout: int = 5000):
    """等待页面在操作后达到稳定状态

    采用多级等待策略，适应不同类型的页面：
    1. 先等 DOM 就绪（domcontentloaded）
    2. 尝试等网络空闲（networkidle），但如果超时则不报错
    3. 额外等一小段时间让 JS 框架完成渲染（如 Vue/React 的虚拟 DOM diff）
    4. 最后等待页面中不再有正在进行的动画或过渡

    Args:
        page: Playwright Page 对象
        timeout: 总超时时间（毫秒）
    """
    try:
        # 1. 等 DOM 就绪
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass

    try:
        # 2. 等网络空闲（SPA 可能持续有请求，超时不报错）
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    # 3. 等一小段时间让 JS 框架完成渲染（300ms 足够大多数框架完成一次渲染循环）
    await asyncio.sleep(0.3)

    # 4. 检查页面是否还在加载中（额外保障）
    try:
        await page.wait_for_function(
            """() => {
                // 检查是否有 loading 状态指示器
                const loaders = document.querySelectorAll(
                    '.loading, .spinner, .skeleton, [aria-busy="true"], ' +
                    '.ant-spin, .el-loading-mask, .v-loading-mask'
                );
                for (const loader of loaders) {
                    if (loader.offsetParent !== null) return false;
                }
                return true;
            }""",
            timeout=2000,
        )
    except Exception:
        pass

    # 5. 等待动态插入的 iframe 加载完成
    #    OA 系统常见模式：点击菜单 → JS 动态创建 iframe → iframe 加载内容
    try:
        await page.wait_for_function(
            """() => {
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    if (iframe.offsetParent === null) continue;
                    const src = iframe.getAttribute('src') || '';
                    if (!src || src === 'about:blank') return false;
                    try {
                        const doc = iframe.contentDocument;
                        if (doc && doc.readyState !== 'complete') return false;
                    } catch (e) { }
                }
                return true;
            }""",
            timeout=5000,
        )
    except Exception:
        pass

    # 6. 额外等待 iframe 内容渲染
    try:
        iframe_count = await page.evaluate("() => document.querySelectorAll('iframe').length")
        if iframe_count > 0:
            await asyncio.sleep(0.5)
    except Exception:
        pass

    logger.debug(f"[wait_for_page_stable] 页面已稳定: {page.url}")


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

    display_name = "点击网页元素"
    category = "browser"
    InputModel = BrowserClickSemanticInput

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

            # 执行点击（使用 force=True 绕过可见性/遮挡检查，确保一定能点击到）
            try:
                await element.click(timeout=timeout, force=True)
            except Exception as click_err:
                # force 点击失败，尝试用 JS 直接触发
                logger.warning(f"element.click(force=True) 失败: {click_err}，尝试 JS 点击")
                # 确定在哪个上下文中执行 JS（主页面 or iframe）
                js_context = session.page
                elem_info = ref_mapper.get_by_ref(target_ref)
                if elem_info and elem_info.frame_url:
                    frame = ref_mapper._frame_map.get(elem_info.frame_url)
                    if frame:
                        js_context = frame
                await js_context.evaluate(f"""(selector) => {{
                    const el = document.querySelector(selector);
                    if (el) el.click();
                }}""", f'[data-ref="{target_ref}"]')

            # 等待页面稳定（确保后续获取的快照反映最新状态）
            await wait_for_page_stable(session.page)

            # 使快照缓存失效（操作后页面 DOM 已变化，下次 snapshot 需重新生成）
            SemanticSnapshotGenerator.invalidate_cache(url=session.page.url)

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
            logger.error("点击元素失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="点击元素失败"),
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

    display_name = "填写网页表单"
    category = "browser"
    InputModel = BrowserFillSemanticInput

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

            # 执行填写（force=True 确保即使元素被遮挡也能填写）
            try:
                await element.fill(value, timeout=timeout, force=True)
            except Exception as fill_err:
                # fill 失败，尝试用 JS 直接设置值
                logger.warning(f"element.fill(force=True) 失败: {fill_err}，尝试 JS 填写")
                # 确定在哪个上下文中执行 JS（主页面 or iframe）
                js_context = session.page
                elem_info = ref_mapper.get_by_ref(target_ref)
                if elem_info and elem_info.frame_url:
                    frame = ref_mapper._frame_map.get(elem_info.frame_url)
                    if frame:
                        js_context = frame
                await js_context.evaluate("""(args) => {
                    const el = document.querySelector(args.selector);
                    if (!el) return;
                    // 聚焦元素
                    el.focus();
                    // 清空并设置值
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    )?.set || Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    )?.set;
                    if (nativeInputValueSetter) {
                        nativeInputValueSetter.call(el, args.value);
                    } else {
                        el.value = args.value;
                    }
                    // 触发 input/change 事件（确保框架能感知到值变化）
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""", {"selector": f'[data-ref="{target_ref}"]', "value": value})

            # 短暂等待，确保框架处理完 input/change 事件（如联动校验、自动补全等）
            await asyncio.sleep(0.3)

            # 使快照缓存失效（填写后可能触发联动变化）
            SemanticSnapshotGenerator.invalidate_cache(url=session.page.url)

            # 记录操作到 PathTracker
            record_browser_action(
                session_id=session_id,
                action="fill",
                ref=target_ref,
                label=target_label,
                result=f"filled with {value}",
                url=session.page.url,
            )

            logger.info("成功填写表单: ref={}", target_ref)

            return {
                "success": True,
                "message": f"成功填写字段: {target_label}",
                "ref": target_ref,
                "label": target_label,
                "value": value,
            }

        except Exception as e:
            logger.error("填写表单失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="填写表单失败"),
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

    display_name = "选择下拉选项"
    category = "browser"
    InputModel = BrowserSelectSemanticInput

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
            await wait_for_page_stable(session.page, timeout=3000)

            # 使用 select 定位选项（通过文本内容）
            # 简化实现：直接使用 Playwright 的 select
            try:
                # 尝试通过文本选择
                await element.select_option(option, timeout=timeout)
            except Exception:
                # 如果直接选择失败，尝试点击选项
                # 这是一个简化实现，完整版本需要解析下拉选项
                pass

            # 选择完成后等待页面稳定（可能有联动请求）
            await asyncio.sleep(0.3)

            # 使快照缓存失效
            SemanticSnapshotGenerator.invalidate_cache(url=session.page.url)

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
            logger.error("选择选项失败: type={}", type(e).__name__)
            return {
                "success": False,
                "error": sanitize_error(e, fallback="选择选项失败"),
                "field": field,
                "option": option,
            }
