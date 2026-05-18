"""
ChannelFactory 单元测试

测试按 config_id 精确查找功能和 subagent_type 返回。
"""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestChannelFactoryCreateFromTenantConfig:
    """create_from_tenant_config 按 config_id 精确查找"""

    def test_create_by_config_id_found_returns_adapter_and_subagent(self):
        """按 config_id 查找成功时，返回 adapter、config_id、subagent_type"""
        from src.saas.services.channel_factory import ChannelFactory

        # 模拟 get_by_id 返回配置
        mock_cfg = {
            "config_id": "chan_aaa",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": {"corp_id": "wx123", "agent_id": "1000001"},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg

            # 模拟 adapter 创建
            mock_adapter = MagicMock()
            with patch.object(ChannelFactory, "create_adapter", return_value=mock_adapter):
                adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_aaa",
                )

                # 验证按 ID 查询
                mock_db.get_by_id.assert_called_once_with("chan_aaa")
                assert cfg_id == "chan_aaa"
                assert subagent == "travel-consultant"
                assert adapter is not None

    def test_create_by_config_id_wrong_tenant_returns_none(self):
        """config_id 存在但不属于目标租户时，返回 None"""
        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg = {
            "config_id": "chan_aaa",
            "tenant_id": "tenant_OTHER",  # 不是目标租户
            "channel_type": "wecom",
            "config": {"corp_id": "wx123"},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg

            adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_001",
                channel_type="wecom",
                config_id="chan_aaa",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    def test_create_by_config_id_wrong_channel_type_returns_none(self):
        """config_id 存在但 channel_type 不匹配时，返回 None"""
        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg = {
            "config_id": "chan_aaa",
            "tenant_id": "tenant_001",
            "channel_type": "dingtalk",  # 不是 wecom
            "config": {"corp_id": "wx123"},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg

            adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_001",
                channel_type="wecom",
                config_id="chan_aaa",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    def test_create_by_config_id_not_found_returns_none(self):
        """config_id 不存在时，返回 None"""
        from src.saas.services.channel_factory import ChannelFactory

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = None

            adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_001",
                channel_type="wecom",
                config_id="chan_nonexistent",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    def test_create_by_config_id_null_subagent_returns_none_subagent(self):
        """subagent_type 为 NULL 时正确返回 None"""
        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg = {
            "config_id": "chan_aaa",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": {"corp_id": "wx123"},
            "subagent_type": None,  # 未绑定数字员工
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg

            mock_adapter = MagicMock()
            with patch.object(ChannelFactory, "create_adapter", return_value=mock_adapter):
                adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_aaa",
                )

                assert cfg_id == "chan_aaa"
                assert subagent is None  # subagent_type 为 None

    def test_create_without_config_id_fallback_to_first_verified(self):
        """不传 config_id 时，兼容旧逻辑：取第一个 verified 配置"""
        from src.saas.services.channel_factory import ChannelFactory

        mock_configs = [
            {
                "config_id": "chan_aaa",
                "tenant_id": "tenant_001",
                "channel_type": "wecom",
                "config": {"corp_id": "wx111"},
                "subagent_type": "travel-consultant",
                "verified": 1,
            },
            {
                "config_id": "chan_bbb",
                "tenant_id": "tenant_001",
                "channel_type": "wecom",
                "config": {"corp_id": "wx222"},
                "subagent_type": "trade-specialist",
                "verified": 0,
            },
        ]

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.list_by_tenant.return_value = mock_configs

            mock_adapter = MagicMock()
            with patch.object(ChannelFactory, "create_adapter", return_value=mock_adapter):
                adapter, cfg_id, subagent = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id=None,  # 不指定 config_id
                )

                # 验证调用了 list_by_tenant 而不是 get_by_id
                mock_db.list_by_tenant.assert_called_once_with("tenant_001", "wecom")
                # 返回第一个 verified 配置
                assert cfg_id == "chan_aaa"
                assert subagent == "travel-consultant"

    def test_multiple_configs_same_tenant_different_subagents(self):
        """同一租户的多个配置有不同的 subagent_type，分别路由正确"""
        from src.saas.services.channel_factory import ChannelFactory

        mock_cfg1 = {
            "config_id": "chan_aaa",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": {"corp_id": "wx111"},
            "subagent_type": "travel-consultant",
            "verified": 1,
        }
        mock_cfg2 = {
            "config_id": "chan_bbb",
            "tenant_id": "tenant_001",
            "channel_type": "wecom",
            "config": {"corp_id": "wx222"},
            "subagent_type": "trade-specialist",
            "verified": 1,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            # 第一次调用返回 cfg1，第二次调用返回 cfg2
            mock_db.get_by_id.side_effect = [mock_cfg1, mock_cfg2]

            mock_adapter = MagicMock()
            with patch.object(ChannelFactory, "create_adapter", return_value=mock_adapter):
                # 查询第一个配置
                adapter1, id1, sub1 = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_aaa",
                )

                # 查询第二个配置
                adapter2, id2, sub2 = ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_bbb",
                )

                # 两个配置返回不同的 subagent_type
                assert id1 == "chan_aaa"
                assert sub1 == "travel-consultant"
                assert id2 == "chan_bbb"
                assert sub2 == "trade-specialist"
