"""browser_automation 工具

统一的浏览器自动化入口，用一个工具替换原来的 12 个浏览器子工具。
用户只需传入任务描述，工具内部通过 LLM 自主完成所有浏览器操作。
支持多轮交互：当页面需要登录/验证码时，会暂停并询问用户，用户回复后继续操作。
"""

from typing import Any, Dict, Optional
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.config.settings import settings
from src.tools.browser.orchestrator import BrowserOrchestrator
from src.tools.browser.session import get_task_context, has_browser_session


class BrowserAutomationInput(BaseModel):
    """浏览器自动化参数"""
    task: Optional[str] = Field(None, description=(
        "要在浏览器中完成的任务描述。"
        "如：「打开 example.com 并登录，用户名 admin 密码 123456」"
        "「访问淘宝搜索'机械键盘'并按销量排序，提取前10个商品名称和价格」"
        "当回复用户的验证码/登录信息时，task 为空，通过 user_response 参数传入"
    ))
    url: Optional[str] = Field(None, description=(
        "起始页面 URL（可选）。如果任务描述中已包含 URL 则可不填。"
        "如需先导航到某个页面再执行任务，可在此指定。"
    ))
    session_id: Optional[str] = Field("default", description="浏览器会话ID，用于会话恢复")
    headless: Optional[bool] = Field(None, description="是否无头模式运行")
    user_response: Optional[str] = Field(None, description=(
        "用户对浏览器操作中提出的问题的回复。"
        "例如浏览器要求提供手机号、验证码、登录凭据时，"
        "将用户的回复通过此参数传入以继续操作。"
    ))


class BrowserAutomationTool(BaseTool):
    """浏览器自动化工具

    通过自然语言任务描述驱动浏览器完成操作。
    内部使用 LLM 编排器自主完成所有步骤。
    支持多轮交互：检测到需要登录/验证码时暂停，用户回复后继续。
    """

    name = "browser_automation"
    description = """通过自然语言任务描述自动完成浏览器操作。

只需描述你想在浏览器中完成的任务，系统会自动打开网页、定位元素、执行操作并返回结果。

适用场景：
- 打开网站并登录/注册
- 填写和提交表单
- 搜索信息并提取结果
- 网页数据采集
- 自动化网页操作流程

使用方式：
- task="打开 https://example.com 并用用户名 admin 密码 123456 登录"
- task="访问京东搜索'机械键盘'，提取前10个商品名称和价格"
- task="在 OA 系统中提交一份报销申请，类型为差旅费，金额 1500 元"

多轮交互（登录/验证码）：
当浏览器需要用户提供信息（如手机号、验证码、密码）时，工具会返回需要确认的问题。
用户提供信息后，再次调用此工具，通过 user_response 参数传入用户的信息，浏览器会继续操作。
例如：user_response="13800138000"（提供手机号）或 user_response="123456"（提供验证码）

注意：
- 涉及敏感信息（如密码）会在返回结果中脱敏显示
- 最多执行 30 步操作，超时限制 5 分钟
- 如需用户确认会中断并返回问题"""
    display_name = "浏览器自动化"
    category = "browser"
    InputModel = BrowserAutomationInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        base = self.display_name
        if tool_args:
            # 多轮交互时显示不同标题
            user_response = tool_args.get("user_response")
            if user_response:
                return f"{base}「用户回复: {user_response[:20]}...」"
            task = tool_args.get("task", "")
            if task:
                if len(task) > 30:
                    return f"{base}「{task[:30]}...」"
                return f"{base}「{task}」"
        return base

    async def execute(self, **kwargs) -> Dict[str, Any]:
        task = kwargs.get("task", "")
        url = kwargs.get("url")
        session_id = kwargs.get("session_id", "default")
        headless = kwargs.get("headless")
        user_response = kwargs.get("user_response")

        # 多轮交互模式：用户提供了回复但没有新任务
        if user_response and not task:
            saved_ctx = get_task_context(session_id)
            if not saved_ctx:
                return {
                    "success": False,
                    "error": "没有待回复的浏览器任务。请先提出浏览器操作任务。",
                }
            task = saved_ctx.task  # 使用保存的任务描述

        if not task and not user_response:
            return {"success": False, "error": "必须提供任务描述"}

        try:
            orchestrator = BrowserOrchestrator(session_id=session_id, headless=headless)
            orchestrator._task_description = task

            result = await orchestrator.execute(
                task=task,
                url=url,
                user_response=user_response,
            )

            # 增强 ask_user 的返回信息
            if result.get("status") == "ask_user":
                result["resume_session_id"] = session_id
                result["instruction"] = (
                    "浏览器操作需要用户提供信息才能继续。"
                    "请将用户的回复通过 user_response 参数再次调用 browser_automation 工具。"
                )

            return result

        except Exception as e:
            logger.error(f"浏览器自动化执行失败: {e}", exc_info=True)
            return {"success": False, "error": f"浏览器自动化执行失败: {str(e)}"}
