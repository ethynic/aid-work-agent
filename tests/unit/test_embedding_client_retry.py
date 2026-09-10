"""
TextEmbeddingV3Client 重试机制单元测试

覆盖 keep-alive 死连接（RemoteDisconnected）等瞬时网络错误的重试行为。
"""
from unittest.mock import patch

import pytest
import requests.exceptions

from src.knowledge.embedding.embedding_client import (
    TextEmbeddingV3Client,
    _is_transient_error,
    _extract_usage_tokens,
    _sanitize_texts,
)


# ---------------------------------------------------------------------------
# _is_transient_error 纯函数测试
# ---------------------------------------------------------------------------


def test_is_transient_error_connection_error():
    """requests.exceptions.ConnectionError（含 RemoteDisconnected）应识别为可重试"""
    e = requests.exceptions.ConnectionError(
        "Connection aborted.",
        Exception("Remote end closed connection without response"),
    )
    assert _is_transient_error(e) is True


def test_is_transient_error_timeout():
    """requests.exceptions.Timeout 应识别为可重试"""
    assert _is_transient_error(requests.exceptions.Timeout("timed out")) is True


def test_is_transient_error_string_match():
    """仅靠字符串匹配也应识别 RemoteDisconnected 风格异常"""
    assert _is_transient_error(RuntimeError("Connection aborted.")) is True
    assert _is_transient_error(Exception("Connection reset by peer")) is True
    assert _is_transient_error(Exception("Connection timed out")) is True


def test_is_transient_error_non_transient():
    """非瞬时错误（业务异常）不应识别为可重试"""
    assert _is_transient_error(ValueError("bad input")) is False
    assert _is_transient_error(Exception("Embedding API 失败: quota exceeded")) is False


# ---------------------------------------------------------------------------
# _call_dashscope_with_retry 重试行为测试
# ---------------------------------------------------------------------------


def _make_client():
    """构造一个 TextEmbeddingV3Client 实例"""
    return TextEmbeddingV3Client(api_key="sk-test-fake-key-for-unit-test")


def _make_fake_resp(status_code=200, embeddings=None):
    """构造一个最小可用的 fake DashScope 响应对象"""
    return type(
        "R",
        (),
        {
            "status_code": status_code,
            "output": {"embeddings": [{"embedding": e} for e in (embeddings or [[0.1, 0.2]])]},
            "usage": None,
            "message": "ok",
        },
    )()


def test_retry_success_on_first_call():
    """成功调用不重试"""
    client = _make_client()
    fake_resp = _make_fake_resp()
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        return_value=fake_resp,
    ) as mock_call:
        resp = client._call_dashscope_with_retry(["hello"])

    assert resp is fake_resp
    assert mock_call.call_count == 1


def test_retry_succeeds_after_transient_failure(monkeypatch):
    """第一次 ConnectionError(RemoteDisconnected)，第二次成功 -> 重试 1 次后成功"""
    monkeypatch.setattr(
        "src.knowledge.embedding.embedding_client.time.sleep", lambda s: None
    )
    client = _make_client()
    fake_resp = _make_fake_resp()
    transient = requests.exceptions.ConnectionError(
        "Connection aborted.",
        Exception("Remote end closed connection without response"),
    )
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        side_effect=[transient, fake_resp],
    ) as mock_call:
        resp = client._call_dashscope_with_retry(["hello"])

    assert resp is fake_resp
    assert mock_call.call_count == 2


def test_retry_exhausted_after_three_failures(monkeypatch):
    """连续 3 次瞬时失败 -> 抛最后一次异常"""
    monkeypatch.setattr(
        "src.knowledge.embedding.embedding_client.time.sleep", lambda s: None
    )
    client = _make_client()
    transient = requests.exceptions.ConnectionError(
        "Connection aborted.",
        Exception("Remote end closed connection without response"),
    )
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        side_effect=transient,
    ) as mock_call:
        with pytest.raises(requests.exceptions.ConnectionError):
            client._call_dashscope_with_retry(["hello"])

    assert mock_call.call_count == 3


def test_no_retry_on_non_transient_error():
    """非瞬时错误（如 KeyError）应直接抛出，不重试"""
    client = _make_client()
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        side_effect=KeyError("not a transient network error"),
    ) as mock_call:
        with pytest.raises(KeyError):
            client._call_dashscope_with_retry(["hello"])

    assert mock_call.call_count == 1


# ---------------------------------------------------------------------------
# embed_sync 路径测试（与 _call_dashscope_with_retry 共享重试逻辑）
# ---------------------------------------------------------------------------


def test_embed_sync_retries_on_transient(monkeypatch):
    """embed_sync 同样应用重试逻辑"""
    monkeypatch.setattr(
        "src.knowledge.embedding.embedding_client.time.sleep", lambda s: None
    )
    client = _make_client()
    fake_resp = _make_fake_resp(embeddings=[[0.1, 0.2, 0.3]])
    transient = requests.exceptions.ConnectionError(
        "Connection aborted.",
        Exception("Remote end closed connection without response"),
    )
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        side_effect=[transient, fake_resp],
    ) as mock_call:
        emb = client.embed_sync("hello")

    assert emb == [0.1, 0.2, 0.3]
    assert mock_call.call_count == 2


