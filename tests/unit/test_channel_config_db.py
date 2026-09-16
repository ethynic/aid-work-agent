"""
ChannelConfigDB 单元测试

测试 subagent_type 字段的新增、查询、更新操作。
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestChannelConfigDBCreateWithSubagentType:
    """创建渠道配置时 subagent_type 字段处理"""

    def test_create_with_subagent_type_saves_field(self):
        """创建时指定 subagent_type，字段被正确保存"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "config_id": "chan_test123",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": json.dumps({"corp_id": "wx123"}),
            "subagent_type": "travel-consultant",
            "verified": 0,
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.create(
                tenant_id="tenant_001",
                channel_type="wecom",
                config={"corp_id": "wx123"},
                subagent_type="travel-consultant",
            )

            # 验证 INSERT 语句包含 subagent_type
            call_args = mock_cursor.execute.call_args_list[0]
            sql = call_args[0][0]
            params = call_args[0][1]

            assert "subagent_type" in sql
            assert params[5] == "travel-consultant"  # 第6个参数是 subagent_type
            assert result["subagent_type"] == "travel-consultant"

    def test_create_without_subagent_type_saves_null(self):
        """创建时不指定 subagent_type，字段为 NULL"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "config_id": "chan_test123",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": json.dumps({"corp_id": "wx123"}),
            "subagent_type": None,
            "verified": 0,
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.create(
                tenant_id="tenant_001",
                channel_type="wecom",
                config={"corp_id": "wx123"},
                subagent_type=None,
            )

            call_args = mock_cursor.execute.call_args_list[0]
            params = call_args[0][1]

            assert params[5] is None  # subagent_type 参数为 None
            assert result["subagent_type"] is None


class TestChannelConfigDBGetById:
    """get_by_id 返回结果包含 subagent_type"""

    def test_get_by_id_returns_subagent_type(self):
        """查询结果包含 subagent_type 字段"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "config_id": "chan_test123",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": json.dumps({"corp_id": "wx123"}),
            "subagent_type": "trade-specialist",
            "verified": 1,
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.get_by_id("chan_test123")

            assert result is not None
            assert result["subagent_type"] == "trade-specialist"

    def test_get_by_id_returns_null_subagent_type_when_not_set(self):
        """未设置 subagent_type 时返回 None"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "config_id": "chan_test123",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": json.dumps({"corp_id": "wx123"}),
            "subagent_type": None,
            "verified": 1,
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.get_by_id("chan_test123")

            assert result is not None
            assert result["subagent_type"] is None


class TestChannelConfigDBResolveByTenantReference:
    """历史数字主键与业务 config_id 的安全兼容解析。"""

    @pytest.mark.parametrize("reference", ["7", "chan_test123"])
    def test_resolves_reference_with_tenant_and_channel_scope(self, reference):
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 7,
            "config_id": "chan_test123",
            "tenant_id": "tenant_001",
            "channel_type": "wecom_personal_rpa",
            "config": json.dumps({"client_id": "client_001"}),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = ChannelConfigDB.resolve_by_tenant_reference(
                "tenant_001", "wecom_personal_rpa", reference
            )

        assert result is not None
        assert result["config_id"] == "chan_test123"
        sql, params = mock_cursor.execute.call_args.args
        assert "tenant_id = %s" in sql
        assert "channel_type = %s" in sql
        assert "CAST(id AS TEXT) = %s" in sql
        assert params == (
            "tenant_001",
            "wecom_personal_rpa",
            reference,
            reference,
        )

    def test_empty_reference_does_not_query_database(self):
        from src.saas.db.channel_config_db import ChannelConfigDB

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            assert (
                ChannelConfigDB.resolve_by_tenant_reference(
                    "tenant_001", "wecom_personal_rpa", " "
                )
                is None
            )
        mock_get_db.assert_not_called()


class TestChannelConfigDBClaimClientIfUnowned:
    """未归属配置必须在行锁保护下原子认领。"""

    def test_claims_unowned_config_under_row_lock(self):
        from src.saas.db.channel_config_db import ChannelConfigDB

        cursor = MagicMock()
        cursor.fetchone.return_value = {"config": json.dumps({"listen_mode": "server"})}
        cursor.rowcount = 1
        conn = MagicMock()
        conn.cursor.return_value = cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as get_db:
            get_db.return_value.__enter__.return_value = conn
            result = ChannelConfigDB.claim_client_if_unowned(
                "tenant_001", "wecom_personal_rpa", "chan_test123", "client_new"
            )

        assert result is True
        assert "FOR UPDATE" in cursor.execute.call_args_list[0].args[0]
        update_params = cursor.execute.call_args_list[1].args[1]
        assert json.loads(update_params[0])["client_id"] == "client_new"
        assert update_params[1:] == (
            "tenant_001", "wecom_personal_rpa", "chan_test123"
        )
        conn.commit.assert_called_once()

    def test_does_not_overwrite_other_client(self):
        from src.saas.db.channel_config_db import ChannelConfigDB

        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "config": json.dumps({"client_id": "client_existing"})
        }
        conn = MagicMock()
        conn.cursor.return_value = cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as get_db:
            get_db.return_value.__enter__.return_value = conn
            result = ChannelConfigDB.claim_client_if_unowned(
                "tenant_001", "wecom_personal_rpa", "chan_test123", "client_new"
            )

        assert result is False
        assert cursor.execute.call_count == 1
        conn.commit.assert_not_called()


class TestChannelConfigDBListByTenant:
    """list_by_tenant 返回结果包含 subagent_type"""

    def test_list_by_tenant_returns_subagent_type_for_all(self):
        """列出租户配置时，每条结果都包含 subagent_type"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "config_id": "chan_aaa",
                "tenant_id": "tenant_001",
                "channel_type": "wecom",
                "config": json.dumps({"corp_id": "wx111"}),
                "subagent_type": "travel-consultant",
                "verified": 1,
            },
            {
                "config_id": "chan_bbb",
                "tenant_id": "tenant_001",
                "channel_type": "wecom",
                "config": json.dumps({"corp_id": "wx222"}),
                "subagent_type": "trade-specialist",
                "verified": 1,
            },
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            results = ChannelConfigDB.list_by_tenant("tenant_001")

            assert len(results) == 2
            assert results[0]["subagent_type"] == "travel-consultant"
            assert results[1]["subagent_type"] == "trade-specialist"


