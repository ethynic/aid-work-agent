"""SIMULATION_MODE 仿真环境门控单测

覆盖 src/core/simulation.py 的判定逻辑与 SMTP dry-run 行为。
"""

from types import SimpleNamespace

import pytest

from src.core.simulation import (
    _is_tenant_attachment_path,
    is_simulation_mode,
    skip_disk_delete,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def sim_mode(monkeypatch):
    from src.config.settings import settings

    monkeypatch.setattr(settings.app, "simulation_mode", True, raising=False)
    yield
    monkeypatch.setattr(settings.app, "simulation_mode", False, raising=False)


class TestIsSimulationMode:
    def test_default_off(self):
        assert is_simulation_mode() is False

    def test_enabled(self, sim_mode):
        assert is_simulation_mode() is True


class TestTenantAttachmentPath:
    def test_tenants_root(self):
        assert _is_tenant_attachment_path("storage/tenants/t1/conversation/a.png") is True

    def test_uploads_root(self):
        assert _is_tenant_attachment_path("uploads/file_abc.docx") is True

    def test_tmp_not_matched(self):
        assert _is_tenant_attachment_path("/tmp/workdir/x.json") is False

    def test_log_not_matched(self):
        assert _is_tenant_attachment_path("log/agent/app.log") is False

    def test_prefix_trick_rejected(self):
        # storage/tenants-evil 不应被前缀误判
        assert _is_tenant_attachment_path("storage/tenants-evil/a.png") is False


class TestSkipDiskDelete:
    def test_production_never_skips(self):
        assert skip_disk_delete("storage/tenants/t1/a.png", context="t") is False

    def test_sim_skips_tenant_attachment(self, sim_mode):
        assert skip_disk_delete("storage/tenants/t1/conversation/a.png", context="t") is True
        assert skip_disk_delete("uploads/file_abc.docx", context="t") is True

    def test_sim_allows_tmp_cleanup(self, sim_mode):
        # 临时文件不拦截，避免磁盘泄漏
        assert skip_disk_delete("/tmp/skill_work/x.json", context="t") is False


class TestEmailDryRun:
    def _user_email(self):
        return SimpleNamespace(
            email_address="bot@corp.com",
            smtp_server="smtp.corp.com",
            smtp_port=465,
            smtp_user="bot@corp.com",
            smtp_password="secret",
            smtp_encryption="ssl",
        )

    @pytest.mark.asyncio
    async def test_dry_run_returns_without_smtp(self, sim_mode, monkeypatch):
        from src.tools.email import email_lib

        connected = {"ssl": False}

        class _FakeSMTP:
            def __init__(self, *a, **k):
                connected["ssl"] = True

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(email_lib.smtplib, "SMTP_SSL", _FakeSMTP)
        result = email_lib.send_email(
            self._user_email(), "a@b.com", "主题", "正文"
        )
        assert result["dry_run"] is True
        assert result["to"] == "a@b.com"
        assert connected["ssl"] is False  # 未建立连接

    def test_dry_run_invalid_recipient_still_raises(self, sim_mode):
        from src.tools.email.email_lib import EmailLibError, send_email

        with pytest.raises(EmailLibError):
            send_email(self._user_email(), " , ", "主题", "正文")

    def test_production_sends_normally(self, monkeypatch):
        from src.tools.email import email_lib

        sent = {"ok": False}

        class _FakeSMTP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, *a):
                pass

            def sendmail(self, *a):
                sent["ok"] = True

        monkeypatch.setattr(email_lib.smtplib, "SMTP_SSL", _FakeSMTP)
        result = email_lib.send_email(self._user_email(), "a@b.com", "主题", "正文")
        assert sent["ok"] is True
        assert "dry_run" not in result
