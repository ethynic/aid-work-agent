"""企微错误码简释映射（error_hints）与 _request 注入测试"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.channels.wecom_kf.api_client import WeComKfApiClient
from src.channels.wecom_kf.error_hints import WECHAT_KF_ERROR_HINTS, append_known_error_hint

pytestmark = pytest.mark.tools


class TestAppendKnownErrorHint:
    def test_known_errcode_appends_hint(self):
        enriched = append_known_error_hint(95003, "send msg quota limit")
        assert "send msg quota limit" in enriched
        assert "已知错误[95003]" in enriched
        assert "未验证企业" in enriched

    def test_unknown_errcode_passthrough(self):
        assert append_known_error_hint(12345, "whatever") == "whatever"

    @pytest.mark.parametrize("code", sorted(WECHAT_KF_ERROR_HINTS))
    def test_all_registered_hints_nonempty(self, code):
        enriched = append_known_error_hint(code, "orig")
        assert "已知错误" in enriched and "orig" in enriched

    @pytest.mark.parametrize("bad_code", [None, "-1", "abc", 1.5])
    def test_non_int_errcode_no_crash(self, bad_code):
        assert append_known_error_hint(bad_code, "orig") == "orig"

    def test_missing_errmsg_known_code(self):
        enriched = append_known_error_hint(45009, None)
        assert "已知错误[45009]" in enriched


def _fake_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, text='{"errcode": 0}', **{"json": lambda: payload})


@pytest.mark.asyncio
async def test_request_enriches_known_errcode_errmsg():
    client = WeComKfApiClient("corp", "secret")
    payload = {"errcode": 95003, "errmsg": "send msg quota limit"}
    fake = AsyncMock()
    fake.post = AsyncMock(return_value=_fake_response(payload))
    with patch.object(client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(client, "_get_client", new=AsyncMock(return_value=fake)):
        result = await client._request("POST", "/cgi-bin/kf/send_msg", json_body={})
    assert result["errcode"] == 95003
    assert "未验证企业" in result["errmsg"]
    assert "send msg quota limit" in result["errmsg"]


@pytest.mark.asyncio
async def test_request_keeps_unknown_errcode_errmsg():
    client = WeComKfApiClient("corp", "secret")
    payload = {"errcode": 61040, "errmsg": "not related"}
    fake = AsyncMock()
    fake.post = AsyncMock(return_value=_fake_response(payload))
    with patch.object(client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(client, "_get_client", new=AsyncMock(return_value=fake)):
        result = await client._request("POST", "/cgi-bin/kf/send_msg", json_body={})
    assert result["errmsg"] == "not related"


@pytest.mark.asyncio
async def test_request_success_untouched():
    client = WeComKfApiClient("corp", "secret")
    payload = {"errcode": 0, "msg_list": []}
    fake = AsyncMock()
    fake.post = AsyncMock(return_value=_fake_response(payload))
    with patch.object(client, "get_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(client, "_get_client", new=AsyncMock(return_value=fake)):
        result = await client._request("POST", "/cgi-bin/kf/sync_msg", json_body={})
    assert result["errcode"] == 0
    assert "已知错误" not in str(result)
