"""官网客服（web_c）渠道单元测试（设计 docs/system/web-c-channel-design.md §7）。

覆盖：HMAC 验签（有效/过期/篡改/name 缺省）、身份格式校验、upload 校验、
终态落库投影、submit() 拆分受理回归、渠道注册接线（工厂/枚举/防滥用白名单）。
"""

import hashlib
import hmac
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.saas.api.web_c import (
    _ANON_UID_RE,
    _CLIENT_REQUEST_ID_RE,
    _EXT_USERID_RE,
    _get_limiters,
    _runner_files_to_attachments,
    _sanitize_filename,
    _validate_attachment_refs,
    _verify_ext_signature,
)
from src.saas.services.credit_gate import check_tenant_credit_blocked

SECRET = "a" * 64


def _sign(userid: str, ts: int, name: str = "") -> str:
    message = f"{userid}.{ts}.{name}"
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


class TestExtSignature:
    def test_valid_signature(self):
        ts = int(time.time())
        assert _verify_ext_signature(SECRET, "u1001", str(ts), "张三", _sign("u1001", ts, "张三"))

    def test_missing_name_signs_with_empty(self):
        ts = int(time.time())
        assert _verify_ext_signature(SECRET, "u1001", str(ts), "", _sign("u1001", ts, ""))

    def test_tampered_signature_rejected(self):
        ts = int(time.time())
        assert not _verify_ext_signature(SECRET, "u1001", str(ts), "张三", "0" * 64)

    def test_expired_timestamp_rejected(self):
        ts = int(time.time()) - 25 * 3600
        assert not _verify_ext_signature(SECRET, "u1001", str(ts), "", _sign("u1001", ts, ""))

    def test_wrong_secret_rejected(self):
        ts = int(time.time())
        sig = hmac.new(b"other-secret", f"u1.{ts}.".encode(), hashlib.sha256).hexdigest()
        assert not _verify_ext_signature(SECRET, "u1", str(ts), "", sig)

    def test_uppercase_sig_accepted(self):
        ts = int(time.time())
        assert _verify_ext_signature(SECRET, "u1", str(ts), "", _sign("u1", ts, "").upper())


class TestIdentityFormats:
    def test_anon_uid_regex(self):
        import uuid

        assert _ANON_UID_RE.match(f"anon_{uuid.uuid4()}")
        assert not _ANON_UID_RE.match("anon_not-a-uuid")
        assert not _ANON_UID_RE.match("ext_u1001")

    def test_ext_userid_regex(self):
        assert _EXT_USERID_RE.match("user-1001")
        assert _EXT_USERID_RE.match("a@b.com")
        assert not _EXT_USERID_RE.match("bad id with space")
        assert not _EXT_USERID_RE.match("x" * 129)

    def test_client_request_id_regex(self):
        assert _CLIENT_REQUEST_ID_RE.match("wc_abc12345-678")
        assert not _CLIENT_REQUEST_ID_RE.match("short")
        assert not _CLIENT_REQUEST_ID_RE.match("bad id!")


class TestUploadGuards:
    def test_sanitize_filename_strips_path_and_controls(self):
        assert _sanitize_filename("../../etc/passwd.pdf") == "etc_passwd.pdf"
        assert _sanitize_filename("a\x00b.txt") == "a_b.txt"
        assert _sanitize_filename("") == "附件"

    def test_validate_refs_rejects_bad_file_id(self):
        with pytest.raises(HTTPException):
            _validate_attachment_refs([{"file_id": "../etc/passwd"}])

    def test_validate_refs_normalizes(self):
        refs = _validate_attachment_refs([
            {"file_id": "file_abcdef123456", "name": "报价单.pdf", "mime_type": "application/pdf", "size": 10},
        ])
        assert refs[0]["file_id"] == "file_abcdef123456"
        assert refs[0]["type"] == "file"
        assert set(refs[0].keys()) == {"file_id", "name", "type", "mime_type", "size"}

    def test_upload_ext_whitelist_rejects_exe(self):
        from src.saas.api.web_c import _UPLOAD_ALLOWED_EXTS

        assert ".exe" not in _UPLOAD_ALLOWED_EXTS
        assert ".pdf" in _UPLOAD_ALLOWED_EXTS and ".csv" in _UPLOAD_ALLOWED_EXTS


