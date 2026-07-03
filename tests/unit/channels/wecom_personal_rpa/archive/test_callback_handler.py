"""archive.callback_handler 单元测试

覆盖：
- is_archive_callback_request 识别 XML vs JSON
- handle_archive_echostr：成功路径 / 配置不存在 / 凭证缺失 / 验签失败
- handle_archive_event：成功路径 / 配置不存在 / 凭证缺失 / body 无 Encrypt 字段 / 验签失败 / 异步触发 fetcher
"""
import asyncio
import base64
import json
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.channels.wecom_personal_rpa.archive import callback_handler, fetcher as fetcher_module
from src.channels.wecom_personal_rpa.archive.callback_handler import (
    handle_archive_echostr,
    handle_archive_event,
    is_archive_callback_request,
)
from src.channels.wecom.crypto import WeComCrypto


TEST_TOKEN = "QDG6eK"
TEST_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
TEST_CORP_ID = "wx5823bf96d3bd56c7"


def _make_crypto() -> WeComCrypto:
    return WeComCrypto(TEST_TOKEN, TEST_AES_KEY, TEST_CORP_ID)


def _make_cfg(channel_type: str = "wecom_personal_rpa", listen_mode: str = "server",
              with_creds: bool = True) -> dict:
    config = {"listen_mode": listen_mode, "last_seq": 100}
    if with_creds:
        config.update({"corp_id": TEST_CORP_ID, "token": TEST_TOKEN, "encoding_aes_key": TEST_AES_KEY})
    return {
        "config_id": "chan_test",
        "tenant_id": "t_test",
        "channel_type": channel_type,
        "config": config,
        "verified": 1,
    }


def _make_request(method: str = "POST", body: bytes = b"", content_type: str = "application/xml",
                  query_params: dict = None) -> Request:
    """构造 mock Request 对象。"""
    req = MagicMock(spec=Request)
    req.headers = {"content-type": content_type}
    req.method = method
    req.query_params = query_params or {}
    # body 是 coroutine
    async def _body():
        return body
    req.body = _body
    return req


# ----------------- is_archive_callback_request -----------------


def test_is_archive_xml_content_type():
    req = _make_request(content_type="application/xml")
    assert is_archive_callback_request(req, b"<xml>") is True


def test_is_archive_text_xml_content_type():
    req = _make_request(content_type="text/xml")
    assert is_archive_callback_request(req, b"<xml>") is True


def test_is_archive_json_content_type():
    req = _make_request(content_type="application/json")
    assert is_archive_callback_request(req, b"{}") is False


def test_is_archive_no_content_type_xml_body():
    """content-type 缺失时按 body 前缀识别。"""
    req = _make_request(content_type="", body=b"<xml><Encrypt>")
    assert is_archive_callback_request(req, b"<xml><Encrypt>") is True


def test_is_archive_no_content_type_json_body():
    req = _make_request(content_type="", body=b'{"event_id":')
    assert is_archive_callback_request(req, b'{"event_id":') is False


# ----------------- handle_archive_echostr -----------------


@pytest.mark.asyncio
async def test_echostr_success(monkeypatch):
    """GET echostr 验签成功 → 返回明文 PlainTextResponse。"""
    cfg = _make_cfg()
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    crypto = _make_crypto()
    plain_echostr = "echostr_plain_text"
    encrypted_echostr = crypto.encrypt(plain_echostr)
    sig = crypto.generate_signature("1409309348", "1372623149", encrypted_echostr)

    req = _make_request(method="GET", query_params={
        "msg_signature": sig,
        "timestamp": "1409309348",
        "nonce": "1372623149",
        "echostr": encrypted_echostr,
    })

    resp = await handle_archive_echostr("t_test", "chan_test", req)
    assert resp.status_code == 200
    body_bytes = resp.body
    assert body_bytes.decode("utf-8") == plain_echostr


