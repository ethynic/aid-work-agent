"""视频生成 WanxProvider 单元测试。

验证（mock httpx，不发真实请求）：
- submit 构造的 body 符合 spike 规格（reference_image + first_frame，negative_prompt 在 parameters）
- submit 正常返回 task_id
- submit 非 200 / 缺 task_id 抛 WanxProviderError
- poll 解析 SUCCEEDED 返回 video_url
- poll 解析 FAILED 返回 error
- get_options 返回 720P/1080P、24h、supports_negative_prompt=True
- WanxProvider 实现 BaseVideoProvider ABC
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.video_gen import wanx_provider as wp
from src.video_gen.base import BaseVideoProvider, VideoGenRequest
from src.video_gen.wanx_provider import WanxProvider

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


def _make_req(**overrides) -> VideoGenRequest:
    """构造默认请求；可覆盖字段。"""
    defaults = dict(
        prompt="prompt",
        reference_image_data_url="data:image/jpeg;base64,xxx",
        first_frame_data_url="data:image/jpeg;base64,xxx",
        seed=42,
        negative_prompt="",
        duration=5,
        resolution="720P",
        ratio="9:16",
    )
    defaults.update(overrides)
    return VideoGenRequest(**defaults)


class TestSubmit:
    def test_submit_same_image(self):
        """产品图同时作 reference_image + first_frame（无模特图场景）。"""
        resp = _resp(200, {"output": {"task_id": "abc-123", "task_status": "PENDING"}, "request_id": "r"})
        client = FakeClient(post_resp=resp)
        img = "data:image/jpeg;base64,xxx"
        req = _make_req(reference_image_data_url=img, first_frame_data_url=img)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            result = _run(provider.submit(req))

        assert result.task_id == "abc-123"
        body = client.last_body
        media_arr = body["input"]["media"]
        assert media_arr[0]["type"] == "reference_image"
        assert media_arr[1]["type"] == "first_frame"
        # 同图：两个 slot url 相同
        assert media_arr[0]["url"] == img
        assert media_arr[1]["url"] == img
        params = body["parameters"]
        assert params["seed"] == 42
        assert params["prompt_extend"] is False
        assert params["watermark"] is False
        assert "negative_prompt" in params
        assert client.last_headers.get("X-DashScope-Async") == "enable"

    def test_submit_distinct_images(self):
        """产品图作 reference_image、模特图作 first_frame（异图场景）。"""
        resp = _resp(200, {"output": {"task_id": "abc-123", "task_status": "PENDING"}})
        client = FakeClient(post_resp=resp)
        product_img = "data:image/jpeg;base64,PROD"
        model_img = "data:image/jpeg;base64,MODEL"
        req = _make_req(reference_image_data_url=product_img, first_frame_data_url=model_img)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            _run(provider.submit(req))

        body = client.last_body
        media_arr = body["input"]["media"]
        # reference_image = 产品图，first_frame = 模特图（异图）
        assert media_arr[0]["url"] == product_img
        assert media_arr[1]["url"] == model_img
        assert media_arr[0]["url"] != media_arr[1]["url"]

    def test_submit_first_frame_fallback_to_reference(self):
        """无 first_frame 时，回退到 reference_image（与旧逻辑一致）。"""
        resp = _resp(200, {"output": {"task_id": "abc", "task_status": "PENDING"}})
        client = FakeClient(post_resp=resp)
        product_img = "data:image/jpeg;base64,PROD"
        req = _make_req(reference_image_data_url=product_img, first_frame_data_url=None)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            _run(provider.submit(req))
        body = client.last_body
        # first_frame 回退到 reference_image
        assert body["input"]["media"][0]["url"] == product_img
        assert body["input"]["media"][1]["url"] == product_img

    def test_submit_non_200_raises(self):
        resp = _resp(400, {"message": "bad request"})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError):
                _run(provider.submit(_make_req()))

    def test_submit_missing_task_id_raises(self):
        resp = _resp(200, {"output": {}})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError):
                _run(provider.submit(_make_req()))

    def test_submit_network_error_raises(self):
        client = FakeClient(post_resp=httpx.ConnectError("conn refused"))
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError):
                _run(provider.submit(_make_req()))

    def test_missing_api_key_raises(self):
        with pytest.raises(wp.WanxProviderError):
            wp.WanxProvider(api_key="")

    def test_submit_invalid_duration_raises(self):
        """duration 不在 (5,10,15) 时应抛 WanxProviderError（防御性校验，防前端脏数据直传阿里云）。"""
        client = FakeClient(post_resp=_resp(200, {"output": {"task_id": "t", "task_status": "PENDING"}}))
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError, match="duration"):
                _run(provider.submit(_make_req(duration=20)))

    def test_submit_duration_5_passes(self):
        """duration=5 应通过校验（万相 r2v 最短时长）。"""
        resp = _resp(200, {"output": {"task_id": "abc", "task_status": "PENDING"}})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            result = _run(provider.submit(_make_req(duration=5)))
        assert result.task_id == "abc"
        # 验证 duration 已写入 body
        assert client.last_body["parameters"]["duration"] == 5

    def test_submit_duration_15_passes(self):
        """duration=15 应通过校验（万相 r2v 最长时长）。"""
        resp = _resp(200, {"output": {"task_id": "abc", "task_status": "PENDING"}})
        client = FakeClient(post_resp=resp)
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            _run(provider.submit(_make_req(duration=15)))
        assert client.last_body["parameters"]["duration"] == 15

    def test_submit_missing_reference_image_raises(self):
        """万相必填 reference_image_data_url，缺失应抛错。"""
        client = FakeClient(post_resp=_resp(200, {"output": {"task_id": "t"}}))
        with patch.object(wp.httpx, "AsyncClient", return_value=client):
            provider = wp.WanxProvider(api_key="sk-test")
            with pytest.raises(wp.WanxProviderError, match="reference_image"):
                _run(provider.submit(_make_req(reference_image_data_url=None)))


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


class TestGetOptions:
    def test_returns_wanx_options(self):
        provider = wp.WanxProvider(api_key="sk-test")
        opts = provider.get_options()
        assert opts.provider == "wanx"
        res_values = [r.value for r in opts.resolutions]
        assert res_values == ["720P", "1080P"]
        ratio_values = [r.value for r in opts.ratios]
        assert ratio_values == ["9:16", "16:9", "1:1", "4:3", "3:4"]
        dur_values = [int(d.value) for d in opts.durations]
        assert dur_values == [5, 10, 15]
        assert opts.default_resolution == "720P"
        assert opts.default_ratio == "9:16"
        assert opts.default_duration == 5
        assert opts.supports_reference_image is True
        assert opts.supports_negative_prompt is True
        assert opts.task_max_age_hours == 24

    def test_resolutions_have_price_per_sec(self):
        """resolutions 应含 price_per_sec（前端展示性价比）。"""
        provider = wp.WanxProvider(api_key="sk-test")
        opts = provider.get_options()
        prices = {r.value: r.price_per_sec for r in opts.resolutions}
        assert prices["720P"] == 0.60
        assert prices["1080P"] == 1.00


class TestImplementsBase:
    def test_is_base_provider(self):
        provider = wp.WanxProvider(api_key="sk-test")
        assert isinstance(provider, BaseVideoProvider)
        assert provider.name == "wanx"
