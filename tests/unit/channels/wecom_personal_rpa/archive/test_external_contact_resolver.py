from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.channels.wecom_personal_rpa.archive import external_contact_resolver as module


class MemoryRedis:
    def __init__(self):
        self.values = {}

    def make_key(self, prefix, suffix):
        return f"{prefix}:{suffix}"

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(module, "redis_client", MemoryRedis())
    return module.ExternalContactResolver()


@pytest.mark.asyncio
async def test_resolve_success_and_cache(resolver, monkeypatch):
    resolver._fetch = AsyncMock(return_value=module.ResolvedExternalContact("wm_1", "孙晨"))
    first = await resolver.resolve("tenant_1", "ww_1", "secret_1", "wm_1")
    second = await resolver.resolve("tenant_1", "ww_1", "secret_1", "wm_1")
    assert first.display_name == second.display_name == "孙晨"
    resolver._fetch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("errcode", [48002, 60020])
async def test_permission_or_visibility_error_isolated_and_backed_off(resolver, errcode):
    resolver._fetch = AsyncMock(side_effect=module.ExternalContactResolveError(errcode, "forbidden"))
    assert await resolver.resolve("tenant_1", "ww_1", "secret_1", "wm_1") is None
    assert await resolver.resolve("tenant_1", "ww_1", "secret_1", "wm_1") is None
    resolver._fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_isolated(resolver):
    request = httpx.Request("GET", "https://qyapi.weixin.qq.com")
    resolver._fetch = AsyncMock(side_effect=httpx.ReadTimeout("timeout", request=request))
    assert await resolver.resolve("tenant_1", "ww_1", "secret_1", "wm_1") is None


def test_token_cache_is_isolated_by_tenant_and_secret(monkeypatch):
    monkeypatch.setattr(module, "redis_client", MemoryRedis())
    key1 = module._token_key("tenant_1", "ww_1", "secret_1")
    key2 = module._token_key("tenant_2", "ww_1", "secret_1")
    key3 = module._token_key("tenant_1", "ww_1", "secret_2")
    assert len({key1, key2, key3}) == 3
    assert "secret_1" not in key1


def test_name_and_failure_cache_are_isolated_by_tenant_corp_and_secret(monkeypatch):
    monkeypatch.setattr(module, "redis_client", MemoryRedis())
    name_keys = {
        module._name_key("tenant_1", "ww_1", "secret_1", "wm_1"),
        module._name_key("tenant_2", "ww_1", "secret_1", "wm_1"),
        module._name_key("tenant_1", "ww_2", "secret_1", "wm_1"),
        module._name_key("tenant_1", "ww_1", "secret_2", "wm_1"),
    }
    fail_keys = {
        module._fail_key("tenant_1", "ww_1", "secret_1", "wm_1"),
        module._fail_key("tenant_2", "ww_1", "secret_1", "wm_1"),
        module._fail_key("tenant_1", "ww_2", "secret_1", "wm_1"),
        module._fail_key("tenant_1", "ww_1", "secret_2", "wm_1"),
    }
    assert len(name_keys) == len(fail_keys) == 4
    assert all("secret_" not in key for key in name_keys | fail_keys)


@pytest.mark.asyncio
async def test_unconfigured_or_internal_id_does_not_call_api(resolver):
    resolver._fetch = AsyncMock()
    assert await resolver.resolve("tenant_1", "ww_1", "", "wm_1") is None
    assert await resolver.resolve("tenant_1", "ww_1", "secret", "internal_user") is None
    resolver._fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_cached_token_is_refreshed_once(resolver, monkeypatch):
    token_key = module._token_key("tenant_1", "ww_1", "secret_1")
    module.redis_client.set(token_key, "expired-token")

    responses = []
    for payload in (
        {"errcode": 40014, "errmsg": "invalid token"},
        {"errcode": 0, "access_token": "fresh-token"},
        {"errcode": 0, "external_contact": {"name": "孙晨"}},
    ):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        responses.append(response)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(side_effect=responses)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)

    result = await resolver.resolve("tenant_1", "ww_1", "secret_1", "wm_1")

    assert result and result.display_name == "孙晨"
    assert client.get.await_count == 3
    assert client.get.await_args_list[1].args[0] == module._GET_TOKEN_URL
    assert client.get.await_args_list[2].kwargs["params"]["access_token"] == "fresh-token"