@pytest.mark.asyncio
async def test_echostr_config_not_found(monkeypatch):
    """配置不存在 → 404。"""
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: None
    )
    req = _make_request(method="GET", query_params={})
    resp = await handle_archive_echostr("t_test", "chan_test", req)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_echostr_credentials_missing(monkeypatch):
    """凭证缺失 → 400。"""
    cfg = _make_cfg(with_creds=False)
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    req = _make_request(method="GET", query_params={})
    resp = await handle_archive_echostr("t_test", "chan_test", req)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_echostr_signature_fail(monkeypatch):
    """验签失败 → 401。"""
    cfg = _make_cfg()
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    crypto = _make_crypto()
    encrypted = crypto.encrypt("plain")
    req = _make_request(method="GET", query_params={
        "msg_signature": "0" * 40,  # 篡改签名
        "timestamp": "1409309348",
        "nonce": "1372623149",
        "echostr": encrypted,
    })
    resp = await handle_archive_echostr("t_test", "chan_test", req)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_echostr_wrong_channel_type(monkeypatch):
    """channel_type 非 wecom_personal_rpa → 404（防御性）。"""
    cfg = _make_cfg(channel_type="wecom_kf")
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    req = _make_request(method="GET", query_params={})
    resp = await handle_archive_echostr("t_test", "chan_test", req)
    assert resp.status_code == 404


# ----------------- handle_archive_event -----------------


@pytest.mark.asyncio
async def test_event_success_triggers_fetcher(monkeypatch):
    """POST 事件验签成功 → 200 + 异步触发 fetcher.fetch_once。"""
    cfg = _make_cfg()
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    # mock fetcher 单例
    fetcher_mock = MagicMock()
    fetcher_mock.fetch_once = AsyncMock()
    monkeypatch.setattr(callback_handler.fetcher, "fetcher", fetcher_mock)

    crypto = _make_crypto()
    plain_xml = "<xml><Event>chat_update</Event></xml>"
    encrypted = crypto.encrypt(plain_xml)
    sig = crypto.generate_signature("1409309348", "1372623149", encrypted)

    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode("utf-8")
    req = _make_request(method="POST", body=body, content_type="application/xml",
                        query_params={"msg_signature": sig, "timestamp": "1409309348", "nonce": "1372623149"})

    resp = await handle_archive_event("t_test", "chan_test", req, body)

    assert resp.status_code == 200
    # 给 create_task 一个 tick
    await asyncio.sleep(0.05)
    fetcher_mock.fetch_once.assert_awaited_once_with("t_test", "chan_test")


@pytest.mark.asyncio
async def test_event_config_not_found(monkeypatch):
    """配置不存在 → 404。"""
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: None
    )
    req = _make_request(method="POST", body=b"", content_type="application/xml")
    resp = await handle_archive_event("t_test", "chan_test", req, b"")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_event_credentials_missing(monkeypatch):
    """凭证缺失 → 400。"""
    cfg = _make_cfg(with_creds=False)
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    req = _make_request(method="POST", body=b"", content_type="application/xml")
    resp = await handle_archive_event("t_test", "chan_test", req, b"")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_event_body_no_encrypt_field(monkeypatch):
    """body 无 <Encrypt> 字段 → 400。"""
    cfg = _make_cfg()
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )
    body = b"<xml><NoEncrypt>xxx</NoEncrypt></xml>"
    req = _make_request(method="POST", body=body, content_type="application/xml")
    resp = await handle_archive_event("t_test", "chan_test", req, body)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_event_signature_fail(monkeypatch):
    """验签失败 → 401（不回退 HMAC）。"""
    cfg = _make_cfg()
    monkeypatch.setattr(
        callback_handler.ChannelConfigDB, "get_by_tenant_and_id", lambda *a, **kw: cfg
    )

    fetcher_mock = MagicMock()
    fetcher_mock.fetch_once = AsyncMock()
    monkeypatch.setattr(callback_handler.fetcher, "fetcher", fetcher_mock)

    crypto = _make_crypto()
    encrypted = crypto.encrypt("plain")
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode("utf-8")
    req = _make_request(method="POST", body=body, content_type="application/xml",
                        query_params={"msg_signature": "0" * 40, "timestamp": "x", "nonce": "y"})

    resp = await handle_archive_event("t_test", "chan_test", req, body)
    assert resp.status_code == 401
    # 验签失败不应触发 fetcher
    await asyncio.sleep(0.05)
    fetcher_mock.fetch_once.assert_not_awaited()
