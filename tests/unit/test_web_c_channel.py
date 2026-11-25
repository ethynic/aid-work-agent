"""官网客服（web_c）渠道单元测试（设计 docs/system/web-c-channel-design.md §7）。

覆盖：HMAC 验签（有效/过期/篡改/name 缺省）、身份格式校验、upload 校验、
终态落库投影、submit() 拆分受理回归、渠道注册接线（工厂/枚举/防滥用白名单）。
"""

import asyncio
import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

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
            "enabled": True, "greeting": "欢迎", "channel_secret": SECRET, "identity_mode": "ext",
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


class TestMultiSession:
    """多会话模型（「新会话」按钮）：额外会话 id 前缀/唯一性 + 请求模型字段。"""

    def _identity(self):
        from src.saas.api.web_c import WebCIdentity

        return WebCIdentity("tenant_x", "chan_1", "anon", "u_abc", "", "main")

    def test_extra_session_id_matches_ownership_prefix(self):
        from src.saas.api.web_c import _create_extra_session_sync

        identity = self._identity()
        captured = {}

        def fake_create_session(**kwargs):
            captured.update(kwargs)
            return {"session_id": kwargs["session_id"]}

        with patch("src.saas.api.web_c.channel_session_manager") as mgr:
            mgr.create_session.side_effect = fake_create_session
            session = _create_extra_session_sync(identity, "user_1")

        prefix = identity.session_prefix  # {tenant}_web_c_{uid}_
        assert session["session_id"].startswith(prefix)
        # 与默认确定性会话 {tenant}_web_c_{uid}_{subagent} 区分：前缀之后为 {subagent}_s{8hex}
        assert session["session_id"][len(prefix):].startswith("main_s")
        # chat_id 恒为 NULL（Runner 授权按该列严格比对，代理侧调用全程 None），
        # 额外会话改用 metadata extra_session 标记与默认会话区分
        assert captured["channel_chat_id"] is None
        assert captured["metadata"]["extra_session"] == "1"
        assert captured["metadata"]["channel_config_id"] == "chan_1"
        assert captured["subagent_id"] == "main"
        assert captured["user_info"] == {"user_id": "user_1", "name": ""}

    def test_extra_session_ids_unique(self):
        from src.saas.api.web_c import _create_extra_session_sync

        identity = self._identity()
        ids = []
        with patch("src.saas.api.web_c.channel_session_manager") as mgr:
            mgr.create_session.side_effect = lambda **kw: dict(kw)
            for _ in range(3):
                created = _create_extra_session_sync(identity, "user_1")
                ids.append(created["session_id"])
        # 随机后缀保证 session_id 唯一（uid 模式下会话 id 不可由 userid 推算）
        assert len(set(ids)) == 3

    def test_chat_request_session_fields_default(self):
        from src.saas.api.web_c import WebCChatRequest

        body = WebCChatRequest(text="hi", client_request_id="wc_abc12345")
        assert body.session_id == ""
        assert body.new_session is False
        body2 = WebCChatRequest(
            text="hi", client_request_id="wc_abc12345",
            session_id="tenant_x_web_c_u_abc_main_s0a1b2c3", new_session=True)
        assert body2.session_id.startswith("tenant_x_web_c_u_abc_")
        assert body2.new_session is True


