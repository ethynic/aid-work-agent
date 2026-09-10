"""微信营销自动化聊天工具（P3-B，R54③ / R56）

三工具共用 src.weixin_marketing 服务层（prepare 与 publish 分离）：

- ``weixin_automation_prepare``：创建/更新草稿（无发送副作用），返回草稿摘要 +
  静态校验（含未来 5 次触发预览）+ 明确「未发布」状态；
- ``weixin_automation_publish``：发布草稿（automation_id + expected_version CAS），
  返回发布结果 + 首次触发时间；不做试发；
- ``weixin_automation_manage``：pause/resume/archive/run/cancel/resolve/retry/
  test_send/list/get_runs/get_run_detail；高危动作（run/test_send/retry）在结果中
  明确提示人工确认语义；服务层错误经稳定码透传。

R56 约束：InputModel 只接业务字段（extra=forbid，不接 tenant/user/raw argv，
LLM 传入身份字段会被参数校验直接拒绝）；认证身份一律取 ToolExecutionContext
（缺失 fail-closed），工具实例不保存请求态。服务层为同步 psycopg2 实现，
统一经 asyncio.to_thread 调用（假异步规范）。
"""

import asyncio
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.tools.base import BaseTool
from src.tools.context import ToolExecutionContext, current_tool_execution_context


# ==================== 稳定错误码（与 src/weixin_marketing/api.py 码表对齐）====================
# 不直接 import api.py 的常量：那会把 FastAPI 路由链拉进工具装配的模块发现路径；
# 码值变动时需与 api._failure_from_service 同步。

CODE_NOT_FOUND = "NOT_FOUND"
CODE_CONFLICT = "CONFLICT"
CODE_TENANT_NOT_ALLOWED = "TENANT_NOT_ALLOWED"
CODE_RETRY_EVIDENCE_REQUIRED = "RETRY_EVIDENCE_REQUIRED"
CODE_VALIDATION_FAILED = "VALIDATION_FAILED"
CODE_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
CODE_VERIFY_FAILED = "VERIFY_FAILED"
CODE_PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
CODE_ADAPTER_UNREGISTERED = "ADAPTER_UNREGISTERED"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"


def _get_service():
    """懒加载服务层（无状态，可安全每次新建；也便于测试 patch.object）"""
    from src.weixin_marketing.service import WeixinMarketingService

    return WeixinMarketingService()


def _get_workbench_service():
    from src.weixin_marketing.workbench import WeixinWorkbenchService

    return WeixinWorkbenchService()


def _jsonable(value: Any) -> Any:
    """datetime/UUID/Decimal → JSON 可序列化形态（递归；照 weixin api._jsonable）"""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _resolve_identity(context: Optional[ToolExecutionContext]) -> Optional[Dict[str, Any]]:
    """从 ToolExecutionContext 解析可信身份；缺失时 fail-closed 返回 None。"""
    # tenant_id 与 api 层同口径 truthy 检查（空串与 None 同样拒绝）
    if not context or not context.user_id or not context.tenant_id:
        return None
    return {"tenant_id": context.tenant_id, "user_id": context.user_id}


def _identity_failure() -> Dict[str, Any]:
    return {
        "success": False,
        "error": "用户未登录或租户上下文缺失，无法执行微信营销操作",
        "code": CODE_NOT_FOUND,
    }


