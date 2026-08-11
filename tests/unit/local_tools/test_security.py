"""本地工具安全原语单元测试（纯逻辑，无 DB）"""

import pytest

from src.local_tools.security import (
    PAIR_CODE_ALPHABET,
    generate_claim_token,
    generate_device_token,
    generate_pair_code,
    sha256_hex,
)

pytestmark = pytest.mark.unit


class TestPairCode:
    def test_pair_code_charset_and_length(self):
        """配对码：8 位，只含去混淆字符集（无 0/O/1/I）"""
        for _ in range(100):
            code = generate_pair_code()
            assert len(code) == 8
            assert all(c in PAIR_CODE_ALPHABET for c in code)
            assert not set(code) & set("0O1I")

    def test_pair_code_random(self):
        """配对码随机：100 次生成无重复"""
        codes = {generate_pair_code() for _ in range(100)}
        assert len(codes) == 100


class TestTokens:
    def test_device_token_length_and_uniqueness(self):
        """设备 token：hex 64 字符（256-bit），两次生成不同"""
        t1, t2 = generate_device_token(), generate_device_token()
        assert len(t1) == 64
        int(t1, 16)  # 合法 hex
        assert t1 != t2

    def test_claim_token_length(self):
        """claim token：hex 64 字符"""
        token = generate_claim_token()
        assert len(token) == 64
        int(token, 16)


class TestHash:
    def test_sha256_hex_stable(self):
        """sha256 哈希稳定：同输入同输出，不同输入不同输出"""
        assert sha256_hex("abc") == sha256_hex("abc")
        assert sha256_hex("abc") != sha256_hex("abd")
        assert len(sha256_hex("abc")) == 64
