"""用户邮箱设置 API 测试（不连数据库，mock EmailCredentialDB 与邮件工具）

覆盖：查询返回 updated_at（ISO 字符串）与密码掩码、未绑定查询、
保存成功 upsert 9 字段、测试邮件发送失败透传 error_code。
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.api


@pytest.fixture(scope="session", autouse=True)
def init_db_pool():
    """本文件不连数据库：覆盖 conftest 的会话级 DB 初始化"""
    yield


from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.email_settings import router
from src.api.auth import get_current_user


MASKED_CONFIG = {
    "email_address": "me@example.com",
    "smtp_server": "smtp.example.com",
    "smtp_port": 465,
    "smtp_user": "me@example.com",
    "smtp_password": "****",
    "smtp_encryption": "ssl",
    "imap_server": "imap.example.com",
    "imap_port": 993,
    "imap_encryption": "ssl",
    "updated_at": datetime(2026, 11, 1, 12, 30, 45),
}


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def client(mock_db):
    """只挂载 email_settings 路由，覆盖登录依赖，mock 模块级 EmailCredentialDB"""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "user_abc"}
    with patch("src.api.email_settings.EmailCredentialDB", mock_db):
        yield TestClient(app)


def _save_payload():
    return {
        "email_address": "me@example.com",
        "smtp_server": "smtp.example.com",
        "smtp_port": 465,
        "smtp_user": "me@example.com",
        "smtp_password": "app-password-123",
        "smtp_encryption": "ssl",
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "imap_encryption": "ssl",
    }


class TestGetEmailSettings:
    """GET /api/email-settings"""

    def test_get_with_config_returns_updated_at_and_mask(self, client, mock_db):
        mock_db.get_masked_by_user.return_value = dict(MASKED_CONFIG)
        resp = client.get("/api/email-settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["bound"] is True
        assert body["data"]["updated_at"] == "2026-11-01T12:30:45"
        assert body["data"]["smtp_password"] == "****"
        mock_db.get_masked_by_user.assert_called_once_with("user_abc")

    def test_get_without_config_unbound(self, client, mock_db):
        mock_db.get_masked_by_user.return_value = None
        resp = client.get("/api/email-settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["bound"] is False
        assert body["data"] is None


class TestSaveEmailSettings:
    """POST /api/email-settings"""

    def test_save_success_upserts_nine_fields(self, client, mock_db):
        mock_db.upsert.return_value = True
        with patch("src.tools.email.email_tool.EmailProcessTool") as mock_tool_cls:
            mock_tool_cls.return_value.execute = AsyncMock(
                return_value={"success": True, "message": "ok"}
            )
            resp = client.post("/api/email-settings", json=_save_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        mock_db.upsert.assert_called_once()
        upsert_user_id, upsert_config = mock_db.upsert.call_args.args
        assert upsert_user_id == "user_abc"
        assert upsert_config == _save_payload()

        # 测试邮件发给自己
        mock_tool_cls.return_value.execute.assert_awaited_once()
        exec_kwargs = mock_tool_cls.return_value.execute.call_args.kwargs
        assert exec_kwargs["action"] == "send"
        assert exec_kwargs["to"] == "me@example.com"

    def test_save_failure_passes_error_code(self, client, mock_db):
        with patch("src.tools.email.email_tool.EmailProcessTool") as mock_tool_cls:
            mock_tool_cls.return_value.execute = AsyncMock(return_value={
                "success": False,
                "error": "邮箱账号或密码（授权码）错误，请检查邮箱配置",
                "error_code": "auth",
            })
            resp = client.post("/api/email-settings", json=_save_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "测试邮件发送失败" in body["error"]
        assert body["error_code"] == "auth"
        mock_db.upsert.assert_not_called()

    def test_save_failure_without_error_code_returns_none(self, client, mock_db):
        with patch("src.tools.email.email_tool.EmailProcessTool") as mock_tool_cls:
            mock_tool_cls.return_value.execute = AsyncMock(return_value={
                "success": False,
                "error": "未知错误",
            })
            resp = client.post("/api/email-settings", json=_save_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "测试邮件发送失败" in body["error"]
        assert body["error_code"] is None
        mock_db.upsert.assert_not_called()