class TestUidIdentityMode:
    """uid 模式（仅 userid 链接免签名，设计 §3.5）：身份解析 / 会话 id 后缀闸门 / 熵。"""

    def _resolve(self, conf: dict, params: dict):
        from types import SimpleNamespace

        from src.saas.api import web_c as web_c_module

        cfg = {"tenant_id": "tenant_x", "channel_type": "web_c",
               "subagent_type": "main", "config": conf}
        request = SimpleNamespace(query_params=params, headers={})
        with patch.object(web_c_module, "_load_web_c_config", return_value=(cfg, conf)):
            return web_c_module._resolve_identity(request, "tenant_x", "chan_1")

    def test_uid_mode_accepts_unsigned_userid(self):
        identity = self._resolve({"identity_mode": "uid", "enabled": True},
                                 {"u": "u1001", "name": "张三"})
        assert identity.mode == "uid"
        assert identity.channel_user_id == "uid_u1001"
        assert identity.display_name == "张三"
        assert identity.session_prefix == "tenant_x_web_c_uid_u1001_"

    def test_uid_mode_ignores_signature_params(self):
        # uid 模式下 ts/sig 被忽略：无签名与伪造签名同样按 uid 身份放行，不升级 ext
        identity = self._resolve({"identity_mode": "uid"},
                                 {"u": "u1001", "ts": "1", "sig": "deadbeef"})
        assert identity.mode == "uid"
        assert identity.channel_user_id == "uid_u1001"

    def test_uid_mode_rejects_bad_userid_format(self):
        with pytest.raises(HTTPException) as exc:
            self._resolve({"identity_mode": "uid"}, {"u": "bad id!空间"})
        assert exc.value.status_code == 403

    def test_uid_mode_rejects_anon_uid(self):
        # 「仅 userid 链接」= uid 链接单一路径，匿名 uid 不放行
        with pytest.raises(HTTPException) as exc:
            self._resolve({"identity_mode": "uid"}, {"uid": "anon_2f0c9d1e-1111-4222-8333-444455556666"})
        assert exc.value.status_code == 403

    def test_ext_mode_rejects_unsigned_userid(self):
        # 非 uid 渠道：u 无签名仍 403（既有安全语义不回归）
        with pytest.raises(HTTPException) as exc:
            self._resolve({"identity_mode": "ext", "channel_secret": SECRET}, {"u": "u1001"})
        assert exc.value.status_code == 403

    def test_anon_mode_rejects_userid(self):
        with pytest.raises(HTTPException) as exc:
            self._resolve({"identity_mode": "anon", "channel_secret": SECRET}, {"u": "u1001"})
        assert exc.value.status_code == 403

    def test_assert_session_owned_requires_random_suffix_in_uid_mode(self):
        from src.saas.api.web_c import _assert_session_owned, WebCIdentity

        identity = WebCIdentity("tenant_x", "chan_1", "uid", "uid_u1001", "", "main")
        # 确定性派生形态（可由 userid 推算）拒绝
        with pytest.raises(HTTPException) as exc:
            _assert_session_owned(identity, "tenant_x_web_c_uid_u1001_main")
        assert exc.value.status_code == 403
        # 随机后缀形态放行（DB 归属校验 mock 通过）
        with patch("src.saas.api.web_c.channel_session_manager") as mgr:
            mgr.get_session_by_id.return_value = {"tenant_id": "tenant_x"}
            session = _assert_session_owned(
                identity, "tenant_x_web_c_uid_u1001_main_s0a1b2c3d4e5")
        assert session["tenant_id"] == "tenant_x"

    def test_assert_session_owned_non_uid_accepts_default_session(self):
        # ext/anon 模式默认确定性会话不受影响（行为不回归）
        from src.saas.api.web_c import _assert_session_owned, WebCIdentity

        identity = WebCIdentity("tenant_x", "chan_1", "ext", "ext_u1001", "", "main")
        with patch("src.saas.api.web_c.channel_session_manager") as mgr:
            mgr.get_session_by_id.return_value = {"tenant_id": "tenant_x"}
            session = _assert_session_owned(identity, "tenant_x_web_c_ext_u1001_main")
        assert session["tenant_id"] == "tenant_x"

    def test_extra_session_suffix_entropy_at_least_12_hex(self):
        from src.saas.api.web_c import _UID_SESSION_SUFFIX_RE, WebCIdentity, _create_extra_session_sync

        identity = WebCIdentity("tenant_x", "chan_1", "uid", "uid_u1001", "", "main")
        with patch("src.saas.api.web_c.channel_session_manager") as mgr:
            mgr.create_session.side_effect = lambda **kw: dict(kw)
            session = _create_extra_session_sync(identity, "user_1")
        match = _UID_SESSION_SUFFIX_RE.search(session["session_id"])
        assert match, f"随机后缀缺失: {session['session_id']}"
        assert len(match.group(0)) - 2 >= 12  # 去掉 _s 前缀，熵 ≥ 48bit


