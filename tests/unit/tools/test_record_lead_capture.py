"""客户留资工具单元测试

验证：
- 工具定义（name / display_name / category / catalog=True）
- 输入模型（phone/landline/email 均可选，工具层校验至少一项）
- 渠道隔离（非 wecom_kf 渠道返回友好失败提示）
- 联系方式校验（全部缺省 / 手机号 / 固话 / 邮箱格式异常）
- 成功留资（写线索 + 更新会话状态机；contact_method 按优先级派生）
- 已留资客户再次明确要求留资 -> 视为新需求，正常落库并通知（注明上次留资时间）
- 客户索要顾问二维码不属于留资（qr 由独立工具 send_advisor_qr 承担）
"""
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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
        "kf_config_id": "cfg_kf_1",
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
def _patch_execute(ctx, *, session_metadata=None, lead_db_return=_NO_LEAD):
    """构造 record_lead_capture 执行的 mock 环境，yield (tool, mocks)。

    必须用 with 包裹调用方：patch 在 with 退出时才还原。
    """
    from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

    session = {"metadata": session_metadata or {}}
    lead_record = lead_db_return if lead_db_return is not _NO_LEAD else {
        "lead_id": "lead_lc_abcdef123456",
        "tenant_id": ctx["tenant_id"],
        "phone": "13800138000",
        "channel_chat_id": ctx["open_kfid"],
    }
    mocks = {
        "get_session_by_id": MagicMock(return_value=session),
        "update_session": MagicMock(),
        "lead_db_create": MagicMock(return_value=lead_record),
        "get_user": MagicMock(return_value={"nickname": "李老师", "username": "lilaoshi"}),
        "publish_event": MagicMock(return_value="notification_ref_1"),
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
        "src.notifications.lead_producer.publish_lead_captured_in_tx",
        mocks["publish_event"],
    ):
        yield RecordLeadCaptureTool(), mocks


