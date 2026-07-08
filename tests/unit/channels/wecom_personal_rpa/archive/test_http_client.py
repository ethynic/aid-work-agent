"""archive.http_client 单元测试

覆盖：
- get_access_token 成功路径（含 Redis 缓存命中/未命中）—— 仍走 HTTP
- get_access_token errcode != 0 抛 WeComApiException
- get_chat_data 成功路径（解析 chatdata 数组为 ChatDataItem）—— 走 C SDK
- get_chat_data errcode=45009 抛 WeComRateLimitException
- get_chat_data errcode != 0 抛 WeComApiException
- 参数校验
- 缓存命中跳过 HTTP 调用

注意：get_chat_data 已从 HTTP 改为 C SDK 调用（见 wecom_finance_sdk.py），
本测试 mock wecom_finance_sdk.get_chat_data_raw 验证 http_client 层的 JSON 解析逻辑。
"""
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa.archive import http_client
from src.channels.wecom_personal_rpa.archive.http_client import (
    ChatDataItem,
    WeComApiException,
    WeComRateLimitException,
)


# ----------------- get_access_token（仍走 HTTP） -----------------


@pytest.fixture
def clear_token_cache():
    """每个测试前后清掉 access_token Redis 缓存。"""
    from src.core.redis_client import redis_client

    # 清所有 archive token 缓存键（测试用 tenant_id 都以 "test-" 开头）
    keys = redis_client.keys("wecom_rpa:archive:token:test-*")
    for k in keys:
        redis_client.delete(k)
    yield
    keys = redis_client.keys("wecom_rpa:archive:token:test-*")
    for k in keys:
        redis_client.delete(k)


@pytest.mark.asyncio
async def test_get_access_token_success(clear_token_cache):
    """成功获取 access_token，写入 Redis 缓存。"""
    response_data = {"errcode": 0, "errmsg": "ok", "access_token": "tok_abc123", "expires_in": 7200}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_data)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        token = await http_client.get_access_token("test-tenant", "ww_corp_1", "archive_secret")

    assert token == "tok_abc123"
    mock_client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_access_token_cache_hit(clear_token_cache):
    """缓存命中时不发 HTTP 请求。"""
    from src.core.redis_client import redis_client

    redis_client.set("wecom_rpa:archive:token:test-tenant:ww_corp_1", "cached_token", ex=6000)

    with patch.object(http_client.httpx, "AsyncClient") as mock_async_client:
        token = await http_client.get_access_token("test-tenant", "ww_corp_1", "secret")
        # 不应创建任何 httpx 客户端
        mock_async_client.assert_not_called()

    assert token == "cached_token"


@pytest.mark.asyncio
async def test_get_access_token_errcode_raises(clear_token_cache):
    """errcode != 0 抛 WeComApiException。"""
    response_data = {"errcode": 40013, "errmsg": "invalid corpid"}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_data)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(WeComApiException) as exc:
            await http_client.get_access_token("test-tenant", "ww_corp_1", "secret")

    assert exc.value.api == "gettoken"
    assert exc.value.errcode == 40013


@pytest.mark.asyncio
async def test_get_access_token_empty_params_raises():
    with pytest.raises(ValueError, match="tenant_id"):
        await http_client.get_access_token("", "corp", "sec")
    with pytest.raises(ValueError, match="corpid"):
        await http_client.get_access_token("t", "", "sec")
    with pytest.raises(ValueError, match="secret"):
        await http_client.get_access_token("t", "corp", "")


# ----------------- get_chat_data（走 C SDK） -----------------


def _make_chat_data_response(items):
    """构造 get_chat_data 成功响应。"""
    return {
        "errcode": 0,
        "errmsg": "ok",
        "chatdata": items,
    }


@pytest.mark.asyncio
async def test_get_chat_data_success():
    """成功解析 chatdata 数组。"""
    items_raw = [
        {
            "seq": 1001,
            "msgid": "msg_001",
            "action": "upload",
            "from": "user_a",
            "tolist": ["user_b"],
            "roomid": None,
            "msgtime": 1700000000,
            "msgtype": "text",
            "encrypt_random_key": "rk_b64_1",
            "encrypt_chat_msg": "cm_b64_1",
        },
        {
            "seq": 1002,
            "msgid": "msg_002",
            "action": "recall",
            "from": "user_a",
            "tolist": [],
            "roomid": "room_x",
            "msgtime": 1700000010,
            "msgtype": "revoke",
            "encrypt_random_key": "rk_b64_2",
            "encrypt_chat_msg": "cm_b64_2",
        },
    ]
    response_data = _make_chat_data_response(items_raw)

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=response_data,
    ) as mock_raw:
        batch = await http_client.get_chat_data("ww_corp", "secret", seq=1000, limit=100)

    mock_raw.assert_called_once_with("ww_corp", "secret", 1000, 100)
    assert len(batch.items) == 2
    item1 = batch.items[0]
    assert item1.seq == 1001
    assert item1.msg_id == "msg_001"
    assert item1.action == "upload"
    assert item1.from_ == "user_a"
    assert item1.tolist == ["user_b"]
    assert item1.roomid is None
    assert item1.msg_time == 1700000000
    assert item1.msg_type == "text"
    assert item1.encrypt_random_key == "rk_b64_1"
    assert item1.encrypt_chat_msg == "cm_b64_1"

    item2 = batch.items[1]
    assert item2.action == "recall"
    assert item2.tolist == []
    assert item2.roomid == "room_x"


@pytest.mark.asyncio
async def test_get_chat_data_empty_chatdata():
    """chatdata 为空数组返回空 batch。"""
    response_data = {"errcode": 0, "errmsg": "ok", "chatdata": []}

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=response_data,
    ):
        batch = await http_client.get_chat_data("ww_corp", "secret", seq=1000, limit=100)

    assert batch.items == []


@pytest.mark.asyncio
async def test_get_chat_data_rate_limited():
    """errcode=45009 抛 WeComRateLimitException。"""
    response_data = {"errcode": 45009, "errmsg": "reach max api daily request limit"}

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=response_data,
    ):
        with pytest.raises(WeComRateLimitException) as exc:
            await http_client.get_chat_data("ww_corp", "secret", seq=1000, limit=100)

    assert exc.value.retry_after_seconds == 60


@pytest.mark.asyncio
async def test_get_chat_data_other_errcode_raises():
    """其他 errcode 抛 WeComApiException。"""
    response_data = {"errcode": 40014, "errmsg": "invalid access_token"}

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=response_data,
    ):
        with pytest.raises(WeComApiException) as exc:
            await http_client.get_chat_data("ww_corp", "secret", seq=1000, limit=100)

    assert exc.value.api == "get_chat_data"
    assert exc.value.errcode == 40014


@pytest.mark.asyncio
async def test_get_chat_data_invalid_params():
    with pytest.raises(ValueError, match="corpid"):
        await http_client.get_chat_data("", "secret", seq=0, limit=100)
    with pytest.raises(ValueError, match="secret"):
        await http_client.get_chat_data("ww", "", seq=0, limit=100)
    with pytest.raises(ValueError, match="limit"):
        await http_client.get_chat_data("ww", "sec", seq=0, limit=0)
    with pytest.raises(ValueError, match="limit"):
        await http_client.get_chat_data("ww", "sec", seq=0, limit=1001)
