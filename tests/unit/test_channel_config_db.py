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
            assert params[4] == "travel-consultant"  # 第5个参数是 subagent_type
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

            assert params[4] is None  # subagent_type 参数为 None
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
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.update(
                config_id="chan_test123",
                config={"corp_id": "wx123"},
                subagent_type="new-subagent",
            )

            call_args = mock_cursor.execute.call_args
            sql = call_args[0][0]
            params = call_args[0][1]

            assert "subagent_type = %s" in sql
            assert params[1] == "new-subagent"  # 第二个参数是 subagent_type
            assert result is True

    def test_update_can_set_subagent_type_to_null(self):
        """更新时可以将 subagent_type 设为 NULL"""
        from src.saas.db.channel_config_db import ChannelConfigDB

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.saas.db.channel_config_db.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = ChannelConfigDB.update(
                config_id="chan_test123",
                config={"corp_id": "wx123"},
                subagent_type=None,
            )

            call_args = mock_cursor.execute.call_args
            params = call_args[0][1]

            assert params[1] is None
            assert result is True