class TestRunnerFilesProjection:
    def test_files_and_images_projection(self):
        row = {
            "snapshot": {
                "downloadableFiles": [
                    {"file_id": "file_a", "name": "报价.xlsx", "mime_type": "application/x", "size": 3, "download_url": "/api/files/file_a/download"},
                    {"no_file_id": True},
                ],
                "images": [],
            },
            "result": {"output": "ok", "images": [
                {"file_id": "file_b", "display_name": "图.png", "mime_type": "image/png", "size_bytes": 5, "download_url": "/api/files/file_b/download"},
            ]},
        }
        atts = _runner_files_to_attachments(row)
        assert [a["file_id"] for a in atts] == ["file_a", "file_b"]
        assert atts[0]["type"] == "file" and atts[1]["type"] == "image"


class TestCreditGate:
    def test_private_cloud_allows_zero_balance(self):
        with patch("src.saas.services.credit_gate.settings") as mock_settings, \
             patch("src.saas.db.tenant_db.TenantDB.get_by_id", return_value={"credit_balance": 0}):
            mock_settings.deployment.private_cloud = True
            assert check_tenant_credit_blocked("t1") is None

    def test_zero_balance_blocks(self):
        with patch("src.saas.services.credit_gate.settings") as mock_settings, \
             patch("src.saas.db.tenant_db.TenantDB.get_by_id", return_value={"credit_balance": 0}):
            mock_settings.deployment.private_cloud = False
            response = check_tenant_credit_blocked("t1")
            assert response is not None and response.status_code == 403

    def test_positive_balance_allows(self):
        with patch("src.saas.services.credit_gate.settings") as mock_settings, \
             patch("src.saas.db.tenant_db.TenantDB.get_by_id", return_value={"credit_balance": 10}):
            mock_settings.deployment.private_cloud = False
            assert check_tenant_credit_blocked("t1") is None

    def test_empty_tenant_allows(self):
        with patch("src.saas.services.credit_gate.settings") as mock_settings:
            mock_settings.deployment.private_cloud = False
            assert check_tenant_credit_blocked(None) is None


class TestLimiters:
    def test_thresholds_from_config(self):
        limiters = _get_limiters("cfg1", {"rate_limit_uid_per_min": 9, "rate_limit_ip_per_hour": 100})
        assert limiters["chat_uid"].max_requests == 9
        assert limiters["chat_ip"].max_requests == 100

    def test_defaults_and_cache(self):
        limiters = _get_limiters("cfg2", {})
        assert limiters["chat_uid"].max_requests == 5
        assert limiters["chat_ip"].max_requests == 60
        assert _get_limiters("cfg2", {}) is limiters


def _valid_runner_row(runner_id="run_1", status="queued", session_id="t1_web_c_ext_u1_"):
    return {
        "runner_id": runner_id,
        "client_request_id": "wc_req_1",
        "profile_id": "main",
        "queue_order": 1,
        "session": {"kind": "channel", "session_id": session_id},
        "status": status,
        "snapshot": {},
        "revision": 0,
        "view_revision": 0,
        "control_revision": 0,
    }


