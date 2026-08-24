"""客户留资工具单元测试

验证：
- 工具定义（name / display_name / category / catalog=False）
- 输入模型（contact_method 必填、phone 可选）
- 渠道隔离（非 wecom_kf 渠道返回友好失败提示）
- 手机号校验（缺省 / 格式异常）
- 成功留资（写线索 + 更新会话状态机）
- 已留资客户再次明确要求留资（加微信/留手机号）-> 视为新需求，正常落库并通知（注明上次留资时间）
- 员工二维码（qr 下发 ImageRef；未配置 → 降级仅引导留手机号）
"""
import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.tools, pytest.mark.unit]

# LeadCaptureDB.create 返回值 sentinel：缺省走成功默认，传 None 表示写入失败
_NO_LEAD = object()


@pytest.fixture(autouse=True)
def _restore_event_loop_after_async():
    """pytest-asyncio 跑完 async 测试后会 set_event_loop(None) 并关闭循环，
    导致后续同步集成测试（用 asyncio.get_event_loop().run_until_complete）在
    MainThread 拿不到事件循环而抛 RuntimeError。这里在每测后重建一个循环兜底。"""
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
        },
        "session_id": "sess_1",
        "tenant_id": "tenant_001",
        "user_id": "tenant_user_1",
        "lead_capture": None,
    }
    ctx.update(overrides)
    return ctx


@contextmanager
def _patch_execute(ctx, *, session_metadata=None, lead_db_return=_NO_LEAD, email_cred=None):
    """构造 record_lead_capture 执行的 mock 环境，yield (tool, mocks)。

    必须用 with 包裹调用方：patch 在 with 退出时才还原。
    """
    from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

    session = {"metadata": session_metadata or {}}
    lead_record = lead_db_return if lead_db_return is not _NO_LEAD else {
        "lead_id": "lead_lc_abcdef123456",
        "tenant_id": ctx["tenant_id"],
        "phone": "13800138000",
    }
    mocks = {
        "get_session_by_id": MagicMock(return_value=session),
        "update_session": MagicMock(),
        "lead_db_create": MagicMock(return_value=lead_record),
        "get_user": MagicMock(return_value={"nickname": "李老师", "username": "lilaoshi"}),
        "get_email_cred": MagicMock(return_value=email_cred),
        "notify_send": AsyncMock(return_value=True),
    }
    with patch(
        "src.channels.wecom_kf.context.get_kf_context",
        return_value=ctx,
    ), patch(
        "src.channels.session.channel_session_manager.get_session_by_id",
        mocks["get_session_by_id"],
    ), patch(
        "src.channels.session.channel_session_manager.update_session",
        mocks["update_session"],
    ), patch(
        "src.saas.db.lead_capture_db.LeadCaptureDB.create",
        mocks["lead_db_create"],
    ), patch(
        "src.db.models.UserDB.get_by_id",
        mocks["get_user"],
    ), patch(
        "src.db.email_credential.EmailCredentialDB.get_by_user",
        mocks["get_email_cred"],
    ), patch(
        "src.services.notification_service.notification_service.send",
        mocks["notify_send"],
    ):
        yield RecordLeadCaptureTool(), mocks


