"""
ChannelFactory 单元测试

测试按 config_id 精确查找功能和 subagent_type 返回，以及三元组缓存键隔离行为。

缓存键语义：
- (tenant_id, channel_type, config_id) 三元组作为键
- 同租户同渠道下不同 config_id 各自独立缓存，互不驱逐
- config_id=None 调用先从 DB 解析实际 config_id（verified 优先），再以该实际 config_id 作为缓存键
- invalidate_adapter(config_id=X) 精确失效单条
- invalidate_adapter(config_id=None) 通配失效该 (t, c) 下所有 config
- invalidate_tenant(t) 失效该租户所有 (channel, config) 组合
"""

from datetime import datetime, timedelta

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def reset_cache():
    """每个测试前后清空 channel_factory 模块级缓存，避免跨测试状态污染"""
    from src.saas.services import channel_factory

    channel_factory._ADAPTER_CACHE.clear()
    channel_factory._CACHE_LOCKS.clear()
    yield
    channel_factory._ADAPTER_CACHE.clear()
    channel_factory._CACHE_LOCKS.clear()


class TestChannelFactoryCreateFromTenantConfig:
    """create_from_tenant_config 按 config_id 精确查找"""

    async def test_create_by_config_id_found_returns_adapter_and_subagent(self):
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
                adapter, cfg_id, subagent = await ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_aaa",
                )

                # 验证按 ID 查询
                mock_db.get_by_id.assert_called_once_with("chan_aaa")
                assert cfg_id == "chan_aaa"
                assert subagent == "travel-consultant"
                assert adapter is not None

    async def test_create_by_config_id_wrong_tenant_returns_none(self):
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

            adapter, cfg_id, subagent = await ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_001",
                channel_type="wecom",
                config_id="chan_aaa",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    async def test_create_by_config_id_wrong_channel_type_returns_none(self):
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

            adapter, cfg_id, subagent = await ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_001",
                channel_type="wecom",
                config_id="chan_aaa",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    async def test_create_by_config_id_not_found_returns_none(self):
        """config_id 不存在时，返回 None"""
        from src.saas.services.channel_factory import ChannelFactory

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = None

            adapter, cfg_id, subagent = await ChannelFactory.create_from_tenant_config(
                tenant_id="tenant_001",
                channel_type="wecom",
                config_id="chan_nonexistent",
            )

            assert adapter is None
            assert cfg_id is None
            assert subagent is None

    async def test_create_by_config_id_null_subagent_returns_none_subagent(self):
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
                adapter, cfg_id, subagent = await ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_aaa",
                )

                assert cfg_id == "chan_aaa"
                assert subagent is None  # subagent_type 为 None

    async def test_create_without_config_id_fallback_to_first_verified(self):
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
                adapter, cfg_id, subagent = await ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id=None,  # 不指定 config_id
                )

                # 验证调用了 list_by_tenant 而不是 get_by_id
                mock_db.list_by_tenant.assert_called_once_with("tenant_001", "wecom")
                # 返回第一个 verified 配置
                assert cfg_id == "chan_aaa"
                assert subagent == "travel-consultant"

    async def test_multiple_configs_same_tenant_different_subagents(self):
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
                adapter1, id1, sub1 = await ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_aaa",
                )

                # 查询第二个配置
                adapter2, id2, sub2 = await ChannelFactory.create_from_tenant_config(
                    tenant_id="tenant_001",
                    channel_type="wecom",
                    config_id="chan_bbb",
                )

                # 两个配置返回不同的 subagent_type
                assert id1 == "chan_aaa"
                assert sub1 == "travel-consultant"
                assert id2 == "chan_bbb"
                assert sub2 == "trade-specialist"


