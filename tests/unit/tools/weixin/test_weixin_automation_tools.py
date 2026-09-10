"""微信营销聊天三工具单元测试（P3-B，R56）

在服务层边界 mock（patch.object WeixinMarketingService / WeixinWorkbenchService
方法），验证：
- 服务调用参数正确（身份只取 ToolExecutionContext，不接 argv 注入的 tenant/user）；
- InputModel 只接业务字段（extra=forbid：tenant_id/user_id 传入被拒）；
- prepare 无发送副作用（publish/manual_run/test_send 零调用）、明确「未发布」状态；
- publish 不做试发、返回首次触发时间与授权来源留痕；
- manage 动作映射与高危动作（run/test_send/retry）人工确认提示；
- 服务层错误码透传（CONFLICT / NOT_FOUND / VALIDATION_FAILED）。

真实 DB 的零副作用断言（零 run / 零 invocation 行）在
tests/unit/weixin_marketing/test_chat_tools_real_db.py（复用该目录测试基建）。
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.tools

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.weixin.weixin_automation_tools import (
    WeixinAutomationManageTool,
    WeixinAutomationPrepareTool,
    WeixinAutomationPublishTool,
)
from src.weixin_marketing.service import WeixinMarketingService


TRIGGER = {"type": "once", "run_at": "2026-09-09T09:00:00+00:00", "timezone": "UTC"}
BLOCKS = [
    {"type": "text", "text_content": "促销通知"},
    {"type": "link", "url": "https://example.com/promo"},
]


def _detail(automation_id="auto-1", status="draft", version=1):
    return {
        "automation": {
            "id": automation_id, "name": "促销群发", "status": status,
            "version": version, "draft_revision_id": "rev-1",
        },
        "revisions": [{"id": "rev-1", "status": "draft", "revision_no": 1}],
        "draft_trigger": TRIGGER,
        "draft_blocks": [
            {"position": 0, "kind": "text"},
            {"position": 1, "kind": "link"},
        ],
        "recent_runs": [],
    }


def _validation(ok=True):
    return {
        "ok": ok,
        "errors": [] if ok else [{"field": "trigger", "message": "触发配置非法"}],
        "warnings": [],
        "next_fires": ["2026-09-09T09:00:00+00:00"],
    }


@pytest.fixture()
def svc():
    """mock 服务层（三工具共用同一服务类，patch 类方法）"""
    with (
        patch.object(WeixinMarketingService, "create_automation", MagicMock(return_value=_detail())),
        patch.object(WeixinMarketingService, "update_draft", MagicMock(return_value=_detail(version=2))),
        patch.object(WeixinMarketingService, "validate_automation", MagicMock(return_value=_validation())),
        patch.object(WeixinMarketingService, "publish", MagicMock(return_value={
            "automation_id": "auto-1", "revision_id": "rev-1", "revision_no": 1,
            "authorization_epoch": 3, "version": 2,
        })),
        patch.object(WeixinMarketingService, "manual_run", MagicMock(return_value={
            "occurrence_id": "occ-1", "run_id": "run-1", "created": True,
        })),
        patch.object(WeixinMarketingService, "list_automations", MagicMock(return_value={
            "items": [{"id": "auto-1", "name": "促销群发", "status": "draft",
                       "version": 1, "updated_at": "2026-09-08 10:00:00+00:00"}],
            "total": 1, "page": 1, "page_size": 5,
        })),
        patch.object(WeixinMarketingService, "pause", MagicMock(return_value={
            "automation_id": "auto-1", "status": "paused", "version": 2,
            "authorization_epoch": 4, "cancelled_runs": 0,
        })),
        patch.object(WeixinMarketingService, "resolve_delivery", MagicMock(return_value={
            "delivery_id": "del-1", "verdict": "not_delivered",
            "decision": "confirmed_not_sent", "machine_state": "unknown",
            "machine_effect": "unknown",
        })),
        patch.object(WeixinMarketingService, "retry_delivery", MagicMock(return_value={
            "delivery_id": "del-1", "attempt_id": "att-2",
        })),
    ):
        yield WeixinMarketingService


def _ctx():
    return ToolExecutionContext(user_id="user-a", tenant_id="tenant_a", session_id="s1")


# ==================== prepare ====================


@pytest.mark.asyncio
async def test_prepare_create_uses_context_identity_and_returns_draft_state(svc):
    """创建模式：身份取自 ToolExecutionContext；返回草稿摘要 + 校验预览 + 未发布"""
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            name="促销群发", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is True
    assert result["published"] is False
    assert result["status"] == "draft"
    assert "未发布" in result["status_hint"]
    assert result["automation_id"] == "auto-1"
    assert result["validation"]["ok"] is True
    assert result["validation"]["next_fires"] == ["2026-09-09T09:00:00+00:00"]
    # 服务调用身份来自可信上下文
    call = svc.create_automation.call_args
    assert call.args[:2] == ("tenant_a", "user-a")
    payload = call.args[2]
    assert payload.name == "促销群发"
    assert payload.group_binding_id == "11111111-1111-1111-1111-111111111111"
    # 创建后做了只读校验
    svc.validate_automation.assert_called_once_with("tenant_a", "auto-1", "user-a")


@pytest.mark.asyncio
async def test_prepare_has_no_send_side_effects(svc):
    """prepare 无发送副作用：publish/manual_run/test_send 全程零调用"""
    from src.weixin_marketing.workbench import WeixinWorkbenchService

    tool = WeixinAutomationPrepareTool()
    with (
        tool_execution_scope(_ctx()),
        patch.object(WeixinWorkbenchService, "test_send", MagicMock()) as test_send,
    ):
        result = await tool.execute(
            name="促销群发", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is True
    svc.publish.assert_not_called()
    svc.manual_run.assert_not_called()
    test_send.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_update_mode_requires_expected_version(svc):
    """更新模式缺 expected_version：结构化拒绝，不触达服务层"""
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            automation_id="auto-1",
            name="促销群发", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is False
    assert result["code"] == "VALIDATION_FAILED"
    svc.update_draft.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_update_calls_update_draft_with_version(svc):
    """更新模式：update_draft 收到 expected_version 与完整业务字段"""
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            automation_id="auto-1", expected_version=1,
            name="促销群发v2", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is True
    call = svc.update_draft.call_args
    assert call.args[:3] == ("tenant_a", "auto-1", "user-a")
    assert call.args[3].expected_version == 1
    assert call.args[3].name == "促销群发v2"
    svc.create_automation.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_validation_issues_returned_as_data(svc):
    """校验未通过不视为工具失败：作为数据返回供 LLM 修正草稿"""
    svc.validate_automation.return_value = _validation(ok=False)
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            name="促销群发", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is True
    assert result["validation"]["ok"] is False
    assert result["validation"]["errors"] == [{"field": "trigger", "message": "触发配置非法"}]


@pytest.mark.asyncio
async def test_prepare_fails_closed_without_context(svc):
    """身份缺失（无 user/租户）fail-closed：拒绝执行且不触达服务层"""
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(ToolExecutionContext(session_id="s1")):
        result = await tool.execute(
            name="促销群发", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is False
    assert "租户上下文缺失" in result["error"] or "未登录" in result["error"]
    svc.create_automation.assert_not_called()


def test_input_models_reject_identity_fields():
    """R56：InputModel 只接业务字段——argv 携带 tenant_id/user_id 被参数校验拒绝"""
    biz = {
        "name": "促销群发", "group_binding_id": "11111111-1111-1111-1111-111111111111",
        "trigger": TRIGGER, "blocks": BLOCKS,
    }
    prepare = WeixinAutomationPrepareTool()
    assert prepare.validate_parameters(**biz) is True
    assert prepare.validate_parameters(tenant_id="evil-tenant", **biz) is False
    assert prepare.validate_parameters(user_id="evil-user", **biz) is False

    publish = WeixinAutomationPublishTool()
    assert publish.validate_parameters(automation_id="auto-1", expected_version=1) is True
    assert publish.validate_parameters(
        automation_id="auto-1", expected_version=1, tenant_id="evil-tenant"
    ) is False

    manage = WeixinAutomationManageTool()
    assert manage.validate_parameters(action="list") is True
    assert manage.validate_parameters(action="list", user_id="evil-user") is False


# ==================== publish ====================


@pytest.mark.asyncio
async def test_publish_returns_result_with_first_fire_and_no_test_send(svc):
    """发布：CAS 参数透传、授权来源留痕 chat、返回首次触发时间；不做试发"""
    from src.weixin_marketing.workbench import WeixinWorkbenchService

    tool = WeixinAutomationPublishTool()
    with (
        tool_execution_scope(_ctx()),
        patch.object(WeixinWorkbenchService, "test_send", MagicMock()) as test_send,
    ):
        result = await tool.execute(automation_id="auto-1", expected_version=1)

    assert result["success"] is True
    assert result["revision_id"] == "rev-1"
    assert result["first_fire_at"] == "2026-09-09T09:00:00+00:00"
    assert "真实生效" in result["message"]
    call = svc.publish.call_args
    assert call.args[:3] == ("tenant_a", "auto-1", "user-a")
    payload = call.args[3]
    assert payload.expected_version == 1
    assert payload.authorization_source == "chat"  # 聊天通道授权留痕
    test_send.assert_not_called()


@pytest.mark.asyncio
async def test_publish_survives_preview_failure(svc):
    """发布成功后触发预览失败：结果保持 success，附带提示而非失败"""
    svc.validate_automation.side_effect = RuntimeError("preview boom")
    tool = WeixinAutomationPublishTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(automation_id="auto-1", expected_version=1)

    assert result["success"] is True
    assert result["first_fire_at"] is None
    assert result["next_fires"] == []


# ==================== manage ====================


@pytest.mark.asyncio
async def test_manage_list_maps_filters(svc):
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="list", keyword="促销", status="draft")

    assert result["success"] is True
    assert result["total"] == 1
    assert result["items"][0]["automation_id"] == "auto-1"
    call = svc.list_automations.call_args
    assert call.args == ("tenant_a", "user-a")
    assert call.kwargs["keyword"] == "促销"
    assert call.kwargs["status"] == "draft"


@pytest.mark.asyncio
async def test_manage_pause_maps_versioned_action(svc):
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            action="pause", automation_id="auto-1", expected_version=1, reason="节假日暂停",
        )

    assert result["success"] is True
    call = svc.pause.call_args
    assert call.args[:3] == ("tenant_a", "auto-1", "user-a")
    assert call.args[3].expected_version == 1
    assert call.args[3].reason == "节假日暂停"


@pytest.mark.asyncio
async def test_manage_pause_requires_version_and_target(svc):
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="pause", automation_id="auto-1")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_FAILED"
    svc.pause.assert_not_called()


@pytest.mark.asyncio
async def test_manage_run_is_high_risk_with_confirmation_note(svc):
    """run 高危动作：真实发送提示 + request_id 幂等键 + run_id 返回"""
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="run", automation_id="auto-1")

    assert result["success"] is True
    assert result["high_risk"] is True
    assert "真实发送" in result["human_confirmation_note"]
    assert result["run_id"] == "run-1"
    call = svc.manual_run.call_args
    assert call.args[:3] == ("tenant_a", "auto-1", "user-a")
    assert call.kwargs["request_id"]  # 幂等触发键由工具侧生成


@pytest.mark.asyncio
async def test_manage_test_send_uses_workbench_and_is_high_risk(svc):
    from src.weixin_marketing.workbench import WeixinWorkbenchService

    tool = WeixinAutomationManageTool()
    with (
        tool_execution_scope(_ctx()),
        patch.object(WeixinWorkbenchService, "test_send", MagicMock(return_value={
            "automation_id": "auto-1", "run_id": "run-t", "delivery_id": "del-t",
            "block_position": 1,
        })) as test_send,
    ):
        result = await tool.execute(
            action="test_send", automation_id="auto-1",
            group_binding_id="11111111-1111-1111-1111-111111111111", block_position=1,
        )

    assert result["success"] is True
    assert result["high_risk"] is True
    # 试发与正式发均为真实副作用：结果中明确提示
    assert "真实发送" in result["human_confirmation_note"]
    call = test_send.call_args
    assert call.args[:3] == ("tenant_a", "auto-1", "user-a")
    assert call.args[3].block_position == 1
    assert call.args[3].group_binding_id == "11111111-1111-1111-1111-111111111111"
    assert call.kwargs["request_id"]


@pytest.mark.asyncio
async def test_manage_retry_is_high_risk_and_passes_confirm(svc):
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="retry", delivery_id="del-1", confirm=True)

    assert result["success"] is True
    assert result["high_risk"] is True
    assert "confirm" in result["human_confirmation_note"]
    call = svc.retry_delivery.call_args
    assert call.args[:3] == ("tenant_a", "del-1", "user-a")
    assert call.kwargs["confirm"] is True


@pytest.mark.asyncio
async def test_manage_resolve_maps_delivery_resolve_input(svc):
    """resolve：verdict/decision/note 透传服务层（人工结论 + 授权决定语义）"""
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            action="resolve", delivery_id="del-1", verdict="not_delivered",
            decision="confirmed_not_sent", note="已在群里人工核对，未发送",
        )

    assert result["success"] is True
    call = svc.resolve_delivery.call_args
    assert call.args[:3] == ("tenant_a", "del-1", "user-a")
    payload = call.args[3]
    assert payload.verdict == "not_delivered"
    assert payload.decision == "confirmed_not_sent"
    assert payload.note == "已在群里人工核对，未发送"


@pytest.mark.asyncio
async def test_manage_resolve_requires_verdict(svc):
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="resolve", delivery_id="del-1")

    assert result["success"] is False
    assert result["code"] == "VALIDATION_FAILED"
    svc.resolve_delivery.assert_not_called()


@pytest.mark.asyncio
async def test_manage_unknown_action_rejected(svc):
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="explode")

    assert result["success"] is False
    assert "不支持的操作" in result["error"]


# ==================== 错误码透传 ====================


@pytest.mark.asyncio
async def test_publish_conflict_error_passthrough(svc):
    from src.weixin_marketing.service import ConflictError

    svc.publish.side_effect = ConflictError("版本冲突：期望 1，实际 2")
    tool = WeixinAutomationPublishTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(automation_id="auto-1", expected_version=1)

    assert result["success"] is False
    assert result["code"] == "CONFLICT"
    assert "版本冲突" in result["error"]


@pytest.mark.asyncio
async def test_not_found_error_mapped_generic(svc):
    """NotFoundError 泛化文案（不区分存在性），服务消息进 debug"""
    from src.weixin_marketing.service import NotFoundError

    svc.pause.side_effect = NotFoundError("自动化任务不存在")
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="pause", automation_id="other", expected_version=1)

    assert result["success"] is False
    assert result["code"] == "NOT_FOUND"
    assert result["error"] == "资源不存在或无权访问"
    assert "自动化任务不存在" in result["debug"]


@pytest.mark.asyncio
async def test_retry_evidence_required_code_passthrough(svc):
    from src.weixin_marketing.service import RetryEvidenceRequiredError

    svc.retry_delivery.side_effect = RetryEvidenceRequiredError(
        "未知效果重试需先 resolve 记录 confirmed_not_sent"
    )
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="retry", delivery_id="del-1", confirm=True)

    assert result["success"] is False
    assert result["code"] == "RETRY_EVIDENCE_REQUIRED"


@pytest.mark.asyncio
async def test_service_validation_error_code_passthrough(svc):
    from src.weixin_marketing.service import WeixinValidationError

    svc.create_automation.side_effect = WeixinValidationError("正文长度超过上限")
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(
            name="促销群发", group_binding_id="11111111-1111-1111-1111-111111111111",
            trigger=TRIGGER, blocks=BLOCKS,
        )

    assert result["success"] is False
    assert result["code"] == "VALIDATION_FAILED"
    assert "正文长度超过上限" in result["error"]


@pytest.mark.asyncio
async def test_unexpected_error_does_not_leak_internals(svc):
    """非服务层异常：只回通用文案，内部异常细节不进会话"""
    svc.list_automations.side_effect = RuntimeError("psycopg2 password=secret-leak")
    tool = WeixinAutomationManageTool()
    with tool_execution_scope(_ctx()):
        result = await tool.execute(action="list")

    assert result["success"] is False
    assert result["code"] == "INTERNAL_ERROR"
    assert result["error"] == "操作失败，请稍后重试"
    assert "secret-leak" not in str(result)
