"""腾讯文档凭据存取单测（TdocCredentialDB 打桩，不连库；APP_SECRET_KEY 注入测试密钥）"""

import pytest

import src.services.tencent_docs.credentials as cred_mod
from src.services.tencent_docs.credentials import (
    CredentialsNotBoundError,
    delete_credentials,
    get_credentials,
    get_status,
    save_credentials,
)
from src.services.tencent_docs.models import TencentDocsCredentials


class FakeCredDB:
    """(tenant_id, user_id) -> row 内存替身"""

    def __init__(self):
        self.store = {}

    def upsert(self, tenant_id, user_id, record):
        self.store[(tenant_id, user_id)] = {"status": "active", **record}

    def get(self, tenant_id, user_id):
        return self.store.get((tenant_id, user_id))

    def delete(self, tenant_id, user_id):
        return self.store.pop((tenant_id, user_id), None) is not None

    def mark_expired(self, tenant_id, user_id):
        row = self.store.get((tenant_id, user_id))
        if row:
            row["status"] = "expired"


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeCredDB()
    monkeypatch.setattr(cred_mod, "TdocCredentialDB", db)
    return db


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-master-key-for-tdoc-credentials")


@pytest.mark.unit
class TestSaveAndGet:
    def test_save_and_roundtrip(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid-12345", "token-abcdef", "oid-67890")
        creds = get_credentials("t1", "u1")
        assert isinstance(creds, TencentDocsCredentials)
        assert creds.client_id == "cid-12345"
        assert creds.access_token == "token-abcdef"
        assert creds.open_id == "oid-67890"

    def test_token_encrypted_at_rest(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid", "plain-token", "oid")
        row = fake_db.store[("t1", "u1")]
        assert row["access_token_enc"] != "plain-token"
        assert row["access_token_enc"].startswith("gAAAAA")  # Fernet 密文

    def test_upsert_overwrites(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid", "token-old", "oid")
        save_credentials("t1", "u1", "cid", "token-new", "oid")
        assert get_credentials("t1", "u1").access_token == "token-new"

    def test_not_bound_raises(self, fake_db, secret_key):
        with pytest.raises(CredentialsNotBoundError):
            get_credentials("t1", "nobody")


@pytest.mark.unit
class TestStatusGate:
    def test_expired_raises(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid", "token", "oid")
        fake_db.mark_expired("t1", "u1")
        with pytest.raises(CredentialsNotBoundError):
            get_credentials("t1", "u1")

    def test_rebind_restores_active(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid", "token", "oid")
        fake_db.mark_expired("t1", "u1")
        save_credentials("t1", "u1", "cid", "token2", "oid")
        assert get_credentials("t1", "u1").access_token == "token2"

    def test_corrupt_ciphertext_raises_rebind(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid", "token", "oid")
        fake_db.store[("t1", "u1")]["access_token_enc"] = "not-a-fernet-token"
        with pytest.raises(CredentialsNotBoundError):
            get_credentials("t1", "u1")


@pytest.mark.unit
class TestStatusView:
    def test_unbound(self, fake_db, secret_key):
        st = get_status("t1", "u1")
        assert st == {"bound": False, "status": "none"}

    def test_masked_no_token_leak(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid-12345678", "secret-token-value", "oid-87654321")
        st = get_status("t1", "u1")
        assert st["bound"] is True
        assert st["client_id_masked"] == "***5678"
        assert st["open_id_masked"] == "***4321"
        # 任何字段都不泄漏 token 明文/密文
        flat = str(st)
        assert "secret-token-value" not in flat
        assert "gAAAAA" not in flat


@pytest.mark.unit
class TestDelete:
    def test_delete(self, fake_db, secret_key):
        save_credentials("t1", "u1", "cid", "token", "oid")
        assert delete_credentials("t1", "u1") is True
        with pytest.raises(CredentialsNotBoundError):
            get_credentials("t1", "u1")
        assert delete_credentials("t1", "u1") is False
