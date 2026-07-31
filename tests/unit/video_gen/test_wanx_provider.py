"""视频生成 WanxProvider 单元测试。

验证（mock httpx，不发真实请求）：
- submit 构造的 body 符合 spike 规格（reference_image + first_frame，negative_prompt 在 parameters）
- submit 正常返回 task_id
- submit 非 200 / 缺 task_id 抛 WanxProviderError
- poll 解析 SUCCEEDED 返回 video_url
- poll 解析 FAILED 返回 error
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.video_gen import wanx_provider as wp

pytestmark = [pytest.mark.unit]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.text = str(payload)
    r.json.return_value = payload
    return r


class FakeClient:
    """模拟 httpx.AsyncClient 上下文管理器。"""

    def __init__(self, post_resp=None, get_resp=None):
        self._post = post_resp
        self._get = get_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.last_body = json
        self.last_headers = headers
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    async def get(self, url, headers=None):
        if isinstance(self._get, Exception):
            raise self._get
        return self._get


class TestSubmit:
    def test_submit_returns_task_id(self):
        resp = _resp(200, {"output": {"task_id": "abc-123", "task_status": "PENDING"}, "request_id": "r"})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            result = _run(provider.submit("prompt", "data:image/jpeg;base64,xxx", seed=42))

        assert result.task_id == "abc-123"
        assert result.task_status == "PENDING"
        # 校验 body 结构（spike 规格）
        body = client.last_body
        assert body["model"] == "wan2.7-r2v-2026-06-12"
        assert body["input"]["prompt"] == "prompt"
        media_arr = body["input"]["media"]
        assert media_arr[0]["type"] == "reference_image"
        assert media_arr[1]["type"] == "first_frame"
        # reference_image 和 first_frame 都传同一张图
        assert media_arr[0]["url"] == media_arr[1]["url"]
        params = body["parameters"]
        assert params["seed"] == 42
        assert params["prompt_extend"] is False
        assert params["watermark"] is False
        assert "negative_prompt" in params
        # header 含 X-DashScope-Async
        assert client.last_headers.get("X-DashScope-Async") == "enable"
        assert client.last_headers.get("Authorization") == "Bearer sk-test"

    def test_submit_non_200_raises(self):
        resp = _resp(400, {"message": "bad request"})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError):
                _run(provider.submit("p", "data:url", seed=1))

    def test_submit_missing_task_id_raises(self):
        resp = _resp(200, {"output": {}})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError):
                _run(provider.submit("p", "data:url", seed=1))

    def test_submit_network_error_raises(self):
        client = FakeClient(post_resp=httpx.ConnectError("conn refused"))
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError):
                _run(provider.submit("p", "data:url", seed=1))

    def test_missing_api_key_raises(self):
        with pytest.raises(wp.WanxProviderError):
            wp.WanxProvider(api_key="")


class TestPoll:
    def test_poll_succeeded_returns_video_url(self):
        resp = _resp(200, {
            "output": {"task_id": "t1", "task_status": "SUCCEEDED", "video_url": "https://x/y.mp4"},
            "usage": {"output_video_duration": 5},
        })
        client = FakeClient(get_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            result = _run(provider.poll("t1"))

        assert result.task_status == "SUCCEEDED"
        assert result.video_url == "https://x/y.mp4"
        assert result.duration == 5
        assert result.error is None

    def test_poll_failed_returns_error(self):
        resp = _resp(200, {"output": {"task_status": "FAILED", "message": "content violation"}})
        client = FakeClient(get_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            result = _run(provider.poll("t1"))

        assert result.task_status == "FAILED"
        assert result.video_url is None
        assert "content violation" in (result.error or "")

    def test_poll_running_returns_no_url(self):
        resp = _resp(200, {"output": {"task_status": "RUNNING"}})
        client = FakeClient(get_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            result = _run(provider.poll("t1"))

        assert result.task_status == "RUNNING"
        assert result.video_url is None
