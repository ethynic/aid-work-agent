"""
隐藏命令拆分测试：清空会话（物理删除） vs 新会话（软删除）
"""
from unittest.mock import patch

from src.core.hidden_commands import HIDDEN_COMMANDS, execute_hidden_command, is_hidden_command


class TestHiddenCommandsSplit:
    def test_command_registry(self):
        """两个命令都注册且精确匹配"""
        assert set(HIDDEN_COMMANDS.keys()) == {"清空会话", "新会话"}
        assert is_hidden_command("清空会话") is True
        assert is_hidden_command("新会话") is True
        assert is_hidden_command("清空会话 ") is True  # strip 后匹配
        assert is_hidden_command("普通消息") is False

    async def test_clear_session_calls_delete_messages(self):
        """清空会话 -> 物理删除 delete_messages"""
        with patch("src.channels.session.channel_session_manager") as mock_mgr:
            reply = await execute_hidden_command("清空会话", "sid", "tenant")
        mock_mgr.delete_messages.assert_called_once_with("sid", "tenant")
        mock_mgr.soft_delete_messages.assert_not_called()
        assert reply == "会话消息已清空，开始新会话。"

    async def test_new_session_calls_soft_delete_messages(self):
        """新会话 -> 软删除 soft_delete_messages"""
        with patch("src.channels.session.channel_session_manager") as mock_mgr:
            reply = await execute_hidden_command("新会话", "sid", "tenant")
        mock_mgr.soft_delete_messages.assert_called_once_with("sid", "tenant")
        mock_mgr.delete_messages.assert_not_called()
        assert reply == "已开启新会话，历史聊天记录已保留。"

    async def test_not_command_returns_none(self):
        """非命令文本返回 None"""
        reply = await execute_hidden_command("随便聊聊", "sid", "tenant")
        assert reply is None


def _make_manager(metadata):
    """构造带 metadata 的 session manager mock 补丁上下文"""
    from unittest.mock import MagicMock

    manager = MagicMock()
    manager.get_session_by_id.return_value = {"session_id": "sid", "metadata": metadata}
    return manager


class TestHiddenCommandsClearLeadCapture:
    """「新会话/清空会话」清除会话留资信息"""

    async def _run_and_assert(self, command, lead_stage="new"):
        metadata = {
            "lead_capture": {"stage": "captured", "lead_id": "lead_lc_abc", "contact_method": "phone"},
            "service_state": 1,
        }
        manager = _make_manager(metadata)
        with patch("src.channels.session.channel_session_manager", manager), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id",
                   return_value={"lead_id": "lead_lc_abc", "stage": lead_stage}), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.delete", return_value=True) as mock_delete:
            reply = await execute_hidden_command(command, "sid", "tenant")

        # metadata.lead_capture 被移除，其他键保留（整体替换语义不能误清）
        written = manager.update_session.call_args.kwargs["metadata"]
        assert "lead_capture" not in written
        assert written["service_state"] == 1
        mock_delete.assert_called_once_with("lead_lc_abc", "tenant")
        assert reply  # 命令文案正常返回

    async def test_clear_session_removes_lead_capture(self):
        await self._run_and_assert("清空会话")

    async def test_new_session_removes_lead_capture(self):
        await self._run_and_assert("新会话")

    async def test_no_lead_capture_skips(self):
        """无留资信息时不写 metadata、不删线索"""
        manager = _make_manager({"service_state": 1})
        with patch("src.channels.session.channel_session_manager", manager), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.delete") as mock_delete:
            await execute_hidden_command("新会话", "sid", "tenant")
        manager.update_session.assert_not_called()
        mock_delete.assert_not_called()

    async def test_delete_failure_does_not_break_command(self):
        """线索删除异常不阻断命令返回"""
        metadata = {"lead_capture": {"lead_id": "lead_lc_abc"}, "service_state": 1}
        manager = _make_manager(metadata)
        with patch("src.channels.session.channel_session_manager", manager), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id",
                   return_value={"lead_id": "lead_lc_abc", "stage": "new"}), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.delete", side_effect=RuntimeError("db down")):
            reply = await execute_hidden_command("新会话", "sid", "tenant")
        assert reply == "已开启新会话，历史聊天记录已保留。"

    async def test_converted_lead_kept(self):
        """已转化（converted）线索不删记录，仅清 metadata 断开推送引用"""
        metadata = {"lead_capture": {"lead_id": "lead_lc_conv"}, "service_state": 1}
        manager = _make_manager(metadata)
        with patch("src.channels.session.channel_session_manager", manager), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id",
                   return_value={"lead_id": "lead_lc_conv", "stage": "converted"}), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.delete") as mock_delete:
            reply = await execute_hidden_command("新会话", "sid", "tenant")
        # metadata 已清（推送链路断开）
        written = manager.update_session.call_args.kwargs["metadata"]
        assert "lead_capture" not in written
        # 线索记录保留
        mock_delete.assert_not_called()
        assert reply

    async def test_empty_tenant_skips_lead_delete(self):
        """无 tenant_id 时仅清 metadata，不删线索（避免误删其他租户数据）"""
        metadata = {"lead_capture": {"lead_id": "lead_lc_abc"}}
        manager = _make_manager(metadata)
        with patch("src.channels.session.channel_session_manager", manager), \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.delete") as mock_delete:
            await execute_hidden_command("新会话", "sid", "")
        mock_delete.assert_not_called()
