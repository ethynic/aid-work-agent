"""
Credentials API 端到端测试
测试数据库交互层：凭据创建、查询、更新、删除
"""

import pytest
import uuid
from datetime import datetime


class TestCredentialsCRUD:
    """凭据 CRUD 测试"""

    @pytest.fixture
    def test_user_for_credential(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"cred_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "cred_test",
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

    def test_create_credential(self, test_user_for_credential):
        """测试凭据创建"""
        from src.db.remote_credential import RemoteCredentialDB

        user_id = test_user_for_credential

        cred_data = {
            "connection_type": "smb",
            "server_host": "192.168.1.100",
            "server_port": 445,
            "username": "testuser",
            "password": "testpass123",
            "remote_path": "/share/folder"
        }

        result = RemoteCredentialDB.create(user_id, cred_data)

        assert result is True

        # 验证凭据已创建
        credentials = RemoteCredentialDB.list_by_user(user_id)
        assert len(credentials) >= 1
        assert credentials[0]["server_host"] == "192.168.1.100"

    def test_get_credential_by_id(self, test_user_for_credential):
        """测试根据 ID 获取凭据"""
        from src.db.remote_credential import RemoteCredentialDB

        user_id = test_user_for_credential

        # 先创建凭据
        cred_data = {
            "connection_type": "smb",
            "server_host": "192.168.1.100",
            "server_port": 445,
            "username": "testuser",
            "password": "testpass123",
            "remote_path": "/share/folder"
        }
        RemoteCredentialDB.create(user_id, cred_data)

        # 获取凭据列表
        credentials = RemoteCredentialDB.list_by_user(user_id)
        credential_id = credentials[0]["credential_id"]

        # 根据 ID 获取
        retrieved = RemoteCredentialDB.get_by_id(credential_id, user_id)

        assert retrieved is not None
        assert retrieved["credential_id"] == credential_id

    def test_list_credentials_by_user(self, test_user_for_credential):
        """测试列出用户的所有凭据"""
        from src.db.remote_credential import RemoteCredentialDB

        user_id = test_user_for_credential

        # 创建多个凭据
        for i in range(3):
            cred_data = {
                "connection_type": "smb" if i % 2 == 0 else "ftp",
                "server_host": f"192.168.1.{i+100}",
                "server_port": 445 if i % 2 == 0 else 21,
                "username": f"user{i}",
                "password": f"pass{i}",
                "remote_path": f"/share/folder{i}"
            }
            RemoteCredentialDB.create(user_id, cred_data)

        # 列出所有
        all_credentials = RemoteCredentialDB.list_by_user(user_id)
        assert len(all_credentials) >= 3

        # 按连接类型过滤
        smb_credentials = RemoteCredentialDB.list_by_user(user_id, connection_type="smb")
        ftp_credentials = RemoteCredentialDB.list_by_user(user_id, connection_type="ftp")

        assert len(smb_credentials) >= 2
        assert len(ftp_credentials) >= 1

    def test_update_credential(self, test_user_for_credential):
        """测试更新凭据"""
        from src.db.remote_credential import RemoteCredentialDB

        user_id = test_user_for_credential

        # 创建凭据
        cred_data = {
            "connection_type": "smb",
            "server_host": "192.168.1.100",
            "server_port": 445,
            "username": "testuser",
            "password": "testpass123",
            "remote_path": "/share/folder"
        }
        RemoteCredentialDB.create(user_id, cred_data)

        # 获取凭据 ID
        credentials = RemoteCredentialDB.list_by_user(user_id)
        credential_id = credentials[0]["credential_id"]

        # 更新凭据
        result = RemoteCredentialDB.update(
            credential_id,
            user_id,
            server_host="192.168.1.200",
            password="newpassword456"
        )

        assert result is True

        # 验证更新成功
        updated = RemoteCredentialDB.get_by_id(credential_id, user_id)
        assert updated["server_host"] == "192.168.1.200"

    def test_delete_credential(self, test_user_for_credential):
        """测试删除凭据"""
        from src.db.remote_credential import RemoteCredentialDB

        user_id = test_user_for_credential

        # 创建凭据
        cred_data = {
            "connection_type": "smb",
            "server_host": "192.168.1.100",
            "server_port": 445,
            "username": "testuser",
            "password": "testpass123",
            "remote_path": "/share/folder"
        }
        RemoteCredentialDB.create(user_id, cred_data)

        # 获取凭据 ID
        credentials = RemoteCredentialDB.list_by_user(user_id)
        credential_id = credentials[0]["credential_id"]

        # 删除凭据
        result = RemoteCredentialDB.delete(credential_id, user_id)

        assert result is True

        # 验证删除成功
        retrieved = RemoteCredentialDB.get_by_id(credential_id, user_id)
        assert retrieved is None


class TestCredentialsEncryption:
    """凭据加密测试"""

    def test_password_encryption(self):
        """测试密码加密功能"""
        from src.db.remote_credential import encryption_manager

        password = "MySecretPassword123"
        encrypted = encryption_manager.encrypt(password)

        # 加密后的内容与原密码不同
        assert encrypted != password

        # 解密后与原密码相同
        decrypted = encryption_manager.decrypt(encrypted)
        assert decrypted == password

    def test_different_encryptions(self):
        """测试每次加密结果不同"""
        from src.db.remote_credential import encryption_manager

        password = "SamePassword"
        enc1 = encryption_manager.encrypt(password)
        enc2 = encryption_manager.encrypt(password)

        # 每次加密结果不同（因为使用随机盐）
        assert enc1 != enc2

        # 但都能解密出正确密码
        assert encryption_manager.decrypt(enc1) == password
        assert encryption_manager.decrypt(enc2) == password