class TestChannelConfigDBUpdate:
    """update 方法更新 subagent_type"""

    def test_update_updates_subagent_type(self):
        """更新时可以修改 subagent_type"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        # update 现在会先 SELECT 取现有配置（判断 channel_type + 保留运行时字段）
        # 用非 wecom_personal_rpa 类型，避免触发加密路径
        mock_cursor.fetchone.return_value = {
            "channel_type": "wecom",
            "config": json.dumps({"corp_id": "wx_old"}),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.update(
                config_id="chan_test123",
                config={"corp_id": "wx123"},
                subagent_type="new-subagent",
            )

            # 最后一次 execute 是 UPDATE 语句
            update_call = mock_cursor.execute.call_args
            sql = update_call[0][0]
            params = update_call[0][1]

            assert "subagent_type = %s" in sql
            assert params[1] == "new-subagent"  # 第二个参数是 subagent_type
            assert result is True

    def test_update_can_set_subagent_type_to_null(self):
        """更新时可以将 subagent_type 设为 NULL"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wecom",
            "config": json.dumps({"corp_id": "wx_old"}),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.update(
                config_id="chan_test123",
                config={"corp_id": "wx123"},
                subagent_type=None,
            )

            update_call = mock_cursor.execute.call_args
            params = update_call[0][1]

            assert params[1] is None
            assert result is True


def test_rpa_update_blank_preserves_but_explicit_null_clears_sensitive_secret():
    """敏感字段空串表示未修改，显式 null 才表示清除。"""
    from src.saas.db.channel_config_db import ChannelConfigDB

    def run_update(incoming):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wecom_personal_rpa",
            "config": json.dumps({"external_contact_secret": "encrypted-old"}),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db, patch(
            "src.saas.db.channel_config_db.credential_codec.encrypt_sensitive_fields",
            side_effect=lambda value: value,
        ):
            mock_get_db.return_value.__enter__.return_value = mock_conn
            assert ChannelConfigDB.update(
                config_id="chan_rpa", config={"external_contact_secret": incoming}
            )
        return json.loads(mock_cursor.execute.call_args.args[1][0])

    assert run_update("")["external_contact_secret"] == "encrypted-old"
    assert "external_contact_secret" not in run_update(None)