class TestRecordLeadCaptureToolDefinition:
    def test_tool_properties(self):
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        tool = RecordLeadCaptureTool()
        assert tool.name == "record_lead_capture"
        assert tool.display_name == "客户留资"
        assert tool.category == "lead_capture"
        # Phase 2 随 pre-sales 智能体上线放开 catalog；渠道隔离靠 execute 内 get_kf_context 兜底
        assert tool.catalog is True

    def test_input_model_contact_method_required(self):
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureInput

        inp = RecordLeadCaptureInput(contact_method="phone")
        assert inp.contact_method == "phone"
        assert inp.phone is None

        with pytest.raises(Exception):
            RecordLeadCaptureInput()

    def test_tool_definition_schema(self):
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        tool = RecordLeadCaptureTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "record_lead_capture"
        assert "input_schema" in defn
        required = defn["input_schema"].get("required", [])
        assert "contact_method" in required
        # description 明确注明重复留资与降级语义
        assert "已留资过" in tool.description
        assert "上次留资时间" in tool.description
        assert "不要重复引导客户留资" in tool.description

    def test_tool_in_catalog_registry(self):
        """catalog=True：工具出现在 discover_tool_classes 发现的目录中（Phase 2 放开）。"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool
        from src.tools.base import _CATALOG
        from src.tools.registry import discover_tool_classes

        discover_tool_classes()
        names = {cls.name for cls in _CATALOG.values()}
        assert RecordLeadCaptureTool.name in names


class TestRecordLeadCaptureExecute:
    @pytest.mark.asyncio
    async def test_non_wecom_channel_returns_friendly_failure(self):
        """非微信客服渠道调用，返回友好失败提示"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ):
            tool = RecordLeadCaptureTool()
            result = await tool.execute(contact_method="phone", phone="13800138000")

        assert result["success"] is False
        assert "不支持客户留资" in result["error"]
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_invalid_contact_method_returns_failure(self):
        """contact_method 非法值返回失败"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_ctx(),
        ):
            tool = RecordLeadCaptureTool()
            result = await tool.execute(contact_method="email")

        assert result["success"] is False
        assert "phone 或 qr" in result["error"]

    @pytest.mark.asyncio
    async def test_phone_missing_returns_failure(self):
        """contact_method=phone 但缺手机号 → 失败并引导重新确认"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_ctx(),
        ):
            tool = RecordLeadCaptureTool()
            result = await tool.execute(contact_method="phone", phone="")

        assert result["success"] is False
        assert "确认手机号" in result["error"]

    @pytest.mark.asyncio
    async def test_phone_bad_format_returns_failure(self):
        """手机号格式异常 → 失败"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_ctx(),
        ):
            tool = RecordLeadCaptureTool()
            result = await tool.execute(contact_method="phone", phone="12345")

        assert result["success"] is False
        assert "格式不正确" in result["error"]

    @pytest.mark.asyncio
    async def test_success_phone_writes_lead_and_updates_state(self):
        """成功留资（phone）：写入线索 + 更新会话状态机"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(
                contact_method="phone",
                phone="13800138000",
                contact_name="张三",
                demand_summary="咨询企业版套餐价格",
            )

        assert result["success"] is True
        assert "客服会尽快联系" in result["message"]

        # 线索写入参数
        create_args = mocks["lead_db_create"].call_args.kwargs
        assert create_args["tenant_id"] == "tenant_001"
        assert create_args["user_id"] == "tenant_user_1"
        assert create_args["customer_user_id"] == "wx_ext_user_1"
        assert create_args["channel_chat_id"] == "kfAAA"
        assert create_args["kf_account_name"] == "售前客服"
        assert create_args["contact_method"] == "phone"
        assert create_args["phone"] == "13800138000"
        assert create_args["assigned_to"] == "emp_001"
        assert create_args["assignee_name"] == "李老师"
        assert create_args["session_id"] == "sess_1"
        assert create_args["lead_id"].startswith("lead_lc_")

        # 会话状态机更新
        mocks["update_session"].assert_called_once()
        kwargs = mocks["update_session"].call_args.kwargs
        assert kwargs["session_id"] == "sess_1"
        lead_state = kwargs["metadata"]["lead_capture"]
        assert lead_state["stage"] == "captured"
        assert lead_state["contact_method"] == "phone"
        assert lead_state["lead_id"] == create_args["lead_id"]

    @pytest.mark.asyncio
    async def test_success_phone_notifies_assigned_employee(self):
        """成功留资（phone）且归属员工配置了邮箱 → 发送邮件通知"""
        ctx = _make_ctx()
        email_cred = {"email_address": "emp@example.com"}
        with _patch_execute(ctx, email_cred=email_cred) as (tool, mocks):
            result = await tool.execute(contact_method="phone", phone="13800138000")

        assert result["success"] is True
        mocks["notify_send"].assert_awaited_once()
        msg = mocks["notify_send"].call_args.args[0]
        assert msg.channel.value == "email"
        assert msg.recipient == "emp@example.com"
        assert "留资" in msg.title
        # 手机号不进通知正文（隐私）
        assert "13800138000" not in str(msg.content)

    @pytest.mark.asyncio
    async def test_success_no_email_skips_notify(self):
        """归属员工未配置邮箱 → 留资成功但不发送通知"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(contact_method="phone", phone="13800138000")

        assert result["success"] is True
        mocks["notify_send"].assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_phone_recapture_creates_new_lead_with_history_note(self):
        """已留资客户再次留下手机号 -> 视为新跟进需求，正常落库并通知（注明上次留资时间）"""
        ctx = _make_ctx()
        email_cred = {"email_address": "emp@example.com"}
        with _patch_execute(
            ctx,
            session_metadata={
                "lead_capture": {
                    "stage": "captured",
                    "lead_id": "lead_lc_old123",
                    "contact_method": "phone",
                    "captured_at": "2026-08-21 10:00:00",
                }
            },
            email_cred=email_cred,
        ) as (tool, mocks):
            result = await tool.execute(contact_method="phone", phone="13800138000")

        assert result["success"] is True
        # 正常落库新线索 + 更新会话状态 + 通知员工
        mocks["lead_db_create"].assert_called_once()
        mocks["update_session"].assert_called_once()
        mocks["notify_send"].assert_called_once()
        # 通知正文注明上次留资时间，提示结合历史需求跟进
        content = mocks["notify_send"].call_args[0][0].content
        assert "2026-08-21 10:00:00" in content
        assert "结合历史需求跟进" in content

    @pytest.mark.asyncio
    async def test_recapture_qr_creates_new_lead_with_history_note(self):
        """已留资客户再次明确要求加微信 -> 视为新需求，正常落库 + 下发二维码 + 通知注明上次留资时间"""
        from src.core.image_asset import ImageRef

        ctx = _make_ctx(
            kf_config={
                "name": "售前客服",
                "tenant_user_id": "emp_001",
                "employee_qr_file_id": "file_employee_qr",
            }
        )
        ref = ImageRef(
            file_id="file_employee_qr",
            download_url="/api/files/file_employee_qr/download",
            display_name="员工二维码.png",
            source="user_upload",
        )
        mock_registry = MagicMock()
        mock_registry.get_ref_by_file_id = AsyncMock(return_value=ref)

        with _patch_execute(
            ctx,
            session_metadata={
                "lead_capture": {
                    "stage": "captured",
                    "lead_id": "lead_lc_old123",
                    "contact_method": "qr",
                    "captured_at": "2026-08-21 10:00:00",
                }
            },
            email_cred={"email_address": "emp@example.com"},
        ) as (tool, mocks):
            with patch("src.core.image_asset.get_image_registry", return_value=mock_registry):
                result = await tool.execute(contact_method="qr")

        assert result["success"] is True
        assert "员工微信" in result["message"]
        assert result["images"][0]["file_id"] == "file_employee_qr"
        assert result["images"][0]["source"] == "user_upload"
        # 客户明确要求加微信视为新需求：正常落库新线索 + 更新会话状态 + 通知员工（注明上次留资时间）
        mocks["lead_db_create"].assert_called_once()
        mocks["update_session"].assert_called_once()
        mocks["notify_send"].assert_called_once()
        content = mocks["notify_send"].call_args[0][0].content
        assert "2026-08-21 10:00:00" in content
        assert "结合历史需求跟进" in content

    @pytest.mark.asyncio
    async def test_qr_without_employee_qr_returns_downgrade(self):
        """contact_method=qr 但未配置员工二维码 → 无副作用降级（不写线索、不置状态机）"""
        ctx = _make_ctx(kf_config={"name": "售前客服", "tenant_user_id": "emp_001"})
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(contact_method="qr")

        assert result["success"] is False
        assert "未配置员工二维码" in result["error"]
        # 降级必须无副作用：不写线索、不更新会话状态机，允许后续引导留手机号
        mocks["lead_db_create"].assert_not_called()
        mocks["update_session"].assert_not_called()

    @pytest.mark.asyncio
    async def test_qr_success_returns_employee_qr_image(self):
        """contact_method=qr 且配置了员工二维码 → 返回 ImageRef 随回复下发"""
        from src.core.image_asset import ImageRef

        ctx = _make_ctx(
            kf_config={
                "name": "售前客服",
                "tenant_user_id": "emp_001",
                "employee_qr_file_id": "file_employee_qr",
            }
        )
        ref = ImageRef(
            file_id="file_employee_qr",
            download_url="/api/files/file_employee_qr/download",
            display_name="员工二维码.png",
            source="user_upload",
        )
        mock_registry = MagicMock()
        mock_registry.get_ref_by_file_id = AsyncMock(return_value=ref)

        with _patch_execute(ctx) as (tool, mocks):
            with patch("src.core.image_asset.get_image_registry", return_value=mock_registry):
                result = await tool.execute(contact_method="qr")

        assert result["success"] is True
        assert "添加下方员工微信" in result["message"]
        assert result["images"][0]["file_id"] == "file_employee_qr"
        assert result["images"][0]["source"] == "user_upload"

    @pytest.mark.asyncio
    async def test_lead_db_create_failure_returns_failure(self):
        """线索写入失败 → 返回失败"""
        ctx = _make_ctx()
        with _patch_execute(ctx, lead_db_return=None) as (tool, mocks):
            result = await tool.execute(contact_method="phone", phone="13800138000")

        assert result["success"] is False
        assert "保存失败" in result["error"]
