"""发送顾问二维码工具单元测试

验证：
- 工具定义（name / display_name / category / catalog=True / 无参数）
- 渠道隔离（非 wecom_kf 渠道返回友好失败提示）
- 未配置顾问二维码 → 降级引导留联系方式
- 二维码资产读取失败 → 降级引导留联系方式
- 成功返回 ImageRef 随回复下发，且不写线索、不发通知、不改会话状态机（发码 ≠ 留资）
"""
import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.tools, pytest.mark.unit]


@pytest.fixture(autouse=True)
def _restore_event_loop_after_async():
    yield
    import asyncio

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _make_ctx(**overrides):
    ctx = {
        "adapter": MagicMock(),
        "open_kfid": "kfAAA",
        "external_userid": "wx_ext_user_1",
        "kf_config": {
            "name": "售前客服",
            "tenant_user_id": "emp_001",
            "employee_qr_file_id": "file_employee_qr",
        },
        "session_id": "sess_1",
        "tenant_id": "tenant_001",
        "user_id": "tenant_user_1",
    }
    ctx.update(overrides)
    return ctx


def _make_qr_ref():
    from src.core.image_asset import ImageRef

    return ImageRef(
        file_id="file_employee_qr",
        download_url="/api/files/file_employee_qr/download",
        display_name="顾问二维码.png",
        source="user_upload",
    )


@contextmanager
def _patch_send_advisor_qr(ctx, *, qr_ref="default"):
    """构造 send_advisor_qr 执行的 mock 环境，yield (tool, mocks)。

    qr_ref：ImageRef 实例 / None（资产缺失）/ "default"（自动构造）。
    同时 patch 线索写入与通知发送，断言发码不触发留资副作用。
    """
    from src.tools.lead_capture.send_advisor_qr import SendAdvisorQrTool

    if qr_ref == "default":
        qr_ref = _make_qr_ref()
    mock_registry = MagicMock()
    mock_registry.get_ref_by_file_id = AsyncMock(return_value=qr_ref)
    mocks = {
        "registry": mock_registry,
        "lead_db_create": MagicMock(),
        "update_session": MagicMock(),
        "notify_send": AsyncMock(return_value=True),
    }
    with patch(
        "src.channels.wecom_kf.context.get_kf_context",
        return_value=ctx,
    ), patch(
        "src.core.image_asset.get_image_registry",
        return_value=mock_registry,
    ), patch(
        "src.saas.db.lead_capture_db.LeadCaptureDB.create",
        mocks["lead_db_create"],
    ), patch(
        "src.channels.session.channel_session_manager.update_session",
        mocks["update_session"],
    ), patch(
        "src.services.notification_service.notification_service.send",
        mocks["notify_send"],
    ):
        yield SendAdvisorQrTool(), mocks


class TestSendAdvisorQrToolDefinition:
    def test_tool_properties(self):
        from src.tools.lead_capture.send_advisor_qr import SendAdvisorQrTool

        tool = SendAdvisorQrTool()
        assert tool.name == "send_advisor_qr"
        assert tool.display_name == "发送顾问二维码"
        assert tool.category == "lead_capture"
        assert tool.catalog is True

    def test_tool_definition_schema(self):
        from src.tools.lead_capture.send_advisor_qr import SendAdvisorQrTool

        tool = SendAdvisorQrTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "send_advisor_qr"
        assert "input_schema" in defn
        # description 明确注明发码不属于留资与降级语义
        assert "不属于留资" in tool.description
        assert "record_lead_capture" in tool.description
        assert "手机号/固定电话/邮箱" in tool.description


class TestSendAdvisorQrExecute:
    @pytest.mark.asyncio
    async def test_non_wecom_channel_returns_friendly_failure(self):
        """非微信客服渠道调用，返回友好失败提示"""
        from src.tools.lead_capture.send_advisor_qr import SendAdvisorQrTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ):
            tool = SendAdvisorQrTool()
            result = await tool.execute()

        assert result["success"] is False
        assert "不支持发送顾问二维码" in result["error"]
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_without_employee_qr_returns_downgrade(self):
        """未配置顾问二维码 → 失败并引导留联系方式"""
        ctx = _make_ctx(kf_config={"name": "售前客服", "tenant_user_id": "emp_001"})
        with _patch_send_advisor_qr(ctx) as (tool, mocks):
            result = await tool.execute()

        assert result["success"] is False
        assert "未配置顾问二维码" in result["error"]
        assert "手机号/固定电话/邮箱" in result["hint"]

    @pytest.mark.asyncio
    async def test_registry_failure_returns_downgrade(self):
        """二维码资产读取失败（返回 None）→ 失败并引导留联系方式"""
        ctx = _make_ctx()
        with _patch_send_advisor_qr(ctx, qr_ref=None) as (tool, mocks):
            result = await tool.execute()

        assert result["success"] is False
        assert "读取失败" in result["error"]
        assert "手机号/固定电话/邮箱" in result["hint"]

    @pytest.mark.asyncio
    async def test_registry_exception_returns_downgrade(self):
        """二维码资产读取抛异常 → 失败并引导留联系方式（不向外抛）"""
        ctx = _make_ctx()
        with _patch_send_advisor_qr(ctx, qr_ref="default") as (tool, mocks):
            mocks["registry"].get_ref_by_file_id = AsyncMock(
                side_effect=RuntimeError("asset missing")
            )
            result = await tool.execute()

        assert result["success"] is False
        assert "读取失败" in result["error"]

    @pytest.mark.asyncio
    async def test_success_returns_employee_qr_image(self):
        """成功 → 返回顾问二维码 ImageRef 随回复下发"""
        ctx = _make_ctx()
        with _patch_send_advisor_qr(ctx) as (tool, mocks):
            result = await tool.execute()

        assert result["success"] is True
        assert "请勿再次复制或下载" in result["message"]
        assert result["images"][0]["file_id"] == "file_employee_qr"
        assert result["images"][0]["source"] == "user_upload"

    @pytest.mark.asyncio
    async def test_success_has_no_lead_capture_side_effects(self):
        """发码成功不写线索、不发通知、不改会话状态机（发码 ≠ 留资）"""
        ctx = _make_ctx()
        with _patch_send_advisor_qr(ctx) as (tool, mocks):
            result = await tool.execute()

        assert result["success"] is True
        mocks["lead_db_create"].assert_not_called()
        mocks["update_session"].assert_not_called()
        mocks["notify_send"].assert_not_called()
