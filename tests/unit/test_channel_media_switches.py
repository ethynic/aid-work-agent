"""媒体识别开关（asr_enabled / image_recognition_enabled，2026-11-19）单元测试。

覆盖：extract_media_switches 缺省/规整/非支持渠道、_build_adapter 注入、
create_adapter 元键剥离、load_media_switches 读取与失败回退、
channel_config 落库归一化。
"""

import pytest
from unittest.mock import patch, MagicMock

from src.saas.services.channel_factory import (
    ChannelFactory,
    DEFAULT_MEDIA_SWITCHES,
    _build_adapter,
    extract_media_switches,
)


class TestExtractMediaSwitches:
    def test_supported_channel_missing_keys_returns_defaults(self):
        # 存量配置不含开关键：ASR 默认开、图片识别默认关
        assert extract_media_switches("wecom", {"corp_id": "ww1"}) == {
            "asr_enabled": True,
            "image_recognition_enabled": False,
        }

    def test_none_config_returns_defaults(self):
        assert extract_media_switches("wecom_kf", None) == DEFAULT_MEDIA_SWITCHES

    def test_explicit_values_preserved(self):
        cfg = {"asr_enabled": False, "image_recognition_enabled": True}
        assert extract_media_switches("dingtalk", cfg) == {
            "asr_enabled": False,
            "image_recognition_enabled": True,
        }

    def test_non_bool_values_fallback_to_defaults(self):
        # 非布尔脏值（前端异常提交）不进运行时，回退缺省
        cfg = {"asr_enabled": "true", "image_recognition_enabled": 1}
        assert extract_media_switches("feishu", cfg) == DEFAULT_MEDIA_SWITCHES

    def test_unsupported_channel_returns_empty(self):
        # wechat_mp / web_c 无媒体识别链路，不注入无关键
        assert extract_media_switches("wechat_mp", {"asr_enabled": True}) == {}
        assert extract_media_switches("web_c", None) == {}


class TestBuildAdapterInjectsSwitches:
    @pytest.mark.asyncio
    async def test_build_adapter_injects_media_switches(self):
        # 开关键从构造参数剥离，经 adapter.media_switches 注入（构造器不接受未知 kwargs）
        captured = {}

        def fake_create_adapter(channel_type, config):
            captured["config"] = config
            return MagicMock()

        config = {
            "corp_id": "ww1",
            "agent_id": "1000002",
            "secret": "s",
            "asr_enabled": False,
            "image_recognition_enabled": True,
        }
        with patch.object(ChannelFactory, "create_adapter", side_effect=fake_create_adapter):
            adapter = await _build_adapter("t1", "wecom", config)
        assert "asr_enabled" not in captured["config"]
        assert "image_recognition_enabled" not in captured["config"]
        assert adapter.media_switches == {
            "asr_enabled": False,
            "image_recognition_enabled": True,
        }

    @pytest.mark.asyncio
    async def test_build_adapter_defaults_for_missing_keys(self):
        with patch.object(ChannelFactory, "create_adapter", return_value=MagicMock()):
            adapter = await _build_adapter("t1", "feishu", {"app_id": "a", "app_secret": "b"})
        assert adapter.media_switches == DEFAULT_MEDIA_SWITCHES


class TestCreateAdapterStripsMetaKeys:
    def test_real_wecom_adapter_rejects_no_meta_keys(self):
        # verify 等直连路径：开关/notify_usage 元键剔除后才进构造器，不抛 TypeError
        adapter = ChannelFactory.create_adapter(
            "wecom",
            {
                "corp_id": "ww1",
                "agent_id": "1000002",
                "secret": "s",
                "asr_enabled": True,
                "image_recognition_enabled": False,
                "notify_usage": "agent_only",
            },
        )
        assert adapter.corp_id == "ww1"


class TestLoadMediaSwitches:
    def test_reads_from_tenant_config(self):
        mock_cfg = {
            "config_id": "chan_aaa",
            "tenant_id": "t1",
            "channel_type": "wecom_kf",
            "config": {"corp_id": "ww1", "asr_enabled": False},
            "verified": True,
        }
        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.return_value = mock_cfg
            switches = ChannelFactory.load_media_switches("t1", "wecom_kf", "chan_aaa")
        assert switches == {"asr_enabled": False, "image_recognition_enabled": False}

    def test_db_failure_falls_back_to_defaults(self):
        with patch("src.saas.services.channel_factory.ChannelConfigDB") as mock_db:
            mock_db.get_by_id.side_effect = RuntimeError("db down")
            switches = ChannelFactory.load_media_switches("t1", "wecom", "chan_x")
        assert switches == DEFAULT_MEDIA_SWITCHES


class TestChannelConfigNormalization:
    def test_normalize_writes_normalized_bools(self):
        from src.saas.api.channel_config import _normalize_media_switch_config

        config = {"corp_id": "ww1", "asr_enabled": "true"}
        _normalize_media_switch_config("wecom", config)
        # 非布尔回退缺省并写回，落库值确定
        assert config["asr_enabled"] is True
        assert config["image_recognition_enabled"] is False

    def test_normalize_unsupported_channel_noop(self):
        from src.saas.api.channel_config import _normalize_media_switch_config

        config = {"asr_enabled": True}
        _normalize_media_switch_config("wechat_mp", config)
        assert config == {"asr_enabled": True}