class TestChannelConfigDBWechatMp:
    """wechat_mp 渠道分支（mock 风格；真实 DB 用例见 tests/unit/wechat_mp/test_callback.py）。"""

    @pytest.fixture()
    def master_key(self, monkeypatch):
        """固定测试主密钥并重置 Fernet 单例，避免跨文件密钥污染。"""
        import src.core.secret_crypto as sc

        monkeypatch.setenv("RPA_SECRET_KEY", "ccdb-unit-test-master-key-0123456789")
        monkeypatch.setattr(sc, "_fernet", None)
        yield
        monkeypatch.setattr(sc, "_fernet", None)

    @pytest.mark.parametrize("blank_token", [None, "", "   "])
    def test_create_blank_token_generated_server_side(self, master_key, blank_token):
        """create：callback_token 留空/缺省仍由服务端生成（WP11 现状行为不变）。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        payload = {
            "appid": "wx0000000000000000",
            "original_id": "gh_test00000000",
            "secret": "fake-secret",
        }
        if blank_token is not None:
            payload["callback_token"] = blank_token

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db,
            patch.object(ChannelConfigDB, "_wechat_mp_appid_exists", return_value=False),
            patch.object(ChannelConfigDB, "get_by_id", return_value={"config_id": "chan_x"}),
        ):
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = ChannelConfigDB.create(
                tenant_id="tenant_001",
                channel_type="wechat_mp",
                name="公众号",
                config=payload,
            )

        assert result == {"config_id": "chan_x"}
        insert_params = mock_cursor.execute.call_args_list[0][0][1]
        written = json.loads(insert_params[4])
        assert written["callback_token"].startswith("gAAAAA")
        assert written["secret"].startswith("gAAAAA")
        assert written["enabled"] is True
        assert written["sync_interval_hours"] == 6
        assert written["credential_version"] == 1

    def test_create_custom_token_encrypted_roundtrip(self, master_key):
        """WP11 create：合法自定义 Token 去空白后原值加密入库（非随机生成）。"""
        from src.core.secret_crypto import decrypt_secret
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db,
            patch.object(ChannelConfigDB, "_wechat_mp_appid_exists", return_value=False),
            patch.object(ChannelConfigDB, "get_by_id", return_value={"config_id": "chan_x"}),
        ):
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = ChannelConfigDB.create(
                tenant_id="tenant_001",
                channel_type="wechat_mp",
                name="公众号",
                config={
                    "appid": "wx0000000000000000",
                    "original_id": "gh_test00000000",
                    "callback_token": " MyCustomToken123 ",
                },
            )

        assert result == {"config_id": "chan_x"}
        written = json.loads(mock_cursor.execute.call_args_list[0][0][1][4])
        assert written["callback_token"].startswith("gAAAAA")
        assert decrypt_secret(written["callback_token"]).decode() == "MyCustomToken123"

    @pytest.mark.parametrize("bad_token", ["ab", "a" * 33, "bad token", "tok-en", "tok@en"])
    def test_create_rejects_invalid_custom_token(self, bad_token):
        """create：自定义 Token 过短/过长/含特殊字符 → ValueError 且不触 DB。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            with pytest.raises(ValueError, match="3~32"):
                ChannelConfigDB.create(
                    tenant_id="tenant_001",
                    channel_type="wechat_mp",
                    config={
                        "appid": "wx0000000000000000",
                        "original_id": "gh_test00000000",
                        "callback_token": bad_token,
                    },
                )
        mock_get_db.assert_not_called()

    def test_update_sensitive_not_clearable_and_runtime_preserved(self):
        """update：敏感字段 null/掩码/缺失均保留旧值；appid/original_id 缺失回填；三态字段保留。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        existing = {
            "appid": "wx0000000000000000",
            "original_id": "gh_test00000000",
            "secret": "gAAAAAoldsecret",
            "encoding_aes_key": "gAAAAAoldkey",
            "callback_token": "gAAAAAoldtoken",
            "config_verified_at": "2026-09-15T00:00:00+00:00",
            "credential_version": 1,
        }
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            ok = ChannelConfigDB.update(
                config_id="chan_x",
                config={"appid": "", "original_id": None, "secret": None, "encoding_aes_key": "***", "enabled": False},
            )

        assert ok is True
        written = json.loads(mock_cursor.execute.call_args[0][1][0])
        assert written["appid"] == "wx0000000000000000"
        assert written["original_id"] == "gh_test00000000"
        assert written["secret"] == "gAAAAAoldsecret"
        assert written["encoding_aes_key"] == "gAAAAAoldkey"
        assert written["callback_token"] == "gAAAAAoldtoken"
        assert written["config_verified_at"] == "2026-09-15T00:00:00+00:00"
        assert written["credential_version"] == 1  # 身份未变，版本不递增
        assert written["enabled"] is False

    def test_update_credential_change_bumps_version_and_revokes_verified(self, master_key):
        """update：AESKey 换新明文 → 加密入库、版本递增、撤销 config_verified_at。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        existing = {
            "appid": "wx0000000000000000",
            "original_id": "gh_test00000000",
            "encoding_aes_key": "gAAAAAoldkey",
            "callback_token": "gAAAAAoldtoken",
            "config_verified_at": "2026-09-15T00:00:00+00:00",
            "credential_version": 1,
        }
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            ok = ChannelConfigDB.update(
                config_id="chan_x",
                config={
                    "appid": "wx0000000000000000",
                    "original_id": "gh_test00000000",
                    "encoding_aes_key": "new-plaintext-aes-key-43chars-abcdefghijklmnop",
                },
            )

        assert ok is True
        written = json.loads(mock_cursor.execute.call_args[0][1][0])
        assert written["encoding_aes_key"].startswith("gAAAAA")
        assert written["encoding_aes_key"] != "gAAAAAoldkey"
        assert written["credential_version"] == 2
        assert "config_verified_at" not in written

    def test_update_custom_token_rotates_and_revokes(self, master_key):
        """WP11 update：传合法新 Token → 改密入库、版本递增、撤销 config_verified_at 与 verified（与轮换一致）。"""
        from src.core.secret_crypto import decrypt_secret
        from src.saas.db.channel_config_db import ChannelConfigDB

        existing = {
            "appid": "wx0000000000000000",
            "original_id": "gh_test00000000",
            "encoding_aes_key": "gAAAAAoldkey",
            "callback_token": "gAAAAAoldtoken",
            "config_verified_at": "2026-09-15T00:00:00+00:00",
            "credential_version": 1,
        }
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            ok = ChannelConfigDB.update(
                config_id="chan_x",
                config={
                    "appid": "wx0000000000000000",
                    "original_id": "gh_test00000000",
                    "callback_token": "NewToken99",
                },
            )

        assert ok is True
        # 第 1 条为 SELECT、第 2 条为主 UPDATE、第 3 条为 verified 撤销 UPDATE
        written = json.loads(mock_cursor.execute.call_args_list[1][0][1][0])
        assert written["callback_token"].startswith("gAAAAA")
        assert decrypt_secret(written["callback_token"]).decode() == "NewToken99"
        assert written["credential_version"] == 2
        assert "config_verified_at" not in written
        revoke_sql = mock_cursor.execute.call_args_list[2][0][0]
        assert "verified = 0" in revoke_sql

    @pytest.mark.parametrize("incoming", ["", "   ", "***", None, "__MISSING__"])
    def test_update_blank_or_mask_token_preserves_old(self, incoming):
        """WP11 update：callback_token 留空/空白/掩码/null/缺失一律保留旧密文（不可清空保护不回归）。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        existing = {
            "appid": "wx0000000000000000",
            "original_id": "gh_test00000000",
            "callback_token": "gAAAAAoldtoken",
            "credential_version": 1,
        }
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        payload = {"appid": "wx0000000000000000", "original_id": "gh_test00000000"}
        if incoming != "__MISSING__":
            payload["callback_token"] = incoming

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            assert ChannelConfigDB.update(config_id="chan_x", config=payload)

        # 无自定义 Token → 不触发 verified 撤销（最后一条 execute 即主 UPDATE）
        written = json.loads(mock_cursor.execute.call_args[0][1][0])
        assert written["callback_token"] == "gAAAAAoldtoken"
        assert written["credential_version"] == 1

    def test_rotate_wechat_mp_token(self, master_key):
        """rotate：新 token 明文返回一次、密文入库、版本递增、验证态撤销、其他字段保留。"""
        from src.core.secret_crypto import decrypt_secret, encrypt_secret
        from src.saas.db.channel_config_db import ChannelConfigDB

        existing = {
            "callback_token": encrypt_secret("old-token-plain"),
            "secret": encrypt_secret("keep-me"),
            "credential_version": 1,
            "config_verified_at": "2026-09-15T00:00:00+00:00",
        }
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            new_token = ChannelConfigDB.rotate_wechat_mp_token("chan_x")

        assert new_token and new_token != "old-token-plain"
        sql, params = mock_cursor.execute.call_args[0]
        assert "verified = 0" in sql
        written = json.loads(params[0])
        assert written["callback_token"].startswith("gAAAAA")
        assert decrypt_secret(written["callback_token"]).decode() == new_token
        assert written["credential_version"] == 2
        assert "config_verified_at" not in written
        assert decrypt_secret(written["secret"]).decode() == "keep-me"

    def test_rotate_rejects_other_channel_type(self):
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "channel_type": "wecom",
            "config": json.dumps({}),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            assert ChannelConfigDB.rotate_wechat_mp_token("chan_x") is None

    @pytest.mark.parametrize("field", ["callback_token", "secret", "encoding_aes_key"])
    def test_update_config_field_rejects_wechat_mp_sensitive(self, field):
        """update_config_field 绕行保护：wechat_mp 敏感键一律拒绝且不触 DB。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            assert ChannelConfigDB.update_config_field("chan_x", field, "v") is False
        mock_get_db.assert_not_called()

    def test_update_config_field_allows_runtime_field(self):
        """三态运行时字段允许走 update_config_field。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {"config": json.dumps({})}
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            assert ChannelConfigDB.update_config_field("chan_x", "last_event_at", "2026-09-15T00:00:00+00:00") is True

        written = json.loads(mock_cursor.execute.call_args[0][1][0])
        assert written["last_event_at"] == "2026-09-15T00:00:00+00:00"

    # ==================== WP13-r1：list_* 保留清单与钳制 ====================

    @staticmethod
    def _update_with_mock(existing: dict, new_config: dict):
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            ok = ChannelConfigDB.update(config_id="chan_x", config=new_config)
        written = json.loads(mock_cursor.execute.call_args[0][1][0])
        return ok, written

    def test_update_preserves_list_fields_from_stale_snapshot(self):
        """CR P2-3 修复回归：前端旧快照（绑定前打开）update 时不得抹掉 list_* 字段。"""
        existing = {
            "appid": "wx0000000000000000",
            "list_sync_status": "active",
            "list_sync_mode": "manual",
            "list_session_at": "2026-09-16T08:00:00+00:00",
            "list_session_expire_at": "2026-09-20T08:00:00+00:00",
            "list_account_nickname": "夹具昵称",
            "list_sync_max_articles": 300,
            "list_backfill_done": True,
            "list_last_sync_at": "2026-09-16T09:30:00",
            "callback_token": "gAAAAAoldtoken",
        }
        # 旧快照：仅 appid/enabled，无任何 list_* 字段
        ok, written = self._update_with_mock(
            existing, {"appid": "wx0000000000000000", "enabled": True}
        )
        assert ok is True
        assert written["list_sync_status"] == "active"
        assert written["list_sync_mode"] == "manual"
        assert written["list_session_at"] == "2026-09-16T08:00:00+00:00"
        assert written["list_session_expire_at"] == "2026-09-20T08:00:00+00:00"
        assert written["list_account_nickname"] == "夹具昵称"
        assert written["list_sync_max_articles"] == 300
        assert written["list_backfill_done"] is True
        # CR 补全：list_last_sync_at 同为运行时写入场，旧快照保存不丢
        assert written["list_last_sync_at"] == "2026-09-16T09:30:00"

    def test_update_list_fields_still_overridable_when_provided(self):
        """保留清单不等于只读：显式传入新值正常覆盖（状态翻转/模式切换/进度推进）。"""
        existing = {"list_sync_mode": "manual", "list_backfill_done": False, "list_sync_max_articles": 100}
        _, written = self._update_with_mock(
            existing,
            {"list_sync_mode": "auto_all", "list_backfill_done": True, "list_sync_max_articles": 250},
        )
        assert written["list_sync_mode"] == "auto_all"
        assert written["list_backfill_done"] is True
        assert written["list_sync_max_articles"] == 250

    def test_update_clamps_list_sync_max_articles(self):
        """update 传入越界值钳制到 1~500；非布尔 backfill_done 规整为 bool。"""
        existing = {}
        _, written = self._update_with_mock(
            existing, {"list_sync_max_articles": 9999, "list_backfill_done": 1}
        )
        assert written["list_sync_max_articles"] == 500
        assert written["list_backfill_done"] is True
        _, written = self._update_with_mock(
            existing, {"list_sync_max_articles": 0, "list_backfill_done": 0}
        )
        assert written["list_sync_max_articles"] == 1
        assert written["list_backfill_done"] is False

    def test_create_clamps_list_sync_max_articles_default(self, master_key):
        """create：缺失默认 100；越界钳到边界（与 update 同一钳制入口）。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        for raw, expected in ((None, 100), (9999, 500), (0, 1), (88, 88)):
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            with (
                patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db,
                patch.object(ChannelConfigDB, "_wechat_mp_appid_exists", return_value=False),
                patch.object(ChannelConfigDB, "get_by_id", return_value={"config_id": "chan_x"}),
            ):
                mock_get_db.return_value.__enter__.return_value = mock_conn
                payload = {"appid": "wx0000000000000000"}
                if raw is not None:
                    payload["list_sync_max_articles"] = raw
                assert ChannelConfigDB.create(
                    tenant_id="tenant_001", channel_type="wechat_mp", config=payload
                )
            written = json.loads(mock_cursor.execute.call_args_list[0][0][1][4])
            assert written["list_sync_max_articles"] == expected, raw


