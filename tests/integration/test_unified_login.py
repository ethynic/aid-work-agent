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

    @pytest.mark.asyncio
    async def test_unified_login_platform_admin_existing_user(self):
        """测试平台管理员登录成功（用户已存在）

        平台管理员：手机号在 admin.phones 且密码等于 QBTOKEN
        - 跳过租户存在性、状态、归属校验
        - 登录后 redirect_url 为 /portal
        """
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        admin_user = {
            "user_id": "user_admin_001",
            "username": "13800000001",
            "phone": "13800000001",
            "password_hash": None,
            "tenant_id": None,
            "role": "platform_admin",
            "avatar_url": None,
        }

        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        from src.config.settings import settings as real_settings

        with patch("src.api.auth.verify_captcha") as mock_verify_captcha, \
             patch.object(real_settings, "admin", MagicMock(phones=["13800000001"])), \
             patch("src.api.auth._verify_qb_token") as mock_verify_qb, \
             patch("src.api.auth.UserDB") as mock_user_db, \
             patch("src.api.auth.generate_token") as mock_generate_token:

            mock_verify_captcha.return_value = True
            mock_verify_qb.return_value = True
            mock_user_db.get_by_phone.return_value = admin_user
            mock_generate_token.return_value = "admin_token_001"

            login_request = UnifiedLoginRequest(
                tenant_code="ANYCODE",
                identifier="13800000001",
                password="qb_token_xxx",
                captcha_code="1234",
                captcha_id="captcha_123"
            )

            response = await unified_login(mock_request, login_request)

            # 平台管理员登录成功
            assert response.success is True
            assert response.token == "admin_token_001"
            assert response.redirect_url == "/portal"
            assert response.tenant_id is None
            assert response.user["is_admin"] is True
            assert response.user["role"] == "platform_admin"

            # 不应触发租户查询
            mock_user_db.get_by_phone.assert_called_once_with("13800000001", bypass_cache=True)

    @pytest.mark.asyncio
    async def test_unified_login_platform_admin_auto_create(self):
        """测试平台管理员用户不存在时自动创建"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        new_admin_user = {
            "user_id": "user_admin_new",
            "username": "13800000002",
            "phone": "13800000002",
            "password_hash": None,
            "tenant_id": None,
            "role": "platform_admin",
            "avatar_url": None,
        }

        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        from src.config.settings import settings as real_settings

        with patch("src.api.auth.verify_captcha") as mock_verify_captcha, \
             patch.object(real_settings, "admin", MagicMock(phones=["13800000002"])), \
             patch("src.api.auth._verify_qb_token") as mock_verify_qb, \
             patch("src.api.auth.UserDB") as mock_user_db, \
             patch("src.api.auth.get_db_connection") as mock_get_conn, \
             patch("src.api.auth.generate_token") as mock_generate_token, \
             patch("src.api.auth.uuid") as mock_uuid:

            mock_verify_captcha.return_value = True
            # 用户首次不存在
            mock_user_db.get_by_phone.return_value = None
            mock_uuid.uuid4.return_value = "fixed-uuid-1234"
            mock_verify_qb.return_value = True
            mock_generate_token.return_value = "admin_token_new"

            # 模拟数据库 cursor
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.__enter__.return_value = mock_conn
            mock_conn.__exit__.return_value = False
            mock_get_conn.return_value = mock_conn
            mock_cursor.fetchone.return_value = new_admin_user

            login_request = UnifiedLoginRequest(
                tenant_code="ANYCODE",
                identifier="13800000002",
                password="qb_token_xxx",
                captcha_code="1234",
                captcha_id="captcha_123"
            )

            response = await unified_login(mock_request, login_request)

            # 自动创建后登录成功
            assert response.success is True
            assert response.token == "admin_token_new"
            assert response.redirect_url == "/portal"
            assert response.user["role"] == "platform_admin"

            # 应执行 INSERT
            assert mock_cursor.execute.call_count >= 2
            first_call_sql = mock_cursor.execute.call_args_list[0][0][0]
            assert "INSERT INTO users" in first_call_sql

    @pytest.mark.asyncio
    async def test_unified_login_platform_admin_skips_tenant_check(self):
        """测试平台管理员跳过租户校验：tenant_code 可以不存在"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest
        from src.saas.db.tenant_db import TenantDB

        admin_user = {
            "user_id": "user_admin_003",
            "username": "13800000003",
            "phone": "13800000003",
            "password_hash": None,
            "tenant_id": None,
            "role": "platform_admin",
            "avatar_url": None,
        }

        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        from src.config.settings import settings as real_settings

        with patch("src.api.auth.verify_captcha") as mock_verify_captcha, \
             patch.object(real_settings, "admin", MagicMock(phones=["13800000003"])), \
             patch("src.api.auth._verify_qb_token") as mock_verify_qb, \
             patch("src.api.auth.UserDB") as mock_user_db, \
             patch.object(TenantDB, "get_by_code") as mock_get_by_code, \
             patch("src.api.auth.generate_token") as mock_generate_token:

            mock_verify_captcha.return_value = True
            mock_verify_qb.return_value = True
            mock_user_db.get_by_phone.return_value = admin_user
            mock_generate_token.return_value = "admin_token_003"

            login_request = UnifiedLoginRequest(
                tenant_code="NOTEXS",
                identifier="13800000003",
                password="qb_token_xxx",
                captcha_code="1234",
                captcha_id="captcha_123"
            )

            response = await unified_login(mock_request, login_request)

            # 即使租户不存在，平台管理员也能登录
            assert response.success is True
            assert response.redirect_url == "/portal"
            # TenantDB.get_by_code 不应被调用
            mock_get_by_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_unified_login_platform_admin_wrong_password(self):
        """测试平台管理员密码错误时回退到普通用户流程"""
        from src.api.auth import unified_login
        from fastapi import Request
        from src.api.auth import UnifiedLoginRequest

        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"

        from src.config.settings import settings as real_settings

        # 平台管理员手机号但密码不是 QBTOKEN
        with patch("src.api.auth.verify_captcha") as mock_verify_captcha, \
             patch.object(real_settings, "admin", MagicMock(phones=["13800000004"])), \
             patch("src.api.auth._verify_qb_token") as mock_verify_qb, \
             patch("src.saas.db.tenant_db.TenantDB") as mock_tenant_db:

            mock_verify_captcha.return_value = True
            # QBTOKEN 校验失败
            mock_verify_qb.return_value = False
            # 租户不存在
            mock_tenant_db.get_by_code.return_value = None

            login_request = UnifiedLoginRequest(
                tenant_code="TEST01",
                identifier="13800000004",
                password="wrong_password",
                captcha_code="1234",
                captcha_id="captcha_123"
            )

            response = await unified_login(mock_request, login_request)

            # 不是平台管理员，走普通用户流程，租户不存在报错
            assert response.success is False
            assert response.errors is not None
            assert any(e["field"] == "tenant_code" for e in response.errors)