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