class TestChannelFactoryCacheIsolation:
    """三元组缓存键隔离行为测试（新增）"""

    async def test_two_configs_same_tenant_separate_cache_slots(self):
        """同 (tenant, channel) 两个 config_id 各自独立缓存，不互相驱逐"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        _t2 = datetime(2026, 8, 27, 10, 1, 0)
        mock_cfg_a = {
            "config_id": "chan_a",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_a"},
            "subagent_type": "travel-consultant",
            "verified": 1,
            "updated_at": _t1,
        }
        mock_cfg_b = {
            "config_id": "chan_b",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_b"},
            "subagent_type": "trade-specialist",
            "verified": 1,
            "updated_at": _t2,
        }

        adapter_a = MagicMock(name="adapter_a")
        adapter_b = MagicMock(name="adapter_b")

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            # 按 config_id 顺序返回不同配置
            mock_db.get_by_id.side_effect = lambda cid: {
                "chan_a": mock_cfg_a,
                "chan_b": mock_cfg_b,
            }.get(cid)
            mock_db.get_config_version.side_effect = lambda cid: {
                "chan_a": _t1,
                "chan_b": _t2,
            }.get(cid)

            # 按 config 创建不同 adapter（用 config["app_id"] 区分）
            def _create_adapter(channel_type, config):
                return adapter_a if config.get("app_id") == "cli_a" else adapter_b

            with patch.object(ChannelFactory, "create_adapter", side_effect=_create_adapter):
                # 首次创建 chan_a / chan_b
                ret_a1 = await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_a"
                )
                ret_b1 = await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_b"
                )

                assert ret_a1[0] is adapter_a
                assert ret_a1[1] == "chan_a"
                assert ret_a1[2] == "travel-consultant"
                assert ret_b1[0] is adapter_b
                assert ret_b1[1] == "chan_b"
                assert ret_b1[2] == "trade-specialist"

                # 两个三元组缓存槽都存在
                assert ("tenant_x", "feishu", "chan_a") in channel_factory._ADAPTER_CACHE
                assert ("tenant_x", "feishu", "chan_b") in channel_factory._ADAPTER_CACHE

                # 第二次调用命中缓存（不应再调 create_adapter）
                assert ChannelFactory.create_adapter.call_count == 2
                ret_a2 = await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_a"
                )
                ret_b2 = await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_b"
                )

                # 仍然是原 adapter 实例（缓存命中）
                assert ret_a2[0] is adapter_a
                assert ret_b2[0] is adapter_b
                # create_adapter 没有被再次调用
                assert ChannelFactory.create_adapter.call_count == 2

    async def test_invalidate_by_config_id_only_invalidates_target(self):
        """invalidate(chan_a) 不影响 (t, c, chan_b) 缓存"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        mock_cfg_a = {
            "config_id": "chan_a",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_a"},
            "subagent_type": "travel-consultant",
            "verified": 1,
            "updated_at": _t1,
        }
        mock_cfg_b = {
            "config_id": "chan_b",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_b"},
            "subagent_type": "trade-specialist",
            "verified": 1,
            "updated_at": _t1,
        }

        adapter_a = MagicMock(name="adapter_a")
        adapter_a.close = AsyncMock()
        adapter_b = MagicMock(name="adapter_b")
        adapter_b.close = AsyncMock()

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.side_effect = lambda cid: {
                "chan_a": mock_cfg_a,
                "chan_b": mock_cfg_b,
            }.get(cid)
            mock_db.get_config_version.return_value = _t1

            def _create_adapter(channel_type, config):
                return adapter_a if config.get("app_id") == "cli_a" else adapter_b

            with patch.object(ChannelFactory, "create_adapter", side_effect=_create_adapter):
                # 预热两个缓存
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_a"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_b"
                )
                assert len(channel_factory._ADAPTER_CACHE) == 2

                # 版本一致：再次调用应命中缓存，不再重建
                assert ChannelFactory.create_adapter.call_count == 2
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_a"
                )
                assert ChannelFactory.create_adapter.call_count == 2

                # 失效 chan_a
                await ChannelFactory.invalidate_adapter(
                    "tenant_x", "feishu", config_id="chan_a", close=True
                )

                # chan_a 缓存被清，chan_b 仍在
                assert ("tenant_x", "feishu", "chan_a") not in channel_factory._ADAPTER_CACHE
                assert ("tenant_x", "feishu", "chan_b") in channel_factory._ADAPTER_CACHE

                # adapter_a.close 被调用，adapter_b.close 没被调用
                adapter_a.close.assert_awaited_once()
                adapter_b.close.assert_not_awaited()

    async def test_none_caller_shares_cache_with_explicit_caller(self):
        """None 调用解析到 chan_a 后，显式调用 chan_a 命中缓存（不产生孤儿缓存）"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        mock_cfg_a = {
            "config_id": "chan_a",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_a"},
            "subagent_type": "travel-consultant",
            "verified": 1,
            "updated_at": _t1,
        }
        # 另一个未 verified 的配置，确保 None 调用解析到 chan_a
        mock_cfg_b_unverified = {
            "config_id": "chan_b",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_b"},
            "subagent_type": "trade-specialist",
            "verified": 0,
            "updated_at": _t1,
        }

        adapter_a = MagicMock(name="adapter_a")

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.list_by_tenant.return_value = [mock_cfg_a, mock_cfg_b_unverified]
            mock_db.get_by_id.return_value = mock_cfg_a
            mock_db.get_config_version.return_value = _t1

            with patch.object(ChannelFactory, "create_adapter", return_value=adapter_a):
                # 1) None 调用：解析到 chan_a（verified 优先），缓存键为 (t, c, "chan_a")
                ret_none = await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id=None
                )
                assert ret_none[1] == "chan_a"
                assert ret_none[0] is adapter_a

                # 缓存键是实际 config_id，不是 None
                assert ("tenant_x", "feishu", "chan_a") in channel_factory._ADAPTER_CACHE
                assert ("tenant_x", "feishu", None) not in channel_factory._ADAPTER_CACHE

                first_call_count = ChannelFactory.create_adapter.call_count
                assert first_call_count == 1

                # 2) 显式 chan_a 调用：应命中上面的缓存，不再 create_adapter
                ret_explicit = await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_a"
                )
                assert ret_explicit[0] is adapter_a
                assert ret_explicit[1] == "chan_a"
                # create_adapter 没有被再次调用
                assert ChannelFactory.create_adapter.call_count == first_call_count

    async def test_invalidate_wildcard_clears_all_configs_for_tenant_channel(self):
        """invalidate_adapter(t, c, config_id=None) 清掉该 (t, c) 下所有 config 缓存"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        mock_cfg_a = {
            "config_id": "chan_a",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_a"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        mock_cfg_b = {
            "config_id": "chan_b",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_b"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        # 另一渠道的配置，不应被清掉
        mock_cfg_other_channel = {
            "config_id": "chan_c",
            "tenant_id": "tenant_x",
            "channel_type": "dingtalk",
            "config": {"app_key": "k_c"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        # 另一租户的配置，不应被清掉
        mock_cfg_other_tenant = {
            "config_id": "chan_d",
            "tenant_id": "tenant_y",
            "channel_type": "feishu",
            "config": {"app_id": "cli_d"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }

        adapters = {
            "chan_a": MagicMock(name="adapter_a"),
            "chan_b": MagicMock(name="adapter_b"),
            "chan_c": MagicMock(name="adapter_c"),
            "chan_d": MagicMock(name="adapter_d"),
        }
        for ad in adapters.values():
            ad.close = AsyncMock()

        configs_map = {
            "chan_a": mock_cfg_a,
            "chan_b": mock_cfg_b,
            "chan_c": mock_cfg_other_channel,
            "chan_d": mock_cfg_other_tenant,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.side_effect = lambda cid: configs_map.get(cid)
            mock_db.get_config_version.return_value = _t1

            def _create_adapter(channel_type, config):
                # 按 app_id/app_key 反查 config_id
                for cid, cfg in configs_map.items():
                    if cfg["config"] == config:
                        return adapters[cid]
                return MagicMock()

            with patch.object(ChannelFactory, "create_adapter", side_effect=_create_adapter):
                # 预热四个缓存
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_a"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_b"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "dingtalk", config_id="chan_c"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_y", "feishu", config_id="chan_d"
                )
                assert len(channel_factory._ADAPTER_CACHE) == 4

                # 通配失效 (tenant_x, feishu, *) —— 只清掉 chan_a / chan_b
                await ChannelFactory.invalidate_adapter(
                    "tenant_x", "feishu", config_id=None, close=True
                )

                assert ("tenant_x", "feishu", "chan_a") not in channel_factory._ADAPTER_CACHE
                assert ("tenant_x", "feishu", "chan_b") not in channel_factory._ADAPTER_CACHE
                # 其他渠道 / 其他租户的缓存仍在
                assert ("tenant_x", "dingtalk", "chan_c") in channel_factory._ADAPTER_CACHE
                assert ("tenant_y", "feishu", "chan_d") in channel_factory._ADAPTER_CACHE

                # adapter_a / adapter_b 的 close 被调用
                adapters["chan_a"].close.assert_awaited_once()
                adapters["chan_b"].close.assert_awaited_once()
                # adapter_c / adapter_d 没被关闭
                adapters["chan_c"].close.assert_not_awaited()
                adapters["chan_d"].close.assert_not_awaited()

    async def test_invalidate_tenant_clears_all_channels(self):
        """invalidate_tenant(t) 清掉该租户所有 (channel, config) 组合"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        mock_cfg_fsh_a = {
            "config_id": "chan_fa",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_fa"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        mock_cfg_fsh_b = {
            "config_id": "chan_fb",
            "tenant_id": "tenant_x",
            "channel_type": "feishu",
            "config": {"app_id": "cli_fb"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        mock_cfg_dt = {
            "config_id": "chan_dt",
            "tenant_id": "tenant_x",
            "channel_type": "dingtalk",
            "config": {"app_key": "k_dt"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        mock_cfg_other_tenant = {
            "config_id": "chan_ot",
            "tenant_id": "tenant_y",
            "channel_type": "feishu",
            "config": {"app_id": "cli_ot"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }

        adapters = {
            "chan_fa": MagicMock(name="adapter_fa"),
            "chan_fb": MagicMock(name="adapter_fb"),
            "chan_dt": MagicMock(name="adapter_dt"),
            "chan_ot": MagicMock(name="adapter_ot"),
        }
        for ad in adapters.values():
            ad.close = AsyncMock()

        configs_map = {
            "chan_fa": mock_cfg_fsh_a,
            "chan_fb": mock_cfg_fsh_b,
            "chan_dt": mock_cfg_dt,
            "chan_ot": mock_cfg_other_tenant,
        }

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.side_effect = lambda cid: configs_map.get(cid)
            mock_db.get_config_version.return_value = _t1

            def _create_adapter(channel_type, config):
                for cid, cfg in configs_map.items():
                    if cfg["config"] == config:
                        return adapters[cid]
                return MagicMock()

            with patch.object(ChannelFactory, "create_adapter", side_effect=_create_adapter):
                # 预热四个缓存（tenant_x 下 3 个 + tenant_y 下 1 个）
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_fa"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "feishu", config_id="chan_fb"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_x", "dingtalk", config_id="chan_dt"
                )
                await ChannelFactory.create_from_tenant_config(
                    "tenant_y", "feishu", config_id="chan_ot"
                )
                assert len(channel_factory._ADAPTER_CACHE) == 4

                # 失效 tenant_x 的所有渠道
                await ChannelFactory.invalidate_tenant("tenant_x")

                # tenant_x 下所有缓存被清
                assert ("tenant_x", "feishu", "chan_fa") not in channel_factory._ADAPTER_CACHE
                assert ("tenant_x", "feishu", "chan_fb") not in channel_factory._ADAPTER_CACHE
                assert ("tenant_x", "dingtalk", "chan_dt") not in channel_factory._ADAPTER_CACHE
                # tenant_y 缓存仍在
                assert ("tenant_y", "feishu", "chan_ot") in channel_factory._ADAPTER_CACHE

                # tenant_x 下所有 adapter 都被 close
                adapters["chan_fa"].close.assert_awaited_once()
                adapters["chan_fb"].close.assert_awaited_once()
                adapters["chan_dt"].close.assert_awaited_once()
                # tenant_y 的 adapter 没被 close
                adapters["chan_ot"].close.assert_not_awaited()


class TestChannelFactoryConfigVersion:
    """配置版本校验：跨 Gunicorn worker 兜底，DB 配置变更后缓存即时失效重建"""

    async def test_config_updated_rebuilds_adapter(self):
        """DB updated_at 变化（模拟其他 worker 已改配置但本 worker 内存仍是旧 adapter）时强制重建"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        _t2 = datetime(2026, 8, 27, 10, 5, 0)
        cfg_v1 = {
            "config_id": "chan_v",
            "tenant_id": "tenant_v",
            "channel_type": "wecom_kf",
            "config": {"credit_limit": 100},
            "subagent_type": "sales-assistant",
            "verified": 1,
            "updated_at": _t1,
        }
        cfg_v2 = {
            "config_id": "chan_v",
            "tenant_id": "tenant_v",
            "channel_type": "wecom_kf",
            "config": {"credit_limit": 0},
            "subagent_type": "sales-assistant",
            "verified": 1,
            "updated_at": _t2,
        }

        adapter_v1 = MagicMock(name="adapter_v1")
        adapter_v2 = MagicMock(name="adapter_v2")

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            # 第一次调用：DB 返回 v1 版本
            mock_db.get_by_id.return_value = cfg_v1
            mock_db.get_config_version.return_value = _t1

            def _create_adapter(channel_type, config):
                # 按 config 内容区分新旧 adapter
                return adapter_v1 if config.get("credit_limit") == 100 else adapter_v2

            with patch.object(ChannelFactory, "create_adapter", side_effect=_create_adapter):
                ret1 = await ChannelFactory.create_from_tenant_config(
                    "tenant_v", "wecom_kf", config_id="chan_v"
                )
                assert ret1[0] is adapter_v1
                assert ChannelFactory.create_adapter.call_count == 1

                # 配置被其他 worker 修改：DB 版本变为 t2，本 worker 缓存仍是旧 adapter
                mock_db.get_by_id.return_value = cfg_v2
                mock_db.get_config_version.return_value = _t2

                ret2 = await ChannelFactory.create_from_tenant_config(
                    "tenant_v", "wecom_kf", config_id="chan_v"
                )
                # 版本不一致 → 强制重建，返回新配置的新 adapter
                assert ret2[0] is adapter_v2
                assert ChannelFactory.create_adapter.call_count == 2
                # 旧缓存被替换为新版本
                assert channel_factory._ADAPTER_CACHE[
                    ("tenant_v", "wecom_kf", "chan_v")
                ]["cfg_updated_at"] == _t2

                # 第三次调用版本一致 → 命中缓存，不再重建
                ret3 = await ChannelFactory.create_from_tenant_config(
                    "tenant_v", "wecom_kf", config_id="chan_v"
                )
                assert ret3[0] is adapter_v2
                assert ChannelFactory.create_adapter.call_count == 2

    async def test_db_read_error_keeps_cache(self):
        """DB 版本查询失败时保守沿用缓存，不阻断业务"""
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        cfg = {
            "config_id": "chan_e",
            "tenant_id": "tenant_e",
            "channel_type": "feishu",
            "config": {"app_id": "cli_e"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        adapter1 = MagicMock(name="adapter1")

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = cfg
            mock_db.get_config_version.return_value = _t1

            with patch.object(ChannelFactory, "create_adapter", return_value=adapter1):
                ret1 = await ChannelFactory.create_from_tenant_config(
                    "tenant_e", "feishu", config_id="chan_e"
                )
                assert ret1[0] is adapter1

                # DB 版本查询抛异常：沿用缓存，不重建不报错
                mock_db.get_config_version.side_effect = RuntimeError("db down")

                ret2 = await ChannelFactory.create_from_tenant_config(
                    "tenant_e", "feishu", config_id="chan_e"
                )
                assert ret2[0] is adapter1
                assert ChannelFactory.create_adapter.call_count == 1

    async def test_config_deleted_rebuild_returns_none(self):
        """配置被删除（版本查询返回 None）时强制重建，重建后返回 None 并清缓存"""
        from src.saas.services import channel_factory
        from src.saas.services.channel_factory import ChannelFactory

        _t1 = datetime(2026, 8, 27, 10, 0, 0)
        cfg = {
            "config_id": "chan_d",
            "tenant_id": "tenant_d",
            "channel_type": "dingtalk",
            "config": {"app_key": "k_d"},
            "subagent_type": None,
            "verified": 1,
            "updated_at": _t1,
        }
        adapter1 = MagicMock(name="adapter1")

        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = cfg
            mock_db.get_config_version.return_value = _t1

            with patch.object(ChannelFactory, "create_adapter", return_value=adapter1):
                ret1 = await ChannelFactory.create_from_tenant_config(
                    "tenant_d", "dingtalk", config_id="chan_d"
                )
                assert ret1[0] is adapter1
                assert ("tenant_d", "dingtalk", "chan_d") in channel_factory._ADAPTER_CACHE

                # 配置被删除：版本查询返回 None，get_by_id 也返回 None
                mock_db.get_config_version.return_value = None
                mock_db.get_by_id.return_value = None

                ret2 = await ChannelFactory.create_from_tenant_config(
                    "tenant_d", "dingtalk", config_id="chan_d"
                )
                assert ret2 == (None, None, None)
                # 旧缓存被清掉
                assert ("tenant_d", "dingtalk", "chan_d") not in channel_factory._ADAPTER_CACHE
