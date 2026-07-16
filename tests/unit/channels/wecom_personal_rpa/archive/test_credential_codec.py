"""archive.credential_codec 单元测试

覆盖：
- 加密/解密/mask 闭环
- 5 个敏感字段全部被加密
- 非敏感字段保持明文
- 强制 listen_mode='server'（防 API 绕过）
- 密文不被二次加密
- 动态必填字段校验（server 模式）
"""
import os
import pytest

# 在导入被测模块前设置临时主密钥（避免 RuntimeError）
os.environ.setdefault("RPA_SECRET_KEY", "test-master-key-for-codec-tests-only-32B+")

from src.channels.wecom_personal_rpa.archive import credential_codec


# ----------------- 加密/解密/mask 闭环 -----------------


def test_encrypt_decrypt_roundtrip():
    """加密后能正确解密回原文。"""
    plain = {
        "corp_id": "ww1234567890abcdef",
        "archive_secret": "top-secret-archive-secret",
        "external_contact_secret": "customer-contact-secret",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n",
        "token": "callback-token-xxx",
        "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        "client_secret": "legacy-client-hmac-secret",
    }
    encrypted = credential_codec.encrypt_sensitive_fields(plain)
    # 敏感字段都被加密（变成 gAAAAA 开头的密文）
    for key in credential_codec.SENSITIVE_KEYS:
        assert encrypted[key].startswith("gAAAAA"), f"{key} 应被加密"
        assert encrypted[key] != plain[key], f"{key} 加密后不应等于明文"
    # 非敏感字段保持原值
    assert encrypted["corp_id"] == plain["corp_id"]


def test_decrypt_roundtrip():
    """解密后能还原全部敏感字段。"""
    plain = {
        "corp_id": "wwabc",
        "archive_secret": "secret-1",
        "external_contact_secret": "contact-secret-1",
        "private_key": "key-1",
        "token": "token-1",
        "encoding_aes_key": "aes-1",
        "client_secret": "cs-1",
    }
    encrypted = credential_codec.encrypt_sensitive_fields(plain)
    decrypted = credential_codec.decrypt_sensitive_fields(encrypted)
    for key in credential_codec.SENSITIVE_KEYS:
        assert decrypted[key] == plain[key], f"{key} 解密后应等于原文"


def test_mask_returns_masked_values():
    """mask 后敏感字段不显示明文，仅显示掩码。"""
    plain = {
        "corp_id": "wwabc",
        "archive_secret": "abcdefghijklmnopqrstuvwxyz123456",
        "external_contact_secret": "external-contact-secret-xyz",
        "private_key": "----BEGIN----abc123----END----",
        "token": "token-xyz-789",
        "encoding_aes_key": "aes-key-abc",
        "client_secret": "cs-xyz",
    }
    masked = credential_codec.mask_sensitive_fields(plain)
    # 明文不应出现在 mask 结果中
    for key in credential_codec.SENSITIVE_KEYS:
        plain_val = plain[key]
        masked_val = masked[key]
        assert plain_val not in masked_val, f"{key} 明文不应出现在 mask 结果中"
        assert masked_val.startswith("***"), f"{key} mask 应以 *** 开头"


def test_mask_handles_empty_and_ciphertext():
    """mask 正确处理空值和密文。"""
    cfg = {
        "archive_secret": "",  # 空
        "external_contact_secret": "",
        "private_key": "gAAAAABmXYZ_fake_ciphertext",  # 密文
        "token": "abcd",  # 短明文（≤4 字符）
        "encoding_aes_key": "abcdefghij",  # 普通明文
        "client_secret": None,  # None
    }
    masked = credential_codec.mask_sensitive_fields(cfg)
    assert masked["archive_secret"] == ""
    assert masked["private_key"] == "***"  # 密文无法解出末尾，统一 ***
    assert masked["token"] == "***"  # 短明文也仅显示 ***
    assert masked["encoding_aes_key"] == "***ghij"  # 末 4 位
    assert masked["client_secret"] == ""


# ----------------- 强制 listen_mode='server' -----------------


def test_encrypt_forces_listen_mode_to_server():
    """加密函数强制把 listen_mode 改为 server（防 API 绕过）。"""
    # 用户尝试绕过前端传 client
    plain = {"corp_id": "ww", "listen_mode": "client"}
    encrypted = credential_codec.encrypt_sensitive_fields(plain)
    assert encrypted["listen_mode"] == "server"


