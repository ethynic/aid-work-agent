"""send_sms_code 无通道 bypass 单元测试（全 mock，无 DB/外部调用）

qb_sms_code 已配置且无可用短信通道时，发送侧跳过真实发信直接返回成功，
校验侧由 verify_sms_code 的 qb_sms_code 放行（私有云未购通道的临时场景）。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import settings

pytestmark = pytest.mark.unit


def _patch_sms_manager(available: bool):
    manager = MagicMock()
    sender = MagicMock(is_available=MagicMock(return_value=available)) if available else None
    manager.get_sender.return_value = sender
    return patch("src.sms.manager.sms_manager", manager)


class TestSendSmsCodeBypass:
    def test_bypass_configured_and_no_channel(self):
        """qb_sms_code 已配置 + 无通道：跳过真实发信返回 True"""
        from src.db.models import send_sms_code

        with patch.object(settings.sms, "qb_sms_code", "147852"), \
             _patch_sms_manager(available=False) as mock_manager:
            assert send_sms_code("13800138000") is True
            mock_manager.send.assert_not_called()

    def test_no_bypass_and_no_channel(self):
        """未配置 qb_sms_code + 无通道：维持原行为返回 False"""
        from src.db.models import send_sms_code

        with patch.object(settings.sms, "qb_sms_code", ""), \
             _patch_sms_manager(available=False):
            assert send_sms_code("13800138000") is False

    def test_bypass_configured_but_channel_available(self):
        """qb_sms_code 已配置但通道可用：不触发 bypass，走真实发送路径"""
        from src.db.models import send_sms_code

        # send 返回非 200 使函数在落库前失败，避免单元测试触碰 DB
        with patch.object(settings.sms, "qb_sms_code", "147852"), \
             _patch_sms_manager(available=True) as mock_manager:
            mock_manager.send.return_value = {"code": 500, "msg": "mocked failure"}
            assert send_sms_code("13800138000") is False
            mock_manager.send.assert_called_once()