def test_embed_sync_empty_input_short_circuits():
    """空文本直接返回 []，不触发 SDK 调用"""
    client = _make_client()
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call"
    ) as mock_call:
        emb = client.embed_sync("")

    assert emb == []
    assert mock_call.call_count == 0


# ---------------------------------------------------------------------------
# _strip_lone_surrogates 清洗测试（生产事故：surrogates not allowed）
# ---------------------------------------------------------------------------


def test_sanitize_removes_high_surrogate():
    """孤立高代理 \\ud83c 应被剔除，其余字符保留"""
    text = "前缀" + "\ud83c" + "后缀"
    out = _sanitize_texts([text])
    assert out == ["前缀后缀"]
    out[0].encode("utf-8")  # 不再抛 UnicodeEncodeError


def test_sanitize_keeps_valid_emoji():
    """完整 emoji（合法代理对）不受影响"""
    text = "你好 😀 完成"
    assert _sanitize_texts([text]) == [text]


def test_sanitize_removes_nul_and_control_chars():
    """NUL 与 C0/C1 控制符应被剔除，保留 \\t \\n \\r"""
    text = "a\x00b\x01c\x7fd\x85e\tf\ng\rh"
    assert _sanitize_texts([text]) == ["abcde\tf\ng\rh"]


def test_sanitize_removes_noncharacter_and_bom():
    """U+FFFE/U+FFFF noncharacter 与 U+FEFF BOM 残留应被剔除"""
    text = "a￾b￿c﻿d"
    assert _sanitize_texts([text]) == ["abcd"]


def test_sanitize_normal_text_no_copy_change():
    """正常文本原样返回（不清洗）"""
    assert _sanitize_texts(["普通文本", ""]) == ["普通文本", ""]


def test_call_dashscope_sanitizes_input_before_sdk():
    """_call_dashscope_with_retry 应在调用 SDK 前剔除孤立代理字符"""
    client = _make_client()
    fake_resp = _make_fake_resp()
    dirty = "含有\ud83c孤立代理的文档内容"
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        return_value=fake_resp,
    ) as mock_call:
        client._call_dashscope_with_retry([dirty])

    sent_texts = mock_call.call_args.kwargs["input"]
    assert sent_texts == ["含有孤立代理的文档内容"]


# ---------------------------------------------------------------------------
# _extract_usage_tokens 提取测试（计费根因：dict 形态 total_tokens）
# ---------------------------------------------------------------------------


def _make_resp_with_usage(usage):
    return type(
        "R",
        (),
        {
            "status_code": 200,
            "output": {"embeddings": [{"embedding": [0.1, 0.2]}]},
            "usage": usage,
            "message": "ok",
        },
    )()


def test_extract_usage_tokens_dict_total_tokens():
    """DashScope 实际返回 dict 形态 {"total_tokens": 9}，应提取为 9"""
    assert _extract_usage_tokens(_make_resp_with_usage({"total_tokens": 9})) == 9


def test_extract_usage_tokens_dict_tokens_fallback():
    """dict 形态无 total_tokens 时回退读 tokens"""
    assert _extract_usage_tokens(_make_resp_with_usage({"tokens": 42})) == 42


def test_extract_usage_tokens_object_total_tokens():
    """对象形态 .total_tokens 优先"""
    obj = type("U", (), {"total_tokens": 7, "tokens": 3})()
    assert _extract_usage_tokens(_make_resp_with_usage(obj)) == 7


def test_extract_usage_tokens_object_tokens_fallback():
    """对象形态无 total_tokens 时回退读 tokens"""
    obj = type("U", (), {"tokens": 15})()
    assert _extract_usage_tokens(_make_resp_with_usage(obj)) == 15


def test_extract_usage_tokens_zero_on_missing():
    """无 usage / 无 token 字段 / token 为 0 -> 返回 0（不计费）"""
    assert _extract_usage_tokens(_make_resp_with_usage(None)) == 0
    assert _extract_usage_tokens(_make_resp_with_usage({})) == 0
    assert _extract_usage_tokens(_make_resp_with_usage({"total_tokens": 0})) == 0


def test_embed_sync_accumulates_last_usage_tokens_from_dict():
    """embed_sync 调用后应从 dict 形态 usage 累加 last_usage_tokens（计费根因修复验证）"""
    client = _make_client()
    fake_resp = _make_resp_with_usage({"total_tokens": 9})
    fake_resp.output = {"embeddings": [{"embedding": [0.1, 0.2, 0.3]}]}
    with patch(
        "src.knowledge.embedding.embedding_client.TextEmbedding.call",
        return_value=fake_resp,
    ):
        emb = client.embed_sync("测试向量检索计费排查")

    assert emb == [0.1, 0.2, 0.3]
    assert client.last_usage_tokens == 9
