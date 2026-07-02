"""archive.callback_crypto 单元测试

覆盖：
- 验签通过 / 验签失败（防时序攻击）
- AES 解密闭环（用 WeComCrypto.encrypt 加密，再用 callback_crypto 解密）
- echostr URL 验证流程
- POST 事件接收流程（返回明文 str，不绑定 JSON/XML 格式）
- SignatureError 异常路径
- EncodingAESKey 无效抛 SignatureError
- 与 wecom 渠道行为一致性（相同 token/aes_key/corp_id 加解密互通）
"""
import json
import os

import pytest

# 在导入被测模块前设置临时主密钥（codec 测试用，本测试其实不依赖）
os.environ.setdefault("RPA_SECRET_KEY", "test-master-key-for-codec-tests-only-32B+")

from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom_personal_rpa.archive import callback_crypto
from src.channels.wecom_personal_rpa.archive.callback_crypto import SignatureError


# 测试固定凭证（无任何真实企业关联，仅用于单元测试）
TEST_TOKEN = "QDG6eK"
TEST_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"  # 43 字符
TEST_CORP_ID = "wx5823bf96d3bd56c7"


def _make_crypto() -> WeComCrypto:
    return WeComCrypto(TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID)


# ----------------- 验签 -----------------


def test_verify_signature_ok():
    """正常验签通过。"""
    crypto = _make_crypto()
    encrypt = "dummy-encrypt"
    sig = crypto.generate_signature("1409309348", "1372623149", encrypt)
    assert callback_crypto.verify_signature(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, sig, "1409309348", "1372623149", encrypt
    ) is True


def test_verify_signature_tampered_signature():
    """签名被篡改 → 验签失败。"""
    encrypt = "dummy-encrypt"
    # 故意用错误的 token 生成签名
    bad_sig = WeComCrypto("wrong-token", TEST_AES_KEY, TEST_CORP_ID).generate_signature(
        "1409309348", "1372623149", encrypt
    )
    assert callback_crypto.verify_signature(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, bad_sig, "1409309348", "1372623149", encrypt
    ) is False


def test_verify_signature_constant_time_comparison():
    """验签使用 hmac.compare_digest（同长度错误签名应返回 False，不应抛异常）。"""
    encrypt = "dummy-encrypt"
    # 同长度的错误签名
    bad_sig = "a" * 40  # SHA1 是 40 个十六进制字符
    assert callback_crypto.verify_signature(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, bad_sig, "1409309348", "1372623149", encrypt
    ) is False


# ----------------- AES 解密闭环 -----------------


def test_decrypt_aes_roundtrip():
    """AES 解密闭环：WeComCrypto.encrypt 加密 → callback_crypto.decrypt_aes 解密。"""
    crypto = _make_crypto()
    plain = "hello wecom archive callback"
    encrypted = crypto.encrypt(plain)
    decrypted = callback_crypto.decrypt_aes(TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, encrypted)
    assert decrypted == plain


def test_decrypt_aes_corp_id_mismatch_raises():
    """corp_id 不匹配 → 抛 SignatureError。"""
    crypto = _make_crypto()
    encrypted = crypto.encrypt("test")  # 加密时绑定了 TEST_CORP_ID
    # 用不同 corp_id 解密，应在 WeComCrypto.decrypt 内部抛 ValueError
    with pytest.raises(SignatureError):
        callback_crypto.decrypt_aes(TEST_TOKEN, TEST_AES_KEY, "wrong-corp", encrypted)


def test_decrypt_aes_invalid_ciphertext_raises():
    """密文损坏 → 抛 SignatureError。"""
    with pytest.raises(SignatureError):
        callback_crypto.decrypt_aes(TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, "not-valid-base64-encrypt-data")


def test_decrypt_aes_invalid_encoding_aes_key_raises():
    """EncodingAESKey 长度错误 → 抛 SignatureError（不是 ValueError）。"""
    with pytest.raises(SignatureError):
        callback_crypto.decrypt_aes(TEST_TOKEN, "too-short", TEST_CORP_ID, "any")


# ----------------- echostr URL 验证 -----------------


