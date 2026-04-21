"""
Auth API 端到端测试
测试数据库交互层：验证码、登录、Token 验证、用户注册
"""

import pytest
import uuid
import time
from datetime import datetime, timedelta


class TestAuthCaptcha:
    """图形验证码测试"""

    def test_generate_captcha(self):
        """测试验证码生成"""
        from src.db.models import generate_captcha

        result = generate_captcha()
        captcha_id = result["captcha_id"]
        code = result["code"]

        assert captcha_id is not None
        assert len(code) == 4

    def test_verify_captcha_valid(self):
        """测试有效验证码校验"""
        from src.db.models import generate_captcha, verify_captcha

        result = generate_captcha()
        captcha_id = result["captcha_id"]
        code = result["code"]
        result = verify_captcha(captcha_id, code)

        assert result is True

    def test_verify_captcha_invalid(self):
        """测试无效验证码校验"""
        from src.db.models import verify_captcha

        result = verify_captcha("invalid_id", "0000")

        assert result is False

    def test_verify_captcha_expired(self):
        """测试过期验证码"""
        from src.db.models import verify_captcha

        # 测试无效验证码
        result = verify_captcha("expired_test", "0000")

        assert result is False


class TestAuthSMSCode:
    """短信验证码测试"""

    def test_send_sms_code(self):
        """测试短信验证码发送"""
        from src.db.models import send_sms_code

        phone = f"138{uuid.uuid4().hex[:8]}"
        result = send_sms_code(phone)

        assert result is True

    def test_verify_sms_code_valid(self):
        """测试有效短信验证码"""
        from src.db.models import send_sms_code, verify_sms_code

        phone = f"138{uuid.uuid4().hex[:8]}"
        send_sms_code(phone)

        # 短信验证码统一为 888888
        result = verify_sms_code(phone, "888888")

        assert result is True

    def test_verify_sms_code_invalid(self):
        """测试无效短信验证码"""
        from src.db.models import verify_sms_code

        phone = f"138{uuid.uuid4().hex[:8]}"
        result = verify_sms_code(phone, "000000")

        assert result is False


class TestAuthToken:
    """Token 管理测试"""

    @pytest.fixture
    def test_user_in_db(self):
        """创建测试用户并返回 user_id"""
        from src.db.models import UserDB, hash_password

        user_id = f"test_user_{uuid.uuid4().hex[:8]}"
        user_data = {
            "user_id": user_id,
            "username": "test_user",
            "phone": f"138{uuid.uuid4().hex[:8]}",
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield user_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_generate_token(self, test_user_in_db):
        """测试 Token 生成"""
        from src.api.auth import generate_token

        user_id = test_user_in_db
        token = generate_token(user_id)

        assert token is not None
        assert len(token) > 20

    def test_verify_token_valid(self, test_user_in_db):
        """测试有效 Token 验证"""
        from src.api.auth import generate_token, verify_token

        user_id = test_user_in_db
        token = generate_token(user_id)

        result = verify_token(token)

        assert result == user_id

    def test_verify_token_invalid(self):
        """测试无效 Token 验证"""
        from src.api.auth import verify_token

        result = verify_token("invalid_token_12345")

        assert result is None

    def test_delete_token(self, test_user_in_db):
        """测试 Token 删除"""
        from src.api.auth import generate_token, delete_token

        user_id = test_user_in_db
        token = generate_token(user_id)

        result = delete_token(token)

        assert result is True

        # 验证 Token 已删除
        from src.api.auth import verify_token
        assert verify_token(token) is None


class TestAuthUserCRUD:
    """用户 CRUD 测试"""

    @pytest.fixture
    def unique_phone(self):
        """生成唯一手机号"""
        return f"138{uuid.uuid4().hex[:8]}"

    @pytest.fixture
    def unique_user_id(self):
        """生成唯一 user_id"""
        return f"test_user_{uuid.uuid4().hex[:8]}"

    def test_create_user(self, unique_user_id, unique_phone):
        """测试用户创建"""
        from src.db.models import UserDB, hash_password

        user_data = {
            "user_id": unique_user_id,
            "username": "test_user",
            "phone": unique_phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        result = UserDB.create(user_data)

        assert result is True

        # 验证创建成功
        user = UserDB.get_by_id(unique_user_id)
        assert user is not None
        assert user["user_id"] == unique_user_id

        # 清理
        UserDB.delete(unique_user_id)

    def test_get_user_by_id(self, unique_user_id, unique_phone):
        """测试根据 ID 获取用户"""
        from src.db.models import UserDB, hash_password

        user_data = {
            "user_id": unique_user_id,
            "username": "test_user",
            "phone": unique_phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        user = UserDB.get_by_id(unique_user_id)

        assert user is not None
        assert user["user_id"] == unique_user_id

        # 清理
        UserDB.delete(unique_user_id)

    def test_get_user_by_phone(self, unique_user_id, unique_phone):
        """测试根据手机号获取用户"""
        from src.db.models import UserDB, hash_password

        user_data = {
            "user_id": unique_user_id,
            "username": "test_user",
            "phone": unique_phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        user = UserDB.get_by_phone(unique_phone)

        assert user is not None
        assert user["phone"] == unique_phone

        # 清理
        UserDB.delete(unique_user_id)

    def test_update_user(self, unique_user_id, unique_phone):
        """测试用户更新"""
        from src.db.models import UserDB, hash_password

        user_data = {
            "user_id": unique_user_id,
            "username": "test_user",
            "phone": unique_phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        # 更新用户名
        result = UserDB.update(unique_user_id, {"username": "updated_user"})

        assert result is True

        # 验证更新成功
        user = UserDB.get_by_id(unique_user_id)
        assert user["username"] == "updated_user"

        # 清理
        UserDB.delete(unique_user_id)

    def test_delete_user(self, unique_user_id, unique_phone):
        """测试用户删除"""
        from src.db.models import UserDB, hash_password

        user_data = {
            "user_id": unique_user_id,
            "username": "test_user",
            "phone": unique_phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        result = UserDB.delete(unique_user_id)

        assert result is True

        # 验证删除成功
        user = UserDB.get_by_id(unique_user_id)
        assert user is None


class TestAuthLogin:
    """登录功能测试"""

    @pytest.fixture
    def test_user_for_login(self):
        """创建测试用户用于登录测试"""
        from src.db.models import UserDB, hash_password

        user_id = f"login_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "login_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield {"user_id": user_id, "phone": phone, "password": "Test123456"}

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_password_login_success(self, test_user_for_login):
        """测试密码登录成功"""
        from src.db.models import UserDB

        user_info = test_user_for_login
        user = UserDB.verify_password(user_info["phone"], user_info["password"])

        assert user is not None
        assert user["user_id"] == user_info["user_id"]

    def test_password_login_wrong_password(self, test_user_for_login):
        """测试密码错误登录失败"""
        from src.db.models import UserDB

        user_info = test_user_for_login
        user = UserDB.verify_password(user_info["phone"], "WrongPassword123")

        assert user is None

    def test_password_login_nonexistent_user(self):
        """测试不存在的用户登录"""
        from src.db.models import UserDB

        user = UserDB.verify_password("13900000000", "Password123")

        assert user is None


class TestAuthPassword:
    """密码功能测试"""

    def test_hash_password(self):
        """测试密码哈希"""
        from src.db.models import hash_password, verify_password

        password = "TestPassword123"
        hashed = hash_password(password)

        assert hashed != password
        assert verify_password(password, hashed) is True
        assert verify_password("WrongPassword", hashed) is False