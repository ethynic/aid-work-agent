"""浏览器自动化编排器

LLM 驱动的编排循环：解析任务 → 循环执行（快照→决策→操作）→ 返回结果。
"""

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from src.llm.gateway import llm_gateway
from src.config.settings import settings
from src.tools._helpers import sanitize_error
from src.tools.browser.human_requirement_detector import (
    detect_access_block,
    detect_human_requirement,
)
from src.tools.browser.page_ops import PageOps
from src.tools.browser.run_manager import BrowserRunManager, RunState
from src.tools.browser.run_store import RunRecord


# 不可恢复的错误关键词
_UNRECOVERABLE_ERRORS = [
    "net::err_connection",
    "net::err_name_not_resolved",
    "net::err_connection_refused",
    "net::err_connection_timed_out",
    "net::err_ssl",
    "404",
    "500",
    "502",
    "503",
    "page crashed",
    "browser closed",
    "target closed",
]

DECISION_PROMPT = """你是一个浏览器自动化助手。你需要根据当前页面状态和用户任务，决定下一步操作。

当前页面状态：
URL: {url}
标题: {title}

页面可操作元素（用 ref 精确指定目标）：
{interactive_elements}

页面可见文本摘要：
{page_text}

用户任务: {task}

已执行步骤:
{completed_steps}
{collected_content}

请决定下一步操作，严格输出以下 JSON 格式（不要输出其他内容）：
{{
  "action": "click" | "fill" | "select" | "navigate" | "scroll" | "get_content" | "wait" | "close_popup" | "done" | "ask_user",
  "target": "元素的 ref（如 e8）或 URL",
  "value": "填写值（fill 时必填）或滚动方向（scroll: up/down，默认 down）或等待秒数（wait）",
  "reason": "为什么这样做"
}}

操作说明：
- click：点击元素。target 必须填写元素列表中的 ref 值（如 "e8"）
- fill：填写输入框。target 填写输入框的 ref 值，value 填写要输入的内容。填写后自动按回车
- select：选择下拉选项。target 填写 ref 值，value 填写选项文本
- navigate：导航到新 URL。target 填写完整 URL
- scroll：滚动页面。value 填写 "up" 或 "down"，默认向下滚动一屏
- get_content：获取当前页面的文本内容（用于提取信息）
- wait：等待页面加载。value 填写等待秒数（如 "2"）
- close_popup：关闭弹窗/遮罩层。会自动点击页面空白区域关闭弹窗
- done：任务已完成。在 reason 中总结完成的内容和提取的信息（用 Markdown 格式）
- ask_user：需要用户确认。在 reason 中写明问题

关键规则：
1. 使用 click/fill/select 时，target 必须是上面元素列表中的 ref 值（如 e1, e2），不要用自然语言描述
2. 如果页面有登录弹窗、对话框等遮挡了主要内容，先用 close_popup 关闭它
3. 搜索框通常是 type=text 的 input 元素，且 label 中包含"搜索"关键词。注意区分搜索框和登录框
4. 搜索流程：找到搜索框 → fill 输入关键词（会自动按回车提交）→ 等待搜索结果加载
5. 如果任务要求提取信息，先 get_content 获取页面内容，然后在 done 时整理结果
6. 如果 fill 后页面没有变化（URL 没变），可能填错了元素，尝试找其他 input 元素
7. 不要对同一个元素反复执行相同的操作。如果上一步已经 fill 了但没有效果，换一个策略
8. 如果页面需要登录才能继续操作，但已获取到部分内容（如搜索结果列表），直接用 done 整理已有内容返回给用户，说明部分信息可能受限
9. 如果已经通过 get_content 获取到页面内容，优先根据已获取的内容整理答案，而不是继续操作页面
"""


