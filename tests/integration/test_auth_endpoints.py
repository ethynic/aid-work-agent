"""
Auth API 端到端测试
测试数据库交互层：验证码、登录、Token 验证、用户注册

对齐当前源码行为（2026-09）：
- generate_captcha() 返回 {captcha_id, svg_base64}，不再返回明文 code（安全加固）
- 短信验证码固定码 888888 已随 demo 模式移除删除，有效码用例改为
  mock 短信通道发送后从 DB 读真实验证码校验
- UserDB.create(phone, password, ...) 逐参数签名，user_id 由内部生成，
  返回 user dict；UserDB 不提供 delete/update（更新走 update_info）
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest


def _hard_delete_user(user_id: str):
    """测试清理：UserDB 不提供 delete API，直接 SQL 删除用户及其 token"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tokens WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        conn.commit()


def _make_sms_sender_available():
    """mock src.sms.manager.sms_manager：通道可用、发送成功（code=200）

    send_sms_code 内部为函数级 `from src.sms.manager import sms_manager`，
    因此 patch 模块属性即可生效。
    """
    manager = MagicMock()
    manager.get_sender.return_value = MagicMock(is_available=MagicMock(return_value=True))
    manager.send.return_value = {"code": 200, "msg": "OK"}
    return patch("src.sms.manager.sms_manager", manager)


def _read_captcha_code(captcha_id: str) -> str:
    """从 DB 读取图形验证码明文（generate_captcha 不再返回 code）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT code FROM captchas WHERE captcha_id = %s", (captcha_id,))
        row = cursor.fetchone()
        assert row is not None, "验证码应已入库"
        return row["code"]


def _read_latest_sms_code(phone: str) -> str:
    """从 DB 读取该手机号最新一条未使用的短信验证码"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT code FROM sms_codes WHERE phone = %s AND used = 0 "
            "ORDER BY created_at DESC LIMIT 1",
            (phone,),
        )
        row = cursor.fetchone()
        assert row is not None, "短信验证码应已入库"
        return row["code"]


class TestAuthCaptcha:
    """图形验证码测试"""

    def test_generate_captcha(self):
        """测试验证码生成：返回 captcha_id + svg_base64，不含明文 code"""
        from src.db.models import generate_captcha

        result = generate_captcha()

        assert result["captcha_id"] is not None
        assert result["svg_base64"]  # SVG 图片 base64 非空
        assert "code" not in result  # 不再返回明文验证码

    def test_verify_captcha_valid(self):
        """测试有效验证码校验（从 DB 读真实 code 后验证）"""
        from src.db.models import generate_captcha, verify_captcha

        result = generate_captcha()
        captcha_id = result["captcha_id"]
        code = _read_captcha_code(captcha_id)

        assert verify_captcha(captcha_id, code) is True

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
        """测试短信验证码发送（mock 通道后落库成功）"""
        from src.db.models import send_sms_code

        phone = f"138{uuid.uuid4().hex[:8]}"
        with _make_sms_sender_available():
            result = send_sms_code(phone)

        assert result is True
        # 验证码已落库
        _read_latest_sms_code(phone)

    def test_verify_sms_code_valid(self):
        """测试有效短信验证码（mock 发送后用 DB 中真实验证码校验）"""
        from src.db.models import send_sms_code, verify_sms_code

        phone = f"138{uuid.uuid4().hex[:8]}"
        with _make_sms_sender_available():
            assert send_sms_code(phone) is True

        code = _read_latest_sms_code(phone)
        result = verify_sms_code(phone, code)

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
        from src.db.models import UserDB

        user = UserDB.create(
            phone=f"138{uuid.uuid4().hex[:8]}",
            password="Test123456",
            username="test_user",
        )
        assert user is not None, "用户创建失败"

        yield user["user_id"]

        # 清理
        try:
            _hard_delete_user(user["user_id"])
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
    def created_user_id(self, unique_phone):
        """通过 UserDB.create 创建用户并返回内部生成的 user_id"""
        from src.db.models import UserDB

        user = UserDB.create(phone=unique_phone, password="Test123456", username="test_user")
        assert user is not None, "用户创建失败"

        yield user["user_id"]

        _hard_delete_user(user["user_id"])

    def test_create_user(self, unique_phone):
        """测试用户创建：create 返回 user dict，user_id 由内部生成"""
        from src.db.models import UserDB

        result = UserDB.create(phone=unique_phone, password="Test123456", username="test_user")

        assert result is not None
        assert result["phone"] == unique_phone
        assert result["user_id"]  # 内部生成的 user_id 非空

        # 验证创建成功
        user = UserDB.get_by_id(result["user_id"])
        assert user is not None
        assert user["user_id"] == result["user_id"]

        # 清理
        _hard_delete_user(result["user_id"])

    def test_get_user_by_id(self, created_user_id):
        """测试根据 ID 获取用户"""
        from src.db.models import UserDB

        user = UserDB.get_by_id(created_user_id)

        assert user is not None
        assert user["user_id"] == created_user_id

    def test_get_user_by_phone(self, unique_phone, created_user_id):
        """测试根据手机号获取用户"""
        from src.db.models import UserDB

        user = UserDB.get_by_phone(unique_phone, bypass_cache=True)

        assert user is not None
        assert user["phone"] == unique_phone

    def test_update_user(self, created_user_id):
        """测试用户更新（走 UserDB.update_info）"""
        from src.db.models import UserDB

        result = UserDB.update_info(created_user_id, username="updated_user")

        assert result is True

        # 验证更新成功
        user = UserDB.get_by_id(created_user_id)
        assert user["username"] == "updated_user"


class TestAuthLogin:
    """登录功能测试"""

    @pytest.fixture
    def test_user_for_login(self):
        """创建测试用户用于登录测试"""
        from src.db.models import UserDB

        phone = f"138{uuid.uuid4().hex[:8]}"
        user = UserDB.create(phone=phone, password="Test123456", username="login_test")
        assert user is not None, "用户创建失败"

        yield {"user_id": user["user_id"], "phone": phone, "password": "Test123456"}

        # 清理
        try:
            _hard_delete_user(user["user_id"])
        except Exception:
            pass

    def test_password_login_success(self, test_user_for_login):
        """测试密码登录成功（get_by_phone 取哈希后模块级 verify_password 校验）"""
        from src.db.models import UserDB, verify_password

        user_info = test_user_for_login
        user = UserDB.get_by_phone(user_info["phone"], bypass_cache=True)

        assert user is not None
        assert user["user_id"] == user_info["user_id"]
        assert verify_password(user_info["password"], user["password_hash"]) is True

    def test_password_login_wrong_password(self, test_user_for_login):
        """测试密码错误登录失败"""
        from src.db.models import UserDB, verify_password

        user_info = test_user_for_login
        user = UserDB.get_by_phone(user_info["phone"], bypass_cache=True)

        assert user is not None
        assert verify_password("WrongPassword123", user["password_hash"]) is False

    def test_password_login_nonexistent_user(self):
        """测试不存在的用户登录"""
        from src.db.models import UserDB

        # 随机手机号避免撞上共享测试库中的真实数据（固定号段可能已被注册）
        user = UserDB.get_by_phone(f"139{uuid.uuid4().hex[:8]}", bypass_cache=True)

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