class TestIdentityModeConfigNormalize:
    """身份模式规整：both 组合模式 2026-11-19 下线，缺省 anon，非法值 400。"""

    def test_normalize_defaults_to_anon(self):
        from src.saas.api.channel_config import _normalize_web_c_config

        config = {}
        _normalize_web_c_config(config)
        assert config["identity_mode"] == "anon"

    def test_normalize_rejects_both_combo_mode(self):
        from src.saas.api.channel_config import _normalize_web_c_config

        with pytest.raises(HTTPException) as exc:
            _normalize_web_c_config({"identity_mode": "both"})
        assert exc.value.status_code == 400

    def test_normalize_accepts_three_single_modes(self):
        from src.saas.api.channel_config import _normalize_web_c_config

        for mode in ("ext", "anon", "uid"):
            config = {"identity_mode": mode}
            _normalize_web_c_config(config)
            assert config["identity_mode"] == mode


class TestLeadAssigneeNormalize:
    """留资归属员工规整（2026-11-20 方案 2）：缺省空串（未配置），前后空白剔除。"""

    def test_normalize_defaults_to_empty(self):
        from src.saas.api.channel_config import _normalize_web_c_config

        config = {}
        _normalize_web_c_config(config)
        assert config["lead_assignee_user_id"] == ""

    def test_normalize_strips_and_keeps_value(self):
        from src.saas.api.channel_config import _normalize_web_c_config

        config = {"lead_assignee_user_id": " emp-1 "}
        _normalize_web_c_config(config)
        assert config["lead_assignee_user_id"] == "emp-1"

    def test_normalize_blank_becomes_empty(self):
        from src.saas.api.channel_config import _normalize_web_c_config

        config = {"lead_assignee_user_id": "   "}
        _normalize_web_c_config(config)
        assert config["lead_assignee_user_id"] == ""