class BrowserOrchestrator:
    """浏览器自动化编排器

    内部维护一个 LLM 驱动的循环：
    1. 获取页面快照
    2. 将快照 + 任务 + 已执行步骤发送给 LLM
    3. LLM 返回下一步操作决策
    4. 执行操作
    5. 循环直到完成或需要用户输入
    """

    def __init__(
        self,
        session_id: str,
        headless: Optional[bool] = None,
        run_manager: Optional[BrowserRunManager] = None,
        run_record: Optional[RunRecord] = None,
    ):
        self.session_id = session_id
        self.headless = headless
        self.run_manager = run_manager
        self.run_record = run_record
        self.executor = None
        self.page_ops: Optional[PageOps] = None
        self.steps: List[Dict[str, Any]] = []
        self.max_steps = 30
        self.max_retries = 3
        self.screenshot_path: Optional[str] = None
        self._collected_content: List[str] = []
        self._page_text = ""
        self._current_url = ""
        self.cancel_event = asyncio.Event()

    def cancel(self) -> None:
        """向当前执行传播取消信号。"""
        self.cancel_event.set()

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise asyncio.CancelledError

    async def _ensure_session(self):
        """通过 RunManager 启动隔离 executor。"""
        if self.run_manager is None or self.run_record is None:
            raise RuntimeError("RUN_CONTEXT_REQUIRED")
        self.executor = await self.run_manager.start(self.run_record)
        self.page_ops = PageOps(self.executor, self.run_record.run_id)

    async def _take_screenshot(self) -> Optional[str]:
        """Phase 2 默认不落盘页面截图。"""
        return None

    async def _get_page_text(self) -> str:
        return self._page_text[:3000]

    async def _get_decision(self, task: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """调用 LLM 获取下一步操作决策"""
        interactive_elements = snapshot.get("interactive_elements", [])
        elements_text = ""
        for elem in interactive_elements[:50]:  # 限制元素数量避免 token 过多
            ref = elem.get("ref", "?")
            label = elem.get("label", "")
            tag = elem.get("tag", "")
            role = elem.get("role", "")
            elem_type = elem.get("element_type", elem.get("type", ""))
            visible = elem.get("visible", True)
            if not visible:
                continue
            parts = [f"ref={ref}", f"label=\"{label}\"", f"tag={tag}"]
            if role:
                parts.append(f"role={role}")
            if elem_type:
                parts.append(f"type={elem_type}")
            elements_text += "  - " + " ".join(parts) + "\n"

        completed_text = ""
        if self.steps:
            for i, step in enumerate(self.steps[-10:], 1):  # 最近 10 步
                completed_text += f"  {i}. [{step.get('action', '?')}] {step.get('target', '')} → {step.get('result', '')}\n"
        else:
            completed_text = "  （尚无步骤）"

        # 如果已收集到内容，添加到上下文中帮助 LLM 决策
        collected_text = ""
        if self._collected_content:
            latest = self._collected_content[-1]
            collected_text = f"\n已获取的页面内容（前1500字）：\n{latest[:1500]}\n"

        # 获取页面可见文本
        page_text = await self._get_page_text()
        if len(page_text) > 2000:
            page_text = page_text[:2000] + "..."

        prompt = DECISION_PROMPT.format(
            url=snapshot.get("url", "unknown"),
            title=snapshot.get("title", "unknown"),
            interactive_elements=elements_text or "（无可操作元素）",
            page_text=page_text or "（无法获取页面文本）",
            task=task,
            completed_steps=completed_text,
            collected_content=collected_text,
        )

        messages = [
            {"role": "system", "content": "你是一个浏览器自动化助手，只输出 JSON 格式的操作决策。使用元素列表中的 ref 值（如 e8）来指定操作目标。"},
            {"role": "user", "content": prompt},
        ]

        logger.debug(
            "[Orchestrator] 请求 LLM 决策: 元素数={}, prompt长度={}",
            len(interactive_elements),
            len(prompt),
        )

        response = await llm_gateway.chat(
            messages=messages,
            temperature=0.1,
            max_tokens=1000,
        )

        from src.services.session_record import record_background_llm_usage
        record_background_llm_usage(
            response.get("usage") if isinstance(response, dict) else None,
            source="browser_orchestrator",
        )

        content = response.get("content", "")
        logger.debug("[Orchestrator] LLM 返回长度: {}", len(content))

        decision = self._parse_decision(content)
        logger.info(
            "[Orchestrator] 步骤决策: step={}, action={}",
            len(self.steps) + 1,
            decision.get("action"),
        )

        return decision

    def _parse_decision(self, content: str) -> Dict[str, Any]:
        """解析 LLM 返回的决策 JSON"""
        import re
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1).strip()

        try:
            decision = json.loads(content)
            action = decision.get("action", "")
            valid_actions = ("click", "fill", "select", "navigate", "scroll", "get_content", "wait", "close_popup", "done", "ask_user")
            if action not in valid_actions:
                return {"action": "done", "reason": f"LLM 返回了无效的 action: {action}"}
            return decision
        except json.JSONDecodeError:
            logger.warning("LLM 决策 JSON 解析失败")
            return {"action": "done", "reason": "决策解析失败，终止任务"}

    async def _try_direct_search(self, task: str) -> Optional[str]:
        """当操作循环时，尝试从任务描述中提取搜索意图并构造直接搜索 URL"""
        import re

        # 检测常见网站的搜索 URL 模式
        current_url = self._current_url

        # 提取搜索关键词
        search_kw = None
        patterns = [
            r"搜索['\"""''](.+?)['\"""'']",
            r"搜索(.+?)[，,\s并然后]",
            r"搜[索]?(\S+)",
            r"查找['\"""''](.+?)['\"""'']",
        ]
        for pat in patterns:
            m = re.search(pat, task)
            if m:
                search_kw = m.group(1).strip()
                break

        if not search_kw:
            return None

        # URL encode
        from urllib.parse import quote
        encoded_kw = quote(search_kw)

        # 常见网站搜索 URL 模板
        search_urls = {
            "xiaohongshu.com": f"https://www.xiaohongshu.com/search_result?keyword={encoded_kw}&source=web_search_result_note",
            "www.xiaohongshu.com": f"https://www.xiaohongshu.com/search_result?keyword={encoded_kw}&source=web_search_result_note",
            "taobao.com": f"https://s.taobao.com/search?q={encoded_kw}",
            "jd.com": f"https://search.jd.com/Search?keyword={encoded_kw}",
            "baidu.com": f"https://www.baidu.com/s?wd={encoded_kw}",
            "google.com": f"https://www.google.com/search?q={encoded_kw}",
            "bing.com": f"https://www.bing.com/search?q={encoded_kw}",
            "zhihu.com": f"https://www.zhihu.com/search?type=content&q={encoded_kw}",
            "bilibili.com": f"https://search.bilibili.com/all?keyword={encoded_kw}",
        }

        for domain, search_url in search_urls.items():
            if domain in current_url:
                logger.info("[Orchestrator] 构造直接搜索 URL")
                return search_url

        return None

    def _is_unrecoverable_error(self, error: str) -> bool:
        """判断是否为不可恢复的错误"""
        error_lower = error.lower()
        return any(kw in error_lower for kw in _UNRECOVERABLE_ERRORS)

    async def execute(
        self,
        task: str,
        url: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        user_response: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行边界：覆盖启动、超时、取消、人工和所有终态的关闭。"""
        if user_response is not None:
            return {
                "success": False,
                "error_code": "DEPRECATED_PARAMETER",
                "error": "旧版浏览器会话恢复参数已停用，请重新发起完整的浏览器任务",
            }

        close_reason = "error"
        suspended = False
        try:
            async with asyncio.timeout(settings.tools.browser.task_timeout):
                self._raise_if_cancelled()
                await self._ensure_session()
                result = await self._execute_task(
                    task=task,
                    url=url,
                    progress_callback=progress_callback,
                )
                if result.get("status") == "ask_user":
                    close_reason = "ask_user"
                    suspended = True
                elif result.get("success"):
                    close_reason = "success"
                elif result.get("error_code") == "MAX_STEPS_EXCEEDED":
                    close_reason = "max_steps"
                else:
                    close_reason = "error"
                return result
        except asyncio.TimeoutError:
            close_reason = "timeout"
            return self._build_result(
                success=False,
                status="timeout",
                error_code="TASK_TIMEOUT",
                error="浏览器任务执行超时",
            )
        except asyncio.CancelledError:
            close_reason = "cancelled"
            self.cancel_event.set()
            raise
        except Exception as exc:
            close_reason = "error"
            logger.error("浏览器编排执行失败: type={}", type(exc).__name__)
            return self._build_result(
                success=False,
                error_code="INTERNAL_ERROR",
                error=sanitize_error(exc, fallback="浏览器自动化执行失败"),
            )
        finally:
            try:
                if not suspended and self.run_manager is not None and self.run_record is not None:
                    terminal = {
                        "success": RunState.SUCCEEDED,
                        "timeout": RunState.TIMED_OUT,
                        "cancelled": RunState.CANCELLED,
                    }.get(close_reason, RunState.FAILED)
                    await asyncio.shield(
                        self.run_manager.finalize(
                            self.run_record.tenant_id,
                            self.run_record.run_id,
                            terminal,
                            close_reason,
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("浏览器边界关闭异常: type={}", type(exc).__name__)

    async def _execute_task(
        self,
        task: str,
        url: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """已启动会话内的编排循环。

        Args:
            task: 任务描述
            url: 起始 URL（可选）
            progress_callback: 进度回调函数
        Returns:
            执行结果
        """
        try:
            if url:
                # Phase 1: 导航到起始页面
                if progress_callback:
                    await progress_callback("navigate", f"正在打开页面: {url[:50]}...")
                result = await self.page_ops.navigate(url)
                if not result["success"]:
                    await self._take_screenshot()
                    return self._build_result(success=False, error=result.get("error", "导航失败"))
                self.steps.append({
                    "action": "navigate", "target": result.get("url", ""),
                    "result": "页面已打开",
                })
                self._current_url = result.get("url", "")
                start_step = 0
                last_url = None
                same_url_count = 0
            else:
                start_step = 0
                last_url = None
                same_url_count = 0

            # Phase 2: LLM 驱动的操作循环
            logger.info("[Orchestrator] 开始 LLM 编排循环")

            last_url = None
            same_url_count = 0

            for step_num in range(start_step, self.max_steps):
                self._raise_if_cancelled()
                logger.info(f"[Orchestrator] === 步骤 {step_num + 1}/{self.max_steps} ===")

                # 获取语义快照
                snapshot = await self.page_ops.take_snapshot()
                if not snapshot.get("success"):
                    logger.error("[Orchestrator] 快照生成失败")
                    await self._take_screenshot()
                    return self._build_result(success=False, error=snapshot.get("error", "快照生成失败"))

                current_url = snapshot.get('url', '?')
                self._current_url = current_url
                self._page_text = snapshot.get("page_text", "")
                logger.info(
                    "[Orchestrator] 快照成功: 元素数={}",
                    len(snapshot.get("interactive_elements", [])),
                )
                access_block = detect_access_block(snapshot)
                if access_block:
                    logger.info("[Orchestrator] 页面访问被确定性阻断")
                    return self._build_result(
                        success=False,
                        status="blocked",
                        error_code=access_block,
                        error="目标网站拒绝了当前浏览器访问",
                    )

                # 检测循环：如果 URL 连续多步未变化，提示 LLM 换策略
                if current_url == last_url:
                    same_url_count += 1
                else:
                    same_url_count = 0
                    last_url = current_url

                # LLM 决策
                decision = await self._get_decision(task, snapshot)
                action = decision.get("action", "done")
                target = decision.get("target", "")
                value = decision.get("value", "")
                reason = decision.get("reason", "")

                # 检测重复操作循环：如果连续 3 步操作相同 target 且 URL 未变化，强制切换策略
                if same_url_count >= 3 and len(self.steps) >= 3:
                    recent_actions = [(s.get("action"), s.get("target")) for s in self.steps[-3:]]
                    if len(set(recent_actions)) == 1:
                        logger.warning(
                            "[Orchestrator] 检测到循环操作: unchanged_steps={}",
                            same_url_count,
                        )
                        # 尝试直接通过 URL 构造搜索链接
                        search_url = await self._try_direct_search(task)
                        if search_url:
                            decision = {"action": "navigate", "target": search_url, "value": "", "reason": "检测到操作循环，尝试直接访问搜索结果 URL"}
                            action = "navigate"
                            target = search_url
                            value = ""
                            reason = decision["reason"]
                            same_url_count = 0

                # 处理 done
                if action == "done":
                    # 确定性人工需求检测（Phase 3R）：接受 LLM done 前先校验页面结构，
                    # 命中验证码/登录/MFA/扫码/文件选择器时强制转人工，LLM done 或文字
                    # 提示"请人工登录"都不能把 run 标记为成功。
                    human_reason = detect_human_requirement(snapshot)
                    if human_reason:
                        logger.info(
                            "[Orchestrator] LLM 返回 done 但页面仍存在人工需求: reason={}",
                            human_reason,
                        )
                        if progress_callback:
                            await progress_callback(
                                "ask_user", f"检测到需要人工操作: {human_reason}"
                            )
                        return self._build_result(
                            success=False,
                            status="ask_user",
                            error_code=human_reason,
                            instruction="请直接在浏览器画面中完成操作，敏感信息不要发送到聊天",
                        )
                    # 合并收集到的内容
                    final_reason = reason
                    if self._collected_content:
                        content_summary = "\n\n".join(self._collected_content)
                        if not reason or len(reason) < 50:
                            final_reason = content_summary
                        else:
                            final_reason = reason
                    logger.info("[Orchestrator] 任务完成")
                    if progress_callback:
                        await progress_callback("done", f"任务完成: {final_reason[:100]}")
                    await self._take_screenshot()
                    return self._build_result(success=True, result=final_reason)

                # 处理 ask_user
                if action == "ask_user":
                    logger.info("[Orchestrator] 需要人工参与，挂起当前工具")
                    # 用结构化检测补充更精确的 reason_code；LLM 自由文本不能覆盖
                    # 结构化证据（设计 §7.2）。
                    human_reason = detect_human_requirement(snapshot) or "HUMAN_REQUIRED"
                    if progress_callback:
                        await progress_callback("ask_user", "任务需要人工参与，等待用户接管")
                    return self._build_result(
                        success=False,
                        status="ask_user",
                        error_code=human_reason,
                        instruction="请直接在浏览器画面中完成操作，敏感信息不要发送到聊天",
                    )

                # 执行操作（带重试）
                logger.info("[Orchestrator] 执行操作: action={}", action)
                op_result = await self._execute_with_retry(action, target, value, progress_callback)

                logger.info("[Orchestrator] 操作结果: success={}", op_result.get("success"))

                if not op_result.get("success"):
                    error_msg = op_result.get("error", "")
                    if self._is_unrecoverable_error(error_msg):
                        if progress_callback:
                            await progress_callback("error", f"无法继续: {error_msg[:100]}")
                        await self._take_screenshot()
                        return self._build_result(success=False, error=error_msg)

                    logger.warning("浏览器步骤失败但可继续尝试")

                # 记录步骤
                recorded_target = target
                if action == "navigate":
                    recorded_target = op_result.get("url", self._current_url)
                self.steps.append({
                    "action": action,
                    "target": recorded_target,
                    "value": "***" if action == "fill" else value,
                    "result": (
                        op_result.get("message", "")
                        if op_result.get("success")
                        else "浏览器步骤执行失败"
                    ),
                })

            # 超过最大步数
            if progress_callback:
                await progress_callback("done", "已达到最大操作步数限制")
            await self._take_screenshot()
            return self._build_result(
                success=False,
                error_code="MAX_STEPS_EXCEEDED",
                error=f"已达到最大操作步数 ({self.max_steps})，任务可能未完成",
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("浏览器自动化执行异常: type={}", type(e).__name__)
            await self._take_screenshot()
            return self._build_result(
                success=False,
                error_code="INTERNAL_ERROR",
                error=sanitize_error(e, fallback="浏览器自动化执行失败"),
            )

    async def resume_from_human(
        self, *, completed_by_human: bool, step_index: int,
    ) -> Dict[str, Any]:
        """从仍存活的同一 executor/page/context 继续，不重新导航起始 URL。"""
        if self.run_manager is None or self.run_record is None or self.page_ops is None:
            return self._build_result(
                success=False, error_code="RESUME_CONTEXT_LOST",
                error="浏览器上下文已丢失，无法继续原任务",
            )
        current = await self.run_manager.store.get(
            self.run_record.tenant_id, self.run_record.run_id
        )
        if (
            current is None
            or current.state != RunState.RESUMING.value
            or step_index != len(self.steps)
        ):
            return self._build_result(
                success=False, error_code="RESUME_CONTEXT_LOST",
                error="浏览器上下文已丢失，无法继续原任务",
            )
        if completed_by_human:
            self.steps.append({
                "action": "human_confirmation", "target": "",
                "value": "", "result": "completed_by_human",
            })
        await self.run_manager.transition(
            self.run_record.tenant_id, self.run_record.run_id, RunState.RUNNING_AGENT
        )
        close_reason = "error"
        result: Dict[str, Any] | None = None
        try:
            result = await self._execute_task(
                task=self._task_description, url=None, progress_callback=None
            )
            if result.get("status") == "ask_user":
                # 第二次人工暂停由调用方建立新 assistance；当前 job 不伪造终态。
                return result
            close_reason = "success" if result.get("success") else "error"
            return result
        finally:
            if result is None or result.get("status") != "ask_user":
                terminal = RunState.SUCCEEDED if close_reason == "success" else RunState.FAILED
                await self.run_manager.finalize(
                    self.run_record.tenant_id, self.run_record.run_id, terminal,
                    "human_resumed_" + close_reason,
                )

    async def _execute_with_retry(
        self,
        action: str,
        target: str,
        value: str,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """带重试的操作执行"""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                result = await self._execute_single_action(action, target, value, progress_callback)
                if result.get("success"):
                    return result

                last_error = result.get("error", "未知错误")

                if self._is_unrecoverable_error(last_error):
                    return result

                if attempt < self.max_retries - 1:
                    logger.info("浏览器操作失败，准备重试: attempt={}", attempt + 1)
                    await self.page_ops.take_snapshot()

            except Exception as e:
                raw_error = str(e)
                if self._is_unrecoverable_error(raw_error):
                    return {"success": False, "error": "浏览器发生不可恢复错误"}
                last_error = sanitize_error(e, fallback="浏览器操作失败")

                if attempt < self.max_retries - 1:
                    logger.info("浏览器操作异常，准备重试: attempt={}", attempt + 1)
                    await self.page_ops.take_snapshot()

        return {
            "success": False,
            "error": f"浏览器操作重试 {self.max_retries} 次后仍失败",
        }

    async def _execute_single_action(
        self,
        action: str,
        target: str,
        value: str,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """执行单个操作"""
        if action == "navigate":
            if progress_callback:
                await progress_callback("navigate", f"正在导航到: {target[:50]}...")
            return await self.page_ops.navigate(target)

        elif action == "click":
            if progress_callback:
                display = target[:30]
                await progress_callback("click", f"正在点击: {display}")
            # 支持 ref 直接指定（如 "e8"）或自然语言描述
            if target.startswith("e") and target[1:].isdigit():
                return await self.page_ops.click(description="", ref=target)
            return await self.page_ops.click(target)

        elif action == "fill":
            if progress_callback:
                await progress_callback("fill", f"正在填写: {target[:30]}")
            if target.startswith("e") and target[1:].isdigit():
                result = await self.page_ops.fill(field="", value=value, ref=target)
            else:
                result = await self.page_ops.fill(target, value)
            # fill 后模拟回车提交搜索
            if result.get("success"):
                try:
                    await self.page_ops.keyboard("Enter")
                    await asyncio.sleep(1.5)
                except Exception as e:
                    logger.debug("fill 后回车失败（可忽略）: type={}", type(e).__name__)
            return result

        elif action == "select":
            if progress_callback:
                await progress_callback("select", f"正在选择: {target[:30]} → {value[:30]}")
            if target.startswith("e") and target[1:].isdigit():
                return await self.page_ops.select(field="", option=value, ref=target)
            return await self.page_ops.select(target, value)

        elif action == "scroll":
            direction = value or "down"
            return await self._scroll_page(direction, progress_callback)

        elif action == "get_content":
            if progress_callback:
                await progress_callback("get_content", "正在获取页面内容...")
            result = await self.page_ops.get_content(format="markdown")
            if result.get("success"):
                content = result.get("content", "")
                self._collected_content.append(content)
                # 返回截断的内容给 LLM 参考下一步
                preview = content[:2000] if len(content) > 2000 else content
                return {"success": True, "message": f"已获取页面内容 ({len(content)} 字符)", "content_preview": preview}
            return result

        elif action == "wait":
            wait_seconds = float(value) if value else 2
            wait_seconds = min(wait_seconds, 10)  # 最多等 10 秒
            if progress_callback:
                await progress_callback("wait", f"等待 {wait_seconds} 秒...")
            import asyncio
            await asyncio.sleep(wait_seconds)
            return {"success": True, "message": f"已等待 {wait_seconds} 秒"}

        elif action == "close_popup":
            return await self._close_popup(progress_callback)

        else:
            return {"success": False, "error": f"不支持的操作类型: {action}"}

    async def _close_popup(self, progress_callback=None) -> Dict[str, Any]:
        """通过 executor 发送 Escape。"""
        if progress_callback:
            await progress_callback("close_popup", "正在关闭弹窗...")
        return await self.page_ops.close_popup()

    async def _scroll_page(self, direction: str, progress_callback=None) -> Dict[str, Any]:
        """滚动页面"""
        try:
            result = await self.page_ops.scroll(direction)
            if progress_callback:
                await progress_callback("scroll", f"已向{direction}滚动")
            return result
        except Exception as e:
            return {
                "success": False,
                "error": sanitize_error(e, fallback="页面滚动失败"),
            }

    def _build_result(
        self,
        success: bool,
        result: str = "",
        error: str = "",
        status: str = "done",
        question: str = "",
        screenshot: Optional[str] = None,
        error_code: str = "",
        instruction: str = "",
    ) -> Dict[str, Any]:
        """构建返回结果"""
        from urllib.parse import urlsplit

        final_url = ""
        final_url = self._current_url
        task_result = {
            "success": success,
            "status": status,
            "steps_taken": len(self.steps),
            "steps": self.steps,
            "final_url": final_url,
        }

        if result:
            task_result["result"] = result
        if error:
            if error_code in {
                "TASK_TIMEOUT",
                "INTERNAL_ERROR",
                "HUMAN_REQUIRED",
                "MAX_STEPS_EXCEEDED",
            }:
                task_result["error"] = error
            else:
                task_result["error"] = sanitize_error(
                    error, fallback="浏览器操作失败，请稍后重试"
                )
        if question:
            task_result["question"] = question
        if error_code:
            task_result["error_code"] = error_code
        if instruction:
            task_result["instruction"] = instruction
        if screenshot or self.screenshot_path:
            task_result["screenshot"] = screenshot or self.screenshot_path

        task_result["message"] = (
            result or task_result.get("error") or "任务执行完毕"
        )

        return task_result