def test_verify_and_decode_echostr_ok():
    """模拟企微 GET 验证：构造合法 echostr，函数返回解密后的明文。"""
    crypto = _make_crypto()
    plain_echostr = "1234567890abcdef1234567890abcdef"
    encrypt = crypto.encrypt(plain_echostr)
    sig = crypto.generate_signature("1409309348", "1372623149", encrypt)
    result = callback_crypto.verify_and_decode_echostr(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, sig, "1409309348", "1372623149", encrypt
    )
    assert result == plain_echostr


def test_verify_and_decode_echostr_bad_signature():
    """echostr 验签失败 → SignatureError。"""
    crypto = _make_crypto()
    plain_echostr = "1234567890abcdef1234567890abcdef"
    encrypt = crypto.encrypt(plain_echostr)
    # 篡改签名
    bad_sig = "0" * 40
    with pytest.raises(SignatureError, match="echostr 验签失败"):
        callback_crypto.verify_and_decode_echostr(
            TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, bad_sig, "1409309348", "1372623149", encrypt
        )


# ----------------- POST 事件接收 -----------------


def test_verify_and_decrypt_event_xml_ok():
    """POST 事件 XML 闭环：构造事件 XML 加密 → 解密回原始明文 XML 字符串。

    企微会话存档回调事件（path/95039）的明文格式是 XML，不是 JSON，
    所以本函数返回明文字符串，由上层根据业务自行解析 XML/JSON。
    """
    crypto = _make_crypto()
    event_xml = (
        "<xml><ToUserName>ww CorpId</ToUserName><AgentID>1000005</AgentID>"
        "<MsgType>event</MsgType><Event>chat_update</Event>"
        "<ChatId>wrjc7bDwAAxxxxxxxxxx</ChatId><ChangeType>create</ChangeType>"
        "</xml>"
    )
    encrypt = crypto.encrypt(event_xml)
    sig = crypto.generate_signature("1409309348", "1372623149", encrypt)
    result = callback_crypto.verify_and_decrypt_event(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, sig, "1409309348", "1372623149", encrypt
    )
    # 返回原始明文，不强制解析格式
    assert result == event_xml


def test_verify_and_decrypt_event_bad_signature():
    """POST 事件验签失败 → SignatureError。"""
    crypto = _make_crypto()
    encrypt = crypto.encrypt("<xml>some event</xml>")
    bad_sig = "0" * 40
    with pytest.raises(SignatureError, match="事件验签失败"):
        callback_crypto.verify_and_decrypt_event(
            TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, bad_sig, "1409309348", "1372623149", encrypt
        )


def test_verify_and_decrypt_event_does_not_force_json():
    """解密成功但明文非 JSON（如 XML）应原样返回，不应抛异常。

    防御回归：早期实现假设明文是 JSON 导致所有 XML 事件被误判为错误，
    现已改为只返回明文字符串，由 callback_handler 自行解析。
    """
    crypto = _make_crypto()
    # 加密一段非 JSON 明文（XML）
    plain = "<xml>not json</xml>"
    encrypt = crypto.encrypt(plain)
    sig = crypto.generate_signature("1409309348", "1372623149", encrypt)
    result = callback_crypto.verify_and_decrypt_event(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, sig, "1409309348", "1372623149", encrypt
    )
    assert result == plain


# ----------------- 跨渠道一致性（与 wecom 渠道行为对齐） -----------------


def test_cross_channel_consistency_with_wecom_crypto():
    """相同 token / aes_key / corp_id 下，本模块与 wecom.crypto.WeComCrypto 行为一致。"""
    crypto = _make_crypto()
    plain = "cross-channel-consistency-test"
    encrypted = crypto.encrypt(plain)

    # 本模块解密应得到同样明文
    decrypted = callback_crypto.decrypt_aes(TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID, encrypted)
    assert decrypted == plain

    # 本模块算出的签名应与 WeComCrypto 算出的一致
    sig_local = callback_crypto.verify_signature(
        TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID,
        crypto.generate_signature("ts", "nc", encrypted),
        "ts", "nc", encrypted,
    )
    assert sig_local is True
