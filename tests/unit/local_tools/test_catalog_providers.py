"""catalog provider key 解析测试（契约补充 #2：providers 数组/manifests/旧字段优先序）"""

import pytest

from src.local_tools import catalog

pytestmark = pytest.mark.unit

BOSS_PROVIDER_ID = "ai.aidwork.boss-recruiting"
WEIXIN_PROVIDER_ID = "ai.aidwork.weixin"


@pytest.fixture()
def with_weixin_provider(monkeypatch):
    """临时注册 weixin provider（P1-B 服务端注册表落地前的测试形态）"""
    registered = dict(catalog.TRUSTED_PROVIDERS)
    monkeypatch.setitem(
        catalog.TRUSTED_PROVIDERS,
        "weixin",
        {"provider_id": WEIXIN_PROVIDER_ID, "min_provider_version": "1.0.0", "tools": []},
    )
    yield
    catalog.TRUSTED_PROVIDERS.clear()
    catalog.TRUSTED_PROVIDERS.update(registered)


class TestWeixinProviderRegistration:
    """P3-A1：weixin provider 服务端受信注册（与 Runtime src/providers.ts manifest 对齐）。

    不依赖 with_weixin_provider 临时注册——weixin 为 TRUSTED_PROVIDERS 常驻条目。
    """

    def test_weixin_entry_registered_with_contract_fields(self):
        entry = catalog.TRUSTED_PROVIDERS["weixin"]
        assert entry["provider_id"] == WEIXIN_PROVIDER_ID
        assert entry["min_provider_version"] == "1.0.0"
        assert entry["execution_target"] == "local_required"

    def test_weixin_tools_read_set_plus_v2_write_name(self):
        tools = set(catalog.allowed_tools("weixin"))
        # 只读集合（与 Runtime manifest 5 工具中的 4 只读对齐）
        assert {"weixin_probe", "weixin_chat_search",
                "weixin_history_read", "weixin_unread_list"} <= tools
        # v2 统一操作名（底座写链路）
        assert "weixin_message_send_v2" in tools
        # v1 写不经底座——不得进入受信清单
        assert "weixin_message_send" not in tools

    def test_weixin_tool_allowlist_gates(self):
        assert catalog.is_tool_allowed("weixin", "weixin_chat_search")
        assert catalog.is_tool_allowed("weixin", "weixin_message_send_v2")
        assert not catalog.is_tool_allowed("weixin", "weixin_message_send")
        assert not catalog.is_tool_allowed("weixin", "boss_send_to")  # 跨 provider 不串扰

    def test_boss_registry_unchanged(self):
        """weixin 注册不改变 boss 既有受信清单"""
        assert catalog.get_provider("boss-recruiting")["provider_id"] == BOSS_PROVIDER_ID
        assert "boss_send_to" in catalog.allowed_tools("boss-recruiting")
        assert "weixin_message_send_v2" not in catalog.allowed_tools("boss-recruiting")

    def test_weixin_capability_resolves_without_fixture(self):
        """真实 capabilities（P1-B 上报格式）无需临时注册即可解析出 weixin key"""
        caps = {
            "providers": ["boss-recruiting", "weixin"],
            "protocol_version": 2,
            "provider_manifests": {
                "weixin": {"provider_id": WEIXIN_PROVIDER_ID, "protocol_version": 1}
            },
        }
        assert "weixin" in catalog.get_provider_keys_for_device(caps)
        assert catalog.get_provider_key_for_device(
            {"provider_id": WEIXIN_PROVIDER_ID}
        ) == "weixin"


class TestProviderKeysForDevice:
    def test_providers_array_direct_key_hit(self, with_weixin_provider):
        """(1) providers 数组条目直接命中 TRUSTED_PROVIDERS 的 key（Runtime 上报 provider_key）"""
        caps = {"providers": ["weixin"], "protocol_version": 2}
        assert catalog.get_provider_keys_for_device(caps) == ["weixin"]

    def test_providers_array_mixed_with_unknown_entries(self, with_weixin_provider):
        """未知 key 忽略，不进结果（不误派不受信 provider）"""
        caps = {"providers": ["boss-recruiting", "weixin", "not-trusted"]}
        assert catalog.get_provider_keys_for_device(caps) == ["boss-recruiting", "weixin"]

    def test_provider_manifests_provider_id_mapping(self, with_weixin_provider):
        """(2) provider_manifests 各 value 的 provider_id 映射回 key——只按 provider_id
        数组匹配会漏 weixin（契约补充 #2 的实证缺陷）"""
        caps = {
            "providers": ["weixin"],
            "protocol_version": 2,
            "provider_manifests": {
                "weixin": {"provider_id": WEIXIN_PROVIDER_ID, "protocol_version": 2},
                "boss-recruiting": {"provider_id": BOSS_PROVIDER_ID, "protocol_version": 1},
            },
        }
        keys = catalog.get_provider_keys_for_device(caps)
        assert "weixin" in keys and "boss-recruiting" in keys

    def test_manifests_only_without_providers_array(self, with_weixin_provider):
        """providers 缺失时 manifests 仍可解析（能力来源齐全性独立成立）"""
        caps = {"provider_manifests": {"weixin": {"provider_id": WEIXIN_PROVIDER_ID}}}
        assert catalog.get_provider_keys_for_device(caps) == ["weixin"]

    def test_legacy_provider_id_fallback(self, with_weixin_provider):
        """(3) 旧 provider_id 字段映射（旧 Runtime 兼容，boss-only 现状）"""
        caps = {"provider_id": BOSS_PROVIDER_ID}
        assert catalog.get_provider_keys_for_device(caps) == ["boss-recruiting"]

    def test_dedup_and_empty(self, with_weixin_provider):
        """三来源并集去重；空/None capabilities 返回空"""
        caps = {
            "providers": ["boss-recruiting"],
            "provider_manifests": {"b": {"provider_id": BOSS_PROVIDER_ID}},
            "provider_id": BOSS_PROVIDER_ID,
        }
        assert catalog.get_provider_keys_for_device(caps) == ["boss-recruiting"]
        assert catalog.get_provider_keys_for_device(None) == []
        assert catalog.get_provider_keys_for_device({}) == []
