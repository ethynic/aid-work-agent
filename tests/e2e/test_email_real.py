"""
端到端测试：真实邮件操作

需要环境变量：TEST_SMTP_SERVER, TEST_SMTP_PASSWORD 等
运行：pytest -m e2e tests/e2e/test_email_real.py
"""

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.email,
    pytest.mark.slow,
]


@pytest.mark.skipif(
    not os.getenv("TEST_SMTP_SERVER"),
    reason="需要邮件测试凭证",
)
class TestEmailReal:
    """真实邮件操作测试"""

    @pytest.mark.asyncio
    async def test_send_email(self):
        """发送测试邮件"""
        from src.models.user import UserEmail, EncryptionType
        from src.tools.email import EmailSendTool

        config = UserEmail(
            email_address=os.getenv("TEST_EMAIL_ADDRESS"),
            smtp_server=os.getenv("TEST_SMTP_SERVER"),
            smtp_port=int(os.getenv("TEST_SMTP_PORT", "465")),
            smtp_user=os.getenv("TEST_SMTP_USER"),
            smtp_password=os.getenv("TEST_SMTP_PASSWORD"),
            smtp_encryption=EncryptionType(os.getenv("TEST_SMTP_ENCRYPTION", "ssl")),
            imap_server=os.getenv("TEST_IMAP_SERVER", "imap.example.com"),
            imap_port=int(os.getenv("TEST_IMAP_PORT", "993")),
            imap_encryption=EncryptionType(os.getenv("TEST_IMAP_ENCRYPTION", "ssl")),
        )

        tool = EmailSendTool(config)
        result = await tool.execute(
            to=os.getenv("TEST_EMAIL_TO", config.email_address),
            subject="[测试] 自动化测试邮件",
            body="这是一封自动化测试邮件，请忽略。",
        )
        assert result["success"] is True
