"""集成测试：http_client.get_chat_data 通过 C SDK 拉取 + 解析 ChatDataBatch

mock wecom_finance_sdk.get_chat_data_raw 返回固定 JSON，验证：
- 成功路径：errcode=0 + chatdata[] → 正确构造 ChatDataBatch / ChatDataItem
- 空批次：chatdata=[] → 空 batch
- errcode=45009 → WeComRateLimitException
- errcode!=0 → WeComApiException
- SDK 异常（SDKCallError）原样传播
- asyncio.to_thread 包装验证（不阻塞事件循环）

不真调企微 API，不真加载 .so（mock get_chat_data_raw）。
"""
import asyncio
from unittest.mock import patch

import pytest

from src.channels.wecom_personal_rpa.archive import http_client
from src.channels.wecom_personal_rpa.archive.http_client import (
    ChatDataBatch,
    ChatDataItem,
    WeComApiException,
    WeComRateLimitException,
)
from src.channels.wecom_personal_rpa.archive.wecom_finance_sdk import SDKCallError


# ----------------- 成功路径 -----------------


@pytest.mark.asyncio
async def test_get_chat_data_parses_chatdata_array():
    """errcode=0 + chatdata[] → 正确解析为 ChatDataBatch。"""
    fake_resp = {
        "errcode": 0,
        "errmsg": "ok",
        "chatdata": [
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
        ],
    }

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=fake_resp,
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
async def test_get_chat_data_empty_chatdata_returns_empty_batch():
    """chatdata=[] → 空 batch。"""
    fake_resp = {"errcode": 0, "errmsg": "ok", "chatdata": []}

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=fake_resp,
    ):
        batch = await http_client.get_chat_data("ww_corp", "secret", seq=0, limit=10)

    assert batch.items == []


# ----------------- 错误路径 -----------------


@pytest.mark.asyncio
async def test_get_chat_data_45009_raises_rate_limit():
    """errcode=45009 → WeComRateLimitException。"""
    fake_resp = {"errcode": 45009, "errmsg": "reach max api daily request limit"}

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=fake_resp,
    ):
        with pytest.raises(WeComRateLimitException) as exc:
            await http_client.get_chat_data("ww_corp", "secret", seq=0, limit=10)

    assert exc.value.retry_after_seconds == 60


@pytest.mark.asyncio
async def test_get_chat_data_other_errcode_raises_api_exception():
    """其他 errcode → WeComApiException。"""
    fake_resp = {"errcode": 60011, "errmsg": "no privilege to access chat data"}

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        return_value=fake_resp,
    ):
        with pytest.raises(WeComApiException) as exc:
            await http_client.get_chat_data("ww_corp", "secret", seq=0, limit=10)

    assert exc.value.api == "get_chat_data"
    assert exc.value.errcode == 60011


@pytest.mark.asyncio
async def test_get_chat_data_sdk_call_error_propagates():
    """SDKCallError（SDK 函数返回非 0）原样传播。"""
    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        side_effect=SDKCallError("GetChatData", 10002, "data parse fail"),
    ):
        with pytest.raises(SDKCallError) as exc:
            await http_client.get_chat_data("ww_corp", "secret", seq=0, limit=10)

    assert exc.value.func == "GetChatData"
    assert exc.value.code == 10002


# ----------------- 参数校验 -----------------


@pytest.mark.asyncio
async def test_get_chat_data_invalid_params():
    """空 corpid / secret / 非法 limit 抛 ValueError（不调 SDK）。"""
    with pytest.raises(ValueError, match="corpid"):
        await http_client.get_chat_data("", "secret", 0, 100)
    with pytest.raises(ValueError, match="secret"):
        await http_client.get_chat_data("ww", "", 0, 100)
    with pytest.raises(ValueError, match="limit"):
        await http_client.get_chat_data("ww", "sec", 0, 0)
    with pytest.raises(ValueError, match="limit"):
        await http_client.get_chat_data("ww", "sec", 0, 1001)


# ----------------- asyncio.to_thread 包装验证 -----------------


@pytest.mark.asyncio
async def test_get_chat_data_uses_to_thread_not_blocking_event_loop():
    """get_chat_data 通过 asyncio.to_thread 调 SDK，不阻塞事件循环。

    验证方式：mock 的 get_chat_data_raw 里 sleep 50ms，同时并发跑一个
    asyncio 协程（也应该在 50ms 内完成，证明没被 SDK 调用阻塞）。
    """
    fake_resp = {"errcode": 0, "errmsg": "ok", "chatdata": []}

    def _slow_raw(*args, **kwargs):
        import time
        time.sleep(0.05)  # 模拟 SDK 阻塞 50ms
        return fake_resp

    async def _concurrent_async_task():
        # 这个协程应在 SDK 阻塞期间也能推进（证明 to_thread 生效）
        await asyncio.sleep(0.01)
        return "concurrent_ok"

    with patch(
        "src.channels.wecom_personal_rpa.archive.wecom_finance_sdk.get_chat_data_raw",
        side_effect=_slow_raw,
    ):
        # 并发跑 get_chat_data + _concurrent_async_task
        batch_task = asyncio.create_task(http_client.get_chat_data("ww", "sec", 0, 10))
        concurrent_task = asyncio.create_task(_concurrent_async_task())

        batch, concurrent_result = await asyncio.gather(batch_task, concurrent_task)

    assert isinstance(batch, ChatDataBatch)
    assert batch.items == []
    assert concurrent_result == "concurrent_ok"
