"""下载票据（HMAC 短期签名）单元测试"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from src.core.download_ticket import (
    _b64url,
    _secret,
    issue_download_ticket,
    payload_is_admin,
    validate_download_ticket,
)


@pytest.fixture(autouse=True)
def _stable_secret(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1/aid_work_agent_test")


class TestDownloadTicket:
    def test_issue_validate_roundtrip(self):
        ticket = issue_download_ticket(
            "/api/knowledge/documents/3587/download", "tenant_d18c257ff434", "user_abc", "user"
        )
        assert ticket.count(".") == 1
        assert validate_download_ticket(ticket, "/api/knowledge/documents/3587/download") == {
            "tenant_id": "tenant_d18c257ff434",
            "user_id": "user_abc",
            "role": "user",
        }

    def test_wrong_path_rejected(self):
        ticket = issue_download_ticket("/api/v1/travel-quote/vehicles/export", "tenant_a", "user_a")
        # 挪用其他下载路径的票据（如改下载别的导出表）应被拒绝
        assert validate_download_ticket(ticket, "/api/v1/travel-quote/meals/export") is None

    def test_garbage_and_empty_rejected(self):
        assert validate_download_ticket("", "/api/x") is None
        assert validate_download_ticket("no-dot", "/api/x") is None
        assert validate_download_ticket("a.b", "/api/x") is None
        assert validate_download_ticket("###.###", "/api/x") is None

    def test_expired_ticket_rejected(self):
        path = "/api/knowledge/documents/1/download"
        body = _b64url(json.dumps({"r": path, "t": "tenant_a", "u": "user_a", "role": "user", "e": int(time.time()) - 10}).encode())
        sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        assert validate_download_ticket(f"{body}.{sig}", path) is None

    def test_tampered_payload_rejected(self):
        path = "/api/knowledge/documents/1/download"
        ticket = issue_download_ticket(path, "tenant_a", "user_a")
        body, sig = ticket.rsplit(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload["t"] = "tenant_b"  # 篡改租户，试图越权
        tampered = _b64url(json.dumps(payload).encode()) + "." + sig
        assert validate_download_ticket(tampered, path) is None

    def test_optional_fields_default_none(self):
        ticket = issue_download_ticket("/api/saas/tenant/config-file/foo")
        assert validate_download_ticket(ticket, "/api/saas/tenant/config-file/foo") == {
            "tenant_id": None,
            "user_id": None,
            "role": None,
        }

    def test_secret_prefers_env_var(self, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_TICKET_SECRET", "custom-secret")
        assert _secret() == hashlib.sha256(b"custom-secret").digest()

    def test_payload_is_admin(self):
        assert payload_is_admin({"role": "platform_admin"})
        assert payload_is_admin({"role": "tenant_admin"})
        assert not payload_is_admin({"role": "user"})
        assert not payload_is_admin(None)
        assert not payload_is_admin({})
