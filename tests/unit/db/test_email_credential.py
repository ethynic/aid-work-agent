"""
EmailCredentialDB.upsert 单元测试（mock get_db_connection，参照 test_subagent_template_file_db 模式）

重点回归（Phase 0 #64 bug）：软删除后重绑场景 —— UPDATE 未命中时 DELETE 后必须继续 INSERT，
不得提前 return（历史 bug：DELETE 分支 return True 导致新配置从未插入，重绑永远"未绑定"）。
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from src.db.email_credential import EmailCredentialDB


def _mock_dbcm(cursor):
    """构造 get_db_connection() 返回的 context manager mock"""
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


def _cursor_with_rowcounts(rowcounts):
    """cursor mock，rowcount 按访问顺序依次返回给定值"""
    cursor = MagicMock()
    type(cursor).rowcount = PropertyMock(side_effect=list(rowcounts))
    return cursor


def _email_config(address="test@example.com"):
    return {
        "email_address": address,
        "smtp_server": "smtp.example.com",
        "smtp_port": 465,
        "smtp_user": address,
        "smtp_password": "smtp_password123",
        "smtp_encryption": "ssl",
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "imap_encryption": "ssl",
    }


@pytest.fixture(autouse=True)
def _mock_encryption():
    """加解密 mock，避免单测依赖真实密钥配置"""
    enc = MagicMock()
    enc.encrypt.side_effect = lambda v: f"enc:{v}"
    enc.decrypt.side_effect = lambda v: v.replace("enc:", "") if isinstance(v, str) else v
    with patch("src.db.email_credential.encryption_manager", enc):
        yield enc


def _sql_texts(cursor):
    """按执行顺序提取 cursor.execute 收到的 SQL 文本"""
    return [" ".join(str(c.args[0]).split()) for c in cursor.execute.call_args_list]


class TestUpsert:
    """upsert 分支行为"""

    def test_update_path(self):
        """UPDATE 命中（active 记录存在）：只 UPDATE，一次 commit"""
        cursor = _cursor_with_rowcounts([1])
        cm = _mock_dbcm(cursor)
        with patch("src.db.email_credential.get_db_connection", return_value=cm):
            ok = EmailCredentialDB.upsert("u1", _email_config())
        assert ok is True
        sqls = _sql_texts(cursor)
        assert len(sqls) == 1
        assert "UPDATE user_email_settings" in sqls[0]
        cm.commit.assert_called_once()

    def test_insert_path_when_not_exists(self):
        """首次绑定（无任何记录）：UPDATE 未命中 → DELETE(0行) → INSERT，一次 commit"""
        cursor = _cursor_with_rowcounts([0, 0, 1])
        cm = _mock_dbcm(cursor)
        with patch("src.db.email_credential.get_db_connection", return_value=cm):
            ok = EmailCredentialDB.upsert("u1", _email_config())
        assert ok is True
        sqls = _sql_texts(cursor)
        assert len(sqls) == 3
        assert "UPDATE user_email_settings" in sqls[0]
        assert "DELETE FROM user_email_settings" in sqls[1]
        assert "INSERT INTO user_email_settings" in sqls[2]
        # 同一事务内一次 commit（DELETE 不再提前 commit/return）
        cm.commit.assert_called_once()

    def test_rebind_after_soft_delete_inserts_new_config(self):
        """回归：软删除后重绑（UPDATE 未命中、DELETE 命中旧行）必须继续 INSERT 新配置"""
        cursor = _cursor_with_rowcounts([0, 1, 1])
        cm = _mock_dbcm(cursor)
        with patch("src.db.email_credential.get_db_connection", return_value=cm):
            ok = EmailCredentialDB.upsert("u1", _email_config("new@example.com"))
        assert ok is True
        sqls = _sql_texts(cursor)
        # 关键断言：DELETE 之后必须有 INSERT（历史 bug 在 DELETE 后提前 return True）
        assert len(sqls) == 3
        assert "DELETE FROM user_email_settings" in sqls[1]
        assert "INSERT INTO user_email_settings" in sqls[2]
        # INSERT 的是新配置
        insert_call = cursor.execute.call_args_list[2]
        assert "new@example.com" in insert_call.args[1]
        cm.commit.assert_called_once()

    def test_rebind_then_get_by_user_reads_new_config(self):
        """回归（完整链路）：解绑后重绑 → get_by_user 能读到新配置"""
        # 第一次 upsert（UPDATE 命中，模拟已有 active 记录被原地更新）
        cursor1 = _cursor_with_rowcounts([1])
        cm1 = _mock_dbcm(cursor1)
        with patch("src.db.email_credential.get_db_connection", return_value=cm1):
            assert EmailCredentialDB.upsert("u1", _email_config("old@example.com")) is True

        # 模拟软删除后的重绑：UPDATE 未命中 → DELETE 旧记录 → INSERT 新配置
        cursor2 = _cursor_with_rowcounts([0, 1, 1])
        cm2 = _mock_dbcm(cursor2)
        with patch("src.db.email_credential.get_db_connection", return_value=cm2):
            assert EmailCredentialDB.upsert("u1", _email_config("new@example.com")) is True

        # get_by_user：fetchone 返回新插入的行 → 能读到新配置
        cursor3 = MagicMock()
        cursor3.fetchone.return_value = {
            "user_id": "u1",
            "email_address": "new@example.com",
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "new@example.com",
            "smtp_password": "enc:pw",
            "smtp_encryption": "ssl",
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "imap_encryption": "ssl",
            "status": "active",
        }
        cm3 = _mock_dbcm(cursor3)
        with patch("src.db.email_credential.get_db_connection", return_value=cm3):
            config = EmailCredentialDB.get_by_user("u1")

        assert config is not None
        assert config["email_address"] == "new@example.com"
        # 密码已解密
        assert config["smtp_password"] == "pw"


class TestGetByUser:
    """get_by_user 行为"""

    def test_returns_none_when_no_row(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with patch(
            "src.db.email_credential.get_db_connection", return_value=_mock_dbcm(cursor)
        ):
            assert EmailCredentialDB.get_by_user("u1") is None

    def test_decrypt_failure_falls_back_empty_password(self):
        """解密失败时密码置空，不抛异常（敏感信息不泄漏）"""
        enc = MagicMock()
        enc.encrypt.side_effect = lambda v: f"enc:{v}"
        enc.decrypt.side_effect = ValueError("解密失败")
        cursor = MagicMock()
        cursor.fetchone.return_value = {"email_address": "a@b.com", "smtp_password": "bad"}
        with patch("src.db.email_credential.encryption_manager", enc), \
             patch("src.db.email_credential.get_db_connection",
                   return_value=_mock_dbcm(cursor)):
            config = EmailCredentialDB.get_by_user("u1")
        assert config["smtp_password"] == ""


class TestDelete:
    """delete 行为（软删除）"""

    def test_delete_success(self):
        cursor = _cursor_with_rowcounts([1])
        cm = _mock_dbcm(cursor)
        with patch("src.db.email_credential.get_db_connection", return_value=cm):
            ok = EmailCredentialDB.delete("u1")
        assert ok is True
        cm.commit.assert_called_once()

    def test_delete_nothing(self):
        cursor = _cursor_with_rowcounts([0])
        cm = _mock_dbcm(cursor)
        with patch("src.db.email_credential.get_db_connection", return_value=cm):
            ok = EmailCredentialDB.delete("u1")
        assert ok is False
