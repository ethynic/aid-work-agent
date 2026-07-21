import pytest

from src.social_media.connectors.base import CapabilityNotSupported
from src.social_media.connectors.capability_resolver import CapabilityResolver
from src.social_media.enums import PlatformCapability


def _account(supported):
    return {"capabilities_json": {"supported": supported}}


def test_resolve_dict_capabilities():
    account = _account(["account_credentials", "api_publish"])
    assert CapabilityResolver.resolve(account) == {"account_credentials", "api_publish"}


def test_resolve_json_string_capabilities():
    # psycopg2 text 列返回 JSON 字符串时也能解析
    import json

    account = {"capabilities_json": json.dumps({"supported": ["assisted_publish"]})}
    assert CapabilityResolver.resolve(account) == {"assisted_publish"}


def test_resolve_missing_capabilities_returns_empty():
    assert CapabilityResolver.resolve({}) == set()
    assert CapabilityResolver.resolve({"capabilities_json": None}) == set()
    assert CapabilityResolver.resolve({"capabilities_json": {}}) == set()


def test_supports_true_false():
    account = _account(["api_publish"])
    assert CapabilityResolver.supports(account, PlatformCapability.API_PUBLISH) is True
    assert CapabilityResolver.supports(account, PlatformCapability.ASSISTED_PUBLISH) is False


def test_require_passes_when_supported():
    account = _account(["assisted_publish"])
    CapabilityResolver.require(account, PlatformCapability.ASSISTED_PUBLISH)  # 不抛异常


def test_require_raises_capability_not_supported_when_missing():
    account = _account(["account_credentials"])
    with pytest.raises(CapabilityNotSupported) as exc_info:
        CapabilityResolver.require(account, PlatformCapability.API_PUBLISH)
    assert exc_info.value.capability == "api_publish"


def test_require_covers_ads_and_web_capabilities():
    # 新增的 ADS_* / WEB_* 能力同样能被 require 门禁拦截
    account = _account(["ads_report"])
    CapabilityResolver.require(account, PlatformCapability.ADS_REPORT)
    with pytest.raises(CapabilityNotSupported):
        CapabilityResolver.require(account, PlatformCapability.ADS_PAUSE)
    with pytest.raises(CapabilityNotSupported):
        CapabilityResolver.require(account, PlatformCapability.WEB_SEARCH)
