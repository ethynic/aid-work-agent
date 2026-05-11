"""
统一登录API测试
"""

import pytest
import uuid
from unittest.mock import MagicMock, patch


class TestUnifiedLoginAPI:
    """统一登录API测试"""

    @pytest.fixture
    def mock_tenant(self):
        """模拟租户数据"""
        return {
            "tenant_id": "tenant_test123",
            "tenant_code": "TEST01",
            "company_name": "测试公司",
            "status": "active",
            "expire_at": None
        }

    @pytest.fixture
    def mock_user(self):
        """模拟用户数据"""
        return {
            "user_id": "user_test123",
            "username": "testuser",
            "phone": "13800138000",
            "password_hash": "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",  # "password123"
            "tenant_id": "tenant_test123",
            "role": "user"
        }

    def test_unified_login_success(self, mock_tenant, mock_user):
        """测试统一登录成功"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        # 模拟请求
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        # 模拟验证码验证
        with patch("src.api.auth.verify_captcha") as mock_verify_captcha:
            mock_verify_captcha.return_value = True

            # 模拟租户查询
            with patch("src.api.auth.TenantDB") as mock_tenant_db:
                mock_tenant_db.get_by_code.return_value = mock_tenant

                # 模拟用户查询
                with patch("src.api.auth.UserDB") as mock_user_db:
                    mock_user_db.get_by_phone.return_value = mock_user

                    # 模拟密码验证
                    with patch("src.api.auth.verify_password") as mock_verify_password:
                        mock_verify_password.return_value = True

                        # 模拟token生成
                        with patch("src.api.auth.generate_token") as mock_generate_token:
                            mock_generate_token.return_value = "test_token_123"

                            # 创建请求
                            login_request = UnifiedLoginRequest(
                                tenant_code="TEST01",
                                identifier="13800138000",
                                password="password123",
                                captcha_code="1234",
                                captcha_id="captcha_123"
                            )

                            # 调用API
                            response = unified_login(mock_request, login_request)

                            # 验证响应
                            assert response.success is True
                            assert response.token == "test_token_123"
                            assert response.tenant_id == "tenant_test123"
                            assert response.redirect_url == "/t/tenant_test123"
                            assert response.errors is None

    def test_unified_login_invalid_tenant_code(self):
        """测试无效租户代码"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        # 模拟请求
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        # 模拟验证码验证
        with patch("src.api.auth.verify_captcha") as mock_verify_captcha:
            mock_verify_captcha.return_value = True

            # 模拟租户查询返回None
            with patch("src.api.auth.TenantDB") as mock_tenant_db:
                mock_tenant_db.get_by_code.return_value = None

                # 创建请求
                login_request = UnifiedLoginRequest(
                    tenant_code="INVALID",
                    identifier="13800138000",
                    password="password123",
                    captcha_code="1234",
                    captcha_id="captcha_123"
                )

                # 调用API
                response = unified_login(mock_request, login_request)

                # 验证响应
                assert response.success is False
                assert response.errors is not None
                assert len(response.errors) == 1
                assert response.errors[0]["field"] == "tenant_code"
                assert "不存在" in response.errors[0]["message"]

    def test_unified_login_invalid_captcha(self):
        """测试无效验证码"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        # 模拟请求
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        # 模拟验证码验证失败
        with patch("src.api.auth.verify_captcha") as mock_verify_captcha:
            mock_verify_captcha.return_value = False

            # 创建请求
            login_request = UnifiedLoginRequest(
                tenant_code="TEST01",
                identifier="13800138000",
                password="password123",
                captcha_code="wrong",
                captcha_id="captcha_123"
            )

            # 调用API
            response = unified_login(mock_request, login_request)

            # 验证响应
            assert response.success is False
            assert response.errors is not None
            assert len(response.errors) == 1
            assert response.errors[0]["field"] == "captcha_code"
            assert "验证码" in response.errors[0]["message"]

    def test_unified_login_user_not_belong_to_tenant(self, mock_tenant, mock_user):
        """测试用户不属于租户"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        # 修改用户租户ID，使其不属于模拟租户
        mock_user_wrong_tenant = mock_user.copy()
        mock_user_wrong_tenant["tenant_id"] = "other_tenant"

        # 模拟请求
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        # 模拟验证码验证
        with patch("src.api.auth.verify_captcha") as mock_verify_captcha:
            mock_verify_captcha.return_value = True

            # 模拟租户查询
            with patch("src.api.auth.TenantDB") as mock_tenant_db:
                mock_tenant_db.get_by_code.return_value = mock_tenant

                # 模拟用户查询
                with patch("src.api.auth.UserDB") as mock_user_db:
                    mock_user_db.get_by_phone.return_value = mock_user_wrong_tenant

                    # 模拟密码验证
                    with patch("src.api.auth.verify_password") as mock_verify_password:
                        mock_verify_password.return_value = True

                        # 创建请求
                        login_request = UnifiedLoginRequest(
                            tenant_code="TEST01",
                            identifier="13800138000",
                            password="password123",
                            captcha_code="1234",
                            captcha_id="captcha_123"
                        )

                        # 调用API
                        response = unified_login(mock_request, login_request)

                        # 验证响应
                        assert response.success is False
                        assert response.errors is not None
                        assert len(response.errors) == 1
                        assert response.errors[0]["field"] == "identifier"
                        assert "不属于" in response.errors[0]["message"]

    def test_unified_login_wrong_password(self, mock_tenant, mock_user):
        """测试错误密码"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        # 模拟请求
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        # 模拟验证码验证
        with patch("src.api.auth.verify_captcha") as mock_verify_captcha:
            mock_verify_captcha.return_value = True

            # 模拟租户查询
            with patch("src.api.auth.TenantDB") as mock_tenant_db:
                mock_tenant_db.get_by_code.return_value = mock_tenant

                # 模拟用户查询
                with patch("src.api.auth.UserDB") as mock_user_db:
                    mock_user_db.get_by_phone.return_value = mock_user

                    # 模拟密码验证失败
                    with patch("src.api.auth.verify_password") as mock_verify_password:
                        mock_verify_password.return_value = False

                        # 创建请求
                        login_request = UnifiedLoginRequest(
                            tenant_code="TEST01",
                            identifier="13800138000",
                            password="wrongpassword",
                            captcha_code="1234",
                            captcha_id="captcha_123"
                        )

                        # 调用API
                        response = unified_login(mock_request, login_request)

                        # 验证响应
                        assert response.success is False
                        assert response.errors is not None
                        assert len(response.errors) == 1
                        assert response.errors[0]["field"] == "password"
                        assert "密码" in response.errors[0]["message"]