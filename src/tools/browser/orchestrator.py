"""浏览器自动化编排器

LLM 驱动的编排循环：解析任务 → 循环执行（快照→决策→操作）→ 返回结果。
"""

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from src.llm.gateway import llm_gateway
from src.config.settings import settings
from src.tools.browser.session import (
    BrowserSession, get_browser_session, close_browser_session, has_browser_session,
)
from src.tools.browser.page_ops import PageOps


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

页面可操作元素：
{interactive_elements}

用户任务: {task}

已执行步骤:
{completed_steps}

请决定下一步操作，严格输出以下 JSON 格式（不要输出其他内容）：
{{
  "action": "click" | "fill" | "select" | "navigate" | "done" | "ask_user",
  "target": "元素描述或URL",
  "value": "填写值或选择值（fill/select时必填）",
  "reason": "为什么这样做"
}}

规则：
- action=done：任务已完成，停止操作。在 reason 中总结完成的内容和提取的信息（如果有数据需要提取，以 Markdown 格式写在 reason 中）
- action=ask_user：信息不足需要用户确认，在 reason 中写明需要用户回答的问题
- action=navigate：需要导航到新 URL，target 填写完整 URL
- action=click：点击页面元素，target 填写元素的自然语言描述（如"登录按钮"、"提交"）
- action=fill：填写表单，target 填写字段描述，value 填写值
- action=select：选择下拉选项，target 填写下拉框描述，value 填写选项
- 如果任务要求提取信息（如"提取商品列表"），在 done 时将提取结果以 Markdown 格式写在 reason 中
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

    def __init__(self, session_id: str, headless: Optional[bool] = None):
        self.session_id = session_id
        self.headless = headless
        self.session: Optional[BrowserSession] = None
        self.page_ops: Optional[PageOps] = None
        self.steps: List[Dict[str, Any]] = []
        self.max_steps = 20
        self.max_retries = 3
        self.screenshot_path: Optional[str] = None

    async def _ensure_session(self):
        """确保浏览器会话已启动"""
        if not has_browser_session(self.session_id):
            self.session = get_browser_session(self.session_id, self.headless)
        else:
            self.session = get_browser_session(self.session_id)

        if not self.session.is_running():
            await self.session.start()

        self.page_ops = PageOps(self.session, self.session_id)

    async def _take_screenshot(self) -> Optional[str]:
        """截图并返回路径"""
        try:
            timestamp = int(time.time())
            screenshot_dir = Path("storage/screenshots")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = str(screenshot_dir / f"browser_{self.session_id}_{timestamp}.png")
            await self.page_ops.take_screenshot(path=path)
            self.screenshot_path = path
            return path
        except Exception as e:
            logger.warning(f"截图失败: {e}")
            return None

    async def _get_decision(self, task: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """调用 LLM 获取下一步操作决策"""
        interactive_elements = snapshot.get("interactive_elements", [])
        elements_text = ""
        for elem in interactive_elements[:50]:  # 限制元素数量避免 token 过多
            ref = elem.get("ref", "?")
            label = elem.get("label", "")
            tag = elem.get("tag", "")
            role = elem.get("role", "")
            visible = elem.get("visible", True)
            if not visible:
                continue
            parts = [f"ref={ref}", f"label=\"{label}\"", f"tag={tag}"]
            if role:
                parts.append(f"role={role}")
            elements_text += "  - " + " ".join(parts) + "\n"

        completed_text = ""
        if self.steps:
            for i, step in enumerate(self.steps[-10:], 1):  # 最近 10 步
                completed_text += f"  {i}. [{step.get('action', '?')}] {step.get('target', '')} → {step.get('result', '')}\n"
        else:
            completed_text = "  （尚无步骤）"

        prompt = DECISION_PROMPT.format(
            url=snapshot.get("url", "unknown"),
            title=snapshot.get("title", "unknown"),
            interactive_elements=elements_text or "（无可操作元素）",
            task=task,
            completed_steps=completed_text,
        )

        messages = [
            {"role": "system", "content": "你是一个浏览器自动化助手，只输出 JSON 格式的操作决策。"},
            {"role": "user", "content": prompt},
        ]

        response = await llm_gateway.chat(
            messages=messages,
            temperature=0.1,
            max_tokens=500,
        )

        content = response.get("content", "")
        return self._parse_decision(content)

    def _parse_decision(self, content: str) -> Dict[str, Any]:
        """解析 LLM 返回的决策 JSON"""
        # 尝试从 markdown 代码块中提取 JSON
        import re
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1).strip()

        try:
            decision = json.loads(content)
            # 验证必要字段
            action = decision.get("action", "")
            if action not in ("click", "fill", "select", "navigate", "done", "ask_user"):
                return {"action": "done", "reason": f"LLM 返回了无效的 action: {action}"}
            return decision
        except json.JSONDecodeError:
            # JSON 解析失败，尝试更宽松的提取
            logger.warning(f"LLM 决策 JSON 解析失败: {content[:200]}")
            return {"action": "done", "reason": "决策解析失败，终止任务"}

    def _is_unrecoverable_error(self, error: str) -> bool:
        """判断是否为不可恢复的错误"""
        error_lower = error.lower()
        return any(kw in error_lower for kw in _UNRECOVERABLE_ERRORS)

    async def execute(
        self,
        task: str,
        url: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """执行浏览器自动化任务

        Args:
            task: 任务描述
            url: 起始 URL（可选，如果任务不需要打开新页面可以不提供）
            progress_callback: 进度回调函数，接收 (action, message) 参数

        Returns:
            执行结果
        """
        start_time = time.time()
        await self._ensure_session()

        try:
            # Phase 1: 导航到起始页面
            if url:
                if progress_callback:
                    await progress_callback("navigate", f"正在打开页面: {url[:50]}...")
                result = await self.page_ops.navigate(url)
                if not result["success"]:
                    await self._take_screenshot()
                    return self._build_result(success=False, error=result.get("error", "导航失败"))
                self.steps.append({"action": "navigate", "target": url, "result": "页面已打开"})

            # Phase 2: LLM 驱动的操作循环
            for step_num in range(self.max_steps):
                # 获取语义快照（不推送给用户）
                snapshot = await self.page_ops.take_snapshot()
                if not snapshot.get("success"):
                    await self._take_screenshot()
                    return self._build_result(success=False, error=snapshot.get("error", "快照生成失败"))

                # LLM 决策
                decision = await self._get_decision(task, snapshot)
                action = decision.get("action", "done")
                target = decision.get("target", "")
                value = decision.get("value", "")
                reason = decision.get("reason", "")

                # 处理 done
                if action == "done":
                    if progress_callback:
                        await progress_callback("done", f"任务完成: {reason[:100]}")
                    await self._take_screenshot()
                    return self._build_result(success=True, result=reason)

                # 处理 ask_user
                if action == "ask_user":
                    if progress_callback:
                        await progress_callback("ask_user", f"需要确认: {reason}")
                    await self._take_screenshot()
                    return self._build_result(
                        success=True,
                        status="ask_user",
                        question=reason,
                        screenshot=self.screenshot_path,
                    )

                # 执行操作（带重试）
                op_result = await self._execute_with_retry(action, target, value, progress_callback)

                if not op_result.get("success"):
                    # 检查是否不可恢复
                    error_msg = op_result.get("error", "")
                    if self._is_unrecoverable_error(error_msg):
                        if progress_callback:
                            await progress_callback("error", f"无法继续: {error_msg[:100]}")
                        await self._take_screenshot()
                        return self._build_result(success=False, error=error_msg)

                    # 可恢复错误（重试已在 _execute_with_retry 中用尽）
                    logger.warning(f"步骤失败但继续尝试: {error_msg}")

                # 记录步骤
                self.steps.append({
                    "action": action,
                    "target": target,
                    "value": value if action != "fill" else "***" if any(
                        kw in target.lower() for kw in ["密码", "password", "passwd", "pwd"]
                    ) else value,
                    "result": op_result.get("message", op_result.get("error", "")),
                })

            # 超过最大步数
            if progress_callback:
                await progress_callback("done", "已达到最大操作步数限制")
            await self._take_screenshot()
            return self._build_result(
                success=False,
                error=f"已达到最大操作步数 ({self.max_steps})，任务可能未完成",
            )

        except Exception as e:
            logger.error(f"浏览器自动化执行异常: {e}", exc_info=True)
            await self._take_screenshot()
            return self._build_result(success=False, error=str(e))

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

                # 不可恢复错误直接返回
                if self._is_unrecoverable_error(last_error):
                    return result

                # 重试前重新获取快照
                if attempt < self.max_retries - 1:
                    logger.info(f"操作失败，准备重试 ({attempt + 1}/{self.max_retries}): {last_error[:100]}")
                    await self.page_ops.take_snapshot()

            except Exception as e:
                last_error = str(e)
                if self._is_unrecoverable_error(last_error):
                    return {"success": False, "error": last_error}

                if attempt < self.max_retries - 1:
                    logger.info(f"操作异常，准备重试 ({attempt + 1}/{self.max_retries}): {last_error[:100]}")
                    await self.page_ops.take_snapshot()

        return {"success": False, "error": f"重试 {self.max_retries} 次后仍失败: {last_error}"}

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
            return await self.page_ops.click(target)

        elif action == "fill":
            if progress_callback:
                await progress_callback("fill", f"正在填写: {target[:30]}")
            return await self.page_ops.fill(target, value)

        elif action == "select":
            if progress_callback:
                await progress_callback("select", f"正在选择: {target[:30]} → {value[:30]}")
            return await self.page_ops.select(target, value)

        else:
            return {"success": False, "error": f"不支持的操作类型: {action}"}

    def _build_result(
        self,
        success: bool,
        result: str = "",
        error: str = "",
        status: str = "done",
        question: str = "",
        screenshot: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建返回结果"""
        task_result = {
            "success": success,
            "task": self._task_description if hasattr(self, '_task_description') else "",
            "status": status,
            "steps_taken": len(self.steps),
            "steps": self.steps,
            "final_url": self.session.page.url if self.session and self.session.page else "",
        }

        if result:
            task_result["result"] = result
        if error:
            task_result["error"] = error
        if question:
            task_result["question"] = question
        if screenshot or self.screenshot_path:
            task_result["screenshot"] = screenshot or self.screenshot_path

        task_result["message"] = result or error or "任务执行完毕"

        return task_result
