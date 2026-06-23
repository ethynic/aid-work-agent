"""转人工客服工具单元测试

验证：
- reason 必填
- 渠道隔离（非微信客服渠道返回友好失败提示）
- 微信客服渠道内：servicer 列表为空、allow_agent_transfer=false、成功转接、metadata 字段
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.tools, pytest.mark.unit]


class TestTransferToHumanToolDefinition:
    def test_tool_properties(self):
        from src.tools.transfer_to_human import TransferToHumanTool
        tool = TransferToHumanTool()
        assert tool.name == "transfer_to_human"
        assert tool.display_name == "转人工客服"
        assert tool.category == "customer_service"
        # usage_guide 必须为空（渠道由 execute 段兜底，提示词约束无效）
        assert tool.usage_guide == ""
        # description 不能再出现"仅微信客服"等渠道约束字样
        assert "仅在企业微信客服" not in tool.description
        assert "仅微信客服" not in tool.description

    def test_input_model_reason_required(self):
        from src.tools.transfer_to_human import TransferToHumanInput
        import pytest as _pytest

        # 合法：带 reason
        inp = TransferToHumanInput(reason="用户投诉")
        assert inp.reason == "用户投诉"

        # 非法：缺 reason
        with _pytest.raises(Exception):
            TransferToHumanInput()

    def test_tool_definition_schema(self):
        from src.tools.transfer_to_human import TransferToHumanTool
        tool = TransferToHumanTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "transfer_to_human"
        assert "input_schema" in defn
        assert "reason" in defn["input_schema"].get("required", [])


class TestTransferToHumanExecute:
    """execute 路径测试"""

    @pytest.mark.asyncio
    async def test_non_wecom_channel_returns_friendly_failure(self):
        """非微信客服渠道调用，返回友好失败提示"""
        from src.tools.transfer_to_human import TransferToHumanTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ):
            tool = TransferToHumanTool()
            result = await tool.execute(reason="用户投诉")

        assert result["success"] is False
        assert "当前渠道未提供人工客服" in result["error"]
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_missing_reason_returns_failure(self):
        """缺 reason 时返回失败（保险，正常路径下 schema 会先校验）"""
        from src.tools.transfer_to_human import TransferToHumanTool

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=None,
        ):
            tool = TransferToHumanTool()
            result = await tool.execute()

        assert result["success"] is False
        assert "reason" in result["error"]

    @pytest.mark.asyncio
    async def test_servicer_list_empty_returns_failure(self):
        """微信渠道但 servicer 列表为空"""
        from src.tools.transfer_to_human import TransferToHumanTool

        ctx = {
            "adapter": MagicMock(),
            "open_kfid": "kfAAA",
            "external_userid": "user1",
            "kf_config": {"servicer_userid_list": []},
            "session_id": "sess1",
        }
        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=ctx,
        ):
            tool = TransferToHumanTool()
            result = await tool.execute(reason="out_of_scope")

        assert result["success"] is False
        assert "未配置人工客服人员" in result["error"]

    @pytest.mark.asyncio
    async def test_allow_agent_transfer_false_returns_failure(self):
        """allow_agent_transfer=false 时拒绝转人工"""
        from src.tools.transfer_to_human import TransferToHumanTool

        ctx = {
            "adapter": MagicMock(),
            "open_kfid": "kfAAA",
            "external_userid": "user1",
            "kf_config": {
                "servicer_userid_list": ["zhangsan"],
                "allow_agent_transfer": False,
            },
            "session_id": "sess1",
        }
        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=ctx,
        ):
            tool = TransferToHumanTool()
            result = await tool.execute(reason="user_request")

        assert result["success"] is False
        assert "管理员已禁用" in result["error"]

    @pytest.mark.asyncio
    async def test_success_path_updates_metadata_with_source(self):
        """成功转接：调用 adapter.transfer_to_human + 更新 metadata（含 transfer_source=agent）"""
        from src.tools.transfer_to_human import TransferToHumanTool

        adapter = MagicMock()
        adapter.transfer_to_human = AsyncMock(return_value=True)

        ctx = {
            "adapter": adapter,
            "open_kfid": "kfAAA",
            "external_userid": "user1",
            "kf_config": {
                "servicer_userid_list": ["zhangsan", "lisi"],
                "allow_agent_transfer": True,
            },
            "session_id": "sess1",
        }

        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=ctx,
        ), patch(
            "src.channels.session.channel_session_manager.update_session"
        ) as mock_update:
            tool = TransferToHumanTool()
            result = await tool.execute(reason="complaint")

        assert result["success"] is True
        assert "zhangsan" in result["message"]

        adapter.transfer_to_human.assert_awaited_once_with(
            open_kfid="kfAAA",
            external_userid="user1",
            servicer_userid="zhangsan",
        )

        mock_update.assert_called_once()
        kwargs = mock_update.call_args.kwargs
        assert kwargs["session_id"] == "sess1"
        metadata = kwargs["metadata"]
        assert metadata["service_state"] == 3
        assert metadata["transferred_to"] == "zhangsan"
        assert metadata["transfer_reason"] == "complaint"
        assert metadata["transfer_source"] == "agent"

    @pytest.mark.asyncio
    async def test_success_default_allow_agent_transfer(self):
        """kf_config 不配置 allow_agent_transfer 时默认允许"""
        from src.tools.transfer_to_human import TransferToHumanTool

        adapter = MagicMock()
        adapter.transfer_to_human = AsyncMock(return_value=True)

        ctx = {
            "adapter": adapter,
            "open_kfid": "kfAAA",
            "external_userid": "user1",
            "kf_config": {"servicer_userid_list": ["zhangsan"]},
            "session_id": "sess1",
        }
        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=ctx,
        ), patch(
            "src.channels.session.channel_session_manager.update_session"
        ):
            tool = TransferToHumanTool()
            result = await tool.execute(reason="user_request")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_adapter_call_failure_returns_failure(self):
        """adapter.transfer_to_human 返回 False 时失败"""
        from src.tools.transfer_to_human import TransferToHumanTool

        adapter = MagicMock()
        adapter.transfer_to_human = AsyncMock(return_value=False)

        ctx = {
            "adapter": adapter,
            "open_kfid": "kfAAA",
            "external_userid": "user1",
            "kf_config": {"servicer_userid_list": ["zhangsan"]},
            "session_id": "sess1",
        }
        with patch(
            "src.channels.wecom_kf.context.get_kf_context",
            return_value=ctx,
        ), patch(
            "src.channels.session.channel_session_manager.update_session"
        ):
            tool = TransferToHumanTool()
            result = await tool.execute(reason="user_request")

        assert result["success"] is False
        assert "转接失败" in result["error"]