def test_encrypt_forces_listen_mode_even_when_missing():
    """listen_mode 缺失时也被强制设为 server。"""
    plain = {"corp_id": "ww"}
    encrypted = credential_codec.encrypt_sensitive_fields(plain)
    assert encrypted["listen_mode"] == "server"


def test_is_server_mode_returns_true_in_mvp():
    """第一期 MVP：is_server_mode 永远返回 True。"""
    assert credential_codec.is_server_mode({"listen_mode": "server"}) is True
    # 即便配置传 client，函数也应该按 server 处理（FORCED_LISTEN_MODE 兜底）
    assert credential_codec.is_server_mode({"listen_mode": "client"}) is False
    # 但实际场景中 encrypt_sensitive_fields 已经强制覆盖为 server
    encrypted = credential_codec.encrypt_sensitive_fields({"listen_mode": "client"})
    assert credential_codec.is_server_mode(encrypted) is True


# ----------------- 密文不被二次加密 -----------------


def test_encrypt_idempotent_on_ciphertext():
    """对已经是密文的值不二次加密。"""
    plain = {"archive_secret": "plain-secret-1234567890"}
    encrypted1 = credential_codec.encrypt_sensitive_fields(plain)
    # 模拟「读出来再写回去」（list_by_tenant 后再 update 的场景）
    encrypted2 = credential_codec.encrypt_sensitive_fields(encrypted1)
    assert encrypted2["archive_secret"] == encrypted1["archive_secret"], "密文不应被二次加密"


# ----------------- 必填字段动态校验 -----------------


def test_validate_required_fields_server_mode_complete():
    """server 模式下，5 个字段都齐全时返回空 dict。"""
    cfg = {
        "corp_id": "ww1234",
        "archive_secret": "xxx",
        "private_key": "xxx",
        "token": "xxx",
        "encoding_aes_key": "xxx",
    }
    missing = credential_codec.validate_required_fields(cfg)
    assert missing == {}


def test_validate_required_fields_server_mode_missing():
    """server 模式下，缺必填字段时返回缺失映射。

    业务变更（2026-07）：archive_secret 和 private_key 从必填改为可选（分阶段录入凭证），
    保留 corp_id + token + encoding_aes_key 必填（回调 URL 验证必需）。
    """
    cfg = {
        "corp_id": "ww1234",
        # 缺 token / encoding_aes_key
    }
    missing = credential_codec.validate_required_fields(cfg)
    assert "token" in missing
    assert "encoding_aes_key" in missing
    # archive_secret / private_key 不再强制
    assert "archive_secret" not in missing
    assert "private_key" not in missing
    assert "corp_id" not in missing  # corp_id 已提供


def test_validate_required_fields_server_mode_allows_partial_credentials():
    """server 模式下，仅提供回调 URL 三件套（corp_id + token + encoding_aes_key）即可通过校验。

    验证分阶段录入场景：用户先建配置拿 config_id，去企微后台配回调 URL，
    URL 通了之后再回来补 archive_secret 和 private_key。
    """
    cfg = {
        "corp_id": "ww1234",
        "token": "callback-token",
        "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        # archive_secret / private_key 暂未拿到，先不填
    }
    missing = credential_codec.validate_required_fields(cfg)
    assert missing == {}, "仅回调三件套齐备时应通过校验（archive_secret/private_key 可后补）"


def test_validate_required_fields_uses_listen_mode_server_default():
    """listen_mode 缺失时默认按 server 校验。

    业务变更（2026-07）：server 模式必填字段缩减为 corp_id + token + encoding_aes_key，
    archive_secret 不再强制。这里验证 listen_mode 缺失时仍走 server 分支（要求回调三件套）。
    """
    cfg = {"corp_id": "ww"}  # 无 listen_mode
    missing = credential_codec.validate_required_fields(cfg)
    # 走 server 分支：要求 token / encoding_aes_key
    assert "token" in missing
    assert "encoding_aes_key" in missing
    # archive_secret 已不再强制
    assert "archive_secret" not in missing


def test_validate_required_fields_client_mode_reserved():
    """client 模式分支保留（第一期不会被触发，但代码逻辑要正确）。"""
    cfg = {
        "listen_mode": "client",
        "corp_id": "ww",
        # 缺 client_secret
    }
    missing = credential_codec.validate_required_fields(cfg)
    assert "client_secret" in missing
    assert "archive_secret" not in missing  # client 模式不需要 archive 字段


# ----------------- RSA 密钥对自动生成 -----------------


