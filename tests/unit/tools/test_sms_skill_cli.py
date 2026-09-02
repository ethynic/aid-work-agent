"""
sms-verification skill CLI 单元测试

测试 sms_cli.py 的 send / verify 子命令核心逻辑。
mock 依赖：send_sms_code / verify_sms_code / sms_manager / redis_client / settings。
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 将 skill scripts 目录加入 path，以便 import sms_cli
SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "skills"
    / "sms-verification-1.0.0"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import sms_cli  # noqa: E402

pytestmark = pytest.mark.tools


# ============== Fixtures ==============

# 说明：settings.demo（演示模式开关）与固定码 888888 已随 demo 模式移除而删除，
# 原 demo_on / demo_off fixtures 一并删除；send/verify 行为不再分模式。


@pytest.fixture
def mock_redis():
    """mock sms_cli.redis_client，返回一个全新的 MagicMock。

    每个测试用例独立，避免状态污染。
    """
    mock = MagicMock(name="redis_client")
    # 默认：键不存在、计数为 0、TTL 为 -2
    mock.exists.return_value = False
    mock.ttl.return_value = -2
    mock.zcard.return_value = 0
    mock.zremrangebyscore.return_value = 0
    mock.zadd.return_value = 1
    mock.expire.return_value = True
    mock.set.return_value = None
    mock.make_key.side_effect = lambda prefix, ident: f"test:{prefix}:{ident}"
    return mock


@pytest.fixture(autouse=True)
def patch_redis(mock_redis):
    """自动 patch sms_cli.redis_client"""
    with patch.object(sms_cli, "redis_client", mock_redis):
        yield mock_redis


@pytest.fixture
def qb_code_empty():
    """qb_sms_code 留空"""
    with patch.object(sms_cli.settings.sms, "qb_sms_code", ""):
        yield


@pytest.fixture
def qb_code_set():
    """qb_sms_code 设为 'QBTEST'"""
    with patch.object(sms_cli.settings.sms, "qb_sms_code", "QBTEST"):
        yield


# ============== send 子命令 ==============



def _sender_available_patch():
    """构造 sms_manager patch：get_sender 返回可用 sender"""
    manager = MagicMock()
    manager.get_sender.return_value = MagicMock(is_available=MagicMock(return_value=True))
    return patch.object(sms_cli, "sms_manager", manager)


class TestSendSms:
    """send_sms 函数测试"""

    @patch.object(sms_cli, "send_sms_code", return_value=True)
    def test_send_success_with_sender_available(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """场景1：sender 可用，发送成功"""
        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is True
        assert result["expires_in_seconds"] == 900
        # 不含 code 字段
        assert "code" not in result
        # 频控计数被记录
        assert mock_redis.set.called
        assert mock_redis.zadd.called

    def test_send_invalid_mobile(self, qb_code_empty):
        """场景5：手机号格式非法"""
        result = sms_cli.send_sms(mobile="12345")
        assert result["success"] is False
        assert "手机号格式错误" in result["error"]
        assert result["debug"] == "invalid mobile format"

    def test_send_invalid_mobile_not_starting_with_1(self, qb_code_empty):
        """场景5：手机号不以 1 开头"""
        result = sms_cli.send_sms(mobile="23800138000")
        assert result["success"] is False

    def test_send_invalid_mobile_with_letters(self, qb_code_empty):
        """场景5：手机号含字母"""
        result = sms_cli.send_sms(mobile="1380013800a")
        assert result["success"] is False

    def test_send_channel_not_configured(self, mock_redis, qb_code_empty):
        """场景4：sender 不可用"""
        manager = MagicMock()
        manager.get_sender.return_value = MagicMock(
            is_available=MagicMock(return_value=False))
        with patch.object(sms_cli, "sms_manager", manager):
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert "短信通道未配置" in result["error"]
        assert result["debug"] == "sms sender not available"
        # 频控计数不应被记录
        assert not mock_redis.zadd.called

    def test_send_channel_no_sender(self, mock_redis, qb_code_empty):
        """场景4：sender 为 None"""
        manager = MagicMock()
        manager.get_sender.return_value = None
        with patch.object(sms_cli, "sms_manager", manager):
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert "短信通道未配置" in result["error"]

    @patch.object(sms_cli, "send_sms_code", return_value=True)
    def test_send_60s_rate_limit_blocked(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """场景2：60s 频控拦截"""
        # 模拟 60s 内已发送过
        mock_redis.exists.return_value = True
        mock_redis.ttl.return_value = 45

        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert result["reason"] == "interval_too_short"
        assert result["retry_after_seconds"] == 45
        assert "45 秒" in result["error"]
        # 底层 send_sms_code 不应被调用
        assert not _mock_send.called
        # 频控计数不应增加
        assert not mock_redis.zadd.called

    @patch.object(sms_cli, "send_sms_code", return_value=True)
    def test_send_60s_rate_limit_ttl_negative(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """场景2：60s 频控拦截，TTL 异常时回退默认值"""
        mock_redis.exists.return_value = True
        mock_redis.ttl.return_value = -1  # 永不过期或异常

        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert result["reason"] == "interval_too_short"
        assert result["retry_after_seconds"] == sms_cli.SEND_INTERVAL_SECONDS

    @patch.object(sms_cli, "send_sms_code", return_value=True)
    def test_send_24h_daily_limit_blocked(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """场景3：24h 上限拦截"""
        # 24h 内已发送 10 次
        mock_redis.zcard.return_value = 10

        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert result["reason"] == "daily_limit_exceeded"
        assert "10 次" in result["error"]
        # 底层 send_sms_code 不应被调用
        assert not _mock_send.called
        # 60s 锁不应被设置
        assert not mock_redis.set.called

    @patch.object(sms_cli, "send_sms_code", return_value=True)
    def test_send_24h_boundary_not_blocked(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """场景3：24h 计数为 9（边界值，未达上限 10）应放行"""
        mock_redis.zcard.return_value = 9

        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is True
        assert _mock_send.called

    @patch.object(sms_cli, "send_sms_code", return_value=False)
    def test_send_backend_failure(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """底层 send_sms_code 返回 False"""
        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert "验证码发送失败" in result["error"]
        # 发送失败时不应记录频控计数
        assert not mock_redis.zadd.called
        assert not mock_redis.set.called

    @patch.object(sms_cli, "send_sms_code", side_effect=Exception("DB connection lost"))
    def test_send_backend_exception(
        self, _mock_send, mock_redis, qb_code_empty
    ):
        """底层 send_sms_code 抛异常"""
        with _sender_available_patch():
            result = sms_cli.send_sms(mobile="13800138000")

        assert result["success"] is False
        assert "验证码发送失败" in result["error"]
        # debug 字段应被脱敏（此处无敏感字段，原样返回）
        assert "DB connection lost" in result["debug"]


# ============== verify 子命令 ==============


class TestVerifySms:
    """verify_sms 函数测试"""

    @patch.object(sms_cli, "verify_sms_code", return_value=True)
    def test_verify_success(
        self, _mock_verify, mock_redis, qb_code_empty
    ):
        """场景6：验证码正确"""
        result = sms_cli.verify_sms(mobile="13800138000", code="123456")

        assert result["success"] is True
        assert result["reason"] == "ok"
        _mock_verify.assert_called_once_with("13800138000", "123456")

    @patch.object(sms_cli, "verify_sms_code", return_value=False)
    def test_verify_invalid_code(
        self, _mock_verify, mock_redis, qb_code_empty
    ):
        """场景7：验证码错误/过期/已使用"""
        result = sms_cli.verify_sms(mobile="13800138000", code="999999")

        assert result["success"] is False
        assert result["reason"] == "invalid"
        assert "验证码错误或已过期" in result["error"]

    @patch.object(sms_cli, "verify_sms_code", return_value=False)
    def test_verify_invalid_mobile(
        self, _mock_verify, mock_redis, qb_code_empty
    ):
        """场景7：手机号格式错误"""
        result = sms_cli.verify_sms(mobile="123", code="123456")

        assert result["success"] is False
        assert result["reason"] == "invalid"
        # 底层不应被调用（手机号校验在前）
        assert not _mock_verify.called

    @patch.object(sms_cli, "verify_sms_code", return_value=True)
    def test_verify_bypass(
        self, _mock_verify, mock_redis, qb_code_set
    ):
        """场景8：bypass 码（qb_sms_code）"""
        result = sms_cli.verify_sms(mobile="13800138000", code="QBTEST")

        assert result["success"] is True
        assert result["reason"] == "bypass"
        # bypass 时底层 verify_sms_code 不应被调用
        assert not _mock_verify.called

    @patch.object(sms_cli, "verify_sms_code", return_value=True)
    def test_verify_bypass_code_not_6_digits(
        self, _mock_verify, mock_redis, qb_code_set
    ):
        """场景8：bypass 码非 6 位数字，仍应走 bypass 分支"""
        # qb_sms_code='QBTEST'（5 位且非纯数字），应直接走 bypass
        result = sms_cli.verify_sms(mobile="13800138000", code="QBTEST")

        assert result["success"] is True
        assert result["reason"] == "bypass"
        assert not _mock_verify.called

    def test_verify_code_wrong_format(
        self, mock_redis, qb_code_empty
    ):
        """验证码格式错误（非 6 位数字）"""
        result = sms_cli.verify_sms(mobile="13800138000", code="abc123")

        assert result["success"] is False
        assert result["reason"] == "invalid"
        assert "验证码格式错误" in result["error"]

    def test_verify_code_too_short(
        self, mock_redis, qb_code_empty
    ):
        """验证码位数不足"""
        result = sms_cli.verify_sms(mobile="13800138000", code="12345")

        assert result["success"] is False
        assert result["reason"] == "invalid"

    @patch.object(sms_cli, "verify_sms_code", side_effect=Exception("DB error"))
    def test_verify_backend_exception(
        self, _mock_verify, mock_redis, qb_code_empty
    ):
        """底层 verify_sms_code 抛异常"""
        result = sms_cli.verify_sms(mobile="13800138000", code="123456")

        assert result["success"] is False
        assert result["reason"] == "invalid"
        assert "验证码校验失败" in result["error"]


# ============== sanitize_error_info ==============


class TestSanitizeErrorInfo:
    """敏感信息脱敏测试"""

    def test_sanitize_password(self):
        msg = "Auth failed: password=secret123 for user admin"
        out = sms_cli.sanitize_error_info(msg)
        assert "secret123" not in out
        assert "password=***" in out

    def test_sanitize_api_key(self):
        msg = "Request failed: api_key=sk-abc123xyz"
        out = sms_cli.sanitize_error_info(msg)
        assert "sk-abc123xyz" not in out
        assert "api_key=***" in out

    def test_sanitize_token(self):
        msg = "Auth failed: token=abc123 for request"
        out = sms_cli.sanitize_error_info(msg)
        assert "abc123" not in out
        assert "token=***" in out

    def test_sanitize_code(self):
        msg = "Verify code=123456 failed"
        out = sms_cli.sanitize_error_info(msg)
        assert "123456" not in out
        assert "code=***" in out

    def test_sanitize_empty(self):
        assert sms_cli.sanitize_error_info("") == ""
        assert sms_cli.sanitize_error_info(None) == ""

    def test_sanitize_no_sensitive(self):
        msg = "Database connection timeout"
        assert sms_cli.sanitize_error_info(msg) == msg
