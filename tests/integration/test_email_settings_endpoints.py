"""
Email Settings API 端到端测试
测试数据库交互层：邮箱配置创建、查询、删除
"""

import pytest
import uuid


class TestEmailSettingsCRUD:
    """邮箱设置 CRUD 测试"""

    @pytest.fixture
    def test_user_for_email(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"email_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "email_test",
            "phone": phone,
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

    def test_upsert_email_config(self, test_user_for_email):
        """测试创建/更新邮箱配置"""
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        email_config = {
            "email_address": "test@example.com",
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "test@example.com",
            "smtp_password": "smtp_password123",
            "smtp_encryption": "ssl",
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }

        result = EmailCredentialDB.upsert(user_id, email_config)

        assert result is True

    def test_get_email_config(self, test_user_for_email):
        """测试获取邮箱配置"""
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        # 先创建配置
        email_config = {
            "email_address": "test@example.com",
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "test@example.com",
            "smtp_password": "smtp_password123",
            "smtp_encryption": "ssl",
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }
        EmailCredentialDB.upsert(user_id, email_config)

        # 获取配置
        config = EmailCredentialDB.get_by_user(user_id)

        assert config is not None
        assert config["email_address"] == "test@example.com"
        assert config["smtp_server"] == "smtp.example.com"

    def test_get_email_config_not_exists(self, test_user_for_email):
        """测试获取不存在的邮箱配置"""
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        config = EmailCredentialDB.get_by_user(user_id)

        assert config is None

    def test_get_masked_email_config(self, test_user_for_email):
        """测试获取脱敏后的邮箱配置"""
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        # 先创建配置
        email_config = {
            "email_address": "test@example.com",
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "test@example.com",
            "smtp_password": "mysecretpassword",
            "smtp_encryption": "ssl",
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }
        EmailCredentialDB.upsert(user_id, email_config)

        # 获取脱敏配置
        config = EmailCredentialDB.get_masked_by_user(user_id)

        assert config is not None
        # 密码应该被脱敏
        assert config["smtp_password"] != "mysecretpassword"
        # 非敏感信息保持不变
        assert config["email_address"] == "test@example.com"

    def test_delete_email_config(self, test_user_for_email):
        """测试删除邮箱配置"""
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        # 先创建配置
        email_config = {
            "email_address": "test@example.com",
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "test@example.com",
            "smtp_password": "smtp_password123",
            "smtp_encryption": "ssl",
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }
        EmailCredentialDB.upsert(user_id, email_config)

        # 删除配置
        result = EmailCredentialDB.delete(user_id)

        assert result is True

        # 验证删除成功
        config = EmailCredentialDB.get_by_user(user_id)
        assert config is None

    def test_upsert_updates_existing(self, test_user_for_email):
        """测试更新已存在的邮箱配置"""
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        # 第一次创建
        email_config1 = {
            "email_address": "old@example.com",
            "smtp_server": "smtp.old.com",
            "smtp_port": 465,
            "smtp_user": "old@example.com",
            "smtp_password": "old_password",
            "smtp_encryption": "ssl",
            "imap_server": "imap.old.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }
        EmailCredentialDB.upsert(user_id, email_config1)

        # 第二次更新
        email_config2 = {
            "email_address": "new@example.com",
            "smtp_server": "smtp.new.com",
            "smtp_port": 587,
            "smtp_user": "new@example.com",
            "smtp_password": "new_password",
            "smtp_encryption": "tls",
            "imap_server": "imap.new.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }
        EmailCredentialDB.upsert(user_id, email_config2)

        # 获取配置，验证已更新
        config = EmailCredentialDB.get_by_user(user_id)

        assert config["email_address"] == "new@example.com"
        assert config["smtp_server"] == "smtp.new.com"
        assert config["smtp_port"] == 587

    def test_rebind_after_delete_restores_config(self, test_user_for_email):
        """回归（Phase 0 邮件工具整改）：软删除后重绑必须能读到新配置

        历史 bug：upsert 的 DELETE 分支提前 return True，新配置从未插入，
        导致用户解绑再绑定后永远"未绑定邮箱"。
        """
        from src.db.email_credential import EmailCredentialDB

        user_id = test_user_for_email

        config1 = {
            "email_address": "old@example.com",
            "smtp_server": "smtp.old.com",
            "smtp_port": 465,
            "smtp_user": "old@example.com",
            "smtp_password": "old_password",
            "smtp_encryption": "ssl",
            "imap_server": "imap.old.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }
        config2 = {
            "email_address": "new@example.com",
            "smtp_server": "smtp.new.com",
            "smtp_port": 587,
            "smtp_user": "new@example.com",
            "smtp_password": "new_password",
            "smtp_encryption": "tls",
            "imap_server": "imap.new.com",
            "imap_port": 993,
            "imap_encryption": "ssl"
        }

        # 1. 首次绑定
        assert EmailCredentialDB.upsert(user_id, config1) is True
        assert EmailCredentialDB.get_by_user(user_id)["email_address"] == "old@example.com"

        # 2. 解绑（软删除）→ 查询为空
        assert EmailCredentialDB.delete(user_id) is True
        assert EmailCredentialDB.get_by_user(user_id) is None

        # 3. 重绑：必须插入新配置（而非停留在 DELETE 早退）
        assert EmailCredentialDB.upsert(user_id, config2) is True

        # 4. 能读到新配置
        config = EmailCredentialDB.get_by_user(user_id)
        assert config is not None
        assert config["email_address"] == "new@example.com"
        assert config["smtp_server"] == "smtp.new.com"
        assert config["smtp_port"] == 587
