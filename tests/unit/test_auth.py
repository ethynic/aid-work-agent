"""
认证和会话用户身份测试
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from fastapi import Request


def _mock_db_with_row(row):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = row
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


class TestAuthModule:
    """测试 auth 模块基本功能"""

    @patch("src.services.auth_service.get_cached", return_value=None)
    @patch("src.services.auth_service.get_db_connection")
    def test_verify_invalid_token(self, mock_db, mock_cache):
        """验证无效 token 返回 None"""
        mock_conn = _mock_db_with_row(None)
        mock_db.return_value = mock_conn

        from src.api import auth
        result = auth.verify_token("invalid_token")
        assert result is None
        mock_db.assert_called_once()
        mock_conn.cursor.return_value.execute.assert_called_once()
        assert mock_conn.cursor.return_value.execute.call_args.args[1] == ("invalid_token",)

    def test_get_current_user_no_header(self):
        """无 Authorization header 返回 None"""
        from src.api import auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {}
        result = auth.get_current_user(mock_request)
        assert result is None


class TestVerifyTokenSuspendedUser:
    """verify_token DB 兜底路径联查 users.status：停用用户立即拒绝且不回写缓存"""

    @patch("src.services.auth_service.set_cached")
    @patch("src.services.auth_service.delete_cached")
    @patch("src.services.auth_service.get_cached", return_value=None)
    @patch("src.services.auth_service.get_db_connection")
    def test_suspended_user_rejected(self, mock_db, mock_cache, mock_del, mock_set):
        from src.services import auth_service

        mock_db.return_value = _mock_db_with_row({
            "user_id": "u1",
            "expires_at": datetime.now() + timedelta(days=1),
            "user_status": "suspended",
        })
        result = auth_service.verify_token("tok_suspended")
        assert result is None
        # 兜底查询必须联查 users.status（SQL 单独回退时此处先失败）
        sql = mock_db.return_value.cursor.return_value.execute.call_args.args[0]
        assert "LEFT JOIN users" in sql and "u.status" in sql
        mock_del.assert_called_once()
        mock_set.assert_not_called()

    @patch("src.services.auth_service.set_cached")
    @patch("src.services.auth_service.get_cached", return_value=None)
    @patch("src.services.auth_service.get_db_connection")
    def test_active_user_passes(self, mock_db, mock_cache, mock_set):
        from src.services import auth_service

        # 剩余 10 天：不触发滑动续期分支，验证状态放行 + 正常回写缓存
        mock_db.return_value = _mock_db_with_row({
            "user_id": "u1",
            "expires_at": datetime.now() + timedelta(days=10),
            "user_status": "active",
        })
        result = auth_service.verify_token("tok_active")
        assert result == "u1"
        mock_set.assert_called_once()

    @patch("src.services.auth_service.set_cached")
    @patch("src.services.auth_service.get_cached", return_value=None)
    @patch("src.services.auth_service.get_db_connection")
    def test_null_user_status_passes(self, mock_db, mock_cache, mock_set):
        """users 表查无对应行（LEFT JOIN 为 NULL）保持放行，不破坏既有行为"""
        from src.services import auth_service

        mock_db.return_value = _mock_db_with_row({
            "user_id": "u_ghost",
            "expires_at": datetime.now() + timedelta(days=10),
            "user_status": None,
        })
        result = auth_service.verify_token("tok_orphan")
        assert result == "u_ghost"


class TestVerifyQbToken:
    """_verify_qb_token：qb_token_hash 优先 bcrypt 校验，明文仅作未配置哈希时的兜底"""

    @patch("src.services.auth_service.settings")
    def test_hash_configured_takes_priority(self, mock_settings):
        """配置了 qb_token_hash 时走 bcrypt 校验，明文 qb_token 即使匹配也不参与"""
        from src.db.models import hash_password
        from src.services import auth_service

        mock_settings.qb_token_hash = hash_password("correct-pass")
        mock_settings.qb_token = "correct-pass"

        assert auth_service._verify_qb_token("correct-pass") is True
        assert auth_service._verify_qb_token("wrong-pass") is False

    @patch("src.services.auth_service.settings")
    def test_plaintext_fallback_without_hash(self, mock_settings):
        """未配置 qb_token_hash 时回退明文比较"""
        from src.services import auth_service

        mock_settings.qb_token_hash = ""
        mock_settings.qb_token = "legacy-token"

        assert auth_service._verify_qb_token("legacy-token") is True
        assert auth_service._verify_qb_token("other") is False

    @patch("src.services.auth_service.settings")
    def test_no_config_returns_false(self, mock_settings):
        """两者都未配置时拒绝（不因空串比较误放行）"""
        from src.services import auth_service

        mock_settings.qb_token_hash = ""
        mock_settings.qb_token = ""

        assert auth_service._verify_qb_token("") is False
        assert auth_service._verify_qb_token("anything") is False
