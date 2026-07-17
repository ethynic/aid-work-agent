"""Agent 可见的统一浏览器自动化工具。"""

import asyncio
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.tools._helpers import sanitize_error
from src.tools.base import BaseTool
from src.tools.browser.orchestrator import BrowserOrchestrator
from src.tools.browser.run_manager import BrowserRunManager, RunState


_DEPRECATED_RESUME_ERROR = (
    "旧版浏览器会话恢复参数已停用，请重新发起完整的浏览器任务"
)


class BrowserAutomationInput(BaseModel):
    """浏览器自动化参数；headless 由服务端配置强制决定。"""

    task: Optional[str] = Field(None, description="要在浏览器中完成的完整任务描述")
    url: Optional[str] = Field(None, description="起始页面 URL（可选）")


class BrowserAutomationTool(BaseTool):
    """每次调用独占 browser run，任何终态均关闭。"""

    name = "browser_automation"
    description = """通过完整自然语言任务自动执行网页操作。

服务端始终使用部署配置的无头模式，每次调用结束都会关闭浏览器。
当前过渡版本不支持验证码/登录等人工步骤的跨请求恢复；遇到人工步骤会安全
关闭并明确返回，不能通过 session_id 或 user_response 续跑。"""
    display_name = "浏览器自动化"
    category = "browser"
    InputModel = BrowserAutomationInput

    def __init__(self) -> None:
        self._tenant_id: Optional[str] = None
        self._user_id: Optional[str] = None

    def set_tenant_id(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """不显示任务、用户回复或表单正文。"""
        del tool_args
        return self.display_name

    async def execute(self, **kwargs) -> Dict[str, Any]:
        if kwargs.get("headless") is not None:
            return {
                "success": False,
                "error_code": "DEPRECATED_PARAMETER",
                "error": "headless 参数已停用，浏览器模式由服务端配置决定",
            }
        if kwargs.get("session_id") is not None or kwargs.get("user_response") is not None:
            return {
                "success": False,
                "error_code": "DEPRECATED_PARAMETER",
                "error": _DEPRECATED_RESUME_ERROR,
            }

        task = kwargs.get("task") or ""
        url = kwargs.get("url")
        if not task:
            return {"success": False, "error_code": "INVALID_INPUT", "error": "必须提供任务描述"}

        tenant_id = kwargs.get("_trusted_tenant_id")
        user_id = kwargs.get("_trusted_user_id")
        try:
            from src.saas.context import get_current_tenant_id, get_current_user_id
            tenant_id = tenant_id or get_current_tenant_id()
            user_id = user_id or get_current_user_id()
        except Exception:
            pass
        tenant_id = tenant_id or self._tenant_id
        user_id = user_id or self._user_id
        if not tenant_id or not user_id:
            return {
                "success": False,
                "error_code": "MISSING_EXECUTION_CONTEXT",
                "error": "浏览器任务缺少租户或用户执行上下文",
            }

        manager = BrowserRunManager()
        audit_session_id = kwargs.get("_audit_session_id") or f"audit_{user_id}"
        record = await manager.create(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=audit_session_id,
            execution_target="server",
        )
        orchestrator = BrowserOrchestrator(
            session_id=record.session_id,
            run_manager=manager,
            run_record=record,
        )
        orchestrator._task_description = task
        try:
            async with asyncio.timeout(settings.tools.browser.task_timeout):
                return await orchestrator.execute(task=task, url=url)
        except asyncio.TimeoutError:
            return {
                "success": False,
                "status": "timeout",
                "error_code": "TASK_TIMEOUT",
                "error": "浏览器任务执行超时",
            }
        except asyncio.CancelledError:
            orchestrator.cancel()
            raise
        except Exception as exc:
            logger.error("浏览器自动化执行失败: type={}", type(exc).__name__)
            return {
                "success": False,
                "error_code": "INTERNAL_ERROR",
                "error": sanitize_error(exc, fallback="浏览器自动化执行失败"),
            }
        finally:
            # Orchestrator 也有边界关闭；这里兜底覆盖外层超时/取消。
            try:
                await asyncio.shield(
                    manager.finalize(
                        record.tenant_id,
                        record.run_id,
                        RunState.FAILED,
                        "tool_boundary",
                    )
                )
            except Exception as exc:
                logger.warning("browser tool 边界回收异常: type={}", type(exc).__name__)