class TestChannelConfigApiWechatMpToken:
    """WP11 API 层：wechat_mp 自定义回调 Token 的入参校验与明文一次性响应（mock DB）。

    锁住语义：创建/更新设置了 Token 时响应 callback_token_plaintext（仅一次）；
    非法格式 400 且不触 DB；留空/缺省不下发 callback_token（服务端生成不变）。
    """

    BASE_CONFIG = {"appid": "wx0000000000000000", "original_id": "gh_test00000000"}

    @pytest.fixture()
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.saas.api.channel_config as cc_api

        def _fake_require_admin(request):
            return {"user_id": "u1", "tenant_id": "tenant_001", "role": "tenant_admin"}

        async def _noop_record(*args, **kwargs):
            return None

        monkeypatch.setattr(cc_api, "require_admin", _fake_require_admin)
        # audit_action 的 wrapper 在 behavior_log 模块内解析 record_behavior，置 no-op 免连库
        monkeypatch.setattr("src.services.behavior_log.record_behavior", _noop_record)

        app = FastAPI()
        app.include_router(cc_api.router)
        return TestClient(app)

    def _post(self, client, config):
        return client.post(
            "/api/saas/channels",
            json={"channel_type": "wechat_mp", "name": "公众号", "config": config},
        )

    def _put(self, client, config):
        return client.put("/api/saas/channels/chan_x", json={"config": config})

    @staticmethod
    def _existing():
        return {
            "config_id": "chan_x",
            "tenant_id": "tenant_001",
            "channel_type": "wechat_mp",
            "config": {"callback_token": "***"},
        }

    def test_create_custom_token_forwarded_and_plaintext_returned_once(self, client):
        import src.saas.api.channel_config as cc_api

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {
                "config_id": "chan_x",
                "tenant_id": kwargs["tenant_id"],
                "channel_type": "wechat_mp",
                "config": {},
            }

        with (
            patch.object(cc_api.ChannelConfigDB, "create", side_effect=fake_create),
            patch.object(
                cc_api.ChannelConfigDB,
                "get_by_id_decrypted",
                return_value={"config": {"callback_token": "MyToken123"}},
            ),
        ):
            resp = self._post(client, {**self.BASE_CONFIG, "callback_token": " MyToken123 "})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["callback_token_plaintext"] == "MyToken123"  # 明文仅本次响应返回
        assert captured["config"]["callback_token"] == "MyToken123"  # 去空白后下发 DB 层

    @pytest.mark.parametrize("bad_token", ["ab", "a" * 33, "bad token", "tok-en"])
    def test_create_invalid_token_rejected_400(self, client, bad_token):
        import src.saas.api.channel_config as cc_api

        with patch.object(cc_api.ChannelConfigDB, "create") as mock_create:
            resp = self._post(client, {**self.BASE_CONFIG, "callback_token": bad_token})
        assert resp.status_code == 400
        assert "3~32" in resp.json()["detail"]
        mock_create.assert_not_called()

    @pytest.mark.parametrize("blank", [None, "", "***"])
    def test_create_blank_token_not_forwarded_still_returns_plaintext(self, client, blank):
        """留空/缺省/掩码 = 服务端生成（现状行为）：不下发 callback_token，响应仍回生成明文一次。"""
        import src.saas.api.channel_config as cc_api

        config = dict(self.BASE_CONFIG)
        if blank is not None:
            config["callback_token"] = blank

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {
                "config_id": "chan_x",
                "tenant_id": kwargs["tenant_id"],
                "channel_type": "wechat_mp",
                "config": {},
            }

        with (
            patch.object(cc_api.ChannelConfigDB, "create", side_effect=fake_create),
            patch.object(
                cc_api.ChannelConfigDB,
                "get_by_id_decrypted",
                return_value={"config": {"callback_token": "generated-plain"}},
            ),
        ):
            resp = self._post(client, config)

        assert resp.status_code == 200
        assert resp.json()["callback_token_plaintext"] == "generated-plain"
        assert "callback_token" not in captured["config"]

    def test_update_custom_token_returns_plaintext_once(self, client):
        import src.saas.api.channel_config as cc_api

        captured = {}

        def fake_update(config_id, config, subagent_type=None, name=None):
            captured["config"] = config
            return True

        with (
            patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=self._existing()),
            patch.object(cc_api.ChannelConfigDB, "update", side_effect=fake_update),
            patch.object(
                cc_api.ChannelConfigDB,
                "get_by_id_decrypted",
                return_value={"config": {"callback_token": "NewToken99"}},
            ),
            patch.object(cc_api.ChannelFactory, "invalidate_adapter", new_callable=AsyncMock),
        ):
            resp = self._put(client, {**self.BASE_CONFIG, "callback_token": "NewToken99"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["callback_token_plaintext"] == "NewToken99"  # 改密后明文仅此一次
        assert captured["config"]["callback_token"] == "NewToken99"

    def test_update_without_token_strips_key_and_no_plaintext(self, client):
        """更新未传/留空/掩码 Token：不下发 callback_token（DB 层保留旧值），也不回明文。

        掩码（*** 开头）= GET 响应原样回传的未改动值，API 层不得 400
        （与 DB 层「掩码保留旧值」语义一致），否则 GET→PUT 整体回传的调用方必挂。
        """
        import src.saas.api.channel_config as cc_api

        captured = {}

        def fake_update(config_id, config, subagent_type=None, name=None):
            captured["config"] = config
            return True

        for token in (None, "", "***"):
            payload = dict(self.BASE_CONFIG)
            if token is not None:
                payload["callback_token"] = token
            with (
                patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=self._existing()),
                patch.object(cc_api.ChannelConfigDB, "update", side_effect=fake_update),
                patch.object(cc_api.ChannelFactory, "invalidate_adapter", new_callable=AsyncMock),
            ):
                resp = self._put(client, payload)

            assert resp.status_code == 200
            assert "callback_token_plaintext" not in resp.json()
            assert "callback_token" not in captured["config"]

    @pytest.mark.parametrize("bad_token", ["ab", "bad token"])
    def test_update_invalid_token_rejected_400(self, client, bad_token):
        import src.saas.api.channel_config as cc_api

        with (
            patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=self._existing()),
            patch.object(cc_api.ChannelConfigDB, "update") as mock_update,
        ):
            resp = self._put(client, {**self.BASE_CONFIG, "callback_token": bad_token})
        assert resp.status_code == 400
        assert "3~32" in resp.json()["detail"]
        mock_update.assert_not_called()


