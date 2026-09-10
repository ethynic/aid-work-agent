"""微信营销聊天工具真实 DB 集成测试（P3-B，R56）

prepare（创建/更新草稿）与 validate 是纯静态路径：断言对租户零 run、
零 invocation 行（无任何发送副作用），且草稿状态明确「未发布」。
复用本目录既有真实 PG 测试基建（conftest：tenant_id/bindings/adapter）。
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.unit

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.weixin.weixin_automation_tools import (
    WeixinAutomationPrepareTool,
    WeixinAutomationPublishTool,
)


def _future_iso(days: int = 7) -> str:
    """未来时刻（整秒、+00:00 后缀）——触发时间须在未来，硬编码日期会随时钟跨过而永久失败"""
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
    )


def _ctx(tenant_id, user_id="owner-1"):
    return ToolExecutionContext(user_id=user_id, tenant_id=tenant_id, session_id="s-chat")


def _zero_rows(tenant_id: str) -> tuple:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM desktop_automation_runs WHERE tenant_id = %s",
            (tenant_id,),
        )
        runs = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM local_tool_invocations WHERE tenant_id = %s",
            (tenant_id,),
        )
        invocations = cur.fetchone()["c"]
    return runs, invocations


async def test_prepare_create_and_validate_have_zero_send_side_effects(
    tenant_id, bindings, adapter, wx_config
):
    """prepare 创建 + 静态校验：真实库断言零 run / 零 invocation（R56 无发送副作用）"""
    from tests.unit.weixin_marketing.conftest import utcnow

    _, group_id = bindings
    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx(tenant_id)):
        result = await tool.execute(
            name="聊天创建草稿",
            group_binding_id=group_id,
            trigger={
                "type": "once",
                "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
                "timezone": "UTC",
            },
            blocks=[{"type": "text", "text_content": "真实库聊天工具内容"}],
        )

    assert result["success"] is True
    assert result["published"] is False
    assert result["status"] == "draft"
    assert result["automation_id"]
    assert result["validation"]["ok"] is True
    assert result["validation"]["next_fires"], "once 触发应有未来触发预览"

    runs, invocations = _zero_rows(tenant_id)
    assert runs == 0, "prepare 不得创建任何 run"
    assert invocations == 0, "prepare 不得创建任何 invocation（零设备下发）"


async def test_prepare_update_draft_still_zero_side_effects(
    tenant_id, bindings, adapter, wx_config, service
):
    """prepare 更新草稿（CAS）：同样零 run / 零 invocation"""
    from tests.unit.weixin_marketing.conftest import make_create_payload

    _, group_id = bindings
    detail = service.create_automation(
        tenant_id, "owner-1", make_create_payload(group_id)
    )
    automation = detail["automation"]
    start_at = _future_iso()

    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx(tenant_id)):
        result = await tool.execute(
            automation_id=str(automation["id"]),
            expected_version=automation["version"],
            name="聊天更新草稿",
            group_binding_id=group_id,
            trigger={
                "type": "interval",
                "start_at": start_at,
                "interval_seconds": 86400,
                "timezone": "UTC",
            },
            blocks=[{"type": "text", "text_content": "更新后的内容"}],
        )

    assert result["success"] is True
    assert result["published"] is False
    assert result["version"] == automation["version"] + 1

    runs, invocations = _zero_rows(tenant_id)
    assert runs == 0
    assert invocations == 0


async def test_prepare_wrong_owner_isolated(tenant_id, bindings, adapter, wx_config, service):
    """属主隔离：非属主身份 prepare 更新他人任务 → NOT_FOUND（泛化文案）且不落任何行"""
    from tests.unit.weixin_marketing.conftest import make_create_payload

    _, group_id = bindings
    detail = service.create_automation(
        tenant_id, "owner-1", make_create_payload(group_id)
    )
    automation = detail["automation"]
    start_at = _future_iso()

    tool = WeixinAutomationPrepareTool()
    with tool_execution_scope(_ctx(tenant_id, user_id="attacker")):
        result = await tool.execute(
            automation_id=str(automation["id"]),
            expected_version=automation["version"],
            name="越权更新",
            group_binding_id=group_id,
            trigger={
                "type": "once",
                "run_at": start_at,
                "timezone": "UTC",
            },
            blocks=[{"type": "text", "text_content": "越权内容"}],
        )

    assert result["success"] is False
    assert result["code"] == "NOT_FOUND"
    assert result["error"] == "资源不存在或无权访问"
    # 原任务未被改动（版本不变、名称不变）
    fresh = service.get_automation_detail(tenant_id, str(automation["id"]), "owner-1")
    assert fresh["automation"]["version"] == automation["version"]
    assert fresh["automation"]["name"] == "测试自动化"


async def test_publish_real_db_creates_subject_and_first_fire(
    tenant_id, bindings, adapter, wx_config, service
):
    """发布链路（真机门禁内）：publish 后 active + 首次触发时间来自校验预览，零 invocation"""
    from tests.unit.weixin_marketing.conftest import make_create_payload

    _, group_id = bindings
    start_at = _future_iso()
    detail = service.create_automation(
        tenant_id, "owner-1",
        make_create_payload(
            group_id,
            trigger={
                "type": "interval",
                "start_at": start_at,
                "interval_seconds": 86400,
                "timezone": "UTC",
            },
        ),
    )
    automation = detail["automation"]

    tool = WeixinAutomationPublishTool()
    with tool_execution_scope(_ctx(tenant_id)):
        result = await tool.execute(
            automation_id=str(automation["id"]),
            expected_version=automation["version"],
        )

    assert result["success"] is True
    assert datetime.fromisoformat(result["first_fire_at"]) == datetime.fromisoformat(start_at)
    assert result["revision_id"]
    fresh = service.get_automation_detail(tenant_id, str(automation["id"]), "owner-1")
    assert fresh["automation"]["status"] == "active"
    # 发布只建 subject/schedule，不派发 invocation（无人值守发送由调度域接管）
    _, invocations = _zero_rows(tenant_id)
    assert invocations == 0