class TestLeadAssigneeValidation:
    """留资归属员工归属校验：本租户 + active 才放行；空值放行（清空合法）。"""

    @staticmethod
    def _user(tenant_id="tenant", status="active"):
        return {"user_id": "emp-1", "tenant_id": tenant_id, "status": status}

    def test_empty_passes(self):
        from src.saas.api.channel_config import _validate_web_c_lead_assignee

        _validate_web_c_lead_assignee("tenant", {"lead_assignee_user_id": ""})

    def test_tenant_active_user_passes(self):
        from unittest.mock import patch

        from src.saas.api.channel_config import _validate_web_c_lead_assignee

        with patch("src.db.models.UserDB.get_by_id", return_value=self._user()):
            _validate_web_c_lead_assignee("tenant", {"lead_assignee_user_id": "emp-1"})

    def test_cross_tenant_user_rejected(self):
        from unittest.mock import patch

        import pytest
        from fastapi import HTTPException

        from src.saas.api.channel_config import _validate_web_c_lead_assignee

        with patch("src.db.models.UserDB.get_by_id", return_value=self._user(tenant_id="other")):
            with pytest.raises(HTTPException) as exc:
                _validate_web_c_lead_assignee("tenant", {"lead_assignee_user_id": "emp-1"})
            assert exc.value.status_code == 400

    def test_deleted_user_rejected(self):
        from unittest.mock import patch

        import pytest
        from fastapi import HTTPException

        from src.saas.api.channel_config import _validate_web_c_lead_assignee

        with patch("src.db.models.UserDB.get_by_id", return_value=self._user(status="disabled")):
            with pytest.raises(HTTPException) as exc:
                _validate_web_c_lead_assignee("tenant", {"lead_assignee_user_id": "emp-1"})
            assert exc.value.status_code == 400

    def test_unknown_user_rejected(self):
        from unittest.mock import patch

        import pytest
        from fastapi import HTTPException

        from src.saas.api.channel_config import _validate_web_c_lead_assignee

        with patch("src.db.models.UserDB.get_by_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                _validate_web_c_lead_assignee("tenant", {"lead_assignee_user_id": "emp-1"})
            assert exc.value.status_code == 400


class TestClarificationPersistence:
    """ask_user 反问落库（2026-11-19 测试反馈：中途追问未落库致接待页/历史缺失）。"""

    @staticmethod
    def _waiting_row(wait_id="wait_1", kind="clarification", form=None):
        row = _valid_runner_row(status="waiting")
        waiting = {"kind": kind, "wait_id": wait_id, "target_execution_id": "exec_1",
                   "question": "请补充你的城市？"}
        if form is not None:
            waiting["form"] = form
        row["snapshot"] = {"waiting": waiting}
        return row

    def test_clarification_question_extracts_text_and_wait_id(self):
        from src.saas.api.web_c import _clarification_question

        question, wait_id = _clarification_question(self._waiting_row())
        assert question == "请补充你的城市？"
        assert wait_id == "wait_1"
        # 非澄清等待（如 child_wait）不产生反问文本
        assert _clarification_question(_valid_runner_row(status="running")) == ("", "")
        child = _valid_runner_row(status="waiting")
        child["snapshot"] = {"waiting": {"kind": "child_wait", "child_wait": {
            "kind": "clarification", "wait_id": "w2", "question": "q"}}}
        assert _clarification_question(child) == ("q", "w2")

    def test_persist_skips_when_question_or_wait_id_missing(self):
        from src.saas.services.web_c_runner_persist import persist_clarification_message

        with patch("src.saas.services.web_c_runner_persist.get_db_connection") as mock_conn:
            assert persist_clarification_message("t1", "s1", "run_1", "", "wait_1") is False
            assert persist_clarification_message("t1", "s1", "run_1", "q", "") is False
            mock_conn.assert_not_called()

    def test_persist_inserts_then_dedupes_by_wait_id(self):
        from src.saas.services.web_c_runner_persist import persist_clarification_message

        class FakeCursor:
            def __init__(self, exists_result):
                self.exists_result = exists_result
                self.executed = []

            def execute(self, sql, params=None):
                self.executed.append(" ".join(sql.split()))

            def fetchone(self):
                return self.exists_result

        class FakeConn:
            def __init__(self, cursor):
                self._cursor = cursor

            def cursor(self):
                return self._cursor

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        # 首次：SELECT 无记录 -> INSERT 成功 -> UPDATE 会话时间戳
        cursor = FakeCursor(exists_result=None)
        cursor.rowcount = 1
        with patch("src.saas.services.web_c_runner_persist.get_db_connection", return_value=FakeConn(cursor)):
            assert persist_clarification_message("t1", "t1_web_c_uid_0011_x_sab", "run_1",
                                                  "请补充你的城市？", "wait_1") is True
        assert any("INSERT INTO channel_messages" in sql for sql in cursor.executed)
        assert any("ON CONFLICT DO NOTHING" in sql for sql in cursor.executed)

        # 已存在（SELECT 命中）-> 不再 INSERT
        cursor2 = FakeCursor(exists_result={"exists": 1})
        with patch("src.saas.services.web_c_runner_persist.get_db_connection", return_value=FakeConn(cursor2)):
            assert persist_clarification_message("t1", "t1_web_c_uid_0011_x_sab", "run_1",
                                                  "请补充你的城市？", "wait_1") is False
        assert len(cursor2.executed) == 1 and cursor2.executed[0].startswith("SELECT 1")

    @pytest.mark.asyncio
    async def test_get_runner_persists_waiting_question(self):
        from unittest.mock import MagicMock

        from src.saas.api.web_c import get_runner

        row = self._waiting_row()

        async def fake_get_owned_runner(identity, runner_id, client=None):
            return row

        # 生产路径经 asyncio.to_thread 调用，桩须为同步 MagicMock
        with patch("src.saas.api.web_c._resolve_identity", return_value=None), \
             patch("src.saas.api.web_c._get_owned_runner", new=fake_get_owned_runner), \
             patch("src.saas.api.web_c._persist_clarification_message", new=MagicMock()) as mock_persist:
            response = await get_runner("t1", "cfg1", "run_1", request=None)
        assert response["runner"]["waiting_text"] == "请补充你的城市？"
        mock_persist.assert_called_once()
        args = mock_persist.call_args.args
        assert args[:2] == ("t1", "t1_web_c_ext_u1_")
        assert args[3] == "请补充你的城市？" and args[4] == "wait_1"


class TestBackfillSessionRunners:
    """会话级终态补写（2026-11-23 事故回归：落库仅由访客行为触发，访客关页
    后回复含文件永不落库；现实现 DB 直读 agent_runners，不依赖 Runner API）。"""

    @staticmethod
    def _completed_row(runner_id="run_a"):
        return {
            "runner_id": runner_id,
            "tenant_id": "t1",
            "session_id": "t1_web_c_uid_0011_x_sab",
            "status": "completed",
            "result": {"output": "报价单已经做好了，你看一下附件。"},
            "snapshot": {"downloadableFiles": [{
                "file_id": "file_54d1dcb58330", "name": "报价单.xlsx",
                "mime_type": "", "size": 8969,
                "download_url": "/api/files/file_54d1dcb58330/download",
            }]},
        }

    @staticmethod
    def _waiting_row(runner_id="run_b"):
        return {
            "runner_id": runner_id,
            "tenant_id": "t1",
            "session_id": "t1_web_c_uid_0011_x_sab",
            "status": "waiting",
            "result": {},
            "snapshot": {"waiting": {"kind": "clarification", "wait_id": "wait_9",
                                     "question": "请补充出行人数？"}},
        }

    @pytest.mark.asyncio
    async def test_completed_and_waiting_backfilled(self):
        from unittest.mock import MagicMock

        from src.saas.services import web_c_runner_persist as svc

        rows = [self._completed_row(), self._waiting_row()]
        with patch.object(svc, "_load_runner_rows", return_value=rows), \
             patch.object(svc, "has_final_message", return_value=False), \
             patch.object(svc, "persist_assistant_message", new=MagicMock()) as mock_persist, \
             patch.object(svc, "persist_clarification_message", new=MagicMock(return_value=True)) as mock_clarify:
            count = await svc.backfill_session_runners("t1", "t1_web_c_uid_0011_x_sab")

        assert count == 2
        args = mock_persist.call_args.args
        assert args[0] == "t1" and args[1] == "t1_web_c_uid_0011_x_sab" and args[2] == "run_a"
        assert args[3] == "报价单已经做好了，你看一下附件。"
        # 附件随消息一起补写（downloadableFiles 投影）
        assert [a["file_id"] for a in args[4]] == ["file_54d1dcb58330"]
        clarify_args = mock_clarify.call_args.args
        assert clarify_args[:2] == ("t1", "t1_web_c_uid_0011_x_sab")
        assert clarify_args[3] == "请补充出行人数？" and clarify_args[4] == "wait_9"

    @pytest.mark.asyncio
    async def test_already_persisted_runner_skipped(self):
        from unittest.mock import MagicMock

        from src.saas.services import web_c_runner_persist as svc

        rows = [self._completed_row()]
        with patch.object(svc, "_load_runner_rows", return_value=rows), \
             patch.object(svc, "has_final_message", return_value=True), \
             patch.object(svc, "persist_assistant_message", new=MagicMock()) as mock_persist:
            count = await svc.backfill_session_runners("t1", "t1_web_c_uid_0011_x_sab")

        assert count == 0
        mock_persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_skips_when_wait_id_missing(self):
        from unittest.mock import MagicMock

        from src.saas.services import web_c_runner_persist as svc

        waiting = self._waiting_row()
        waiting["snapshot"] = {"waiting": {"kind": "clarification", "question": "q"}}
        with patch.object(svc, "_load_runner_rows", return_value=[waiting]), \
             patch.object(svc, "persist_clarification_message", new=MagicMock()) as mock_clarify:
            count = await svc.backfill_session_runners("t1", "s1")

        assert count == 0
        mock_clarify.assert_not_called()


class TestWebCRunnerPersistSweeper:
    """background 兜底扫描器：访客离线时 completed 回复（含文件）与 waiting 反问补写。"""

    @staticmethod
    def _row(**overrides):
        row = {
            "runner_id": "run_s1",
            "tenant_id": "t1",
            "session_id": "t1_web_c_uid_0011_x_sab",
            "status": "completed",
            "result": {"output": "两份文件都做好了。"},
            "snapshot": {"downloadableFiles": [{
                "file_id": "file_a", "file_name": "行程方案.xlsx",
                "file_size": 8995, "mime_type": "",
                "download_url": "/api/files/file_a/download",
            }]},
        }
        row.update(overrides)
        return row

    @pytest.mark.asyncio
    async def test_sweep_persists_completed_and_waiting(self):
        from unittest.mock import MagicMock

        from src.saas.services import web_c_runner_persist as svc

        waiting = self._row(runner_id="run_s2", status="waiting",
                            result={}, snapshot={"waiting": {
                                "kind": "clarification", "wait_id": "wait_5",
                                "question": "需要确定日期吗？"}})
        sweeper = svc.WebCRunnerPersistSweeper()
        with patch.object(svc, "_load_recent_runner_rows",
                          return_value=[self._row(), waiting]), \
             patch.object(svc, "has_final_message", return_value=False), \
             patch.object(svc, "persist_assistant_message", new=MagicMock()) as mock_persist, \
             patch.object(svc, "persist_clarification_message", new=MagicMock(return_value=True)):
            count = await sweeper.sweep_once()

        assert count == 2
        args = mock_persist.call_args.args
        # file_name 兜底：snapshot 字段为 file_name，投影应回填 name
        assert args[4][0]["name"] == "行程方案.xlsx"
        assert args[4][0]["size"] == 8995

    @pytest.mark.asyncio
    async def test_sweep_skips_persisted_and_missing_fields(self):
        from unittest.mock import MagicMock

        from src.saas.services import web_c_runner_persist as svc

        sweeper = svc.WebCRunnerPersistSweeper()
        rows = [self._row(), self._row(runner_id="", tenant_id=None)]
        with patch.object(svc, "_load_recent_runner_rows", return_value=rows), \
             patch.object(svc, "has_final_message", return_value=True), \
             patch.object(svc, "persist_assistant_message", new=MagicMock()) as mock_persist:
            count = await sweeper.sweep_once()

        assert count == 0
        mock_persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_sweep_completed_persist_error_does_not_break_round(self):
        from unittest.mock import MagicMock

        from src.saas.services import web_c_runner_persist as svc

        sweeper = svc.WebCRunnerPersistSweeper()
        with patch.object(svc, "_load_recent_runner_rows", return_value=[self._row()]), \
             patch.object(svc, "has_final_message", return_value=False), \
             patch.object(svc, "persist_assistant_message",
                          new=MagicMock(side_effect=RuntimeError("db down"))):
            count = await sweeper.sweep_once()

        assert count == 0  # 单条失败仅记日志，不中断本轮其余行

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        from src.saas.services import web_c_runner_persist as svc

        sweeper = svc.WebCRunnerPersistSweeper()
        assert await sweeper.start() is True
        # 重复启动幂等
        assert await sweeper.start() is True
        await sweeper.stop()
        assert sweeper._task is None

    @pytest.mark.asyncio
    async def test_sweep_once_error_swallowed_in_loop(self):
        """_run_loop 单轮异常自吞（下轮重试），不令扫描器任务退出。"""
        from src.saas.services import web_c_runner_persist as svc

        sweeper = svc.WebCRunnerPersistSweeper()
        calls = {"n": 0}

        async def fake_sweep():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")

        with patch.object(sweeper, "sweep_once", new=fake_sweep):
            task = asyncio.get_running_loop().create_task(sweeper._run_loop())
            await asyncio.sleep(0.05)
            assert not task.done()  # 首轮异常后仍存活
            await sweeper.stop()



class TestAlignSessionProfile:
    """渠道改绑数字员工后的存量会话对齐（CHANNEL_PROFILE_MISMATCH 修复）。"""

    def _identity(self, subagent="sales-assistant"):
        from src.saas.api.web_c import WebCIdentity

        return WebCIdentity("tenant_x", "chan_1", "anon", "anon_uid1", "访客", subagent)

    def _make_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        return conn, cursor

    def test_same_binding_no_db_write(self):
        """subagent_id 与当前绑定一致时不写库。"""
        from src.saas.api.web_c import _align_session_profile

        session = {"session_id": "tenant_x_web_c_anon_uid1_sales-assistant_sabc", "subagent_id": "sales-assistant"}
        with patch("src.saas.api.web_c.get_db_connection") as mock_db:
            _align_session_profile(self._identity("sales-assistant"), dict(session))
            mock_db.assert_not_called()

    def test_empty_binding_matches_empty(self):
        """双方均为空（未绑定员工）时不写库。"""
        from src.saas.api.web_c import _align_session_profile

        with patch("src.saas.api.web_c.get_db_connection") as mock_db:
            _align_session_profile(self._identity(""), {"session_id": "s1", "subagent_id": ""})
            mock_db.assert_not_called()

    def test_rebind_migrates_session_row(self):
        """改绑后提交前把会话 subagent_id 迁移为当前绑定（幂等 UPDATE + 内存同步）。"""
        from src.saas.api.web_c import _align_session_profile

        conn, cursor = self._make_conn()
        session = {"session_id": "tenant_x_web_c_anon_uid1_old-agent_sabc123",
                   "subagent_id": "sales-assistant-tenant-e9b2fab93a2f-ubnn"}
        with patch("src.saas.api.web_c.get_db_connection", return_value=conn):
            _align_session_profile(self._identity("sales-assistant"), session)
        sql = cursor.execute.call_args[0][0]
        assert "UPDATE channel_sessions SET subagent_id" in sql
        assert "AND tenant_id = %s" in sql
        params = cursor.execute.call_args[0][1]
        assert params[0] == "sales-assistant"  # 迁移为当前绑定
        assert params[2] == "tenant_x_web_c_anon_uid1_old-agent_sabc123"
        assert params[3] == "tenant_x"
        assert session["subagent_id"] == "sales-assistant"  # 内存同步
        conn.commit.assert_called_once()

    def test_wipe_binding_migrates_to_empty(self):
        """绑定被清空（None）时会话迁移为空串，提交口径回落 main。"""
        from src.saas.api.web_c import _align_session_profile

        conn, cursor = self._make_conn()
        session = {"session_id": "s1", "subagent_id": "old-agent"}
        with patch("src.saas.api.web_c.get_db_connection", return_value=conn):
            _align_session_profile(self._identity(None), session)
        assert cursor.execute.call_args[0][1][0] == ""
        assert session["subagent_id"] == ""
