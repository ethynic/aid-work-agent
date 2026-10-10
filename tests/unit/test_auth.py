"""
认证和会话用户身份测试
"""

from unittest.mock import MagicMock, patch
from fastapi import Request


class TestAuthModule:
    """测试 auth 模块基本功能"""

    @patch("src.services.auth_service.get_cached", return_value=None)
    @patch("src.services.auth_service.get_db_connection")
    def test_verify_invalid_token(self, mock_db, mock_cache):
        """验证无效 token 返回 None"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.return_value = mock_conn

        from src.api import auth
        result = auth.verify_token("invalid_token")
        assert result is None
        mock_db.assert_called_once()
        mock_cursor.execute.assert_called_once()
        assert mock_cursor.execute.call_args.args[1] == ("invalid_token",)

    def test_get_current_user_no_header(self):
        """无 Authorization header 返回 None"""
        from src.api import auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {}
        result = auth.get_current_user(mock_request)
        assert result is None
