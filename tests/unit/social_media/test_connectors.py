import pytest

from src.social_media.connectors.registry import connector_registry
import src.social_media.connectors  # noqa: F401


@pytest.mark.asyncio
async def test_wechat_channels_declares_assisted_publish_only_for_publish():
    connector = connector_registry.create("wechat_channels")
    capabilities = await connector.validate_account({})
    supported = {item.value for item in capabilities.supported}
    assert "assisted_publish" in supported
    assert "api_publish" not in supported


@pytest.mark.asyncio
async def test_wechat_official_validates_required_article_fields():
    connector = connector_registry.create("wechat_official")
    result = await connector.validate_variant({"content_json": {"title": "标题"}})
    assert result["valid"] is False
    assert result["issues"][0]["field"] == "body"
