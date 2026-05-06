"""browser_automation 工具

统一的浏览器自动化入口，用一个工具替换原来的 12 个浏览器子工具。
用户只需传入任务描述，工具内部通过 LLM 自主完成所有浏览器操作。
"""

from typing import Any, Dict, Optional
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.config.settings import settings
from src.tools.browser.orchestrator import BrowserOrchestrator


class BrowserAutomationInput(BaseModel):
    """浏览器自动化参数"""
    task: str = Field(..., description=(
        "要在浏览器中完成的任务描述。"
        "如：「打开 example.com 并登录，用户名 admin 密码 123456」"
        "「访问淘宝搜索'机械键盘'并按销量排序，提取前10个商品名称和价格」"
    ))
    url: Optional[str] = Field(None, description=(
        "起始页面 URL（可选）。如果任务描述中已包含 URL 则可不填。"
        "如需先导航到某个页面再执行任务，可在此指定。"
    ))
    session_id: Optional[str] = Field("default", description="浏览器会话ID，用于会话恢复")
    headless: Optional[bool] = Field(None, description="是否无头模式运行")


class BrowserAutomationTool(BaseTool):
    """浏览器自动化工具

    通过自然语言任务描述驱动浏览器完成操作。
    内部使用 LLM 编排器自主完成所有步骤。
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

注意：
- 涉及敏感信息（如密码）会在返回结果中脱敏显示
- 最多执行 20 步操作，超时限制 5 分钟
- 如需用户确认会中断并返回问题"""
    display_name = "浏览器自动化"
    category = "browser"
    InputModel = BrowserAutomationInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        base = self.display_name
        if tool_args:
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

        if not task:
            return {"success": False, "error": "必须提供任务描述"}

        try:
            orchestrator = BrowserOrchestrator(session_id=session_id, headless=headless)
            orchestrator._task_description = task

            result = await orchestrator.execute(
                task=task,
                url=url,
            )

            return result

        except Exception as e:
            logger.error(f"浏览器自动化执行失败: {e}", exc_info=True)
            return {"success": False, "error": f"浏览器自动化执行失败: {str(e)}"}
