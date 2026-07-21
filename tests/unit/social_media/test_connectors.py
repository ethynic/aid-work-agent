import copy

import pytest

from src.social_media.connectors.base import (
    CapabilityNotSupported,
    SocialPlatformConnector,
)
from src.social_media.connectors.registry import connector_registry
from src.social_media.enums import PlatformCapability
import src.social_media.connectors  # noqa: F401  触发连接器注册

# 声明即实现：以下能力若被声明，连接器必须重写对应方法（基类默认抛 CapabilityNotSupported）。
# 这正是 S0 要修的「声明与实现矛盾」——契约测试锁住，防回归。
METHOD_BACKED_CAPABILITIES: dict[PlatformCapability, str] = {
    PlatformCapability.REMOTE_DRAFT: "create_remote_draft",
    PlatformCapability.API_PUBLISH: "publish",
    PlatformCapability.PUBLISH_STATUS: "query_publish_status",
    PlatformCapability.ASSISTED_PUBLISH: "build_assisted_package",
    PlatformCapability.API_ANALYTICS: "fetch_metrics",
}

VALID_ERROR_CATEGORIES = {"transient", "auth", "validation", "permission", "duplicate_risk", "permanent"}


@pytest.fixture(params=connector_registry.platforms())
def connector(request) -> SocialPlatformConnector:
    return connector_registry.create(request.param)


@pytest.mark.asyncio
async def test_validate_account_returns_capabilities(connector):
    """validate_account 必须返回带 supported 集合的能力对象，且只含已知枚举值。"""
    capabilities = await connector.validate_account({})
    known = {cap.value for cap in PlatformCapability}
    supported = {item.value for item in capabilities.supported}
    assert supported, f"{connector.platform} 至少应声明一项能力"
    assert supported <= known, f"{connector.platform} 声明了未知能力: {supported - known}"


@pytest.mark.asyncio
async def test_declared_method_backed_capabilities_are_implemented(connector):
    """核心契约：声明的能力必须有实现，不抛 CapabilityNotSupported。"""
    capabilities = await connector.validate_account({})
    supported = set(capabilities.supported)
    for capability, method_name in METHOD_BACKED_CAPABILITIES.items():
        if capability in supported:
            base_method = getattr(SocialPlatformConnector, method_name)
            actual_method = getattr(type(connector), method_name)
            assert actual_method is not base_method, (
                f"{connector.platform} 声明了 {capability.value} 但未重写 {method_name}()（声明与实现矛盾）"
            )


@pytest.mark.asyncio
async def test_unsupported_capability_methods_raise(connector):
    """未声明的能力，调用对应方法必须抛 CapabilityNotSupported（门禁语义）。"""
    capabilities = await connector.validate_account({})
    supported = set(capabilities.supported)
    call_args = {
        "create_remote_draft": ({"payload": {}},),
        "publish": ({"payload": {}}, "idem-key"),
        "query_publish_status": ("ext-1",),
        "build_assisted_package": ({"variant": {}},),
        "fetch_metrics": ({"request": {}},),
    }
    for capability, method_name in METHOD_BACKED_CAPABILITIES.items():
        if capability in supported:
            continue
        method = getattr(connector, method_name)
        with pytest.raises(CapabilityNotSupported):
            await method(*call_args[method_name])


@pytest.mark.asyncio
async def test_validate_variant_does_not_mutate_input(connector):
    """validate_variant 不得修改传入的 variant（防副作用）。"""
    variant = {"content_json": {"title": "标题", "body": "正文", "description": "描述"}}
    snapshot = copy.deepcopy(variant)
    await connector.validate_variant(variant)
    assert variant == snapshot, f"{connector.platform}.validate_variant 修改了输入"


@pytest.mark.asyncio
async def test_map_error_returns_stable_category(connector):
    """map_error 必须返回带稳定 category 字段的错误分类（执行器据此走重试/熔断）。"""
    result = connector.map_error(ValueError("boom"))
    assert isinstance(result, dict)
    assert result.get("category") in VALID_ERROR_CATEGORIES, (
        f"{connector.platform}.map_error category 不在稳定集合内: {result.get('category')}"
    )


@pytest.mark.asyncio
async def test_normalize_metrics_preserves_count(connector):
    """normalize_metrics 不得丢弃记录（保条数，原始保留由各连接器自行处理）。"""
    records = [{"impressions": 10}, {"impressions": 20}]
    result = connector.normalize_metrics(records)
    assert len(result) == len(records)


@pytest.mark.asyncio
async def test_capabilities_do_not_leak_credentials(connector):
    """能力序列化结果不得包含任何凭证字段（凭证走独立加密列）。"""
    capabilities = await connector.validate_account({"credentials": {"appsecret": "topsecret", "token": "abc"}})
    serialized = capabilities.to_json()
    blob = str(serialized).lower()
    for forbidden in ("topsecret", "appsecret", "token", "password", "secret"):
        assert forbidden not in blob, f"{connector.platform} 能力对象泄漏了凭证字段 {forbidden}"


@pytest.mark.asyncio
async def test_wechat_official_stub_declares_no_unimplemented_publish_caps():
    """wechat_official 为 stub：不得声明无实现的发布/回查/分析能力（S0 矛盾修复回归锁）。"""
    connector = connector_registry.create("wechat_official")
    capabilities = await connector.validate_account({})
    supported = set(capabilities.supported)
    for cap in (
        PlatformCapability.API_PUBLISH,
        PlatformCapability.SCHEDULED_PUBLISH,
        PlatformCapability.PUBLISH_STATUS,
        PlatformCapability.API_ANALYTICS,
        PlatformCapability.REMOTE_DRAFT,
    ):
        assert cap not in supported, f"wechat_official stub 不应声明未实现能力 {cap.value}"
    assert PlatformCapability.ACCOUNT_CREDENTIALS in supported


@pytest.mark.asyncio
async def test_wechat_channels_supports_assisted_publish():
    """wechat_channels 辅助发布型：声明 ASSISTED_PUBLISH 且 build_assisted_package 有实现。"""
    connector = connector_registry.create("wechat_channels")
    capabilities = await connector.validate_account({})
    assert PlatformCapability.ASSISTED_PUBLISH in set(capabilities.supported)
    assert type(connector).build_assisted_package is not SocialPlatformConnector.build_assisted_package


@pytest.mark.asyncio
async def test_wechat_official_validate_variant_rejects_missing_required_field():
    """必填字段缺失时 validate_variant 必须判 invalid（恢复删旧测试时丢掉的不变量覆盖）。"""
    connector = connector_registry.create("wechat_official")
    result = await connector.validate_variant({"content_json": {"title": "标题"}})  # 缺 body
    assert result["valid"] is False
    missing = {issue["field"] for issue in result["issues"]}
    assert "body" in missing


@pytest.mark.asyncio
async def test_wechat_channels_validate_variant_rejects_missing_required_field():
    """视频号必填字段缺失时同样判 invalid（video 必填 title/description）。"""
    connector = connector_registry.create("wechat_channels")
    result = await connector.validate_variant({"content_json": {"title": "标题"}})  # 缺 description
    assert result["valid"] is False
    missing = {issue["field"] for issue in result["issues"]}
    assert "description" in missing
