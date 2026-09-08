"""微信客服：员工姓名反查（servicer_name）单元测试

覆盖 _resolve_kf_servicer_name 与 _persist_kf_servicer_message 的 metadata：
- 缓存未命中 -> 调 get_user 查姓名并写缓存，metadata.servicer_name 落库
- 缓存命中 -> 不再调 get_user
- get_user 返回 errcode!=0 -> 姓名为空、不写缓存
- get_user 抛异常 -> 姓名为空，不影响落库
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.saas.api.channel_routes as cr_module
from src.saas.api.channel_routes import _persist_kf_servicer_message, _resolve_kf_servicer_name


def _servicer_msg(servicer="sv1", msgid="m1"):
    return {
        "msgid": msgid,
        "origin": 5,
        "msgtype": "text",
        "external_userid": "u1",
        "send_time": 1720000000,
        "servicer_userid": servicer,
        "text": {"content": "您好，我是人工客服"},
    }


def _api_client(errcode=0, name="张三", raises=False):
    client = MagicMock()
    if raises:
        client.get_user = AsyncMock(side_effect=RuntimeError("network down"))
    else:
        client.get_user = AsyncMock(return_value={"errcode": errcode, "name": name})
    return client


@pytest.mark.asyncio
@patch("src.saas.api.channel_routes.redis_client")
async def test_resolve_name_cache_miss_queries_and_caches(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(errcode=0, name="张三")

    name = await _resolve_kf_servicer_name(client, "t1", "sv1")

    assert name == "张三"
    client.get_user.assert_awaited_once_with("sv1")
    mock_redis.set.assert_called_once()
    assert "t1:sv1" in mock_redis.set.call_args.args[0]


@pytest.mark.asyncio
@patch("src.saas.api.channel_routes.redis_client")
async def test_resolve_name_cache_hit_skips_api(mock_redis):
    mock_redis.get.return_value = "张三"
    client = _api_client()

    name = await _resolve_kf_servicer_name(client, "t1", "sv1")

    assert name == "张三"
    client.get_user.assert_not_awaited()
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
@patch("src.saas.api.channel_routes.redis_client")
async def test_resolve_name_errcode_nonzero_returns_empty(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(errcode=60111, name="")

    name = await _resolve_kf_servicer_name(client, "t1", "bad_id")

    assert name == ""
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
@patch("src.saas.api.channel_routes.redis_client")
async def test_resolve_name_api_raises_returns_empty(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(raises=True)

    name = await _resolve_kf_servicer_name(client, "t1", "sv1")

    assert name == ""
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
@patch("src.saas.api.channel_routes.redis_client")
async def test_persist_message_metadata_contains_servicer_name(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(errcode=0, name="张三")

    with patch.object(cr_module.channel_session_manager, "get_or_create_session",
                      return_value={"session_id": "s1", "metadata": {}}), \
         patch.object(cr_module.channel_session_manager, "add_message") as add_mock:
        await _persist_kf_servicer_message(
            _servicer_msg(), "kfid", "t1", "sub1", api_client=client
        )

    add_mock.assert_called_once()
    metadata = add_mock.call_args.kwargs["metadata"]
    assert metadata["source"] == "servicer"
    assert metadata["servicer_userid"] == "sv1"
    assert metadata["servicer_name"] == "张三"