class StubRunnerClient:
    """RunnerServiceClient 桩：记录调用并按序返回 canned 响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, path, *, authorization=None, tenant=None, body=None,
                      params=None, accept_new=None, channel_source=None, channel_user=None,
                      channel_chat=None):
        self.calls.append({"method": method, "path": path, "body": body,
                           "channel_source": channel_source, "channel_user": channel_user})
        return self.responses.pop(0)


class TestSubmitSplit:
    """submit() 拆分回归：受理语义与 process_message_sync 轮询段保持不变。"""

    def _make_agent(self, client):
        from src.channels.runner_agent import ChannelRunnerAgent

        return ChannelRunnerAgent(
            source="web_c", session_id="t1_web_c_ext_u1_", channel_user_id="ext_u1",
            channel_chat_id=None, profile_id="main", config_id="cfg1", tenant_id="t1",
            client=client,
        )

    @pytest.mark.asyncio
    async def test_submit_accepts_new_runner(self):
        accepted_row = _valid_runner_row(status="queued")
        client = StubRunnerClient([
            {"success": True, "runners": [], "active_runners": [], "has_more": False, "next_cursor": None},
            {"success": True, "runner": accepted_row},
        ])
        agent = self._make_agent(client)
        outcome = await agent.submit(user_input="你好", client_request_id="wc_req_1")
        assert outcome.row is accepted_row
        assert outcome.direct_response is None
        assert outcome.reply_wait_id is None
        submit_call = client.calls[-1]
        assert submit_call["method"] == "POST" and submit_call["path"] == "/v1/runners"
        assert submit_call["body"]["client_request_id"] == "wc_req_1"
        assert submit_call["body"]["session"] == {"kind": "channel", "session_id": "t1_web_c_ext_u1_"}
        assert submit_call["channel_source"] == "web_c"

    @pytest.mark.asyncio
    async def test_submit_cancels_stale_active_runner(self):
        stale = _valid_runner_row(runner_id="run_old", status="running")
        accepted_row = _valid_runner_row(runner_id="run_new", status="queued")
        client = StubRunnerClient([
            {"success": True, "runners": [], "active_runners": [stale], "has_more": False, "next_cursor": None},
            {"success": True, "runner": _valid_runner_row(runner_id="run_old", status="cancelled")},
            {"success": True, "runner": accepted_row},
        ])
        agent = self._make_agent(client)
        outcome = await agent.submit(user_input="第二条")
        assert outcome.row is accepted_row
        cancel_call = client.calls[1]
        assert cancel_call["path"].endswith("/runners/run_old/cancel")

    @pytest.mark.asyncio
    async def test_submit_generates_request_id_when_absent(self):
        client = StubRunnerClient([
            {"success": True, "runners": [], "active_runners": [], "has_more": False, "next_cursor": None},
            {"success": True, "runner": _valid_runner_row()},
        ])
        agent = self._make_agent(client)
        await agent.submit(user_input="hi")
        body = client.calls[-1]["body"]
        assert body["client_request_id"] and body["client_request_id"] != "wc_req_1"


class TestChannelRegistration:
    def test_factory_maps_web_c(self):
        from src.saas.services.channel_factory import ChannelFactory
        from src.channels.web_c.adapter import WebCAdapter

        assert ChannelFactory._ADAPTER_CLASSES["web_c"].endswith("WebCAdapter")
        adapter = ChannelFactory.create_adapter("web_c", {
            "enabled": True, "greeting": "欢迎", "channel_secret": SECRET, "identity_mode": "both",
        })
        assert isinstance(adapter, WebCAdapter)
        assert adapter.channel_type == "web_c"
        assert adapter.greeting == "欢迎"

    def test_adapter_noop_send(self):
        import asyncio
        from src.channels.web_c.adapter import WebCAdapter
        from src.models.message import UnifiedResponse

        adapter = WebCAdapter()
        response = UnifiedResponse(message_id="m1", reply_to="u1", content={"text": "hi"})
        assert asyncio.run(adapter.send_message(response)) is True

    def test_enums_registered(self):
        from src.models.message import ChannelType
        from src.saas.models.enums import ChatRecordSourceType
        from src.services.anti_abuse.rules import SESSION_SOURCE_TYPES

        assert ChannelType.WEB_C.value == "web_c"
        assert ChatRecordSourceType.WEB_C.value == "web_c"
        assert "web_c" in SESSION_SOURCE_TYPES

    def test_web_c_in_ask_single_group(self):
        from src.channels.ask_render import SINGLE_MESSAGE_SOURCES, source_segment_limit

        assert "web_c" in SINGLE_MESSAGE_SOURCES
        assert source_segment_limit("web_c") == 8192

    def test_auto_register_channel_name(self):
        from src.saas.services.auto_register import CHANNEL_TYPE_NAME

        assert CHANNEL_TYPE_NAME["web_c"] == "官网客服"
