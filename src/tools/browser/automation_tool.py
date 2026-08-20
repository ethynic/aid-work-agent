"""Agent 可见的统一浏览器自动化工具。"""

import asyncio
import ctypes
import os
from ctypes import wintypes
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.tools._helpers import sanitize_error
from src.tools.base import BaseTool
from src.tools.browser.orchestrator import BrowserOrchestrator
from src.tools.browser.run_manager import BrowserRunManager, RunState
from src.tools.browser.human_control import HumanControlCoordinator


_DEPRECATED_RESUME_ERROR = (
    "旧版浏览器会话恢复参数已停用，请重新发起完整的浏览器任务"
)
# 进程内能力令牌，无法由 JSON/tool arguments 伪造。可信本地调用方必须显式导入并注入。
_LOCAL_INTERACTIVE_TRUST = object()


class BrowserAutomationInput(BaseModel):
    """浏览器自动化参数；可见模式仅允许可信本地交互调用。"""

    task: Optional[str] = Field(None, description="要在浏览器中完成的完整任务描述")
    url: Optional[str] = Field(None, description="起始页面 URL（可选）")
    headless: Optional[bool] = Field(
        None,
        description="浏览器模式；省略时使用服务端配置，false 仅限可信本地交互桌面",
    )


def _windows_interactive_desktop(
    session_id_provider=None,
    open_input_desktop=None,
    close_desktop=None,
) -> bool:
    """用 Windows session 和 input desktop 能力判断；任何 API 异常均拒绝。"""
    try:
        if session_id_provider is None:
            def session_id_provider():
                session_api = ctypes.windll.kernel32.ProcessIdToSessionId
                session_api.argtypes = [
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                session_api.restype = wintypes.BOOL
                session_id = wintypes.DWORD()
                if not session_api(
                    os.getpid(), ctypes.byref(session_id)
                ):
                    raise OSError("ProcessIdToSessionId failed")
                return int(session_id.value)

        if int(session_id_provider()) == 0:
            return False

        if open_input_desktop is None:
            open_api = ctypes.windll.user32.OpenInputDesktop
            open_api.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_api.restype = wintypes.HANDLE
            open_input_desktop = lambda: open_api(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if close_desktop is None:
            close_api = ctypes.windll.user32.CloseDesktop
            close_api.argtypes = [wintypes.HANDLE]
            close_api.restype = wintypes.BOOL
            close_desktop = lambda handle: close_api(handle)

        desktop = open_input_desktop()
        if not desktop:
            return False
        return bool(close_desktop(desktop))
    except Exception:
        return False


def _has_interactive_desktop() -> bool:
    """只判断本机进程是否位于交互桌面，不接受请求方提供的环境描述。"""
    if os.name == "nt":
        return _windows_interactive_desktop()
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class BrowserAutomationTool(BaseTool):
    """每次调用独占 browser run，任何终态均关闭。"""

    name = "browser_automation"
    description = """通过完整自然语言任务自动执行网页操作。

默认使用部署配置的浏览器模式。可见模式只允许可信本地交互桌面调用。
当前过渡版本不支持验证码/登录等人工步骤的跨请求恢复；遇到人工步骤会安全
关闭并明确返回，不能通过 session_id 或 user_response 续跑。"""
    display_name = "浏览器自动化"
    category = "browser"
    InputModel = BrowserAutomationInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """不显示任务、用户回复或表单正文。"""
        del tool_args
        return self.display_name

    async def execute_local_interactive(
        self, *, task: str, url: Optional[str] = None
    ) -> Any:
        """供同进程本地桌面入口调用；该方法不会暴露到 Agent tool schema。"""
        from src.tools.context import ExecutionContextFactory, tool_execution_scope
        with tool_execution_scope(ExecutionContextFactory.for_agent_call()):
            return await self.execute(
                task=task,
                url=url,
                headless=False,
                _trusted_local_interactive=_LOCAL_INTERACTIVE_TRUST,
            )

    async def execute(self, **kwargs) -> Any:
        requested_headless = kwargs.get("headless")
        if requested_headless is False and not (
            kwargs.get("_trusted_local_interactive") is _LOCAL_INTERACTIVE_TRUST
            and _has_interactive_desktop()
        ):
            return {
                "success": False,
                "error_code": "VISIBLE_BROWSER_NOT_ALLOWED",
                "error": "可见浏览器只允许可信本地交互桌面调用",
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

        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        tenant_id = context.tenant_id if context else None
        user_id = context.user_id if context else None
        if not tenant_id or not user_id:
            return {
                "success": False,
                "error_code": "MISSING_EXECUTION_CONTEXT",
                "error": "浏览器任务缺少租户或用户执行上下文",
            }

        manager = BrowserRunManager()
        # 私有属性只在本次 run manager 内存中生效，不进入持久化记录或用户输入面。
        manager.headless_override = requested_headless
        # session 只能来自可信边界构造的不可变上下文；模型参数或遗留私有参数
        # 不得覆盖审计归属。
        audit_session_id = context.session_id or f"audit_{user_id}"
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
                result = await orchestrator.execute(task=task, url=url)
                if result.get("status") != "ask_user":
                    return result
                if manager.degraded:
                    await manager.finalize(
                        record.tenant_id, record.run_id, RunState.FAILED,
                        "tool_suspend_redis_unavailable",
                    )
                    return {
                        "success": False, "error_code": "TOOL_SUSPEND_FAILED",
                        "error": "当前无法安全保存人工接管状态，请稍后重试",
                    }
                execution_id = context.agent_execution_id
                tool_call_id = context.tool_call_id
                if not execution_id or not tool_call_id:
                    await manager.finalize(
                        record.tenant_id, record.run_id, RunState.FAILED,
                        "tool_suspend_context_missing",
                    )
                    return {
                        "success": False, "error_code": "TOOL_SUSPEND_FAILED",
                        "error": "当前无法安全保存人工接管状态，请稍后重试",
                    }
                return await HumanControlCoordinator().suspend(
                    tenant_id=record.tenant_id, user_id=record.user_id,
                    session_id=record.session_id, agent_execution_id=execution_id,
                    tool_call_id=tool_call_id, run_id=record.run_id,
                    manager=manager, orchestrator=orchestrator,
                    executor=orchestrator.executor,
                    reason_code=result.get("error_code", "HUMAN_REQUIRED"),
                    step_index=len(orchestrator.steps),
                )
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
                store = getattr(manager, "store", None)
                state = await store.get(record.tenant_id, record.run_id) if store else None
                if state is None or state.state not in {
                    RunState.WAITING_HUMAN.value, RunState.RUNNING_HUMAN.value,
                    RunState.RESUMING.value,
                }:
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
