"""渠道客户信息工具单元测试

验证：
- 工具定义（name / display_name / category / catalog=True）
- 微信客服渠道：读 kf context 的 channel_user_info（昵称/头像/性别/unionid/external_userid）
- 归属员工解析（assignee_name / assignee_phone，供外部系统委托登录）
- 渠道用户信息缺失（channel_user_info 为空/缺字段）不报错，字段回退空串
- 非微信客服渠道回退：ToolExecutionContext + users 表
- 非渠道会话（无 ToolExecutionContext / 无 channel / 无 user_id）返回友好失败
"""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.unit]


def _make_kf_ctx(**overrides):
    ctx = {
        "open_kfid": "kfAAA",
        "external_userid": "wm_ext_user_1",
        "kf_config": {"name": "售前客服", "tenant_user_id": "emp_001"},
        "session_id": "sess_1",
        "tenant_id": "tenant_001",
        "user_id": "tenant_user_1",
        "channel_user_info": {
            "nickname": "小团长",
            "avatar": "https://wx.qlogo.cn/a.png",
            "gender": 1,
            "wx_unionid": "o_union_1",
        },
    }
    ctx.update(overrides)
    return ctx


def _patch_user_db(user):
    return patch("src.db.models.UserDB.get_by_id", MagicMock(return_value=user))


class TestGetChannelUserInfoToolDefinition:
    def test_tool_properties(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        tool = GetChannelUserInfoTool()
        assert tool.name == "get_channel_user_info"
        assert tool.display_name == "渠道客户信息"
        assert tool.category == "channel"
        assert tool.catalog is True

    def test_no_input_params(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        tool = GetChannelUserInfoTool()
        schema = tool.to_tool_definition()["input_schema"]
        assert schema.get("type") == "object"
        assert not schema.get("properties")


class TestWecomKfPath:
    @pytest.mark.asyncio
    async def test_returns_kf_context_info(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_kf_ctx(),
        ), _patch_user_db({"nickname": "李老师", "username": "lilaoshi", "phone": "13800138000"}):
            result = await GetChannelUserInfoTool().execute()

        assert result["success"] is True
        assert result["source"] == "wecom_kf"
        assert result["channel"] == "wecom_kf"
        assert result["external_userid"] == "wm_ext_user_1"
        assert result["nickname"] == "小团长"
        assert result["avatar"] == "https://wx.qlogo.cn/a.png"
        assert result["gender"] == 1
        assert result["gender_label"] == "男"
        assert result["wx_unionid"] == "o_union_1"
        assert result["session_id"] == "sess_1"
        assert result["assignee_name"] == "李老师"
        assert result["assignee_phone"] == "13800138000"

    @pytest.mark.asyncio
    async def test_missing_channel_user_info_degrades_to_empty(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_kf_ctx(channel_user_info=None),
        ), _patch_user_db(None):
            result = await GetChannelUserInfoTool().execute()

        assert result["success"] is True
        assert result["nickname"] == ""
        assert result["avatar"] == ""
        assert result["gender"] == 0
        assert result["gender_label"] == "未知"
        assert result["wx_unionid"] == ""

    @pytest.mark.asyncio
    async def test_unknown_gender_label(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_kf_ctx(channel_user_info={"nickname": "匿名", "gender": 2}),
        ), _patch_user_db(None):
            result = await GetChannelUserInfoTool().execute()

        assert result["gender_label"] == "女"

    @pytest.mark.asyncio
    async def test_assignee_phone_none_when_not_configured(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_kf_ctx(kf_config={"name": "售前客服"}),
        ), _patch_user_db(None):
            result = await GetChannelUserInfoTool().execute()

        assert result["assignee_name"] is None
        assert result["assignee_phone"] is None

    @pytest.mark.asyncio
    async def test_empty_phone_normalized_to_none(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=_make_kf_ctx(),
        ), _patch_user_db({"nickname": "李老师", "phone": ""}):
            result = await GetChannelUserInfoTool().execute()

        assert result["assignee_phone"] is None


class TestUsersDbFallbackPath:
    @pytest.mark.asyncio
    async def test_fallback_to_users_db_for_other_channels(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool
        from src.tools.context import ToolExecutionContext

        tool_ctx = ToolExecutionContext(
            tenant_id="tenant_001",
            user_id="u_1",
            session_id="sess_9",
            channel="dingtalk",
        )
        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ), patch(
            "src.tools.context.current_tool_execution_context",
            return_value=tool_ctx,
        ), _patch_user_db({"nickname": "钉钉用户", "avatar_url": "https://a.b/c.png", "wx_unionid": ""}):
            result = await GetChannelUserInfoTool().execute()

        assert result["success"] is True
        assert result["source"] == "users_db"
        assert result["channel"] == "dingtalk"
        assert result["nickname"] == "钉钉用户"
        assert result["avatar"] == "https://a.b/c.png"
        assert result["external_userid"] == ""
        assert result["wx_unionid"] == ""

    @pytest.mark.asyncio
    async def test_reject_non_channel_session(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool
        from src.tools.context import ToolExecutionContext

        tool_ctx = ToolExecutionContext(tenant_id="tenant_001", user_id="u_1")
        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ), patch(
            "src.tools.context.current_tool_execution_context",
            return_value=tool_ctx,
        ):
            result = await GetChannelUserInfoTool().execute()

        assert result["success"] is False
        assert "渠道" in result["error"]

    @pytest.mark.asyncio
    async def test_reject_when_no_context_at_all(self):
        from src.tools.channel.channel_user_info import GetChannelUserInfoTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ), patch(
            "src.tools.context.current_tool_execution_context",
            return_value=None,
        ):
            result = await GetChannelUserInfoTool().execute()

        assert result["success"] is False
