"""本地工具配对业务单元测试（mock repository，无真实 DB）

验证：
- 过期/已用配对码不可消费（pair 返回 None）
- 配对成功只返回一次 token 明文，库只存 hash
- 创建配对码时作废旧码
"""

from unittest.mock import patch

import pytest

from src.local_tools import pairing

pytestmark = pytest.mark.unit


class TestPair:
    def test_expired_or_used_code_returns_none(self):
        """consume_ticket 返回 None（过期/已用/不存在）→ pair 返回 None，不建设备"""
        with patch("src.local_tools.repository.consume_ticket", return_value=None), \
             patch("src.local_tools.repository.create_device") as mock_create:
            assert pairing.pair("ABCD2345") is None
            mock_create.assert_not_called()

    def test_pair_success_returns_token_once(self):
        """配对成功：返回 device_id + 64 位 token 明文；落库的是 token hash"""
        ticket = {"tenant_id": "tenant_t", "user_id": "user_t"}
        created_hashes = {}

        def fake_create_device(**kwargs):
            created_hashes.update(kwargs)
            return {"id": "dev-1"}

        with patch("src.local_tools.repository.consume_ticket", return_value=ticket), \
             patch("src.local_tools.repository.create_device", side_effect=fake_create_device):
            result = pairing.pair("abcd2345", name="测试机", platform="windows")

        assert result["device_id"] == "dev-1"
        assert len(result["device_token"]) == 64
        # 落库的是 hash，不是明文
        assert created_hashes["token_hash"] != result["device_token"]
        assert len(created_hashes["token_hash"]) == 64
        assert created_hashes["tenant_id"] == "tenant_t"

    def test_fingerprint_stored_as_hash(self):
        """机器指纹只存哈希"""
        ticket = {"tenant_id": "t", "user_id": "u"}
        captured = {}

        def fake_create_device(**kwargs):
            captured.update(kwargs)
            return {"id": "dev-2"}

        with patch("src.local_tools.repository.consume_ticket", return_value=ticket), \
             patch("src.local_tools.repository.create_device", side_effect=fake_create_device):
            pairing.pair("ABCD2345", machine_fingerprint="raw-fingerprint")

        assert captured["machine_fingerprint_hash"] != "raw-fingerprint"
        assert len(captured["machine_fingerprint_hash"]) == 64


class TestCreatePairingTicket:
    def test_invalidates_old_tickets(self):
        """创建新配对码前作废同一用户所有未使用旧码"""
        with patch("src.local_tools.repository.invalidate_unused_tickets") as mock_invalidate, \
             patch("src.local_tools.repository.create_ticket", return_value="ticket-1") as mock_create:
            result = pairing.create_pairing_ticket("tenant_t", "user_t")

        mock_invalidate.assert_called_once_with("tenant_t", "user_t")
        mock_create.assert_called_once()
        # create_ticket 收到的是 code hash 而非明文
        code_hash_arg = mock_create.call_args[0][2]
        assert code_hash_arg != result["code"]
        assert len(code_hash_arg) == 64
        assert len(result["code"]) == 8
        assert result["expires_at"] is not None
