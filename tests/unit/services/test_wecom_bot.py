"""企微群机器人发送单测（wecom_bot，招聘面试邀约通知 Phase 1）

全部 monkeypatch httpx.AsyncClient（仓库无 respx，沿
test_external_contact_resolver.py 的 MagicMock + AsyncMock stub 范式），
不发出真实网络请求。覆盖：errcode 校验、失败重试 1 次、网络异常不抛、
超长 markdown 按行边界截断分多条、text 消息 mentioned_mobile_list 结构。
"""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.services import wecom_bot as module

pytestmark = pytest.mark.unit

URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key"


def _make_client(responses):
    """构造 httpx.AsyncClient 替身：responses 依次出队（dict=正常响应 / Exception=网络异常）"""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    def side_effect(url, json=None):
        item = responses.pop(0) if responses else {"errcode": 0, "errmsg": "ok"}
        if isinstance(item, Exception):
            raise item
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = item
        return resp

    client.post = AsyncMock(side_effect=side_effect)
    return client


def _patch_client(monkeypatch, responses):
    client = _make_client(responses)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)
    return client


class TestSendMarkdown:
    async def test_success_single_post(self, monkeypatch):
        client = _patch_client(monkeypatch, [{"errcode": 0, "errmsg": "ok"}])
        ok, err = await module.send_markdown(URL, "【面试邀约知会】PHP开发工程师")
        assert ok is True and err is None
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        assert args[0] == URL
        assert kwargs["json"] == {
            "msgtype": "markdown",
            "markdown": {"content": "【面试邀约知会】PHP开发工程师"},
        }

    async def test_errcode_failure_retries_once_then_success(self, monkeypatch):
        client = _patch_client(
            monkeypatch,
            [{"errcode": 93000, "errmsg": "not robot webhook"}, {"errcode": 0, "errmsg": "ok"}],
        )
        ok, err = await module.send_markdown(URL, "内容")
        assert ok is True and err is None
        assert client.post.await_count == 2  # 失败重试 1 次

    async def test_errcode_failure_after_retry_returns_error(self, monkeypatch):
        client = _patch_client(
            monkeypatch,
            [{"errcode": 93000, "errmsg": "bad"}, {"errcode": 93000, "errmsg": "bad"}],
        )
        ok, err = await module.send_markdown(URL, "内容")
        assert ok is False
        assert "93000" in err
        assert client.post.await_count == 2

    async def test_network_exception_does_not_raise(self, monkeypatch):
        """两次都网络异常：不外抛，返回 (False, 网络异常文案)"""
        client = _patch_client(monkeypatch, [httpx.ConnectError("refused"), httpx.ConnectTimeout("timeout")])
        ok, err = await module.send_markdown(URL, "内容")
        assert ok is False
        assert "网络异常" in err
        assert client.post.await_count == 2

    async def test_network_exception_then_success(self, monkeypatch):
        client = _patch_client(monkeypatch, [httpx.ConnectError("refused"), {"errcode": 0, "errmsg": "ok"}])
        ok, err = await module.send_markdown(URL, "内容")
        assert ok is True and err is None

    async def test_empty_url_or_content(self, monkeypatch):
        client = _patch_client(monkeypatch, [])
        ok, err = await module.send_markdown("", "内容")
        assert ok is False and "为空" in err
        ok, err = await module.send_markdown(URL, "")
        assert ok is False and "为空" in err
        client.post.assert_not_awaited()

    async def test_whitespace_only_content_no_fake_success(self, monkeypatch):
        """纯空白（切块后为空）不发假成功：与空串契约一致返回 False"""
        client = _patch_client(monkeypatch, [])
        ok, err = await module.send_markdown(URL, "\n" * 3000)
        assert ok is False and "为空" in err
        client.post.assert_not_awaited()

    async def test_long_content_split_into_multiple_posts(self, monkeypatch):
        """超 2040 字节：按行边界截断分多条，每条 payload 字节数 ≤ 2040"""
        # 6 行中文（每行约 700 字节）共约 4200 字节 → 应分 3 条左右
        content = "\n".join("招聘智能体拟邀约以下候选人面试：" + "亮点描述文字。" * 45 for _ in range(6))
        assert len(content.encode("utf-8")) > 2040
        client = _patch_client(monkeypatch, [{"errcode": 0, "errmsg": "ok"}] * 10)
        ok, err = await module.send_markdown(URL, content)
        assert ok is True and err is None
        assert client.post.await_count >= 2
        for call in client.post.await_args_list:
            chunk = call.kwargs["json"]["markdown"]["content"]
            assert len(chunk.encode("utf-8")) <= 2040

    async def test_single_long_line_hard_split_preserves_text(self, monkeypatch):
        """单行超限：硬截成多条，拼接后原文无损"""
        content = "超" * 5000  # 15000 字节，单行
        client = _patch_client(monkeypatch, [{"errcode": 0, "errmsg": "ok"}] * 10)
        ok, err = await module.send_markdown(URL, content)
        assert ok is True
        joined = "".join(
            call.kwargs["json"]["markdown"]["content"] for call in client.post.await_args_list
        )
        assert joined == content
        for call in client.post.await_args_list:
            assert len(call.kwargs["json"]["markdown"]["content"].encode("utf-8")) <= 2040


class TestSplitMarkdownUnit:
    def test_short_content_single_chunk(self):
        assert module._split_markdown("abc") == ["abc"]
        assert module._split_markdown("") == []

    def test_line_boundary_split(self):
        content = "a" * 1000 + "\n" + "b" * 1000 + "\n" + "c" * 1000
        chunks = module._split_markdown(content)
        assert len(chunks) == 2
        assert all(len(c.encode("utf-8")) <= 2040 for c in chunks)


class TestSendText:
    async def test_payload_with_mentioned_mobile_list(self, monkeypatch):
        """text 消息结构：手机号放 mentioned_mobile_list（企微按手机号 @ 群成员，
        mentioned_list 只接受 userid，手机号放错字段不会 @）"""
        client = _patch_client(monkeypatch, [{"errcode": 0, "errmsg": "ok"}])
        ok, err = await module.send_text(URL, "面试邀约已完成，请查看上条详情", ["13800000001", "13900000002"])
        assert ok is True and err is None
        args, kwargs = client.post.await_args
        assert args[0] == URL
        assert kwargs["json"] == {
            "msgtype": "text",
            "text": {
                "content": "面试邀约已完成，请查看上条详情",
                "mentioned_mobile_list": ["13800000001", "13900000002"],
            },
        }

    async def test_at_mobiles_none_sends_empty_list(self, monkeypatch):
        client = _patch_client(monkeypatch, [{"errcode": 0, "errmsg": "ok"}])
        ok, _ = await module.send_text(URL, "文本", None)
        assert ok is True
        assert client.post.await_args.kwargs["json"]["text"]["mentioned_mobile_list"] == []

    async def test_errcode_failure_returns_error(self, monkeypatch):
        client = _patch_client(
            monkeypatch,
            [{"errcode": 81013, "errmsg": "user not in group"}, {"errcode": 81013, "errmsg": "user not in group"}],
        )
        ok, err = await module.send_text(URL, "文本", ["13800000001"])
        assert ok is False
        assert "81013" in err
        assert client.post.await_count == 2