class TestRecordLeadCaptureToolDefinition:
    def test_tool_properties(self):
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        tool = RecordLeadCaptureTool()
        assert tool.name == "record_lead_capture"
        assert tool.display_name == "客户留资"
        assert tool.category == "lead_capture"
        assert tool.catalog is True

    def test_input_model_all_optional(self):
        """联系方式字段全部可选：至少一项的约束在工具层校验（便于给出引导性错误）。"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureInput

        inp = RecordLeadCaptureInput()
        assert inp.phone is None
        assert inp.landline is None
        assert inp.email is None

        inp = RecordLeadCaptureInput(phone="13800138000", email="a@example.com")
        assert inp.phone == "13800138000"
        assert inp.email == "a@example.com"

    def test_tool_definition_schema(self):
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        tool = RecordLeadCaptureTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "record_lead_capture"
        assert "input_schema" in defn
        props = defn["input_schema"].get("properties", {})
        assert set(props) == {"phone", "landline", "email", "contact_name", "demand_summary"}
        # description 明确注明重复留资、降级语义与 qr 不算留资
        assert "已留资过" in tool.description
        assert "上次留资时间" in tool.description
        assert "不要重复引导客户留资" in tool.description
        assert "send_advisor_qr" in tool.description


class TestRecordLeadCaptureValidate:
    @pytest.mark.asyncio
    async def test_non_wecom_channel_returns_friendly_failure(self):
        """非微信客服渠道调用，返回友好失败提示"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ):
            tool = RecordLeadCaptureTool()
            result = await tool.execute(phone="13800138000")

        assert result["success"] is False
        assert "不支持客户留资" in result["error"]
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_all_contacts_missing_returns_failure(self):
        """phone/landline/email 全部缺省 → 失败并引导确认联系方式（渠道校验在前）"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with _patch_execute(_make_ctx()) as (tool, mocks):
            result = await tool.execute(phone="", landline="", email="")

        assert result["success"] is False
        assert "至少一项联系方式" in result["error"]
        mocks["lead_db_create"].assert_not_called()

    @pytest.mark.asyncio
    async def test_phone_bad_format_returns_failure(self):
        """手机号格式异常 → 失败"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with _patch_execute(_make_ctx()) as (tool, mocks):
            result = await tool.execute(phone="12345")

        assert result["success"] is False
        assert "手机号格式不正确" in result["error"]
        mocks["lead_db_create"].assert_not_called()

    @pytest.mark.asyncio
    async def test_landline_bad_format_returns_failure(self):
        """固定电话格式异常（缺区号）→ 失败"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with _patch_execute(_make_ctx()) as (tool, mocks):
            result = await tool.execute(landline="88888888")

        assert result["success"] is False
        assert "固定电话格式不正确" in result["error"]
        mocks["lead_db_create"].assert_not_called()

    @pytest.mark.asyncio
    async def test_email_bad_format_returns_failure(self):
        """邮箱格式异常（无域名点号）→ 失败"""
        from src.tools.lead_capture.record_lead_capture import RecordLeadCaptureTool

        with _patch_execute(_make_ctx()) as (tool, mocks):
            result = await tool.execute(email="abc@example")

        assert result["success"] is False
        assert "邮箱格式不正确" in result["error"]
        mocks["lead_db_create"].assert_not_called()


class TestRecordLeadCaptureExecute:
    @pytest.mark.asyncio
    async def test_success_phone_writes_lead_and_updates_state(self):
        """成功留资（phone）：写入线索 + 更新会话状态机"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(
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
    async def test_success_landline_derives_contact_method(self):
        """仅留固定电话：contact_method=landline，固话原值传 DB 层加密"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(landline="0571-88888888")

        assert result["success"] is True
        create_args = mocks["lead_db_create"].call_args.kwargs
        assert create_args["contact_method"] == "landline"
        assert create_args["landline"] == "0571-88888888"
        assert create_args["phone"] is None

    @pytest.mark.asyncio
    async def test_success_email_derives_contact_method(self):
        """仅留邮箱：contact_method=email"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(email="zhangsan@example.com")

        assert result["success"] is True
        create_args = mocks["lead_db_create"].call_args.kwargs
        assert create_args["contact_method"] == "email"
        assert create_args["email"] == "zhangsan@example.com"

    @pytest.mark.asyncio
    async def test_success_multi_contacts_phone_priority(self):
        """手机号+邮箱同时提供：contact_method 派生为 phone，两项都传 DB 层"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(
                phone="13800138000", email="zhangsan@example.com"
            )

        assert result["success"] is True
        create_args = mocks["lead_db_create"].call_args.kwargs
        assert create_args["contact_method"] == "phone"
        assert create_args["phone"] == "13800138000"
        assert create_args["email"] == "zhangsan@example.com"

        # 会话状态机同样记录派生类型
        lead_state = mocks["update_session"].call_args.kwargs["metadata"]["lead_capture"]
        assert lead_state["contact_method"] == "phone"

    @pytest.mark.asyncio
    async def test_success_phone_publishes_event(self):
        """成功留资（phone）→ 同事务发布 lead.captured 事件（通知由规则引擎展开）"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(phone="13800138000")

        assert result["success"] is True
        publisher = mocks["lead_db_create"].call_args.kwargs["event_publisher"]
        assert callable(publisher)
        publisher(MagicMock(), mocks["lead_db_create"].return_value)
        mocks["publish_event"].assert_called_once()
        args, kwargs = mocks["publish_event"].call_args
        assert args[1]["phone"] == "13800138000"
        # kf_config_id 从会话上下文透传（供规则 kf_config 层匹配）
        assert kwargs["kf_config_id"] == "cfg_kf_1"
        assert kwargs["previous_captured_at"] is None

    @pytest.mark.asyncio
    async def test_success_landline_publishes_other_contact(self):
        """固话留资：other_contact 明文随记录传给事件发布（规则引擎正文标签由模板渲染）"""
        ctx = _make_ctx()
        record = {
            "lead_id": "lead_lc_abcdef123456",
            "tenant_id": "tenant_001",
            "channel_chat_id": "kfAAA",
            "landline": "0571-88888888",
            "other_contact": [{"type": "landline", "value": "0571-88888888"}],
        }
        with _patch_execute(ctx, lead_db_return=record) as (tool, mocks):
            result = await tool.execute(landline="0571-88888888")

        assert result["success"] is True
        publisher = mocks["lead_db_create"].call_args.kwargs["event_publisher"]
        publisher(MagicMock(), record)
        args, _ = mocks["publish_event"].call_args
        assert args[1]["other_contact"][0]["value"] == "0571-88888888"

    @pytest.mark.asyncio
    async def test_success_email_publishes_other_contact(self):
        """邮箱留资：other_contact 明文随记录传给事件发布"""
        ctx = _make_ctx()
        record = {
            "lead_id": "lead_lc_abcdef123456",
            "tenant_id": "tenant_001",
            "channel_chat_id": "kfAAA",
            "email": "zhangsan@example.com",
            "other_contact": [{"type": "email", "value": "zhangsan@example.com"}],
        }
        with _patch_execute(ctx, lead_db_return=record) as (tool, mocks):
            result = await tool.execute(email="zhangsan@example.com")

        assert result["success"] is True
        publisher = mocks["lead_db_create"].call_args.kwargs["event_publisher"]
        publisher(MagicMock(), record)
        args, _ = mocks["publish_event"].call_args
        assert args[1]["other_contact"][0]["value"] == "zhangsan@example.com"

    @pytest.mark.asyncio
    async def test_recapture_creates_new_lead_with_history_note(self):
        """已留资客户再次留下联系方式 -> 视为新跟进需求，正常落库并发布事件（注明上次留资时间）"""
        ctx = _make_ctx()
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
        ) as (tool, mocks):
            result = await tool.execute(phone="13800138000")

        assert result["success"] is True
        # 正常落库新线索 + 更新会话状态
        mocks["lead_db_create"].assert_called_once()
        mocks["update_session"].assert_called_once()
        # 事件发布携带上次留资时间，由模板渲染「结合历史需求跟进」文案
        publisher = mocks["lead_db_create"].call_args.kwargs["event_publisher"]
        publisher(MagicMock(), mocks["lead_db_create"].return_value)
        _, kwargs = mocks["publish_event"].call_args
        assert kwargs["previous_captured_at"] == "2026-08-21 10:00:00"

    @pytest.mark.asyncio
    async def test_lead_db_create_failure_returns_failure(self):
        """线索写入失败 → 返回失败"""
        ctx = _make_ctx()
        with _patch_execute(ctx, lead_db_return=None) as (tool, mocks):
            result = await tool.execute(phone="13800138000")

        assert result["success"] is False
        assert "保存失败" in result["error"]

    @pytest.mark.asyncio
    async def test_success_phone_returns_assignee_phone(self):
        """归属员工配置了手机号 -> 成功结果返回 assignee_phone 供外部系统委托登录。"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            mocks["get_user"].return_value = {
                "nickname": "李老师",
                "username": "lilaoshi",
                "phone": "13800138001",
            }
            result = await tool.execute(phone="13800138000")

        assert result["success"] is True
        assert result["assignee_phone"] == "13800138001"

    @pytest.mark.asyncio
    async def test_success_assignee_phone_none_when_user_has_no_phone(self):
        """归属员工无手机号 -> assignee_phone 为 None（优雅降级，不阻塞留资）。"""
        ctx = _make_ctx()
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(phone="13800138000")

        assert result["success"] is True
        assert result["assignee_phone"] is None

    @pytest.mark.asyncio
    async def test_success_assignee_phone_none_when_no_owner(self):
        """客服账号未配置归属用户（tenant_user_id 为空）-> assignee_phone 为 None。"""
        ctx = _make_ctx(kf_config={"name": "售前客服"})
        with _patch_execute(ctx) as (tool, mocks):
            result = await tool.execute(phone="13800138000")

        assert result["success"] is True
        assert result["assignee_phone"] is None