def _service_failure(exc: Exception) -> Dict[str, Any]:
    """服务层异常 → 稳定码 + 中文说明（映射与 api._failure_from_service 同语义）。

    NotFoundError 统一泛化文案（跨租户/非属主不区分存在性），原服务消息进 debug。
    """
    from src.weixin_marketing.service import NotFoundError as WxNotFoundError

    if isinstance(exc, ValidationError):
        field_errors = [
            {"field": ".".join(str(x) for x in e.get("loc", ())), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return {
            "success": False,
            "error": f"参数校验失败: {exc.errors()[0].get('msg', '') if exc.errors() else ''}",
            "code": CODE_VALIDATION_FAILED,
            "field_errors": field_errors,
        }
    if isinstance(exc, WxNotFoundError):
        return {
            "success": False,
            "error": "资源不存在或无权访问",
            "code": CODE_NOT_FOUND,
            "debug": str(exc),
        }
    # 其余 WeixinMarketingError 子类的 message 均为面向用户的中文说明，可直出
    from src.weixin_marketing.service import (
        ConflictError,
        ConfigurationError,
        PreflightFailedError,
        QuotaExceededError,
        RetryEvidenceRequiredError,
        TenantNotAllowedError,
        VerifyFailedError,
        WeixinValidationError,
    )

    if isinstance(exc, TenantNotAllowedError):
        code = CODE_TENANT_NOT_ALLOWED
    elif isinstance(exc, RetryEvidenceRequiredError):
        code = CODE_RETRY_EVIDENCE_REQUIRED
    elif isinstance(exc, WeixinValidationError):
        code = CODE_VALIDATION_FAILED
    elif isinstance(exc, QuotaExceededError):
        code = CODE_QUOTA_EXCEEDED
    elif isinstance(exc, VerifyFailedError):
        code = CODE_VERIFY_FAILED
    elif isinstance(exc, PreflightFailedError):
        code = CODE_PREFLIGHT_FAILED
    elif isinstance(exc, ConflictError):
        code = CODE_CONFLICT
    elif isinstance(exc, ConfigurationError):
        code = CODE_ADAPTER_UNREGISTERED
    else:
        code = CODE_INTERNAL_ERROR
    return {"success": False, "error": str(exc), "code": code}


def _unexpected_failure(tool_name: str, exc: Exception) -> Dict[str, Any]:
    """非服务层异常：完整堆栈只进后端日志，不向会话泄漏内部细节。"""
    logger.opt(exception=True).error(f"后端日志：{tool_name} 执行异常: {exc}")
    return {"success": False, "error": "操作失败，请稍后重试", "code": CODE_INTERNAL_ERROR}


def _draft_summary(detail: Dict[str, Any]) -> Dict[str, Any]:
    """create/update 返回的详情 → 聊天侧草稿摘要（不含正文全文，控制上下文体积）"""
    automation = detail.get("automation") or {}
    blocks = detail.get("draft_blocks") or []
    trigger = detail.get("draft_trigger") or {}
    return {
        "automation_id": str(automation.get("id") or ""),
        "name": automation.get("name"),
        "status": automation.get("status"),
        "version": automation.get("version"),
        "revision_id": str(automation.get("draft_revision_id") or ""),
        "trigger_type": trigger.get("type"),
        "block_count": len(blocks),
        "blocks": [{"position": b.get("position"), "kind": b.get("kind")} for b in blocks],
    }


def _validation_view(validation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(validation.get("ok")),
        "errors": validation.get("errors") or [],
        "warnings": validation.get("warnings") or [],
        "next_fires": (validation.get("next_fires") or [])[:5],
    }


# ==================== prepare ====================


class WeixinAutomationPrepareInput(BaseModel):
    """草稿创建/更新参数（R56：只接业务字段；身份字段一律拒绝）"""

    model_config = ConfigDict(extra="forbid")

    automation_id: Optional[str] = Field(
        default=None,
        description="自动化任务 ID；提供则进入更新模式（按 expected_version CAS 更新草稿），缺省为新建草稿",
    )
    name: str = Field(min_length=1, max_length=128, description="任务名称")
    group_binding_id: str = Field(description="目标群绑定 ID（群身份凭证，来自群绑定工作台）")
    trigger: Dict[str, Any] = Field(
        description=(
            "触发配置（判别字段 type）："
            '{"type":"once","run_at":"ISO时间"} | '
            '{"type":"interval","start_at":"ISO时间","interval_seconds":3600} | '
            '{"type":"calendar","hour":9,"minute":0} | '
            '{"type":"event","source_ref":"事件源","event_type":"*"}'
        ),
    )
    blocks: List[Dict[str, Any]] = Field(
        min_length=1,
        description=(
            "固定内容块列表（顺序发送）：文本 {\"type\":\"text\",\"text_content\":\"...\"}、"
            "链接 {\"type\":\"link\",\"url\":\"https://...\"}、"
            "图片 {\"type\":\"image\",\"asset_id\":\"...\"}"
        ),
    )
    expected_version: Optional[int] = Field(
        default=None, ge=1, description="更新模式必填：当前版本号（乐观锁，版本冲突服务层返回 409 语义）"
    )
    policy: Optional[Dict[str, Any]] = Field(
        default=None, description="可选执行策略（如失败处理），一般无需提供"
    )


class WeixinAutomationPrepareTool(BaseTool):
    """创建/更新微信营销自动化草稿（无发送副作用）"""

    name = "weixin_automation_prepare"
    description = (
        "创建或更新微信营销自动化草稿：固定内容块 + 触发配置 + 目标群绑定。"
        "本工具只落草稿并做静态校验（含未来 5 次触发预览），不发布、不试发、无任何发送副作用。"
        "创建后需用户明确确认再调用 weixin_automation_publish 发布；"
        "更新已有任务时必须提供 automation_id 与当前 expected_version。"
    )
    display_name = "准备微信营销草稿"
    category = "weixin"
    InputModel = WeixinAutomationPrepareInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.weixin_marketing.models import (
            AutomationCreateInput,
            DraftUpdateInput,
        )

        identity = _resolve_identity(current_tool_execution_context())
        if identity is None:
            return _identity_failure()
        tenant_id, user_id = identity["tenant_id"], identity["user_id"]

        try:
            args = WeixinAutomationPrepareInput(**kwargs)
        except ValidationError as e:
            return _service_failure(e)
        automation_id = args.automation_id
        was_update = bool(automation_id)
        service = _get_service()
        try:
            if automation_id:
                # ---- 更新模式（草稿原地重写；发布后迭代则新建下一号草稿）----
                if args.expected_version is None:
                    return {
                        "success": False,
                        "error": "更新草稿必须提供 expected_version（当前版本号，可先用 manage list 查询）",
                        "code": CODE_VALIDATION_FAILED,
                    }
                payload = DraftUpdateInput(
                    expected_version=args.expected_version,
                    name=args.name,
                    trigger=args.trigger,
                    blocks=args.blocks,
                    group_binding_id=args.group_binding_id,
                    policy=args.policy,
                )
                detail = await asyncio.to_thread(
                    service.update_draft, tenant_id, automation_id, user_id, payload
                )
            else:
                # ---- 创建模式：automation(draft) + revision(draft, no=1) ----
                payload = AutomationCreateInput(
                    name=args.name,
                    trigger=args.trigger,
                    blocks=args.blocks,
                    group_binding_id=args.group_binding_id,
                    policy=args.policy or {},
                )
                detail = await asyncio.to_thread(
                    service.create_automation, tenant_id, user_id, payload
                )
            automation = detail.get("automation") or {}
            automation_id = str(automation.get("id") or automation_id)
            # 静态校验（只读预检：字段错误/能力缺口/未来 5 次触发；无发送）
            validation = await asyncio.to_thread(
                service.validate_automation, tenant_id, automation_id, user_id
            )
        except ValidationError as e:
            return _service_failure(e)
        except Exception as e:  # noqa: BLE001 —— 统一映射稳定码
            from src.weixin_marketing.service import WeixinMarketingError

            if isinstance(e, WeixinMarketingError):
                return _service_failure(e)
            return _unexpected_failure(self.name, e)

        summary = _draft_summary(detail)
        view = _validation_view(validation)
        status = summary["status"] or "draft"
        fired_hint = (
            "、".join(view["next_fires"][:2]) if view["next_fires"] else "暂无（校验未通过或事件触发）"
        )
        return {
            "success": True,
            **_jsonable(summary),
            "published": False,
            "status_hint": f"{status}（未发布）",
            "validation": _jsonable(view),
            "message": (
                f"草稿已{'更新' if was_update else '创建'}（状态：{status}，未发布，无任何发送副作用）。"
                f"校验{'通过' if view['ok'] else '未通过'}（{len(view['errors'])} 项错误 / {len(view['warnings'])} 项提醒），"
                f"触发预览：{fired_hint}。发布需用户明确确认后调用 weixin_automation_publish。"
            ),
        }


# ==================== publish ====================


class WeixinAutomationPublishInput(BaseModel):
    """发布参数（R56：只接业务字段；幂等语义由服务层事务保证）"""

    model_config = ConfigDict(extra="forbid")

    automation_id: str = Field(description="要发布的自动化任务 ID")
    expected_version: int = Field(ge=1, description="当前版本号（乐观锁；不匹配时服务层返回版本冲突）")


class WeixinAutomationPublishTool(BaseTool):
    """发布微信营销自动化草稿（真实生效；不做试发）"""

    name = "weixin_automation_publish"
    description = (
        "发布微信营销自动化草稿，使其按触发配置真实生效（发布不可变，修改需另建草稿再发布）。"
        "调用前必须已向用户复述任务名、目标群、内容摘要与触发时间并获得用户明确确认。"
        "只接受 automation_id 与 expected_version；发布不包含试发，"
        "需要验证效果请在发布前用 weixin_automation_manage 的 test_send。"
    )
    display_name = "发布微信营销自动化"
    category = "weixin"
    InputModel = WeixinAutomationPublishInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.weixin_marketing.models import PublishInput

        identity = _resolve_identity(current_tool_execution_context())
        if identity is None:
            return _identity_failure()
        tenant_id, user_id = identity["tenant_id"], identity["user_id"]

        try:
            args = WeixinAutomationPublishInput(**kwargs)
        except ValidationError as e:
            return _service_failure(e)
        service = _get_service()
        try:
            result = await asyncio.to_thread(
                service.publish,
                tenant_id,
                args.automation_id,
                user_id,
                PublishInput(
                    expected_version=args.expected_version,
                    authorization_source="chat",  # 审计留痕：发布授权来源为聊天通道
                ),
            )
        except ValidationError as e:
            return _service_failure(e)
        except Exception as e:  # noqa: BLE001
            from src.weixin_marketing.service import WeixinMarketingError

            if isinstance(e, WeixinMarketingError):
                return _service_failure(e)
            return _unexpected_failure(self.name, e)

        # 首次触发时间（只读预检；发布后草稿指针已清空，校验自动落在 active revision）
        next_fires: List[str] = []
        fire_warning: Optional[str] = None
        try:
            validation = await asyncio.to_thread(
                service.validate_automation, tenant_id, args.automation_id, user_id
            )
            next_fires = list(validation.get("next_fires") or [])[:5]
        except Exception as e:  # noqa: BLE001 —— 发布已成功，预览失败不回滚结果
            logger.warning(f"后端日志：weixin publish 触发预览失败 automation={args.automation_id}: {e}")
            fire_warning = "触发时间预览暂不可用，请稍后用 manage get_runs 或工作台查看"

        first_fire = next_fires[0] if next_fires else None
        return {
            "success": True,
            **_jsonable(result),
            "first_fire_at": first_fire,
            "next_fires": next_fires,
            "message": (
                "已发布并真实生效（触发后将向目标群真实发送）。"
                + (f"首次触发时间：{first_fire}。" if first_fire else "暂无时间触发预览（事件触发或预览不可用）。")
                + (f" {fire_warning}" if fire_warning else "")
                + " 如需暂停用 weixin_automation_manage 的 pause。"
            ),
        }


# ==================== manage ====================


class WeixinAutomationManageInput(BaseModel):
    """管理动作参数（R56：只接业务字段）"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(
        description=(
            "操作：list 任务列表 | pause 暂停 | resume 恢复 | archive 归档 | "
            "run 手动触发一次（真实发送，高危） | cancel 取消运行中的 run | "
            "resolve 投递人工结论 | retry 重试投递（真实发送，高危） | "
            "test_send 试发单条内容块（真实发送，高危） | "
            "get_runs 运行列表 | get_run_detail 运行详情"
        )
    )
    automation_id: Optional[str] = Field(default=None, description="自动化任务 ID（pause/resume/archive/run/test_send 用）")
    run_id: Optional[str] = Field(default=None, description="运行 ID（cancel/get_run_detail 用）")
    delivery_id: Optional[str] = Field(default=None, description="投递 ID（resolve/retry 用）")
    expected_version: Optional[int] = Field(default=None, ge=1, description="当前版本号（pause/resume/archive 必填）")
    reason: Optional[str] = Field(default=None, max_length=500, description="操作原因（pause/archive 可选）")
    verdict: Optional[str] = Field(
        default=None, description="resolve 必填人工结论：delivered 已送达 | not_delivered 未送达 | uncertain 不确定"
    )
    decision: Optional[str] = Field(
        default=None, description="resolve 可选授权决定：confirmed_not_sent（人工确认未发送并授权重试，必须附 note）"
    )
    note: Optional[str] = Field(default=None, max_length=2000, description="resolve 证据说明（decision 时必填）")
    confirm: bool = Field(default=False, description="retry 必须为 true（显式人工决定）")
    group_binding_id: Optional[str] = Field(default=None, description="test_send 必填：试发目标群绑定 ID")
    block_position: Optional[int] = Field(
        default=None, ge=1, description="test_send 必填：试发的块序号（从 1 开始，与工作台内容块编号一致）"
    )
    keyword: Optional[str] = Field(default=None, description="list 可选：任务名关键词")
    status: Optional[str] = Field(default=None, description="list 可选：状态过滤 draft/active/paused/archived")
    state: Optional[str] = Field(default=None, description="get_runs 可选：运行状态过滤")
    page: int = Field(default=1, ge=1, description="分页页码")
    page_size: int = Field(default=5, ge=1, le=20, description="每页条数（聊天上下文有限，默认 5）")


class WeixinAutomationManageTool(BaseTool):
    """管理微信营销自动化（状态/运行/投递/试发）"""

    name = "weixin_automation_manage"
    description = (
        "管理微信营销自动化任务：list 列表、pause/resume/archive 状态管理（需 expected_version）、"
        "run 手动触发一次、cancel 取消运行、get_runs/get_run_detail 查询运行、"
        "resolve 投递人工结论、retry 重试、test_send 试发单条内容块。"
        "run/test_send/retry 均为真实发送副作用，调用前必须获得用户明确确认。"
    )
    display_name = "管理微信营销自动化"
    category = "weixin"
    InputModel = WeixinAutomationManageInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.weixin_marketing.models import (
            DeliveryResolveInput,
            TestSendInput,
            VersionedActionInput,
        )

        identity = _resolve_identity(current_tool_execution_context())
        if identity is None:
            return _identity_failure()
        tenant_id, user_id = identity["tenant_id"], identity["user_id"]

        try:
            args = WeixinAutomationManageInput(**kwargs)
        except ValidationError as e:
            return _service_failure(e)

        def _require(condition: bool, message: str) -> Optional[Dict[str, Any]]:
            return None if condition else {
                "success": False, "error": message, "code": CODE_VALIDATION_FAILED,
            }

        service = _get_service()
        try:
            # ---------- 查询类 ----------
            if args.action == "list":
                result = await asyncio.to_thread(
                    service.list_automations,
                    tenant_id, user_id,
                    keyword=args.keyword, status=args.status,
                    page=args.page, page_size=args.page_size,
                )
                items = [
                    {
                        "automation_id": str(i.get("id")),
                        "name": i.get("name"),
                        "status": i.get("status"),
                        "version": i.get("version"),
                        "updated_at": str(i.get("updated_at")) if i.get("updated_at") else None,
                    }
                    for i in result.get("items") or []
                ]
                return {
                    "success": True,
                    "items": items,
                    "total": result.get("total"),
                    "page": result.get("page"),
                    "page_size": result.get("page_size"),
                    "message": f"共 {result.get('total')} 个微信营销任务（当前第 {result.get('page')} 页）",
                }

            if args.action == "get_runs":
                result = await asyncio.to_thread(
                    service.list_runs,
                    tenant_id, user_id,
                    automation_id=args.automation_id, state=args.state,
                    page=args.page, page_size=args.page_size,
                )
                items = [
                    {
                        "run_id": str(i.get("id")),
                        "automation_id": i.get("task_ref"),
                        "state": i.get("state"),
                        "created_at": str(i.get("created_at")) if i.get("created_at") else None,
                        "finished_at": str(i.get("finished_at")) if i.get("finished_at") else None,
                    }
                    for i in result.get("items") or []
                ]
                return {
                    "success": True,
                    "items": items,
                    "total": result.get("total"),
                    "message": f"共 {result.get('total')} 条运行记录",
                }

            if args.action == "get_run_detail":
                miss = _require(bool(args.run_id), "get_run_detail 需要提供 run_id")
                if miss:
                    return miss
                result = await asyncio.to_thread(
                    service.get_run_detail, tenant_id, args.run_id, user_id
                )
                return {"success": True, **_jsonable(result)}

            # ---------- 状态管理 ----------
            if args.action in ("pause", "resume", "archive"):
                miss = _require(
                    bool(args.automation_id) and args.expected_version is not None,
                    f"{args.action} 需要提供 automation_id 与 expected_version",
                )
                if miss:
                    return miss
                result = await asyncio.to_thread(
                    getattr(service, args.action),
                    tenant_id, args.automation_id, user_id,
                    VersionedActionInput(expected_version=args.expected_version, reason=args.reason),
                )
                action_label = {"pause": "暂停", "resume": "恢复", "archive": "归档"}[args.action]
                return {
                    "success": True,
                    **_jsonable(result),
                    "message": (
                        f"任务 {args.automation_id} 已{action_label}"
                        "（版本已递增，后续操作请用新 version）"
                    ),
                }

            if args.action == "cancel":
                miss = _require(bool(args.run_id), "cancel 需要提供 run_id")
                if miss:
                    return miss
                result = await asyncio.to_thread(
                    service.cancel_run, tenant_id, args.run_id, user_id
                )
                return {
                    "success": True,
                    **_jsonable(result),
                    "message": (
                        f"运行 {args.run_id} 取消请求已受理（未开始条目跳过；"
                        "已提交条目照实回收，不撤回不重发）"
                    ),
                }

            # ---------- 投递人工结论 / 重试 ----------
            if args.action == "resolve":
                miss = _require(
                    bool(args.delivery_id) and bool(args.verdict),
                    "resolve 需要提供 delivery_id 与 verdict（delivered/not_delivered/uncertain）",
                )
                if miss:
                    return miss
                result = await asyncio.to_thread(
                    service.resolve_delivery,
                    tenant_id, args.delivery_id, user_id,
                    DeliveryResolveInput(verdict=args.verdict, decision=args.decision, note=args.note),
                )
                return {
                    "success": True,
                    **_jsonable(result),
                    "message": "人工结论已记录（独立审计留痕，不覆盖机器记录）",
                }

            if args.action == "retry":
                miss = _require(bool(args.delivery_id), "retry 需要提供 delivery_id")
                if miss:
                    return miss
                result = await asyncio.to_thread(
                    service.retry_delivery,
                    tenant_id, args.delivery_id, user_id,
                    confirm=args.confirm,
                )
                return {
                    "success": True,
                    **_jsonable(result),
                    "high_risk": True,
                    "human_confirmation_note": (
                        "重试为真实发送副作用，本次执行默认已获用户显式确认（confirm=true）。"
                        "未知效果投递必须先 resolve 记录 confirmed_not_sent 才允许重试。"
                    ),
                    "message": f"投递 {args.delivery_id} 已建新 attempt 并派发（真实发送）",
                }

            # ---------- 手动触发 / 试发（高危）----------
            if args.action == "run":
                miss = _require(bool(args.automation_id), "run 需要提供 automation_id")
                if miss:
                    return miss
                result = await asyncio.to_thread(
                    service.manual_run,
                    tenant_id, args.automation_id, user_id,
                    request_id=str(uuid.uuid4()),
                )
                return {
                    "success": True,
                    **_jsonable(result),
                    "high_risk": True,
                    "human_confirmation_note": (
                        "手动触发为真实发送副作用（向目标群真实发送一轮），本次执行默认已获用户明确确认。"
                    ),
                    "message": (
                        f"手动运行已受理（run_id={result.get('run_id')}），设备侧领取后将真实发送，"
                        "进度可用 get_run_detail 查看"
                    ),
                }

            if args.action == "test_send":
                miss = _require(
                    bool(args.automation_id) and bool(args.group_binding_id) and args.block_position is not None,
                    "test_send 需要提供 automation_id、group_binding_id 与 block_position",
                )
                if miss:
                    return miss
                workbench = _get_workbench_service()
                result = await asyncio.to_thread(
                    workbench.test_send,
                    tenant_id, args.automation_id, user_id,
                    TestSendInput(
                        group_binding_id=args.group_binding_id,
                        block_position=args.block_position,
                    ),
                    request_id=str(uuid.uuid4()),
                )
                return {
                    "success": True,
                    **_jsonable(result),
                    "high_risk": True,
                    "human_confirmation_note": (
                        "试发与正式发送同样是真实发送副作用（消息会真实出现在目标群），"
                        "本次执行默认已获用户明确确认。"
                    ),
                    "message": (
                        f"试发已派发（block_position={args.block_position}，目标群绑定 {args.group_binding_id}），"
                        "结果可用 get_run_detail 查看试发 run"
                    ),
                }

            return {
                "success": False,
                "error": f"不支持的操作: {args.action}",
                "code": CODE_VALIDATION_FAILED,
            }
        except ValidationError as e:
            return _service_failure(e)
        except Exception as e:  # noqa: BLE001
            from src.weixin_marketing.service import WeixinMarketingError

            if isinstance(e, WeixinMarketingError):
                return _service_failure(e)
            return _unexpected_failure(self.name, e)
