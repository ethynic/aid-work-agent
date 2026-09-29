"""企业微信 Provider 6 工具云端注册测试（M11c，2026-09-29）

覆盖：
- catalog wecom 条目与 runtime manifest（clients/agent-tool-runtime/src/providers.ts
  WECOM_TOOLS，M11b bdcd1233）逐字一致（全量 6 工具含 v1 写，照 boss 先例）
- is_tool_allowed 两态：wecom 能力设备放行 wecom 工具；boss-only 设备拒绝
- proxy tool Input 校验：target_ref/target_name 二选一（XOR）、text/image_path/
  file_path 必填、max_pages/since_days 边界
- 防双计费：6 工具 _tool_credit_price 全部为 0
- 设备闸门：wecom 能力设备建 invocation 且行 provider_key='wecom'（claim 只派给
  wecom 能力设备）；boss-only 设备拒绝；多 Provider（boss+wecom）设备放行
- boss 既有行为不回归：boss 工具在 boss 设备仍放行、invocation 行 provider_key 仍 NULL
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.local_tools import catalog
from src.local_tools.manifest import LOCAL_PROXY_TOOL_NAMES
from src.local_tools.proxy_tool import (
    LOCAL_PROXY_TOOL_CLASSES,
    BossGotoTool,
    WecomMessageSendTool,
    WecomProbeTool,
    WecomReadSessionTool,
    WecomSendFileTool,
    WecomSendImageTool,
    WecomUnreadListTool,
)

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.proxy_tool.repository"

# 与 runtime wecom manifest / catalog 条目逐字一致（顺序也一致）
WECOM_MANIFEST_TOOLS = [
    "wecom_probe",
    "wecom_message_send",
    "wecom_send_image",
    "wecom_send_file",
    "wecom_read_session",
    "wecom_unread_list",
]

WECOM_PROXY_CLASSES = [
    WecomProbeTool,
    WecomMessageSendTool,
    WecomSendImageTool,
    WecomSendFileTool,
    WecomReadSessionTool,
    WecomUnreadListTool,
]


def _device(caps):
    return {
        "id": "dev-1",
        "selected": True,
        "status": "active",
        "last_seen_at": datetime.now(),
        "capabilities_json": caps,
    }


WECOM_ONLY_CAPS = {"providers": ["wecom"], "provider_id": "ai.aidwork.wecom"}
BOSS_ONLY_CAPS = {"provider_id": "ai.aidwork.boss-recruiting"}
MULTI_PROVIDER_CAPS = {
    "providers": ["boss-recruiting", "wecom"],
    "provider_manifests": {"wecom": {"provider_id": "ai.aidwork.wecom"}},
    "provider_id": "ai.aidwork.boss-recruiting",
}


def _kwargs(**extra):
    return {"_trusted_tenant_id": TENANT, "_trusted_user_id": USER, **extra}


def _patch_repo(devices, invocation=None):
    """与 test_proxy_tool.py 同款 mock：只 mock repository 函数，无真实 DB"""
    create_invocation = MagicMock(return_value="inv-1")
    request_cancel = MagicMock(return_value=True)
    patches = patch.multiple(
        REPO,
        list_devices=MagicMock(return_value=devices),
        create_invocation=create_invocation,
        list_events=MagicMock(return_value=[]),
        get_invocation=MagicMock(return_value=invocation),
        request_cancel=request_cancel,
    )
    return patches, create_invocation, request_cancel


SUCCEEDED_INVOCATION = {
    "id": "inv-1",
    "state": "succeeded",
    "effect": "applied",
    "credit_cost": 0,
    "result_json": {"success": True, "message": "已发送", "data": {"sent": True}},
}


class TestCatalogWecomEntry:
    def test_wecom_entry_registered_with_contract_fields(self):
        entry = catalog.TRUSTED_PROVIDERS["wecom"]
        assert entry["provider_id"] == "ai.aidwork.wecom"
        assert entry["min_provider_version"] == "1.0.0"
        assert entry["execution_target"] == "local_required"

    def test_wecom_tools_match_runtime_manifest_verbatim(self):
        """6 工具清单与 runtime WECOM_TOOLS 逐字一致（含顺序；全量含 v1 写）"""
        assert catalog.allowed_tools("wecom") == WECOM_MANIFEST_TOOLS

    def test_wecom_v1_write_tools_included(self):
        """照 boss 先例含 v1 写三件套（weixin 排除 v1 写的特例不适用 wecom）"""
        for tool in ("wecom_message_send", "wecom_send_image", "wecom_send_file"):
            assert catalog.is_tool_allowed("wecom", tool)

    def test_wecom_tool_allowlist_gates(self):
        """is_tool_allowed 两态：wecom 工具在 wecom provider 放行，跨 provider 拒绝"""
        for tool in WECOM_MANIFEST_TOOLS:
            assert catalog.is_tool_allowed("wecom", tool)
            assert not catalog.is_tool_allowed("boss-recruiting", tool)
            assert not catalog.is_tool_allowed("weixin", tool)

    def test_boss_registry_unchanged(self):
        """wecom 注册不改变 boss 既有受信清单"""
        assert "boss_send_to" in catalog.allowed_tools("boss-recruiting")
        assert not set(catalog.allowed_tools("boss-recruiting")) & set(WECOM_MANIFEST_TOOLS)

    def test_wecom_capability_resolution(self):
        """Runtime 真实上报形态（providers 数组/manifests/旧 provider_id）都能解析出 wecom"""
        assert catalog.get_provider_keys_for_device(WECOM_ONLY_CAPS) == ["wecom"]
        assert "wecom" in catalog.get_provider_keys_for_device(MULTI_PROVIDER_CAPS)
        assert catalog.get_provider_key_for_device({"provider_id": "ai.aidwork.wecom"}) == "wecom"

    def test_manifest_static_list_contains_wecom(self):
        """manifest.py 静态清单含 6 工具（装配按名称交集注册）"""
        assert set(WECOM_MANIFEST_TOOLS) <= LOCAL_PROXY_TOOL_NAMES


class TestWecomToolDefinitions:
    def test_six_classes_registered(self):
        for cls in WECOM_PROXY_CLASSES:
            assert cls in LOCAL_PROXY_TOOL_CLASSES
        assert {c.name for c in WECOM_PROXY_CLASSES} == set(WECOM_MANIFEST_TOOLS)

    def test_common_registration_surface(self):
        """wecom 工具公共面：LOCAL_REQUIRED + local_wecom 分类 + invocation 行
        provider_key='wecom'（claim 只派给 wecom 能力设备）+ 不参与 boss 弹层自愈"""
        from src.tools.base import ExecutionTarget

        for cls in WECOM_PROXY_CLASSES:
            tool = cls()
            assert tool.execution_target == ExecutionTarget.LOCAL_REQUIRED
            assert tool.category == "local_wecom"
            assert tool.provider_key == "wecom"
            assert tool.invocation_provider_key == "wecom"
            assert tool.heal_eligible is False
            assert tool.display_name
            assert tool.description

    def test_all_wecom_tools_zero_credit_price(self):
        """防双计费：6 工具计价全部为 0（read_session 模型通道已计费、send 系 Phase 1 免工具积分）"""
        for cls in WECOM_PROXY_CLASSES:
            assert cls()._tool_credit_price() == 0.0


class TestWecomInputValidation:
    """Input 模型对齐 CLI MCP schema（toolDefs.ts）：XOR 互斥与必填在 executor 层先拦"""

    def test_message_send_target_xor(self):
        """target_ref 与 target_name 二选一：都不传/都传拒绝，单传通过"""
        with pytest.raises(ValidationError):
            WecomMessageSendTool.InputModel(text="你好")
        with pytest.raises(ValidationError):
            WecomMessageSendTool.InputModel(
                target_ref="ref-1", target_name="张三", text="你好")
        model = WecomMessageSendTool.InputModel(target_name="张三", text="你好")
        assert model.text == "你好"
        WecomMessageSendTool.InputModel(target_ref="ref-1", text="你好")

    def test_message_send_text_required_and_bounded(self):
        with pytest.raises(ValidationError):
            WecomMessageSendTool.InputModel(target_name="张三")
        with pytest.raises(ValidationError):
            WecomMessageSendTool.InputModel(target_name="张三", text="")
        with pytest.raises(ValidationError):
            WecomMessageSendTool.InputModel(target_name="张三", text="x" * 2001)

    def test_message_send_validate_parameters_paths(self):
        tool = WecomMessageSendTool()
        assert tool.validate_parameters(target_name="张三", text="你好") is True
        assert tool.validate_parameters(target_name="张三") is False  # 缺 text
        assert tool.validate_parameters(text="你好") is False  # 缺目标（XOR 不满足）
        assert (
            tool.validate_parameters(target_ref="r", target_name="张三", text="你好") is False
        )  # 双目标

    def test_send_image_requires_local_image_path(self):
        with pytest.raises(ValidationError):
            WecomSendImageTool.InputModel(target_name="张三")
        model = WecomSendImageTool.InputModel(target_name="张三", image_path=r"C:\tmp\a.png")
        assert model.image_path == r"C:\tmp\a.png"
        assert WecomSendImageTool().validate_parameters(
            target_name="张三", image_path=r"C:\tmp\a.png") is True

    def test_send_file_requires_local_file_path(self):
        with pytest.raises(ValidationError):
            WecomSendFileTool.InputModel(target_ref="ref-1")
        assert WecomSendFileTool().validate_parameters(
            target_ref="ref-1", file_path=r"C:\tmp\jd.pdf") is True

    def test_read_session_target_xor_and_page_bounds(self):
        with pytest.raises(ValidationError):
            WecomReadSessionTool.InputModel()
        with pytest.raises(ValidationError):
            WecomReadSessionTool.InputModel(target_ref="r", target_name="张三")
        with pytest.raises(ValidationError):
            WecomReadSessionTool.InputModel(target_name="张三", max_pages=0)
        with pytest.raises(ValidationError):
            WecomReadSessionTool.InputModel(target_name="张三", max_pages=11)
        with pytest.raises(ValidationError):
            WecomReadSessionTool.InputModel(target_name="张三", since_days=0)
        assert WecomReadSessionTool().validate_parameters(
            target_name="张三", max_pages=5, since_days=3) is True

    def test_probe_and_unread_accept_no_args(self):
        assert WecomProbeTool().validate_parameters() is True
        assert WecomUnreadListTool().validate_parameters() is True
        assert WecomUnreadListTool().validate_parameters(name="张") is True

    def test_path_semantics_documented_in_descriptions(self):
        """image_path/file_path 语义（本机绝对路径、文件须已在 runtime 所在机器上）
        必须写进字段 description，LLM 依赖它取参"""
        image_desc = WecomSendImageTool.InputModel.model_fields["image_path"].description
        file_desc = WecomSendFileTool.InputModel.model_fields["file_path"].description
        assert "本机" in image_desc and "绝对路径" in image_desc
        assert "本机" in file_desc and "绝对路径" in file_desc


class TestWecomDeviceGate:
    async def test_wecom_device_passes_gate_and_routes_invocation(self):
        """wecom 能力设备：过闸门建 invocation，行 provider_key='wecom'（claim 过滤键）"""
        patches, create_invocation, _ = _patch_repo(
            [_device(WECOM_ONLY_CAPS)], SUCCEEDED_INVOCATION)
        with patches:
            result = await WecomMessageSendTool().execute(
                **_kwargs(target_name="张三", text="你好，简历已收到"))
        assert result["success"] is True
        create_invocation.assert_called_once()
        assert create_invocation.call_args.kwargs.get("provider_key") == "wecom"

    async def test_boss_only_device_rejects_wecom_tool(self):
        """设备能力不含 wecom → DEVICE_UNAVAILABLE + 企微引导文案，不建 invocation"""
        patches, create_invocation, _ = _patch_repo([_device(BOSS_ONLY_CAPS)])
        with patches:
            result = await WecomUnreadListTool().execute(**_kwargs())
        assert result["success"] is False
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "企业微信" in result["message"]
        create_invocation.assert_not_called()

    async def test_multi_provider_device_allows_wecom_tool(self):
        """boss+wecom 双能力设备：多 Provider 解析放行 wecom 工具（单键解析会漏）"""
        patches, create_invocation, _ = _patch_repo(
            [_device(MULTI_PROVIDER_CAPS)], SUCCEEDED_INVOCATION)
        with patches:
            result = await WecomReadSessionTool().execute(
                **_kwargs(target_name="产品交流群", max_pages=2))
        assert result["success"] is True
        create_invocation.assert_called_once()

    async def test_wecom_only_device_still_rejects_boss_tool(self):
        """boss 既有行为不回归：wecom 设备对 boss 工具仍是 boss 引导文案"""
        patches, create_invocation, _ = _patch_repo([_device(WECOM_ONLY_CAPS)])
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="chat"))
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "BOSS 招聘操作" in result["message"]
        create_invocation.assert_not_called()

    async def test_boss_invocation_provider_key_stays_null(self):
        """boss 既有行为不回归：boss 工具 invocation 行 provider_key 仍为 NULL（旧行为）"""
        invocation = {
            "id": "inv-1", "state": "succeeded", "effect": "applied",
            "result_json": {"success": True, "message": "已切换", "data": {}},
        }
        patches, create_invocation, _ = _patch_repo([_device(BOSS_ONLY_CAPS)], invocation)
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="chat"))
        assert result["success"] is True
        create_invocation.assert_called_once()
        assert create_invocation.call_args.kwargs.get("provider_key") is None

    async def test_zero_price_skips_credit_precheck(self):
        """计价 0 → 不做余额预检（价格 0 工具不因余额耗尽被阻断）"""
        from src.local_tools.proxy_tool import LocalToolProxyTool

        patches, _, _ = _patch_repo([_device(WECOM_ONLY_CAPS)], SUCCEEDED_INVOCATION)
        with patches, patch.object(
            LocalToolProxyTool, "_tenant_credit_blocked",
            MagicMock(side_effect=AssertionError("计价 0 不应触发余额预检")),
        ) as blocked:
            result = await WecomMessageSendTool().execute(
                **_kwargs(target_name="张三", text="你好"))
        assert result["success"] is True
        blocked.assert_not_called()
