"""wecom_personal_rpa.auth 单元测试

覆盖契约（docs/system/wecom-personal-rpa-protocol.md §A.1 / §B.1）：
- compute_signature 确定性 + 与独立 hmac 计算一致
- verify_request：合法签名通过
- verify_request：错误签名拒绝
- verify_request：时间戳超窗拒绝
- verify_request：nonce 重复第二次拒绝（防重放）
- verify_request：缺头拒绝
- verify_request：get_secret 返回 None（客户端不存在/禁用）拒绝

外部依赖全部 mock：redis_client、get_secret 回调。不发起任何真实网络/Redis 调用。
"""

import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa import auth
from src.channels.wecom_personal_rpa.auth import VerifyResult, compute_signature, verify_request
from src.channels.wecom_personal_rpa.schemas import (
    HEADER_CLIENT_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
)

pytestmark = [pytest.mark.unit, pytest.mark.channels]

# 测试常量
TEST_CLIENT_ID = "client_001"
TEST_SECRET = b"super-secret-key-for-hmac"
TEST_BODY_STR = '{"event_id":"evt_1","client_id":"client_001"}'
TEST_BODY_BYTES = TEST_BODY_STR.encode("utf-8")


def _make_headers(timestamp: str, nonce: str, signature: str) -> dict:
    """构造完整鉴权头。"""
    return {
        HEADER_CLIENT_ID: TEST_CLIENT_ID,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: signature,
    }


def _sign(client_id: str, timestamp: str, nonce: str, body, secret: bytes) -> str:
    """独立实现的签名，用于交叉验证 auth.compute_signature。"""
    msg = client_id.encode() + timestamp.encode() + nonce.encode() + (
        body if isinstance(body, bytes) else body.encode("utf-8")
    )
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


@pytest.fixture
def fresh_now():
    """返回当前秒级时间戳字符串。"""
    return str(int(time.time()))


@pytest.fixture
def get_secret_mock():
    """返回解密后的 secret bytes 的 get_secret 回调 mock。"""
    fn = MagicMock(return_value=TEST_SECRET)
    return fn


@pytest.fixture(autouse=True)
def _reset_redis_and_state():
    """每个测试前后重置 auth 模块的降级告警标志，并 patch redis_client.acquire_lock 为内存实现。

    用内存 dict 模拟 SETNX+TTL，保证测试隔离、不依赖真实 Redis。
    """
    acquired_keys: set = set()

    def _fake_acquire_lock(key: str, value: str, ex: int = 60) -> bool:
        if key in acquired_keys:
            return False
        acquired_keys.add(key)
        return True

    # 模拟 redis_client 已连接（不触发降级 warning 分支）
    with patch.object(auth, "redis_client") as mock_redis:
        mock_redis.acquire_lock.side_effect = _fake_acquire_lock
        mock_redis._connected = True
        # 重置降级告警标志
        auth._DEGRADED_WARNED = False
        yield acquired_keys
    # 清理模块级状态
    auth._DEGRADED_WARNED = False


class TestComputeSignature:
    """compute_signature 契约测试。"""

    def test_deterministic(self):
        """同一组输入两次计算结果完全一致（确定性）。"""
        sig_a = compute_signature(TEST_CLIENT_ID, "1700000000", "n1", TEST_BODY_STR, TEST_SECRET)
        sig_b = compute_signature(TEST_CLIENT_ID, "1700000000", "n1", TEST_BODY_STR, TEST_SECRET)
        assert sig_a == sig_b

    def test_lowercase_hex(self):
        """签名应为小写十六进制。"""
        sig = compute_signature(TEST_CLIENT_ID, "1700000000", "n1", TEST_BODY_STR, TEST_SECRET)
        assert sig == sig.lower()
        assert all(c in "0123456789abcdef" for c in sig)
        assert len(sig) == 64  # SHA-256 hex 长度

    def test_matches_independent_hmac(self):
        """与独立实现的 hmac_sha256 结果完全一致（交叉验证拼接顺序）。"""
        ts, nonce = "1700000000", "nonce_abc"
        expected = _sign(TEST_CLIENT_ID, ts, nonce, TEST_BODY_STR, TEST_SECRET)
        actual = compute_signature(TEST_CLIENT_ID, ts, nonce, TEST_BODY_STR, TEST_SECRET)
        assert actual == expected

    def test_body_str_and_bytes_equivalent(self):
        """body 以 str 或 UTF-8 bytes 传入应得到相同签名。"""
        ts, nonce = "1700000000", "n1"
        sig_str = compute_signature(TEST_CLIENT_ID, ts, nonce, TEST_BODY_STR, TEST_SECRET)
        sig_bytes = compute_signature(TEST_CLIENT_ID, ts, nonce, TEST_BODY_BYTES, TEST_SECRET)
        assert sig_str == sig_bytes

    def test_different_secret_different_signature(self):
        """不同 secret 产生不同签名。"""
        ts, nonce = "1700000000", "n1"
        sig1 = compute_signature(TEST_CLIENT_ID, ts, nonce, TEST_BODY_STR, TEST_SECRET)
        sig2 = compute_signature(TEST_CLIENT_ID, ts, nonce, TEST_BODY_STR, b"another-secret")
        assert sig1 != sig2