class TestChannelConfigApiWechatMpOptionalIdentity:
    """API 层：wechat_mp appid/original_id 可选化（明文模式回调链路不强制）。

    锁住语义：appid/original_id 不填可创建（空值规整为删键，防 JSONB appid
    唯一索引撞空串 409）；original_id 有值仍校验 gh_ 前缀；安全模式
    （encoding_aes_key 有值）时 appid 缺失 400；其他渠道必填校验不回归。
    """

    @pytest.fixture()
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.saas.api.channel_config as cc_api

        def _fake_require_admin(request):
            return {"user_id": "u1", "tenant_id": "tenant_001", "role": "tenant_admin"}

        async def _noop_record(*args, **kwargs):
            return None

        monkeypatch.setattr(cc_api, "require_admin", _fake_require_admin)
        monkeypatch.setattr("src.services.behavior_log.record_behavior", _noop_record)

        app = FastAPI()
        app.include_router(cc_api.router)
        return TestClient(app)

    def _post(self, client, config, channel_type="wechat_mp"):
        return client.post(
            "/api/saas/channels",
            json={"channel_type": channel_type, "name": "公众号", "config": config},
        )

    def _put(self, client, config):
        return client.put("/api/saas/channels/chan_x", json={"config": config})

    @staticmethod
    def _fake_create(captured):
        def _create(**kwargs):
            captured.update(kwargs)
            return {
                "config_id": "chan_x",
                "tenant_id": kwargs["tenant_id"],
                "channel_type": "wechat_mp",
                "config": {},
            }
        return _create

    @staticmethod
    def _existing(appid=None):
        config = {"callback_token": "***"}
        if appid is not None:
            config["appid"] = appid
        return {
            "config_id": "chan_x",
            "tenant_id": "tenant_001",
            "channel_type": "wechat_mp",
            "config": config,
        }

    def test_create_without_appid_original_id_succeeds_and_blank_keys_stripped(self, client):
        """不填 appid/original_id 可创建；空串/纯空白可选键从下发 config 中删除。"""
        import src.saas.api.channel_config as cc_api

        captured = {}
        with (
            patch.object(cc_api.ChannelConfigDB, "create", side_effect=self._fake_create(captured)),
            patch.object(
                cc_api.ChannelConfigDB,
                "get_by_id_decrypted",
                return_value={"config": {"callback_token": "generated-plain"}},
            ),
        ):
            resp = self._post(client, {"appid": "", "original_id": "   ", "encoding_aes_key": "", "secret": ""})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["channel"]["config_id"] == "chan_x"
        # 空串必须删除（而非写空串）：键缺失 → JSONB ->> 返回 NULL → 同租户 appid 唯一索引不冲突
        assert "appid" not in captured["config"]
        assert "original_id" not in captured["config"]
        assert "encoding_aes_key" not in captured["config"]
        assert "secret" not in captured["config"]

    @pytest.mark.parametrize("bad_original_id", ["wx123", "gh", "my_gh_x"])
    def test_create_original_id_not_gh_prefixed_rejected_400(self, client, bad_original_id):
        """original_id 有值才校验 gh_ 前缀，格式非法 400 且不触 DB。"""
        import src.saas.api.channel_config as cc_api

        with patch.object(cc_api.ChannelConfigDB, "create") as mock_create:
            resp = self._post(client, {"original_id": bad_original_id})
        assert resp.status_code == 400
        assert "gh_" in resp.json()["detail"]
        mock_create.assert_not_called()

    def test_create_safe_mode_aes_key_without_appid_rejected_400(self, client):
        """提供 EncodingAESKey（安全模式）但 appid 缺失 → 400 且不触 DB。"""
        import src.saas.api.channel_config as cc_api

        with patch.object(cc_api.ChannelConfigDB, "create") as mock_create:
            resp = self._post(client, {"encoding_aes_key": "k" * 43})
        assert resp.status_code == 400
        assert "安全模式" in resp.json()["detail"]
        assert "AppID" in resp.json()["detail"]
        mock_create.assert_not_called()

    def test_create_safe_mode_aes_key_with_appid_passes(self, client):
        """安全模式同时提供 appid → 通过（channel 落库参数保留两键）。"""
        import src.saas.api.channel_config as cc_api

        captured = {}
        with (
            patch.object(cc_api.ChannelConfigDB, "create", side_effect=self._fake_create(captured)),
            patch.object(
                cc_api.ChannelConfigDB,
                "get_by_id_decrypted",
                return_value={"config": {"callback_token": "generated-plain"}},
            ),
        ):
            resp = self._post(client, {"appid": "wx0000000000000000", "encoding_aes_key": "k" * 43})

        assert resp.status_code == 200
        assert captured["config"]["appid"] == "wx0000000000000000"
        assert captured["config"]["encoding_aes_key"] == "k" * 43

    def test_update_blank_values_popped_mask_preserved(self, client):
        """update 空串可选键 pop（不写空串）；掩码敏感键原样下发由 DB 层保留旧值。"""
        import src.saas.api.channel_config as cc_api

        captured = {}

        def fake_update(config_id, config, subagent_type=None, name=None):
            captured["config"] = config
            return True

        existing = self._existing(appid="wx_old")
        existing["config"].update(
            {
                "original_id": "gh_old",
                "secret": "gAAAAAoldsecret",
                "encoding_aes_key": "gAAAAAoldkey",
                "callback_token": "gAAAAAoldtoken",
            }
        )
        with (
            patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=existing),
            patch.object(cc_api.ChannelConfigDB, "update", side_effect=fake_update),
            patch.object(cc_api.ChannelFactory, "invalidate_adapter", new_callable=AsyncMock),
        ):
            resp = self._put(
                client,
                {"appid": "", "original_id": "  ", "secret": "", "encoding_aes_key": "***", "callback_token": "***"},
            )

        assert resp.status_code == 200
        cfg = captured["config"]
        # 空串/纯空白 → pop（DB 层按缺失回填旧身份值，语义不变）
        assert "appid" not in cfg
        assert "original_id" not in cfg
        assert "secret" not in cfg
        assert "callback_token" not in cfg  # 掩码 Token 由既有 _validate_wechat_mp_custom_token 规整
        # 掩码 AESKey = GET 原样回传的未改动值，不下 400、原样转发（DB 层掩码保留旧值）
        assert cfg["encoding_aes_key"] == "***"

    def test_update_safe_mode_aes_key_without_merged_appid_rejected_400(self, client):
        """update：本次提交与 DB 现有 appid 合并后仍为空，但传了新 AESKey → 400。"""
        import src.saas.api.channel_config as cc_api

        with (
            patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=self._existing()),
            patch.object(cc_api.ChannelConfigDB, "update") as mock_update,
        ):
            resp = self._put(client, {"encoding_aes_key": "new-aes-key-plaintext-43chars-long-enough"})

        assert resp.status_code == 400
        assert "安全模式" in resp.json()["detail"]
        mock_update.assert_not_called()

    def test_update_safe_mode_mask_aes_key_skips_appid_check(self, client):
        """update：掩码 AESKey（未改动回传）跳过安全模式校验，不因 DB 无 appid 而 400。"""
        import src.saas.api.channel_config as cc_api

        with (
            patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=self._existing()),
            patch.object(cc_api.ChannelConfigDB, "update", return_value=True),
            patch.object(cc_api.ChannelFactory, "invalidate_adapter", new_callable=AsyncMock),
        ):
            resp = self._put(client, {"encoding_aes_key": "***"})
        assert resp.status_code == 200

    def test_update_existing_appid_satisfies_safe_mode(self, client):
        """update：本次未传 appid 但 DB 已有（get_by_id 返回明文）→ 新 AESKey 可保存。"""
        import src.saas.api.channel_config as cc_api

        captured = {}

        def fake_update(config_id, config, subagent_type=None, name=None):
            captured["config"] = config
            return True

        with (
            patch.object(cc_api.ChannelConfigDB, "get_by_id", return_value=self._existing(appid="wx_old")),
            patch.object(cc_api.ChannelConfigDB, "update", side_effect=fake_update),
            patch.object(cc_api.ChannelFactory, "invalidate_adapter", new_callable=AsyncMock),
        ):
            resp = self._put(client, {"encoding_aes_key": "new-aes-key-plaintext-43chars-long-enough"})

        assert resp.status_code == 200
        assert captured["config"]["encoding_aes_key"] == "new-aes-key-plaintext-43chars-long-enough"

    def test_create_wecom_missing_required_fields_still_400(self, client):
        """回归：wechat_mp 退出必填表后，其他渠道（wecom）缺必填仍 400 且不触 DB。"""
        import src.saas.api.channel_config as cc_api

        with patch.object(cc_api.ChannelConfigDB, "create") as mock_create:
            resp = self._post(client, {"token": "tk"}, channel_type="wecom")
        assert resp.status_code == 400
        assert "缺少必填字段" in resp.json()["detail"]
        assert "corp_id" in resp.json()["detail"]
        mock_create.assert_not_called()


