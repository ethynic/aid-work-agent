"""微信客服：员工姓名反查（servicer_name）单元测试

覆盖：
1. resolve_servicer_name 共享辅助（src/channels/wecom_kf/servicer.py）：
   - 缓存未命中 -> 调 get_user 查姓名并写缓存
   - 缓存命中 -> 不再调 get_user
   - get_user 返回 errcode!=0 / 抛异常 -> 姓名为空、不写缓存
   - api_client 为空 -> 经 ChannelFactory 按租户配置兜底
   - 工厂取不到客户端 -> 空串
2. channel_routes._resolve_kf_servicer_name 委托（回归）与
   _persist_kf_servicer_message 的 metadata.servicer_name 落库
3. 员工消息落库后人工期任务入队门控（service_state 3/4 入队，其余不入队）
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.saas.api.channel_routes as cr_module
from src.channels.wecom_kf.servicer import resolve_servicer_name
from src.saas.api.channel_routes import _persist_kf_servicer_message, _resolve_kf_servicer_name

_SERVICER_REDIS = "src.channels.wecom_kf.servicer.redis_client"


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
@patch(_SERVICER_REDIS)
async def test_resolve_name_cache_miss_queries_and_caches(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(errcode=0, name="张三")

    name = await resolve_servicer_name("t1", "sv1", api_client=client)

    assert name == "张三"
    client.get_user.assert_awaited_once_with("sv1")
    mock_redis.set.assert_called_once()
    assert "t1:sv1" in mock_redis.set.call_args.args[0]


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_resolve_name_cache_hit_skips_api(mock_redis):
    mock_redis.get.return_value = "张三"
    client = _api_client()

    name = await resolve_servicer_name("t1", "sv1", api_client=client)

    assert name == "张三"
    client.get_user.assert_not_awaited()
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_resolve_name_errcode_nonzero_returns_empty(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(errcode=60111, name="")

    name = await resolve_servicer_name("t1", "bad_id", api_client=client)

    assert name == ""
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_resolve_name_api_raises_returns_empty(mock_redis):
    mock_redis.get.return_value = None
    client = _api_client(raises=True)

    name = await resolve_servicer_name("t1", "sv1", api_client=client)

    assert name == ""
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_resolve_name_empty_userid_returns_empty(mock_redis):
    name = await resolve_servicer_name("t1", "", api_client=_api_client())

    assert name == ""
    mock_redis.get.assert_not_called()


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_resolve_name_factory_fallback(mock_redis):
    """api_client 为空（后台 runner 场景）：经 ChannelFactory 按租户配置现取"""
    mock_redis.get.return_value = None
    adapter = MagicMock()
    adapter.api_client = _api_client(errcode=0, name="覃姗")

    with patch("src.saas.services.channel_factory.ChannelFactory.create_from_tenant_config",
               new=AsyncMock(return_value=(adapter, "cfg1", "pre-sales"))):
        name = await resolve_servicer_name("t1", "YeWeiYang")

    assert name == "覃姗"
    adapter.api_client.get_user.assert_awaited_once_with("YeWeiYang")
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_resolve_name_factory_unavailable_returns_empty(mock_redis):
    mock_redis.get.return_value = None

    with patch("src.saas.services.channel_factory.ChannelFactory.create_from_tenant_config",
               new=AsyncMock(return_value=(None, None, None))):
        name = await resolve_servicer_name("t1", "sv1")

    assert name == ""


# ==================== channel_routes 委托与落库 ====================


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
async def test_channel_routes_delegate_cache_hit(mock_redis):
    """channel_routes._resolve_kf_servicer_name 委托共享辅助（回归）"""
    mock_redis.get.return_value = "张三"
    client = _api_client()

    name = await _resolve_kf_servicer_name(client, "t1", "sv1")

    assert name == "张三"
    client.get_user.assert_not_awaited()


@pytest.mark.asyncio
@patch(_SERVICER_REDIS)
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


# ==================== 员工消息落库后人工期任务入队门控 ====================


def _session_with_state(state):
    return {"session_id": "s1", "metadata": {"service_state": state}}


@pytest.mark.asyncio
@patch("src.services.recap.runner.enqueue_human_period_tasks")
@patch(_SERVICER_REDIS)
async def test_persist_enqueues_in_human_state(mock_redis, mock_enqueue):
    """人工接待中(3)：员工消息落库后入队，round=msgid"""
    mock_redis.get.return_value = "张三"
    client = _api_client(errcode=0, name="张三")

    with patch.object(cr_module.channel_session_manager, "get_or_create_session",
                      return_value=_session_with_state(3)), \
         patch.object(cr_module.channel_session_manager, "add_message"):
        await _persist_kf_servicer_message(
            _servicer_msg(msgid="m_svc_1"), "kfid", "t1", "sub1", api_client=client
        )

    mock_enqueue.assert_called_once_with("t1", "s1", "m_svc_1")


@pytest.mark.asyncio
@patch("src.services.recap.runner.enqueue_human_period_tasks")
@patch(_SERVICER_REDIS)
async def test_persist_enqueues_in_ended_state(mock_redis, mock_enqueue):
    """会话已结束(4)：员工收尾发言同样入队"""
    mock_redis.get.return_value = None
    client = _api_client(errcode=0, name="张三")

    with patch.object(cr_module.channel_session_manager, "get_or_create_session",
                      return_value=_session_with_state(4)), \
         patch.object(cr_module.channel_session_manager, "add_message"):
        await _persist_kf_servicer_message(
            _servicer_msg(), "kfid", "t1", "sub1", api_client=client
        )

    mock_enqueue.assert_called_once()


@pytest.mark.asyncio
@patch("src.services.recap.runner.enqueue_human_period_tasks")
@patch(_SERVICER_REDIS)
async def test_persist_no_enqueue_in_ai_state(mock_redis, mock_enqueue):
    """智能体接待中(1)：员工手动插话不触发推送任务"""
    mock_redis.get.return_value = None
    client = _api_client(errcode=0, name="张三")

    with patch.object(cr_module.channel_session_manager, "get_or_create_session",
                      return_value=_session_with_state(1)), \
         patch.object(cr_module.channel_session_manager, "add_message"):
        await _persist_kf_servicer_message(
            _servicer_msg(), "kfid", "t1", "sub1", api_client=client
        )

    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
@patch("src.services.recap.runner.enqueue_human_period_tasks")
@patch(_SERVICER_REDIS)
async def test_persist_no_enqueue_without_msgid(mock_redis, mock_enqueue):
    """msgid 缺失：不入队（round_message_id 守卫同样要求，双保险）"""
    mock_redis.get.return_value = None
    client = _api_client(errcode=0, name="张三")

    with patch.object(cr_module.channel_session_manager, "get_or_create_session",
                      return_value=_session_with_state(3)), \
         patch.object(cr_module.channel_session_manager, "add_message"):
        await _persist_kf_servicer_message(
            _servicer_msg(msgid=""), "kfid", "t1", "sub1", api_client=client
        )

    mock_enqueue.assert_not_called()
