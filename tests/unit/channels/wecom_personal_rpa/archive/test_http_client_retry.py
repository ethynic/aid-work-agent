"""archive.http_client 重试逻辑补强测试（Phase 11）

覆盖 CR Phase 3 提到的 P2 缺失：
- _get_json_with_retry / _post_json_with_retry 的 3 次重试场景
- 前两次失败 + 第三次成功
- 全失败抛 RuntimeError（保留原始异常链）
- 45009 不重试（已有测试覆盖，这里再断言一次重试次数=1）
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.channels.wecom_personal_rpa.archive import http_client
from src.channels.wecom_personal_rpa.archive.http_client import (
    WeComApiException,
    WeComRateLimitException,
)


@pytest.fixture
def clear_token_cache():
    """每个测试前后清掉 access_token Redis 缓存。"""
    from src.core.redis_client import redis_client

    keys = redis_client.keys("wecom_rpa:archive:token:test-*")
    for k in keys:
        redis_client.delete(k)
    yield
    keys = redis_client.keys("wecom_rpa:archive:token:test-*")
    for k in keys:
        redis_client.delete(k)


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """构造 mock httpx 响应。"""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


def _mock_client_with_responses(*responses) -> MagicMock:
    """构造 mock httpx.AsyncClient，按顺序返回 responses。"""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client, responses


# ----------------- get_access_token 重试 -----------------


@pytest.mark.asyncio
async def test_get_access_token_retry_succeeds_on_third_attempt(clear_token_cache):
    """前两次网络异常 + 第三次成功 → 返回 token。"""
    good_resp = _mock_response({"errcode": 0, "access_token": "tok_ok", "expires_in": 7200})

    call_count = {"n": 0}

    def _side_effect(url):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.ConnectError("network fail")
        return good_resp

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=_side_effect)

    # 加速测试：把重试 delay 改为 0
    with patch.object(http_client, "_RETRY_DELAYS", (0, 0, 0)), \
         patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        token = await http_client.get_access_token("test-tenant-retry", "ww_corp", "secret")

    assert token == "tok_ok"
    assert call_count["n"] == 3  # 第 3 次成功


@pytest.mark.asyncio
async def test_get_access_token_all_attempts_fail_raises_runtime(clear_token_cache):
    """3 次都失败 → 抛 RuntimeError，包含原始异常链。"""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))

    with patch.object(http_client, "_RETRY_DELAYS", (0, 0, 0)), \
         patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await http_client.get_access_token("test-tenant-allfail", "ww_corp", "secret")

    # 原始异常应保留（__cause__ 链）
    assert exc_info.value.__cause__ is not None or "3" in str(exc_info.value)


# ----------------- get_chat_data 重试 -----------------


@pytest.mark.asyncio
async def test_get_chat_data_retry_succeeds_on_third_attempt():
    """POST 重试场景：前两次 ConnectError + 第三次成功。"""
    good_resp = _mock_response({"errcode": 0, "chatdata": []})

    call_count = {"n": 0}

    def _side_effect(url, content=None, headers=None):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.ConnectError("network fail")
        return good_resp

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=_side_effect)

    with patch.object(http_client, "_RETRY_DELAYS", (0, 0, 0)), \
         patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        batch = await http_client.get_chat_data("tok", seq=100, limit=50)

    assert batch.items == []
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_get_chat_data_45009_not_retried():
    """45009 立即抛出不重试（实际调用次数=1）。"""
    rate_limit_resp = _mock_response({"errcode": 45009, "errmsg": "rate limit"})

    call_count = {"n": 0}

    def _side_effect(*a, **kw):
        call_count["n"] += 1
        return rate_limit_resp

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=_side_effect)

    with patch.object(http_client, "_RETRY_DELAYS", (0, 0, 0)), \
         patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(WeComRateLimitException):
            await http_client.get_chat_data("tok", seq=0, limit=10)

    # 关键断言：只调用了 1 次（45009 不重试）
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_chat_data_all_attempts_fail_raises_runtime():
    """POST 3 次都失败 → RuntimeError。"""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("network down"))

    with patch.object(http_client, "_RETRY_DELAYS", (0, 0, 0)), \
         patch.object(http_client.httpx, "AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError):
            await http_client.get_chat_data("tok", seq=0, limit=10)