class TestVerifyRequestValid:
    """verify_request 合法请求通过。"""

    def test_valid_signature_passes(self, fresh_now, get_secret_mock):
        """完整、合法的签名应当通过，返回 ok=True 与 client_id。"""
        nonce = "nonce_valid_001"
        sig = _sign(TEST_CLIENT_ID, fresh_now, nonce, TEST_BODY_BYTES, TEST_SECRET)
        headers = _make_headers(fresh_now, nonce, sig)

        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)

        assert result.ok is True
        assert result.client_id == TEST_CLIENT_ID
        assert result.error is None
        get_secret_mock.assert_called_once_with(TEST_CLIENT_ID)

    def test_valid_signature_with_str_body(self, fresh_now, get_secret_mock):
        """raw_body 以 str 传入也应通过（与 bytes 等价）。"""
        nonce = "nonce_str_body"
        sig = _sign(TEST_CLIENT_ID, fresh_now, nonce, TEST_BODY_STR, TEST_SECRET)
        headers = _make_headers(fresh_now, nonce, sig)

        result = verify_request(headers, TEST_BODY_STR, get_secret_mock)

        assert result.ok is True
        assert result.client_id == TEST_CLIENT_ID


class TestVerifyRequestReject:
    """verify_request 各类失败场景拒绝。"""

    def test_wrong_signature_rejected(self, fresh_now, get_secret_mock):
        """错误签名应拒绝（auth_failed）。"""
        nonce = "nonce_wrong_sig"
        wrong_sig = "0" * 64  # 合法格式但内容错误
        headers = _make_headers(fresh_now, nonce, wrong_sig)

        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)

        assert result.ok is False
        assert result.client_id is None
        assert result.error == "auth_failed"

    def test_timestamp_out_of_window_rejected(self, get_secret_mock):
        """时间戳超出 300 秒窗口应拒绝（auth_failed）。

        意图：防止重放过期请求。窗口外即视为陈旧/伪造。
        """
        # 偏移 1 小时
        old_ts = str(int(time.time()) - 3600)
        nonce = "nonce_old"
        sig = _sign(TEST_CLIENT_ID, old_ts, nonce, TEST_BODY_BYTES, TEST_SECRET)
        headers = _make_headers(old_ts, nonce, sig)

        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)

        assert result.ok is False
        assert result.error == "auth_failed"

    def test_future_timestamp_rejected(self, get_secret_mock):
        """未来时间戳超出窗口也应拒绝（双向窗口）。"""
        future_ts = str(int(time.time()) + 3600)
        nonce = "nonce_future"
        sig = _sign(TEST_CLIENT_ID, future_ts, nonce, TEST_BODY_BYTES, TEST_SECRET)
        headers = _make_headers(future_ts, nonce, sig)

        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)

        assert result.ok is False
        assert result.error == "auth_failed"

    def test_nonce_replay_rejected_second_time(self, fresh_now, get_secret_mock, _reset_redis_and_state):
        """同一 nonce 第二次请求应拒绝（防重放，核心安全意图）。

        依赖 _reset_redis_and_state fixture 暴露的 acquired_keys 集合，
        模拟 Redis SETNX 在第二次返回 False。
        """
        nonce = "nonce_replay_once"
        sig = _sign(TEST_CLIENT_ID, fresh_now, nonce, TEST_BODY_BYTES, TEST_SECRET)
        headers = _make_headers(fresh_now, nonce, sig)

        # 第一次：通过
        result1 = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)
        assert result1.ok is True

        # 第二次：同一 nonce 应被拒绝
        result2 = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)
        assert result2.ok is False
        assert result2.error == "auth_failed"
        assert result2.client_id is None

    def test_missing_headers_rejected(self, get_secret_mock):
        """缺失任一鉴权头应拒绝（auth_failed）。

        意图：所有头均为必填，缺失即视为非法请求。
        """
        # 完全空 headers
        result = verify_request({}, TEST_BODY_BYTES, get_secret_mock)
        assert result.ok is False
        assert result.error == "auth_failed"

    def test_partial_missing_headers_rejected(self, get_secret_mock, fresh_now):
        """只缺 X-Signature 也应拒绝。"""
        nonce = "nonce_partial"
        headers = {
            HEADER_CLIENT_ID: TEST_CLIENT_ID,
            HEADER_TIMESTAMP: fresh_now,
            HEADER_NONCE: nonce,
            # 缺 HEADER_SIGNATURE
        }
        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)
        assert result.ok is False
        assert result.error == "auth_failed"

    def test_get_secret_none_rejected(self, fresh_now):
        """get_secret 返回 None（客户端不存在/禁用）应拒绝。

        意图：未知客户端不得通过鉴权。
        """
        nonce = "nonce_unknown_client"
        sig = _sign(TEST_CLIENT_ID, fresh_now, nonce, TEST_BODY_BYTES, TEST_SECRET)
        headers = _make_headers(fresh_now, nonce, sig)
        get_secret_none = MagicMock(return_value=None)

        result = verify_request(headers, TEST_BODY_BYTES, get_secret_none)

        assert result.ok is False
        assert result.error == "auth_failed"

    def test_invalid_timestamp_format_rejected(self, get_secret_mock):
        """时间戳非数字应拒绝（auth_failed）。"""
        nonce = "nonce_bad_ts"
        headers = _make_headers("not-a-number", nonce, "0" * 64)
        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)
        assert result.ok is False
        assert result.error == "auth_failed"


class TestVerifyRequestEdgeCases:
    """边界与大小写场景。"""

    def test_case_insensitive_headers(self, fresh_now, get_secret_mock):
        """HTTP 头大小写不敏感：小写头名也应被识别。"""
        nonce = "nonce_lowercase"
        sig = _sign(TEST_CLIENT_ID, fresh_now, nonce, TEST_BODY_BYTES, TEST_SECRET)
        headers = {
            "x-client-id": TEST_CLIENT_ID,
            "x-timestamp": fresh_now,
            "x-nonce": nonce,
            "x-signature": sig,
        }
        result = verify_request(headers, TEST_BODY_BYTES, get_secret_mock)
        assert result.ok is True
        assert result.client_id == TEST_CLIENT_ID
