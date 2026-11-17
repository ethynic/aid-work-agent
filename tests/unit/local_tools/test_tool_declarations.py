"""契约 2.20 Phase 3 单元测试：声明即调度授权——claim 行级放行与设备闸门的声明分流

覆盖（对应裁决：服务端对已上报声明的设备以 declared_tools 放行；未上报声明的旧设备
沿既有静态目录回退，行为逐字节不变）：
- catalog.declared_tools_for_device：capabilities 含 tool_declarations_v1 且 declared_tools
  非空合法时返回归一映射；缺失/损坏/全空一律 None（调用方回退）
- catalog.is_tool_allowed_for_device：声明覆盖的 provider 以声明为准（内部命令拒绝）；
  未被声明覆盖的 provider（skill-runner/runtime-recovery 等）与 NULL provider 行回退目录
- device_policy.device_ready_error：声明设备按 declared_tools 判定覆盖（新声明工具不被
  静态目录闸门挡住）；无声明回退原逻辑
- api.runtime_claim：声明设备端到端放行/拒绝；旧设备决策与现状一致
"""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.local_tools import api as local_tools_api
from src.local_tools import catalog, device_policy

pytestmark = pytest.mark.unit

TENANT = "tenant_test"
DEVICE = "11111111-1111-1111-1111-111111111111"

WECOM_DECLARED = [
    "wecom_probe", "wecom_message_send", "wecom_send_status", "wecom_send_image",
    "wecom_send_file", "wecom_unread_list", "wecom_read_session", "wecom_read_sessions",
    "wecom_session_directory", "wecom_group_poll", "wecom_config_doctor", "wecom_config_calibrate",
]


def declared_capabilities(extra=None):
    """新版 Runtime 心跳形态：providers + 能力串 tool_declarations_v1 + declared_tools 映射"""
    caps = {
        "providers": ["wecom"],
        "capabilities": ["local_v2", "tool_declarations_v1"],
        "declared_tools": {"wecom": list(WECOM_DECLARED)},
    }
    if extra:
        caps.update(extra)
    return caps


def _device(capabilities_json, *, selected=True, status="active", age_seconds=5):
    return {
        "selected": selected,
        "status": status,
        "last_seen_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        "capabilities_json": capabilities_json,
    }


class TestDeclaredToolsForDevice:
    def test_valid_declaration_returns_normalized_map(self):
        caps = declared_capabilities({"declared_tools": {"wecom": ["wecom_probe", 3, "wecom_unread_list"],
                                                         "boss-recruiting": ["bad"]}})
        assert catalog.declared_tools_for_device(caps) == {
            "wecom": ["wecom_probe", "wecom_unread_list"],
            "boss-recruiting": ["bad"],
        }, "字符串列表逐键保留（不做名单校验，设备上报什么按什么放行），非字符串项剔除"

    def test_get_provider_keys_for_device_includes_declared_providers(self):
        """声明驱动可领 provider 列表：declared_tools 的 provider_key 并入（含目录外新插件）"""
        caps = declared_capabilities({"declared_tools": {"wecom": WECOM_DECLARED, "resume-tool": ["resume_parse"]}})
        keys = catalog.get_provider_keys_for_device(caps)
        assert keys == ["wecom", "resume-tool"], "已有 provider 顺序不变，声明 provider 追加在后"
        # 已有 provider 集合与旧解析一致（不因声明重复/换序）
        assert catalog.get_provider_keys_for_device(
            declared_capabilities({"declared_tools": {"resume-tool": ["resume_parse"]}})) == \
            ["wecom", "resume-tool"]

    def test_legacy_device_provider_keys_unchanged(self):
        """旧设备（无声明上报）：可领 provider 列表与升级前逐字节一致"""
        legacy = {"providers": ["wecom"], "provider_id": "ai.aidwork.boss-recruiting"}
        assert catalog.get_provider_keys_for_device(legacy) == ["wecom", "boss-recruiting"]
        assert catalog.get_provider_keys_for_device({"providers": ["skill-runner"]}) == ["skill-runner"]
        # 无 tool_declarations_v1 能力串的 declared_tools 不生效（形态归一回退，不抛错）
        assert catalog.get_provider_keys_for_device({"declared_tools": {"wecom": ["wecom_probe"]}}) == []
        assert catalog.get_provider_keys_for_device(None) == []
        assert catalog.get_provider_keys_for_device({}) == []

    def test_capability_string_missing_falls_back(self):
        caps = declared_capabilities({"capabilities": ["local_v2"]})
        assert catalog.declared_tools_for_device(caps) is None, "缺 tool_declarations_v1 能力串 = 无声明设备"

    def test_missing_or_empty_declared_tools_fall_back(self):
        assert catalog.declared_tools_for_device(declared_capabilities({"declared_tools": {}})) is None
        assert catalog.declared_tools_for_device(declared_capabilities({"declared_tools": None})) is None
        caps = dict(declared_capabilities())
        caps.pop("declared_tools")
        assert catalog.declared_tools_for_device(caps) is None

    def test_malformed_entries_yield_none(self):
        assert catalog.declared_tools_for_device(None) is None
        assert catalog.declared_tools_for_device("oops") is None
        assert catalog.declared_tools_for_device(declared_capabilities({"declared_tools": "oops"})) is None
        assert catalog.declared_tools_for_device(declared_capabilities({"declared_tools": {"wecom": "not-a-list"}})) is None
        assert catalog.declared_tools_for_device(declared_capabilities({"declared_tools": {7: ["a"]}})) is None
        assert catalog.declared_tools_for_device(declared_capabilities({"declared_tools": {"wecom": [1, 2]}})) is None, \
            "全部键无效 → None（回退目录），不抛错"


