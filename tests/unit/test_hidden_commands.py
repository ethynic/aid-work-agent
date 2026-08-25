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
