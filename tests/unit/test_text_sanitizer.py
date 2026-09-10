"""text_sanitizer 异常字符清洗单元测试

覆盖孤立代理、控制符、noncharacter、BOM 的剔除，以及
LLMGateway 两个调用必经点（_call_with_pool / _stream_with_pool）对
messages 的统一清洗。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.text_sanitizer import sanitize_messages, sanitize_text, sanitize_texts
from src.llm.gateway import LLMGateway


# ---------------------------------------------------------------------------
# sanitize_text / sanitize_texts
# ---------------------------------------------------------------------------


def test_sanitize_text_removes_high_surrogate():
    """孤立高代理 \\ud83c 应被剔除，其余字符保留"""
    out = sanitize_text("前缀" + "\ud83c" + "后缀")
    assert out == "前缀后缀"
    out.encode("utf-8")  # 不再抛 UnicodeEncodeError


def test_sanitize_text_keeps_valid_emoji():
    """完整 emoji（合法代理对）不受影响"""
    text = "你好 😀 完成"
    assert sanitize_text(text) == text


def test_sanitize_text_keeps_tab_newline_cr():
    """\\t \\n \\r 属于正常文本结构，保留"""
    text = "a\tb\nc\rd"
    assert sanitize_text(text) == text


def test_sanitize_text_removes_nul_and_control_chars():
    """NUL 与 C0/C1 控制符应被剔除"""
    assert sanitize_text("a\x00b\x01c\x7fd\x85e") == "abcde"


def test_sanitize_text_removes_noncharacter_and_bom():
    """U+FFFE/U+FFFF noncharacter 与 U+FEFF BOM 残留应被剔除"""
    assert sanitize_text("a\ufffeb\uffffc\ufeffd") == "abcd"


def test_sanitize_text_no_match_returns_same_object():
    """无异常字符时返回原对象（零拷贝快速路径）"""
    text = "普通文本"
    assert sanitize_text(text) is text


def test_sanitize_texts_batch():
    assert sanitize_texts(["普通", "含\ud83c代理"]) == ["普通", "含代理"]


# ---------------------------------------------------------------------------
# sanitize_messages
# ---------------------------------------------------------------------------


def test_sanitize_messages_string_content():
    """纯字符串 content 清洗；未命中时返回原列表对象"""
    msgs = [{"role": "user", "content": "含\ud83c代理的提问"}]
    out = sanitize_messages(msgs)
    assert out is not msgs
    assert out[0]["content"] == "含代理的提问"

    clean = [{"role": "user", "content": "正常提问"}]
    assert sanitize_messages(clean) is clean


def test_sanitize_messages_multimodal_parts():
    """多模态分段列表：清洗 text 分段，图片分段原样保留"""
    image_part = {"type": "image_url", "image_url": {"url": "https://x/a.png"}}
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "描述\ud83c这张图"},
            image_part,
        ],
    }]
    out = sanitize_messages(msgs)
    assert out[0]["content"][0]["text"] == "描述这张图"
    assert out[0]["content"][1] is image_part


def test_sanitize_messages_empty():
    """空消息列表原样返回"""
    assert sanitize_messages([]) == []
    assert sanitize_messages(None) is None


# ---------------------------------------------------------------------------
# LLMGateway 必经点清洗
# ---------------------------------------------------------------------------


def _new_gateway() -> LLMGateway:
    return LLMGateway.__new__(LLMGateway)


@pytest.mark.asyncio
async def test_call_with_pool_sanitizes_messages():
    """_call_with_pool 应在调用 provider 前剔除 messages 中的异常字符"""
    gw = _new_gateway()

    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value="sk-fake-key")
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire_ctx
    gw._key_pool = pool
    gw.provider_name = "qwen"
    gw._model_codes = {}

    captured = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"content": "ok"}

    provider = MagicMock()
    provider.chat = fake_chat

    with patch("src.llm.gateway._build_provider", return_value=provider):
        await gw._call_with_pool("chat", messages=[
            {"role": "user", "content": "含\ud83c代理"}
        ])

    assert captured["messages"][0]["content"] == "含代理"


@pytest.mark.asyncio
async def test_stream_with_pool_sanitizes_messages():
    """_stream_with_pool 应在调用 provider 前剔除 messages 中的异常字符"""
    gw = _new_gateway()

    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value="sk-fake-key")
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire_ctx
    gw._key_pool = pool
    gw.provider_name = "qwen"
    gw._model_codes = {}

    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield "chunk"

    provider = MagicMock()
    provider.stream_chat = fake_stream

    with patch("src.llm.gateway._build_provider", return_value=provider):
        chunks = [c async for c in gw._stream_with_pool("stream_chat", messages=[
            {"role": "user", "content": "含\ud83c代理"}
        ])]

    assert chunks == ["chunk"]
    assert captured["messages"][0]["content"] == "含代理"


# ---------------------------------------------------------------------------
# sanitize_value（递归结构清洗，工具结果/复合数据出口用）
# ---------------------------------------------------------------------------


def test_sanitize_value_str():
    from src.core.text_sanitizer import sanitize_value

    assert sanitize_value("含\ud83c代理") == "含代理"


def test_sanitize_value_nested_dict_list():
    from src.core.text_sanitizer import sanitize_value

    dirty = {"success": True, "items": [{"text": "a\ud83cb", "n": 1}], "raw": b"bytes"}
    cleaned = sanitize_value(dirty)
    assert cleaned["items"][0]["text"] == "ab"
    assert cleaned["items"][0]["n"] == 1
    assert cleaned["raw"] == b"bytes"


def test_sanitize_value_clean_returns_same_object():
    from src.core.text_sanitizer import sanitize_value

    value = {"a": [1, "x", {"b": "y"}]}
    assert sanitize_value(value) is value


def test_sanitize_value_tuple():
    from src.core.text_sanitizer import sanitize_value

    cleaned = sanitize_value(("x\ud83cy", 2))
    assert cleaned == ("xy", 2)
    assert isinstance(cleaned, tuple)