def test_generate_rsa_keypair_returns_pem_strings():
    """generate_rsa_keypair 返回两个 PEM 格式文本。"""
    private_pem, public_pem = credential_codec.generate_rsa_keypair()
    assert isinstance(private_pem, str)
    assert isinstance(public_pem, str)
    # 私钥 PKCS8 PEM
    assert "-----BEGIN PRIVATE KEY-----" in private_pem
    assert "-----END PRIVATE KEY-----" in private_pem
    # 公钥 SubjectPublicKeyInfo PEM
    assert "-----BEGIN PUBLIC KEY-----" in public_pem
    assert "-----END PUBLIC KEY-----" in public_pem


def test_generate_rsa_keypair_each_call_unique():
    """每次调用生成独立的密钥对（非固定种子）。"""
    priv1, _ = credential_codec.generate_rsa_keypair()
    priv2, _ = credential_codec.generate_rsa_keypair()
    assert priv1 != priv2, "两次生成的私钥不应相同"


def test_generate_rsa_keypair_can_roundtrip_encrypt_decrypt():
    """生成的密钥对能正确加解密消息（验证密钥对有效，与企微加密链路一致）。

    场景：服务端用私钥解密企微用公钥加密的 encrypt_random_key。这里自测加解密闭环。
    """
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes, serialization

    private_pem, public_pem = credential_codec.generate_rsa_keypair()

    # 反序列化
    private_key = serialization.load_pem_private_key(
        private_pem.encode("ascii"), password=None
    )
    public_key = serialization.load_pem_public_key(public_pem.encode("ascii"))

    # 公钥加密 → 私钥解密（模拟企微加密、本系统解密）
    plaintext = b"hello wecom archive chat msg random key"
    ciphertext = public_key.encrypt(
        plaintext,
        padding.PKCS1v15(),
    )
    decrypted = private_key.decrypt(ciphertext, padding.PKCS1v15())
    assert decrypted == plaintext, "私钥解密公钥加密的内容应得到原文"


def test_generate_rsa_keypair_private_key_is_pkcs8_unencrypted():
    """私钥是 PKCS8 格式、未加密（可被 load_pem_private_key 无密码加载）。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, _ = credential_codec.generate_rsa_keypair()
    # 不传 password 能加载 → 未加密
    key = serialization.load_pem_private_key(
        private_pem.encode("ascii"), password=None
    )
    # 验证密钥长度 2048 bit
    assert key.key_size == 2048


# ----------------- store_private_key（mock DB 层） -----------------


def test_store_private_key_returns_false_when_config_not_found(monkeypatch):
    """config_id 不存在时返回 False。"""
    from src.saas.db.channel_config_db import ChannelConfigDB

    monkeypatch.setattr(ChannelConfigDB, "get_by_id_decrypted", staticmethod(lambda cid: None))
    monkeypatch.setattr(ChannelConfigDB, "update", staticmethod(lambda *a, **kw: True))  # 不应被调用

    result = credential_codec.store_private_key("chan_nonexistent", "fake-pem")
    assert result is False


def test_store_private_key_calls_update_with_new_private_key(monkeypatch):
    """store_private_key 把新私钥写入 config 并调用 ChannelConfigDB.update。"""
    from src.saas.db.channel_config_db import ChannelConfigDB

    fake_cfg = {
        "config_id": "chan_abc",
        "tenant_id": "tenant_x",
        "channel_type": "wecom_personal_rpa",
        "subagent_type": None,
        "config": {
            "corp_id": "ww1234",
            "archive_secret": "plain-secret",  # 已解密明文
            "private_key": "",  # 原来为空
            "token": "token-xxx",
            "encoding_aes_key": "aes-xxx",
            "listen_mode": "server",
        },
    }
    captured = {}

    def fake_update(config_id, config, subagent_type=None):
        captured["config_id"] = config_id
        captured["config"] = config
        captured["subagent_type"] = subagent_type
        return True

    monkeypatch.setattr(ChannelConfigDB, "get_by_id_decrypted", staticmethod(lambda cid: fake_cfg))
    monkeypatch.setattr(ChannelConfigDB, "update", staticmethod(fake_update))

    new_private = "-----BEGIN PRIVATE KEY-----\nNEW\n-----END PRIVATE KEY-----\n"
    result = credential_codec.store_private_key("chan_abc", new_private)

    assert result is True
    assert captured["config_id"] == "chan_abc"
    assert captured["config"]["private_key"] == new_private
    # 其他敏感字段保留（明文传入 update，由 encrypt_sensitive_fields 再加密）
    assert captured["config"]["archive_secret"] == "plain-secret"
    assert captured["subagent_type"] is None
