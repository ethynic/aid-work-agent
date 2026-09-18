"""会话任务工具：仅可信上下文身份，发布凭据只能由工作台签发。"""
import asyncio
from typing import Any, Dict, Literal, Optional
from urllib.parse import quote
from uuid import UUID

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.session_tasks.constants import SessionTaskError
from src.session_tasks.models import TaskDraftCreatePayload, TaskSpecPayload
from src.tools.base import BaseTool
from src.tools.context import current_tool_execution_context
from src.tools.weixin.weixin_automation_tools import _jsonable


def _get_service():
    from src.session_tasks import service
    return service


class SessionTaskPrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: Optional[UUID] = None
    expected_version: Optional[int] = Field(default=None, ge=1)
    device_id: Optional[UUID] = None
    account_binding_id: Optional[UUID] = None
    conversation_binding_id: Optional[UUID] = None
    resolution_invocation_id: Optional[UUID] = None
    spec: TaskSpecPayload

    @model_validator(mode="after")
    def _operation_fields(self):
        bindings = (self.device_id, self.account_binding_id, self.conversation_binding_id, self.resolution_invocation_id)
        if self.task_id:
            if self.expected_version is None or any(bindings):
                raise ValueError("更新需要 expected_version，且不可修改设备和会话绑定")
        elif self.expected_version is not None or not self.device_id:
            raise ValueError("新建需要设备，且不接受 expected_version")
        elif self.resolution_invocation_id:
            if self.account_binding_id or self.conversation_binding_id:
                raise ValueError("名称定位与既有绑定不能混用")
        elif not self.account_binding_id or not self.conversation_binding_id:
            raise ValueError("新建需要名称定位结果或完整既有绑定")
        return self


class SessionTaskPublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    expected_version: int = Field(ge=1)
    confirmation_id: UUID = Field(description="用户在工作台点击授权后签发的一次性凭据；工具不可生成")


class ResumeFrom(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["fresh_baseline"]
    expected_input_version: int = Field(ge=0)


class SessionTaskManageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "get", "pause", "stop", "handoff", "resume"]
    task_id: Optional[UUID] = None
    expected_version: Optional[int] = Field(default=None, ge=1)
    reason_code: Optional[str] = Field(default=None, min_length=1, max_length=100)
    resume_from: Optional[ResumeFrom] = None
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)
    status: Optional[Literal["draft", "active", "paused", "blocked", "completed", "stopped", "human_required"]] = None

    @model_validator(mode="after")
    def _action_fields(self):
        if self.action != "list" and not self.task_id:
            raise ValueError("此操作需要 task_id")
        if self.action not in ("list", "get") and self.expected_version is None:
            raise ValueError("控制操作需要 expected_version")
        if self.action == "stop" and not self.reason_code:
            raise ValueError("停止需要 reason_code")
        if (self.action == "resume") != (self.resume_from is not None):
            raise ValueError("仅恢复操作必须提供显式 fresh_baseline 水位选择")
        return self


async def _execute(model, kwargs, operation):
    context = current_tool_execution_context()
    if not context or not context.tenant_id or not context.user_id:
        return {"success": False, "code": "NOT_FOUND", "error": "登录身份或租户上下文缺失"}
    try:
        args = model.model_validate(kwargs)
        result = await asyncio.to_thread(operation, _get_service(), context, args)
        return {"success": True, **_jsonable(result)}
    except ValidationError:
        # 不回显模型输入（可能包含正文或误传凭据）。
        return {"success": False, "code": "VALIDATION_FAILED", "error": "参数无效，请检查操作所需字段及任务配置"}
    except SessionTaskError as exc:
        return {"success": False, "code": exc.code, "error": str(exc)}
    except Exception as exc:
        logger.error("会话任务工具调用失败，异常类型={}", type(exc).__name__)
        return {"success": False, "code": "INTERNAL_ERROR", "error": "操作失败，请稍后重试"}


class SessionTaskPrepareTool(BaseTool):
    name = "session_task_prepare"
    display_name = "准备会话任务"
    category = "weixin"
    description = "创建或修改会话任务草稿，不执行。名称任务先调用 weixin_name_resolve，使用返回的 device_id 和 resolution_invocation_id，无需微信号或账号绑定。返回工作台授权表单入口；更新需 task_id 与 expected_version。"
    InputModel = SessionTaskPrepareInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        def prepare(service, context, args):
            if args.task_id:
                result = service.update_draft(context.tenant_id, context.user_id, args.task_id,
                                              args.expected_version, args.spec.model_dump(mode="json"))
            else:
                payload = TaskDraftCreatePayload(
                    device_id=str(args.device_id),
                    account_binding_id=str(args.account_binding_id) if args.account_binding_id else None,
                    conversation_binding_id=str(args.conversation_binding_id) if args.conversation_binding_id else None,
                    resolution_invocation_id=str(args.resolution_invocation_id) if args.resolution_invocation_id else None,
                    # B1.2 envelope（设计 §4.3）：spec 字段为 Dict[str, Any]，工具输入模型
                    # TaskSpecPayload 实例在此转 plain dict（校验语义不变，仅表示形式）
                    spec=args.spec.model_dump(mode="json"))
                result = service.create_draft(context.tenant_id, context.user_id, payload)
            task_id = result.get("task_id") or args.task_id
            return {**result, "published": False,
                    "confirmation_url": f"/t/{quote(str(context.tenant_id), safe='')}/weixin-marketing/session-tasks/{task_id}",
                    "message": "草稿已保存。请打开工作台核对完整授权范围并点击发布；聊天确认不构成发布凭据。"}
        return await _execute(self.InputModel, kwargs, prepare)


class SessionTaskPublishTool(BaseTool):
    name = "session_task_publish"
    display_name = "发布会话任务"
    category = "weixin"
    description = "使用用户工作台签发的 confirmation_id 发布会话任务。不得生成确认凭据；聊天同意或 confirmed=true 无效。发布后无需保持主智能体等待。"
    InputModel = SessionTaskPublishInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        return await _execute(self.InputModel, kwargs, lambda service, context, args: service.publish_task_once(
            context.tenant_id, context.user_id, args.task_id, args.expected_version, args.confirmation_id))


class SessionTaskManageTool(BaseTool):
    name = "session_task_manage"
    display_name = "管理会话任务"
    category = "weixin"
    description = "查询 list/get，或 pause/stop/handoff/resume。控制需要当前 expected_version。恢复需用户显式选择 fresh_baseline 及 expected_input_version，跳过历史积压消息。禁止任意命令及自行循环轮询。"
    InputModel = SessionTaskManageInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        def manage(service, context, args):
            if args.action == "list":
                return service.list_tasks(context.tenant_id, context.user_id, limit=args.limit,
                                          offset=args.offset, status=args.status)
            if args.action == "get":
                return service.get_task(context.tenant_id, context.user_id, args.task_id)
            return service.control_task(context.tenant_id, context.user_id, args.task_id, args.action,
                                        args.expected_version, args.reason_code,
                                        resume_from=args.resume_from.model_dump() if args.resume_from else None)
        return await _execute(self.InputModel, kwargs, manage)