class TestIsToolAllowedForDevice:
    def test_declared_device_allows_declared_tool(self):
        caps = declared_capabilities()
        assert catalog.is_tool_allowed_for_device(caps, "wecom", "wecom_message_send") is True
        assert catalog.is_tool_allowed_for_device(caps, "wecom", "wecom_read_session") is True

    def test_declared_device_rejects_undeclared_tool(self):
        caps = declared_capabilities()
        # 内部命令：清单有、声明无 → 云端不可派发
        assert catalog.is_tool_allowed_for_device(caps, "wecom", "wecom_chat_search") is False
        # 声明覆盖 provider 的跨 provider 工具
        assert catalog.is_tool_allowed_for_device(caps, "wecom", "boss_send_to") is False

    def test_declared_device_new_tool_not_in_catalog_allowed(self):
        """声明即调度授权：新版本插件声明的工具即使尚未进服务端静态目录也放行"""
        caps = declared_capabilities({"declared_tools": {"wecom": WECOM_DECLARED + ["wecom_future_read"]}})
        assert catalog.is_tool_allowed_for_device(caps, "wecom", "wecom_future_read") is True

    def test_declared_device_non_declared_provider_falls_back_to_catalog(self):
        """未被声明覆盖的 provider（skill-runner/runtime-recovery 等非商店来源）走静态目录"""
        caps = declared_capabilities()
        assert catalog.is_tool_allowed_for_device(caps, "skill-runner", "skill_script_run") is True
        assert catalog.is_tool_allowed_for_device(caps, "skill-runner", "boss_send_to") is False
        assert catalog.is_tool_allowed_for_device(caps, "runtime-recovery", "runtime_recovery_observe") is True

    def test_null_provider_row_uses_catalog_with_default(self):
        """provider_key NULL 行（旧聊天链路）仍走原目录判定：按 default provider 校验"""
        caps = declared_capabilities({"provider_id": "ai.aidwork.boss-recruiting"})
        assert catalog.is_tool_allowed_for_device(caps, None, "boss_goto", default_provider_key="boss-recruiting") is True
        assert catalog.is_tool_allowed_for_device(caps, None, "wecom_chat_search", default_provider_key="boss-recruiting") is False
        assert catalog.is_tool_allowed_for_device(caps, None, "boss_goto", default_provider_key=None) is False

    def test_legacy_device_catalog_verbatim(self):
        """旧设备（无声明上报）：决策与 catalog.is_tool_allowed 逐字节一致"""
        legacy = {"providers": ["wecom"], "provider_id": "ai.aidwork.boss-recruiting"}
        for provider, tool, expected in [
            ("wecom", "wecom_message_send", True),
            ("wecom", "wecom_chat_search", False),
            ("wecom", "boss_send_to", False),
            ("boss-recruiting", "boss_send_to", True),
            ("skill-runner", "skill_script_run", True),
        ]:
            assert catalog.is_tool_allowed_for_device(legacy, provider, tool) is \
                catalog.is_tool_allowed(provider, tool), f"{provider}/{tool} 必须与目录判定一致"

    def test_malformed_capabilities_fall_back_to_catalog(self):
        for bad in (None, "oops", {"capabilities": "not-a-list"}):
            assert catalog.is_tool_allowed_for_device(bad, "wecom", "wecom_message_send") is \
                catalog.is_tool_allowed("wecom", "wecom_message_send")


