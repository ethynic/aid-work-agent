"""视频生成 MiniMaxProvider 单元测试。

验证（mock httpx，不发真实请求）：
- submit 构造的 body 符合 MiniMax API 规范（content 数组、ratio 在 i2v 时 adaptive、aigc_watermark=False）
- submit 正常返回 task_id
- submit 非 200 / 缺 task_id 抛 MiniMaxProviderError
- poll 状态码映射：queued->PENDING、running->RUNNING、succeeded->SUCCEEDED、failed->FAILED、cancelled->CANCELED
- poll 解析 SUCCEEDED 返回 video_url + duration
- poll 解析 FAILED 返回 error
- get_options 返回 768P/2K、168h、supports_negative_prompt=False
- 不传 negative_prompt 时不报错（MiniMax 忽略）
- 缺 api_key 抛 MiniMaxProviderError
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.video_gen import minimax_provider as mp
from src.video_gen.base import BaseVideoProvider, VideoGenRequest
from src.video_gen.minimax_provider import MiniMaxProvider

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
        prompt="产品展示",
        reference_image_data_url="data:image/jpeg;base64,PROD",
        first_frame_data_url="data:image/jpeg;base64,MODEL",
        seed=42,
        negative_prompt="变形",     # MiniMax 应忽略
        duration=5,
        resolution="768P",
        ratio="9:16",
    )
    defaults.update(overrides)
    return VideoGenRequest(**defaults)


class TestSubmit:
    def test_submit_text_only_t2v(self):
        """文生视频（无 first_frame_image）：ratio 用显式 ratio，content 只有 text。"""
        resp = _resp(200, {"task_id": "mm-1"})
        client = FakeClient(post_resp=resp)
        req = _make_req(first_frame_data_url=None, reference_image_data_url=None)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.submit(req))

        assert result.task_id == "mm-1"
        assert result.task_status == "PENDING"
        body = client.last_body
        assert body["model"] == "MiniMax-H3"
        assert body["resolution"] == "768P"
        assert body["duration"] == 5
        assert body["ratio"] == "9:16"
        assert body["aigc_watermark"] is False
        # content 只有 text 项
        assert len(body["content"]) == 1
        assert body["content"][0]["type"] == "text"

    def test_submit_i2v_ratio_forced_adaptive(self):
        """图生视频（有 first_frame）：ratio 强制 adaptive，content 含 image_url 项作 first_frame。"""
        resp = _resp(200, {"task_id": "mm-2"})
        client = FakeClient(post_resp=resp)
        # 前端传 9:16，但有 first_frame，provider 应改为 adaptive
        req = _make_req(first_frame_data_url="data:image/jpeg;base64,MODEL")
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            _run(provider.submit(req))

        body = client.last_body
        assert body["ratio"] == "adaptive"
        assert len(body["content"]) == 2
        assert body["content"][0]["type"] == "text"
        assert body["content"][1]["type"] == "image_url"
        assert body["content"][1]["role"] == "first_frame"
        assert body["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,MODEL"

    def test_submit_i2v_fallback_to_reference_image(self):
        """无 first_frame 时，回退用 reference_image 作 first_frame（与万相一致）。"""
        resp = _resp(200, {"task_id": "mm-3"})
        client = FakeClient(post_resp=resp)
        req = _make_req(first_frame_data_url=None, reference_image_data_url="data:image/jpeg;base64,PROD")
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            _run(provider.submit(req))

        body = client.last_body
        # 回退到 reference_image，故仍是 i2v，ratio adaptive
        assert body["ratio"] == "adaptive"
        assert len(body["content"]) == 2
        assert body["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,PROD"

    def test_submit_negative_prompt_ignored(self):
        """MiniMax 不支持 negative_prompt，provider 内部忽略（不写入 body）。"""
        resp = _resp(200, {"task_id": "mm-4"})
        client = FakeClient(post_resp=resp)
        req = _make_req(negative_prompt="任何反向词")
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            _run(provider.submit(req))

        body = client.last_body
        # MiniMax body 无 negative_prompt 字段
        assert "negative_prompt" not in body

    def test_submit_aigc_watermark_false(self):
        """aigc_watermark 始终 False（项目自己烧录 AI 标识）。"""
        resp = _resp(200, {"task_id": "mm-5"})
        client = FakeClient(post_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            _run(provider.submit(_make_req()))
        assert client.last_body["aigc_watermark"] is False

    def test_submit_headers_bearer(self):
        """headers 含 Authorization: Bearer + Content-Type，无 X-DashScope-Async。"""
        resp = _resp(200, {"task_id": "mm-6"})
        client = FakeClient(post_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            _run(provider.submit(_make_req()))
        assert client.last_headers["Authorization"] == "Bearer sk-mm"
        assert client.last_headers["Content-Type"] == "application/json"
        assert "X-DashScope-Async" not in client.last_headers

    def test_submit_non_200_raises(self):
        resp = _resp(400, {"message": "bad request"})
        client = FakeClient(post_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            with pytest.raises(mp.MiniMaxProviderError):
                _run(provider.submit(_make_req()))

    def test_submit_missing_task_id_raises(self):
        resp = _resp(200, {"unexpected": "no task_id"})
        client = FakeClient(post_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            with pytest.raises(mp.MiniMaxProviderError):
                _run(provider.submit(_make_req()))

    def test_submit_invalid_resolution_raises(self):
        """resolution 不在 768P/2K 应抛错（防御性校验）。"""
        client = FakeClient(post_resp=_resp(200, {"task_id": "x"}))
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            with pytest.raises(mp.MiniMaxProviderError, match="resolution"):
                _run(provider.submit(_make_req(resolution="1080P")))

    def test_submit_invalid_duration_raises(self):
        """duration 不在 4-15 应抛错。"""
        client = FakeClient(post_resp=_resp(200, {"task_id": "x"}))
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            with pytest.raises(mp.MiniMaxProviderError, match="duration"):
                _run(provider.submit(_make_req(duration=20)))

    def test_missing_api_key_raises(self):
        with pytest.raises(mp.MiniMaxProviderError):
            MiniMaxProvider(api_key="")


class TestPoll:
    def test_poll_queued_maps_to_pending(self):
        resp = _resp(200, {"task": {"id": "t", "status": "queued"}})
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.poll("t"))
        assert result.task_status == "PENDING"
        assert result.video_url is None

    def test_poll_running_maps_to_running(self):
        resp = _resp(200, {"task": {"id": "t", "status": "running"}})
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.poll("t"))
        assert result.task_status == "RUNNING"

    def test_poll_succeeded_returns_video_url(self):
        resp = _resp(200, {
            "task": {
                "id": "t",
                "status": "succeeded",
                "content": {"url": "https://cdn.minimaxi.com/v.mp4"},
                "duration": 5,
            }
        })
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.poll("t"))
        assert result.task_status == "SUCCEEDED"
        assert result.video_url == "https://cdn.minimaxi.com/v.mp4"
        assert result.duration == 5
        assert result.error is None

    def test_poll_failed_returns_error(self):
        resp = _resp(200, {
            "task": {
                "id": "t",
                "status": "failed",
                "error": {"code": "1026", "message": "content violation"},
            }
        })
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.poll("t"))
        assert result.task_status == "FAILED"
        assert result.video_url is None
        assert "content violation" in (result.error or "")

    def test_poll_cancelled_maps_to_canceled(self):
        resp = _resp(200, {"task": {"id": "t", "status": "cancelled"}})
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.poll("t"))
        assert result.task_status == "CANCELED"

    def test_poll_unknown_status_maps_to_unknown(self):
        """未知状态码映射到 UNKNOWN（防御性，避免 provider 改了状态码导致 service 卡死）。"""
        resp = _resp(200, {"task": {"id": "t", "status": "weird_status"}})
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            result = _run(provider.poll("t"))
        assert result.task_status == "UNKNOWN"

    def test_poll_non_200_raises(self):
        resp = _resp(404, {"message": "task not found"})
        client = FakeClient(get_resp=resp)
        with patch.object(mp.httpx, "AsyncClient", return_value=client):
            provider = MiniMaxProvider(api_key="sk-mm")
            with pytest.raises(mp.MiniMaxProviderError):
                _run(provider.poll("missing"))


class TestGetOptions:
    def test_returns_minimax_options(self):
        provider = MiniMaxProvider(api_key="sk-mm")
        opts = provider.get_options()
        assert opts.provider == "minimax"
        # 分辨率 768P / 2K
        res_values = [r.value for r in opts.resolutions]
        assert res_values == ["768P", "2K"]
        # 7 个 ratio（含 adaptive）
        ratio_values = [r.value for r in opts.ratios]
        assert "adaptive" in ratio_values
        assert "9:16" in ratio_values
        assert len(ratio_values) == 7
        # durations 5/10/15
        dur_values = [int(d.value) for d in opts.durations]
        assert dur_values == [5, 10, 15]
        # 默认值
        assert opts.default_resolution == "768P"
        assert opts.default_ratio == "9:16"
        assert opts.default_duration == 5
        # 能力声明
        assert opts.supports_reference_image is True
        assert opts.supports_negative_prompt is False
        # 7 天 = 168h
        assert opts.task_max_age_hours == 168

    def test_resolutions_have_price_per_sec(self):
        """resolutions 应含 price_per_sec（前端展示性价比）。"""
        provider = MiniMaxProvider(api_key="sk-mm")
        opts = provider.get_options()
        prices = {r.value: r.price_per_sec for r in opts.resolutions}
        assert prices["768P"] == 0.50
        assert prices["2K"] == 0.80


class TestImplementsBase:
    def test_is_base_provider(self):
        provider = MiniMaxProvider(api_key="sk-mm")
        assert isinstance(provider, BaseVideoProvider)
        assert provider.name == "minimax"
