"""
ChannelConfigDB 单元测试

测试 subagent_type 字段的新增、查询、更新操作。
"""

import json
import pytest
from unittest.mock import patch, MagicMock


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