class TestChannelConfigDBWechatMpOptionalIdentity:
    """DB 层：wechat_mp create 不写空串 appid/original_id（唯一索引兼容）。"""

    @pytest.fixture()
    def master_key(self, monkeypatch):
        import src.core.secret_crypto as sc

        monkeypatch.setenv("RPA_SECRET_KEY", "ccdb-unit-test-master-key-0123456789")
        monkeypatch.setattr(sc, "_fernet", None)
        yield
        monkeypatch.setattr(sc, "_fernet", None)

    def test_create_without_appid_original_id_writes_no_identity_keys(self, master_key):
        """落库 config 无 appid/original_id 键（键缺失 → JSONB ->> NULL → 部分唯一索引不冲突）。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db,
            patch.object(ChannelConfigDB, "_wechat_mp_appid_exists", return_value=False) as mock_appid_exists,
            patch.object(ChannelConfigDB, "get_by_id", return_value={"config_id": "chan_x"}),
        ):
            mock_get_db.return_value.__enter__.return_value = mock_conn
            # API 层空值规整后 DB 收到的形态：不含 appid/original_id 键
            result = ChannelConfigDB.create(
                tenant_id="tenant_001",
                channel_type="wechat_mp",
                config={},
            )

        assert result == {"config_id": "chan_x"}
        written = json.loads(mock_cursor.execute.call_args_list[0][0][1][4])
        assert "appid" not in written
        assert "original_id" not in written
        assert written["callback_token"]  # 服务端生成的回调 Token 仍写入
        # 空 appid 不触发同租户唯一性查询（空值场景无撞索引可能）
        mock_appid_exists.assert_not_called()

    def test_update_blank_identity_and_sensitive_preserve_old(self, master_key):
        """update：空串身份/敏感键经 DB 层回填旧值（API 层 pop 后的缺失路径等价）。"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        existing = {
            "appid": "wx_old",
            "original_id": "gh_old",
            "secret": "gAAAAAoldsecret",
            "encoding_aes_key": "gAAAAAoldkey",
            "callback_token": "gAAAAAoldtoken",
            "credential_version": 1,
        }
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.fetchone.return_value = {
            "channel_type": "wechat_mp",
            "config": json.dumps(existing),
        }
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            # 模拟 API 层空值规整后的下发形态：可选键整体缺失
            assert ChannelConfigDB.update(config_id="chan_x", config={"enabled": True})

        written = json.loads(mock_cursor.execute.call_args[0][1][0])
        assert written["appid"] == "wx_old"
        assert written["original_id"] == "gh_old"
        assert written["secret"] == "gAAAAAoldsecret"
        assert written["encoding_aes_key"] == "gAAAAAoldkey"
        assert written["callback_token"] == "gAAAAAoldtoken"
        assert written["credential_version"] == 1