class TestDeviceReadyErrorDeclarations:
    def test_ready_checks_precede_declaration(self):
        caps = declared_capabilities()
        assert device_policy.device_ready_error(_device(caps, selected=False), "wecom_message_send", datetime.now(timezone.utc)) == "not_selected"
        assert device_policy.device_ready_error(_device(caps, status="revoked"), "wecom_message_send", datetime.now(timezone.utc)) == "not_selected"
        assert device_policy.device_ready_error(_device(caps, age_seconds=3600), "wecom_message_send", datetime.now(timezone.utc)) == "offline"

    def test_declared_device_ready_for_declared_tool(self):
        assert device_policy.device_ready_error(_device(declared_capabilities()), "wecom_message_send",
                                                datetime.now(timezone.utc)) is None

    def test_declared_device_ready_for_new_tool_outside_catalog(self):
        """新声明工具不被静态目录闸门挡住（否则新插件设备会被 unsupported 拒绝）"""
        caps = declared_capabilities({"declared_tools": {"wecom": WECOM_DECLARED + ["wecom_future_read"]}})
        assert device_policy.device_ready_error(_device(caps), "wecom_future_read", datetime.now(timezone.utc)) is None

    def test_declared_device_unsupported_for_undeclared_tool(self):
        assert device_policy.device_ready_error(_device(declared_capabilities()), "wecom_chat_search",
                                                datetime.now(timezone.utc)) == "unsupported"

    def test_legacy_device_unsupported_for_tool_outside_catalog(self):
        legacy = {"providers": ["wecom"]}
        assert device_policy.device_ready_error(_device(legacy), "wecom_chat_search",
                                                datetime.now(timezone.utc)) == "unsupported"
        assert device_policy.device_ready_error(_device(legacy), "wecom_message_send",
                                                datetime.now(timezone.utc)) is None

    def test_hybrid_device_catalog_fallback_for_non_declared_provider(self):
        """混合设备（声明 wecom + 目录覆盖 skill-runner）：非声明 provider 沿目录判定"""
        caps = declared_capabilities({"providers": ["wecom", "skill-runner"]})
        assert device_policy.device_ready_error(_device(caps), "skill_script_run", datetime.now(timezone.utc)) is None


class TestRuntimeClaimDeclarationGate:
    @staticmethod
    def _patch_repository(claim_row, executed):
        def fake_write_result(*args, **kwargs):
            executed.append(("write_runtime_result", args, kwargs))
            return True

        return [
            patch.object(local_tools_api.repository, "claim_next", lambda *a, **k: claim_row),
            patch.object(local_tools_api.repository, "expire_stale_claims", lambda: None),
            patch.object(local_tools_api.repository, "write_runtime_result", fake_write_result),
        ]

    @staticmethod
    def _invocation(tool_name, provider_key="wecom"):
        return {"id": "inv-1", "tool_name": tool_name, "provider_key": provider_key,
                "arguments_json": {}, "lease_expires_at": None}

    def test_claim_allows_declared_tool(self):
        executed = []
        device = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": declared_capabilities()}
        patches = self._patch_repository(self._invocation("wecom_message_send"), executed)
        with patches[0], patches[1], patches[2]:
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, device=device))
        assert result["invocation_id"] == "inv-1"
        assert result["tool_name"] == "wecom_message_send"
        assert result["provider"] == "wecom"
        assert executed == [], "声明内工具直接放行，不写失败回执"

    def test_claim_rejects_undeclared_tool(self):
        executed = []
        device = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": declared_capabilities()}
        patches = self._patch_repository(self._invocation("wecom_chat_search"), executed)
        with patches[0], patches[1], patches[2]:
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, device=device))
        assert result == {"success": True, "invocation": None}
        writes = [entry for entry in executed if entry[0] == "write_runtime_result"]
        assert len(writes) == 1
        assert writes[0][1][3] is False and writes[0][1][4] == "TOOL_NOT_ALLOWED", "未声明工具置 TOOL_NOT_ALLOWED 终态"

    def test_claim_parallel_lane_covered_by_same_gate(self):
        """并行领取（parallel_only=true）与串行领取共用同一放行判定"""
        executed = []
        device = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": declared_capabilities()}
        patches = self._patch_repository(self._invocation("wecom_read_session"), executed)
        with patches[0], patches[1], patches[2]:
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, parallel_only=True, device=device))
        assert result["invocation_id"] == "inv-1", "声明内并行工具放行"

    def test_claim_null_provider_row_catalog_even_on_declared_device(self):
        """声明设备的 NULL provider 行仍走原目录判定（default provider 解析不变）"""
        executed = []
        device = {"id": DEVICE, "tenant_id": TENANT,
                  "capabilities_json": declared_capabilities({"provider_id": "ai.aidwork.boss-recruiting"})}
        patches = self._patch_repository(self._invocation("boss_goto", provider_key=None), executed)
        with patches[0], patches[1], patches[2]:
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, device=device))
        assert result["invocation_id"] == "inv-1", "NULL provider 行按 default provider 目录判定放行"

    def test_claim_legacy_device_decisions_unchanged(self):
        """旧设备（无声明上报）：放行/拒绝决策与现状逐字节一致"""
        executed = []
        legacy = {"id": DEVICE, "tenant_id": TENANT,
                  "capabilities_json": {"providers": ["wecom"], "capabilities": ["local_v2"]}}
        patches = self._patch_repository(self._invocation("wecom_message_send"), executed)
        with patches[0], patches[1], patches[2]:
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, device=legacy))
        assert result["invocation_id"] == "inv-1", "旧设备声明内工具照常放行"
        assert executed == []

        executed = []
        patches = self._patch_repository(self._invocation("wecom_chat_search"), executed)
        with patches[0], patches[1], patches[2]:
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, device=legacy))
        assert result == {"success": True, "invocation": None}
        writes = [entry for entry in executed if entry[0] == "write_runtime_result"]
        assert len(writes) == 1 and writes[0][1][4] == "TOOL_NOT_ALLOWED", "旧设备目录外工具照常拒绝"
