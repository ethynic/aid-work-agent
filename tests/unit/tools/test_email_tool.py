"""
邮件工具单元测试

测试 EmailSendTool, EmailReadTool, EmailListFoldersTool（mock 邮件服务器）
"""

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.email]
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.user import UserEmail, EncryptionType
from src.tools.email import EmailSendTool, EmailReadTool, EmailListFoldersTool, create_email_tools


@pytest.fixture
def user_email():
    """测试邮箱配置"""
    return UserEmail(
        email_address="test@example.com",
        smtp_server="smtp.example.com",
        smtp_port=465,
        smtp_user="test@example.com",
        smtp_password="test_password",
        smtp_encryption=EncryptionType.SSL,
        imap_server="imap.example.com",
        imap_port=993,
        imap_encryption=EncryptionType.SSL,
    )


class TestEmailSendTool:
    """邮件发送测试"""

    @pytest.mark.asyncio
    @patch("src.tools.email.email_tool.smtplib.SMTP_SSL")
    async def test_send_simple_email(self, mock_smtp_ssl, user_email):
        mock_smtp_instance = MagicMock()
        mock_smtp_ssl.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_ssl.return_value.__exit__ = MagicMock(return_value=False)

        tool = EmailSendTool(user_email)
        result = await tool.execute(
            to="recipient@example.com",
            subject="Test Subject",
            body="Test body content",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_send_empty_to_fails(self, user_email):
        tool = EmailSendTool(user_email)
        result = await tool.execute(
            to="",
            subject="Test",
            body="Test body",
        )
        assert result["success"] is False

    def test_tool_definition(self, user_email):
        tool = EmailSendTool(user_email)
        defn = tool.to_tool_definition()
        assert defn["name"] == "email_send"
        assert "input_schema" in defn


class TestEmailReadTool:
    """邮件收取测试"""

    @pytest.mark.asyncio
    @patch("src.tools.email.email_tool.imaplib.IMAP4_SSL")
    async def test_read_emails(self, mock_imap_ssl, user_email):
        mock_imap = MagicMock()
        mock_imap_ssl.return_value = mock_imap
        mock_imap.login.return_value = ("OK", [b""])
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.search.return_value = ("OK", [b"1 2 3"])
        # 模拟空邮件
        mock_imap.fetch.return_value = ("OK", [(None, b"")])

        tool = EmailReadTool(user_email)
        result = await tool.execute(limit=5)
        assert "success" in result

    def test_tool_definition(self, user_email):
        tool = EmailReadTool(user_email)
        defn = tool.to_tool_definition()
        assert defn["name"] == "email_read"


class TestCreateEmailTools:
    """邮件工具工厂函数测试"""

    def test_creates_three_tools(self, user_email):
        tools = create_email_tools(user_email)
        assert len(tools) == 3
        assert tools[0].name == "email_send"
        assert tools[1].name == "email_read"
        assert tools[2].name == "email_list_folders"

    def test_all_tools_have_definitions(self, user_email):
        tools = create_email_tools(user_email)
        for tool in tools:
            defn = tool.to_tool_definition()
            assert "name" in defn
            assert "input_schema" in defn


class TestUserEmailModel:
    """UserEmail 模型测试"""

    def test_default_ports(self):
        email = UserEmail(
            email_address="test@example.com",
            smtp_server="smtp.example.com",
            smtp_user="test@example.com",
            smtp_password="password",
            imap_server="imap.example.com",
        )
        assert email.smtp_port == 465
        assert email.imap_port == 993
        assert email.smtp_encryption == EncryptionType.SSL

    def test_get_imap_credentials(self):
        email = UserEmail(
            email_address="test@example.com",
            smtp_server="smtp.example.com",
            smtp_user="test@example.com",
            smtp_password="password",
            imap_server="imap.example.com",
        )
        creds = email.get_imap_credentials()
        assert creds == ("test@example.com", "password")
