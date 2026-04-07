"""
认证和会话用户身份测试
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi import Request


class TestAuthModule:
    """测试 auth 模块基本功能"""

    def test_auth_module_imports(self):
        """验证 auth 模块可导入"""
        from src.api import auth
        assert hasattr(auth, "verify_token")
        assert hasattr(auth, "get_current_user")

    @patch("src.api.auth.get_db_connection")
    def test_verify_invalid_token(self, mock_db):
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

    def test_get_current_user_no_header(self):
        """无 Authorization header 返回 None"""
        from src.api import auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {}
        result = auth.get_current_user(mock_request)
        assert result is None
