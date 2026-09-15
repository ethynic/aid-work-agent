#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""external_push 适配器单元测试（租户文档驱动 + LLM http_api 工具循环）"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 先触发 src.llm 包导入（其模块顶层会以真实 settings 构造 llm_gateway 单例），
# 避免在测试内 patch settings 时首次导入导致构造失败
import src.llm.gateway  # noqa: F401,E402

from src.services.recap.runner import RecapPayload
from src.services.recap.tasks import RECAP_TASK_ADAPTERS
from src.services.recap.tasks.external_push import (
    ExternalPushAdapter,
    PushReportTool,
    _build_user_message,
    _collect_context,
    _create_tool_runtime,
    _delegate_login,
    _extract_business_code,
    _extract_json_object,
    _get_agent_token,
    _load_tenant_doc,
    _resolve_assignee,
    _run_push_loop,
    _strip_excluded_sections,
    _summarize,
    parse_api_meta,
    parse_wecom_kf_session,
)


def _make_payload(round_message_id=101):
    return RecapPayload(
        tenant_id="tenant_x",
        session_id="tenant_x_wecom_kf_wkS6oOTAAAqAvd_wmS6oOTAAAhVHj3_umWg_pre-sales",
        subagent_name="售前咨询专员",
        round_message_id=round_message_id,
        user_content="这个产品多少钱？",
        assistant_reply="您好，具体价格取决于配置，我发您一份报价单。",
        record_service=None,
    )


def _ctx():
    return {
        "open_kfid": "wkS6oOTAAAqAvd",
        "external_userid": "wmS6oOTAAAhVHj3_umWg",
        "subagent": "pre-sales",
        "nickname": "小团长",
        "avatar": None,
        "gender": 0,
        "lead_phone": "13916323347",
        "assignee_phone": "13701602974",
        "assignee_name": "王顾问",
    }


def _summary():
    return {"customer_need": "询问价格", "reply_summary": "介绍了报价", "customer_name_hint": ""}


def _meta():
    return {
        "login_url": "https://erp.example.com/api/login",
        "auth_mode": "delegate_login",
        "user_token_name": "client_token",
        "external_userid_field": "unionid",
    }


def _login():
    return {"client_token": "tok", "display_name": "客服小王", "record_id": 4, "agent_name": "AI助手"}


def _doc(login_url="https://erp.example.com/api/login"):
    return (
        "# 租户接口文档\n\n"
        "```api-meta\n"
        f"login_url: {login_url}\n"
        "auth_mode: delegate_login\n"
        "user_token_name: client_token\n"
        "external_userid_field: unionid\n"
        "```\n\n"
        "## 1 接口\n\n查询接口 POST /api/list，字段：name、phone。\n"
    )


# ============== session_id 解析 ==============


class TestParseWecomKfSession:
    def test_valid_session(self):
        parsed = parse_wecom_kf_session(
            "tenant_9b9fb62aff5e_wecom_kf_wkS6oOTAAAqAvd_wmS6oOTAAAhVHj3_umWg_pre-sales"
        )
        assert parsed == {
            "open_kfid": "wkS6oOTAAAqAvd",
            "external_userid": "wmS6oOTAAAhVHj3_umWg",
            "subagent": "pre-sales",
        }

    def test_non_wecom_kf_returns_none(self):
        assert parse_wecom_kf_session("tenant_x_dingtalk_user1_sa") is None
        assert parse_wecom_kf_session("") is None
        assert parse_wecom_kf_session(None) is None

    def test_malformed_returns_none(self):
        # 标记后只有一段，无 external_userid/subagent 结构
        assert parse_wecom_kf_session("tenant_x_wecom_kf_onlyone") is None


# ============== JSON 提取 ==============


class TestExtractJsonObject:
    def test_plain_json(self):
        assert _extract_json_object('{"a": 1}') == {"a": 1}

    def test_code_fence(self):
        assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_with_noise(self):
        assert _extract_json_object('结果如下：{"a": 1}') == {"a": 1}

    def test_invalid_returns_none(self):
        assert _extract_json_object("not json") is None
        assert _extract_json_object("") is None


# ============== api-meta 解析 ==============


class TestParseApiMeta:
    def test_standard_block_all_keys(self):
        meta = parse_api_meta(_doc())
        assert meta["login_url"] == "https://erp.example.com/api/login"
        assert meta["auth_mode"] == "delegate_login"
        assert meta["user_token_name"] == "client_token"
        assert meta["external_userid_field"] == "unionid"

    def test_optional_keys_default_to_10605_conventions(self):
        doc = "# 标题\n```api-meta\nlogin_url: https://x.example.com/login\n```\n正文"
        meta = parse_api_meta(doc)
        assert meta["user_token_name"] == "client_token"
        assert meta["external_userid_field"] == "unionid"

    def test_custom_token_name_and_field(self):
        doc = (
            "```api-meta\n"
            "login_url: https://x.example.com/login\n"
            "user_token_name: user_token\n"
            "external_userid_field: wx_id\n"
            "```\n"
        )
        meta = parse_api_meta(doc)
        assert meta["user_token_name"] == "user_token"
        assert meta["external_userid_field"] == "wx_id"

    def test_missing_block_returns_none(self):
        assert parse_api_meta("# 标题\n正文，无 api-meta 块") is None
        assert parse_api_meta("") is None

    def test_missing_login_url_returns_none(self):
        doc = "```api-meta\nauth_mode: delegate_login\n```"
        assert parse_api_meta(doc) is None

    def test_http_url_rejected(self):
        doc = "```api-meta\nlogin_url: http://erp.example.com/login\n```"
        assert parse_api_meta(doc) is None

    def test_multiple_blocks_takes_first(self):
        doc = (
            "```api-meta\nlogin_url: https://first.example.com/login\n```\n"
            "```api-meta\nlogin_url: https://second.example.com/login\n```\n"
        )
        assert parse_api_meta(doc)["login_url"] == "https://first.example.com/login"

    def test_comments_blank_lines_and_trailing_comma_tolerated(self):
        doc = (
            "```api-meta\n"
            "# 注释行\n"
            "<!-- html注释 -->\n"
            "\n"
            "login_url: https://x.example.com/login,\n"
            "auth_mode: delegate_login,\n"
            "```\n"
        )
        meta = parse_api_meta(doc)
        assert meta["login_url"] == "https://x.example.com/login"
        assert meta["auth_mode"] == "delegate_login"

    def test_unknown_keys_skipped(self):
        doc = "```api-meta\nlogin_url: https://x.example.com/login\nfuture_key: v1\n```"
        meta = parse_api_meta(doc)
        assert meta["login_url"] == "https://x.example.com/login"


# ============== 租户文档加载 ==============


class TestStripExcludedSections:
    def _doc(self):
        return (
            "# 租户接口文档\n\n前言内容。\n\n"
            "## 1. 通用规范\n\n通用内容。\n"
            "## 2. 委托登录接口\n\n登录内容。\n"
            "## 3. 客户列表接口\n\n列表内容。\n"
            "## 4. 客户信息详情接口\n\n详情内容。\n"
        )

    def test_no_key_returns_original(self):
        doc = self._doc()
        assert _strip_excluded_sections(doc, {"login_url": "https://e/x"}) == doc

    def test_empty_key_returns_original(self):
        doc = self._doc()
        assert _strip_excluded_sections(doc, {"push_exclude_sections": ""}) == doc

    def test_excludes_by_substring_with_number_prefix(self):
        meta = {"push_exclude_sections": "委托登录接口,客户信息详情接口"}
        result = _strip_excluded_sections(self._doc(), meta)
        assert "登录内容" not in result
        assert "详情内容" not in result
        assert "通用内容" in result
        assert "列表内容" in result
        assert "前言内容" in result

    def test_chinese_comma_separator_supported(self):
        """租户写中文逗号"，"也能正确切分"""
        meta = {"push_exclude_sections": "委托登录接口，客户信息详情接口"}
        result = _strip_excluded_sections(self._doc(), meta)
        assert "登录内容" not in result
        assert "详情内容" not in result
        assert "列表内容" in result

    def test_all_sections_excluded_keeps_preamble(self):
        meta = {"push_exclude_sections": "通用规范,委托登录接口,客户列表接口,客户信息详情接口"}
        result = _strip_excluded_sections(self._doc(), meta)
        assert "## " not in result
        assert "前言内容" in result


class TestLoadTenantDoc:
    def test_missing_file_returns_none(self, tmp_path):
        with patch("src.services.recap.tasks.external_push._tenant_doc_path",
                   return_value=tmp_path / "templates" / "pre-sales-api.md"):
            assert _load_tenant_doc("tenant_x", "pre-sales") is None

    def test_existing_file_returns_full_text_no_truncation(self, tmp_path):
        doc_file = tmp_path / "pre-sales-api.md"
        doc_file.write_text(_doc(), encoding="utf-8")
        with patch("src.services.recap.tasks.external_push._tenant_doc_path",
                   return_value=doc_file):
            assert _load_tenant_doc("tenant_x", "pre-sales") == _doc()

    def test_doc_path_derived_from_subagent_name(self):
        """文档路径按子智能体推导：templates/{subagent}-api.md（不回退他智能体文档）"""
        from src.services.recap.tasks.external_push import _tenant_doc_path

        path = _tenant_doc_path("tenant_x", "my-custom-agent")
        assert path.name == "my-custom-agent-api.md"
        assert path.parent.name == "templates"


# ============== 委托登录（缓存 + api-meta login_url） ==============


class TestDelegateLogin:
    def test_cache_hit_zero_http(self):
        cached = {"client_token": "tok_cached", "record_id": 1, "display_name": "d",
                  "agent_name": "a", "login_url": "https://erp.example.com/login"}
        with patch("src.core.cache_utils.get_cached", return_value=cached) as mock_get, \
             patch("src.services.recap.tasks.external_push._post_json") as mock_post:
            login = _delegate_login("tenant_x", "13701602974", "agent_tok", "https://erp.example.com/login")
        assert login["client_token"] == "tok_cached"
        assert login["cached"] is True
        mock_get.assert_called_once()
        mock_post.assert_not_called()

    def test_cache_key_includes_subagent_scope(self):
        """缓存键带子智能体维度（空值归一为 '-'），隔离同租户多智能体对接的不同系统"""
        cached = {"client_token": "tok_cached", "login_url": "https://erp.example.com/login"}
        with patch("src.core.cache_utils.get_cached", return_value=cached) as mock_get, \
             patch("src.services.recap.tasks.external_push._post_json"):
            _delegate_login("tenant_x", "m", "tok", "https://erp.example.com/login", subagent_name="foo")
        assert mock_get.call_args.args == ("external_login_token", "tenant_x", "foo", "m")

    def test_cache_login_url_mismatch_treated_as_miss(self):
        """缓存值 login_url 与本次不一致（跨系统串号场景）按未命中处理，重新登录"""
        cached = {"client_token": "tok_other_system", "login_url": "https://other.example.com/login"}
        with patch("src.core.cache_utils.get_cached", return_value=cached), \
             patch("src.core.cache_utils.set_cached") as mock_set, \
             patch("src.services.recap.tasks.external_push._post_json",
                   return_value={"Code": 0, "Response": {"client_token": "tok_new"}}) as mock_post:
            login = _delegate_login("tenant_x", "m", "tok", "https://erp.example.com/login")
        assert login["client_token"] == "tok_new"
        assert login["cached"] is False
        mock_post.assert_called_once()
        # 回写的新缓存值带本次 login_url
        assert mock_set.call_args.kwargs["value"]["login_url"] == "https://erp.example.com/login"

    def test_cache_miss_uses_login_url_from_meta(self):
        with patch("src.core.cache_utils.get_cached", return_value=None), \
             patch("src.core.cache_utils.set_cached") as mock_set, \
             patch("src.services.recap.tasks.external_push._post_json",
                   return_value={"Code": 0, "Response": {
                       "client_token": "tok_new", "record_id": 4,
                       "display_name": "客服小王", "agent_name": "AI助手"}}) as mock_post:
            login = _delegate_login(
                "tenant_x", "13701602974", "agent_tok", "https://erp.example.com/login"
            )
        assert login["client_token"] == "tok_new"
        assert login["cached"] is False
        # login_url 由调用方传入（来自 api-meta），非硬编码常量
        assert mock_post.call_args.args[0] == "https://erp.example.com/login"
        assert mock_post.call_args.args[1] == {"mobile": "13701602974"}
        mock_set.assert_called_once()

    def test_name_passed_when_provided(self):
        """name 非空时随 mobile 一起传给登录接口（自动建号用）"""
        with patch("src.core.cache_utils.get_cached", return_value=None), \
             patch("src.core.cache_utils.set_cached"), \
             patch("src.services.recap.tasks.external_push._post_json",
                   return_value={"Code": 0, "Response": {"client_token": "tok_new", "created": True}}) as mock_post:
            login = _delegate_login(
                "tenant_x", "13701602974", "agent_tok", "https://erp.example.com/login",
                name="王顾问",
            )
        assert mock_post.call_args.args[1] == {"mobile": "13701602974", "name": "王顾问"}
        assert login["created"] is True

    def test_name_omitted_when_empty(self):
        """name 为空时不传，避免覆盖外部系统兜底命名"""
        with patch("src.core.cache_utils.get_cached", return_value=None), \
             patch("src.core.cache_utils.set_cached"), \
             patch("src.services.recap.tasks.external_push._post_json",
                   return_value={"Code": 0, "Response": {"client_token": "tok_new"}}) as mock_post:
            login = _delegate_login(
                "tenant_x", "13701602974", "agent_tok", "https://erp.example.com/login",
                name="",
            )
        assert mock_post.call_args.args[1] == {"mobile": "13701602974"}
        assert login["created"] is False

    def test_business_error_returns_none(self):
        with patch("src.core.cache_utils.get_cached", return_value=None), \
             patch("src.services.recap.tasks.external_push._post_json",
                   return_value={"Code": -1, "Error": "鉴权失败"}):
            assert _delegate_login("tenant_x", "m", "tok", "https://x/login") is None

    def test_http_error_returns_none(self):
        with patch("src.core.cache_utils.get_cached", return_value=None), \
             patch("src.services.recap.tasks.external_push._post_json",
                   side_effect=RuntimeError("conn refused")):
            assert _delegate_login("tenant_x", "m", "tok", "https://x/login") is None

    def test_force_refresh_skips_cache(self):
        cached = {"client_token": "tok_old"}
        with patch("src.core.cache_utils.get_cached", return_value=cached) as mock_get, \
             patch("src.core.cache_utils.set_cached"), \
             patch("src.services.recap.tasks.external_push._post_json",
                   return_value={"Code": 0, "Response": {"client_token": "tok_new"}}):
            login = _delegate_login("tenant_x", "m", "tok", "https://x/login", force_refresh=True)
        mock_get.assert_not_called()
        assert login["client_token"] == "tok_new"


# ============== 业务 Code 提取 ==============


class TestExtractBusinessCode:
    def test_from_data_dict(self):
        assert _extract_business_code({"success": True, "data": {"Code": 0}}) == 0
        assert _extract_business_code({"success": False, "data": {"Code": -99}}) == -99

    def test_from_error_text(self):
        assert _extract_business_code({"success": False, "error": "业务错误 Code=-99: token失效"}) == -99

    def test_absent_returns_none(self):
        assert _extract_business_code({"success": True, "data": {"rows": []}}) is None
        assert _extract_business_code({"success": False, "error": "HTTP 500"}) is None


# ============== report_push_result 工具 ==============


class TestPushReportTool:
    def test_appends_to_holder(self):
        holder = []
        tool = PushReportTool(holder)
        result = asyncio.run(tool.execute(success=True, detail="查重+创建+跟进 完成"))
        assert result["recorded"] is True
        assert holder == [{"success": True, "detail": "查重+创建+跟进 完成"}]

    def test_values_coerced(self):
        holder = []
        tool = PushReportTool(holder)
        asyncio.run(tool.execute(success="yes", detail=123))
        assert holder[0]["success"] is True
        assert holder[0]["detail"] == "123"


# ============== 上下文采集 ==============


class TestCollectContext:
    def test_non_wecom_kf_session_aborts(self):
        payload = RecapPayload(
            tenant_id="t", session_id="tenant_t_dingtalk_u1_sa",
            subagent_name="n", round_message_id=1,
            user_content="u", assistant_reply="a",
        )
        assert _collect_context(payload) is None

    def test_full_context(self):
        payload = _make_payload()
        session_row = {
            "username": "小团长",
            "metadata": {"lead_capture": {"lead_id": "lead_lc_abc", "stage": "captured"}},
        }
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id", return_value={"phone": "13916323347"}), \
             patch("src.services.recap.tasks.external_push._resolve_assignee",
                   return_value=("13701602974", "王顾问")):
            mock_mgr.get_session_by_id.return_value = session_row
            ctx = _collect_context(payload)

        assert ctx == _ctx()

    def test_lead_read_failure_degrades(self):
        payload = _make_payload()
        session_row = {
            "username": "小团长",
            "metadata": {"lead_capture": {"lead_id": "lead_lc_abc"}},
        }
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id", side_effect=RuntimeError("db down")), \
             patch("src.services.recap.tasks.external_push._resolve_assignee",
                   return_value=("13701602974", "王顾问")):
            mock_mgr.get_session_by_id.return_value = session_row
            ctx = _collect_context(payload)
        assert ctx["lead_phone"] is None

    def test_avatar_gender_read_from_users(self):
        """会话带 user_id 时从 users 表读取头像/性别；读取失败降级为空值"""
        payload = _make_payload()
        session_row = {
            "username": "小团长",
            "user_id": "user_e265cf6cf4cb",
            "metadata": {},
        }
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
             patch("src.db.models.UserDB.get_by_id",
                   return_value={"avatar_url": "http://wx.qlogo.cn/mmhead/x.png", "gender": 2}), \
             patch("src.services.recap.tasks.external_push._resolve_assignee",
                   return_value=("13701602974", "王顾问")):
            mock_mgr.get_session_by_id.return_value = session_row
            ctx = _collect_context(payload)
        assert ctx["avatar"] == "http://wx.qlogo.cn/mmhead/x.png"
        assert ctx["gender"] == 2

    def test_avatar_read_failure_degrades(self):
        payload = _make_payload()
        session_row = {"username": "小团长", "user_id": "user_x", "metadata": {}}
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
             patch("src.db.models.UserDB.get_by_id", side_effect=RuntimeError("db down")), \
             patch("src.services.recap.tasks.external_push._resolve_assignee",
                   return_value=("13701602974", "王顾问")):
            mock_mgr.get_session_by_id.return_value = session_row
            ctx = _collect_context(payload)
        assert ctx["avatar"] is None
        assert ctx["gender"] == 0


# ============== 归属员工手机号解析 ==============


class TestBuildUserMessage:
    def test_contains_avatar_and_gender(self):
        ctx = {**_ctx(), "avatar": "http://wx.qlogo.cn/mmhead/x.png", "gender": 2}
        msg = _build_user_message(_make_payload(), ctx, _summary(),
                                  {"client_token": "tok"}, _meta())
        assert "微信头像：http://wx.qlogo.cn/mmhead/x.png" in msg
        assert "性别：女" in msg

    def test_missing_avatar_placeholder(self):
        msg = _build_user_message(_make_payload(), _ctx(), _summary(),
                                  {"client_token": "tok"}, _meta())
        assert "微信头像：（无，留空）" in msg
        assert "性别：未知" in msg


class TestResolveAssigneePhone:
    def test_found(self):
        kf = {"open_kfid": "wk1", "tenant_user_id": "u1"}
        cfg = {"config_id": "c1", "config": {"kf_account": [kf]}}
        with patch("src.saas.db.channel_config_db.ChannelConfigDB.list_by_tenant",
                   return_value=[cfg]), \
             patch("src.db.models.UserDB.get_by_id",
                   return_value={"phone": "13916323347", "nickname": "王顾问"}):
            assert _resolve_assignee("t", "wk1") == ("13916323347", "王顾问")

    def test_no_binding_returns_none(self):
        kf = {"open_kfid": "wk1"}  # 未绑定员工
        cfg = {"config_id": "c1", "config": {"kf_account": [kf]}}
        with patch("src.saas.db.channel_config_db.ChannelConfigDB.list_by_tenant",
                   return_value=[cfg]):
            assert _resolve_assignee("t", "wk1") == (None, None)

    def test_db_error_returns_none(self):
        with patch("src.saas.db.channel_config_db.ChannelConfigDB.list_by_tenant",
                   side_effect=RuntimeError("db down")):
            assert _resolve_assignee("t", "wk1") == (None, None)


# ============== 租户 AGENT_TOKEN 解析 ==============


class TestGetAgentToken:
    def test_exact_subagent_hit(self):
        with patch("src.db.subagent_env_var.SubagentEnvVarDB.get_vars",
                   return_value=[{"var_name": "AGENT_TOKEN", "var_value": "tok_exact"}]), \
             patch("src.db.subagent_env_var.SubagentEnvVarDB.get_all_vars_for_tenant") as mock_all:
            assert _get_agent_token("tenant_x", "pre-sales") == "tok_exact"
            mock_all.assert_not_called()  # 精确命中不再遍历

    def test_fallback_to_tenant_wide(self):
        with patch("src.db.subagent_env_var.SubagentEnvVarDB.get_vars",
                   return_value=[{"var_name": "OTHER", "var_value": "x"}]), \
             patch("src.db.subagent_env_var.SubagentEnvVarDB.get_all_vars_for_tenant",
                   return_value=[{"var_name": "AGENT_TOKEN", "var_value": "tok_wide"}]):
            assert _get_agent_token("tenant_x", "pre-sales") == "tok_wide"

    def test_empty_value_skipped(self):
        with patch("src.db.subagent_env_var.SubagentEnvVarDB.get_vars",
                   return_value=[{"var_name": "AGENT_TOKEN", "var_value": ""}]), \
             patch("src.db.subagent_env_var.SubagentEnvVarDB.get_all_vars_for_tenant",
                   return_value=[]):
            assert _get_agent_token("tenant_x", "pre-sales") is None

    def test_db_error_returns_none(self):
        with patch("src.db.subagent_env_var.SubagentEnvVarDB.get_vars",
                   side_effect=RuntimeError("db down")):
            assert _get_agent_token("tenant_x", "pre-sales") is None


# ============== LLM 摘要（含计费） ==============


class TestSummarize:
    def _run(self, chat_return):
        payload = _make_payload()
        ctx = {"nickname": "小团长"}
        with patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway.llm_gateway") as mock_gw, \
             patch("src.services.session_record.record_background_llm_usage") as mock_bill:
            mock_settings.external_push.pre_sales.summary_max_tokens = 300
            mock_settings.llm.get_lite_target.return_value = ("qwen", "qwen3.8-flash")
            mock_gw.chat_lite = AsyncMock(return_value=chat_return)
            summary = asyncio.run(_summarize(payload, ctx))
            return summary, mock_gw.chat_lite, mock_bill

    def test_success_and_billing(self):
        chat_return = {
            "content": json.dumps({
                "customer_need": "询问产品价格",
                "reply_summary": "介绍了报价区间",
                "customer_name_hint": "",
            }, ensure_ascii=False),
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model": "qwen3.8-flash",
        }
        summary, mock_gw, mock_bill = self._run(chat_return)
        assert summary["customer_need"] == "询问产品价格"
        assert summary["reply_summary"] == "介绍了报价区间"
        # temperature <= 0.3、max_tokens=300、无工具（billing 前置：LLM 确实被调用）
        kwargs = mock_gw.call_args.kwargs
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 300
        assert kwargs.get("tools") is None
        # 计费：条件 A，source=external_push_{subagent}（summarize 无 ctx 时 unknown），model 透传
        assert mock_bill.called
        assert mock_bill.call_args.kwargs["source"] == "external_push_unknown"
        assert mock_bill.call_args.kwargs["tenant_id"] == "tenant_x"
        assert mock_bill.call_args.kwargs["model"] == "qwen3.8-flash"

    def test_json_parse_failure_falls_back_to_truncated_original(self):
        chat_return = {"content": "这不是JSON", "usage": None}
        summary, _, _ = self._run(chat_return)
        assert summary["customer_need"] == "这个产品多少钱？"
        assert summary["reply_summary"].startswith("您好")

    def test_llm_exception_falls_back(self):
        payload = _make_payload()
        ctx = {"nickname": "小团长"}
        with patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway.llm_gateway") as mock_gw, \
             patch("src.services.session_record.record_background_llm_usage"):
            mock_settings.external_push.pre_sales.summary_max_tokens = 300
            mock_gw.chat_lite = MagicMock(side_effect=RuntimeError("llm down"))
            summary = asyncio.run(_summarize(payload, ctx))
        assert summary["customer_need"] == "这个产品多少钱？"


# ============== 推送循环（_run_push_loop） ==============


def _tool_call(name, args=None, call_id="call_1", raw_arguments=None):
    arguments = raw_arguments if raw_arguments is not None else json.dumps(args or {}, ensure_ascii=False)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _llm_resp(tool_calls=None, content="", model="main-model"):
    resp = {
        "content": content,
        "tool_calls": tool_calls or [],
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
        "model": model,
    }
    return resp


class PushLoopHarness:
    """推送循环测试脚手架：脚本化 chat_lite 响应 + 假执行器（report 写入 holder）"""

    def __init__(self, responses=None, executor_results=None):
        self.responses = list(responses or [])
        self.executor_results = list(executor_results or [])
        self.executor_calls = []
        self.holder = []
        self.mock_gw = None
        self.mock_bill = None
        self._mocks = []

    def _fake_executor(self):
        harness = self
        executor = MagicMock()

        async def fake_execute(name, args, context=None, **kwargs):
            harness.executor_calls.append((name, args))
            if name == "report_push_result":
                harness.holder.append({
                    "success": bool(args.get("success")),
                    "detail": str(args.get("detail") or ""),
                })
                return {"success": True, "recorded": True}
            if harness.executor_results:
                return harness.executor_results.pop(0)
            return {"success": True, "data": {}}

        executor.execute = AsyncMock(side_effect=fake_execute)
        return executor

    def __enter__(self):
        registry = MagicMock()
        registry.get_tool_definitions.return_value = [{"name": "http_api"}]

        def fake_create_runtime(context):
            return registry, self._fake_executor(), self.holder

        self._mocks = [
            patch("src.config.settings.settings"),
            patch("src.llm.gateway.llm_gateway"),
            patch("src.services.session_record.record_background_llm_usage"),
            patch("src.services.recap.tasks.external_push._create_tool_runtime",
                  side_effect=fake_create_runtime),
        ]
        mock_settings, mock_gw, mock_bill, _ = [m.__enter__() for m in self._mocks]
        mock_settings.external_push.pre_sales.max_tool_rounds = 8
        mock_settings.llm.get_lite_target.return_value = ("qwen", "qwen3.8-flash")
        mock_settings.llm.model = "main-model"
        mock_gw.chat_lite = AsyncMock(side_effect=list(self.responses))
        mock_gw.chat_with_tools = AsyncMock()
        self.mock_gw = mock_gw
        self.mock_bill = mock_bill
        return self

    def __exit__(self, *exc):
        for m in self._mocks:
            m.__exit__(*exc)
        return False

    def run(self, payload=None):
        payload = payload or _make_payload()

        async def _run():
            await _run_push_loop(
                payload, _ctx(), _summary(), _doc(), _meta(), "agent_tok", _login()
            )

        return asyncio.run(_run())


class TestPushLoop:
    def test_happy_path_dedup_create_followup_report(self):
        harness = PushLoopHarness(
            responses=[
                _llm_resp([_tool_call("http_api", {"url": "https://e/list", "method": "POST"}, "c1")]),
                _llm_resp([_tool_call("http_api", {"url": "https://e/create", "method": "POST"}, "c2")]),
                _llm_resp([_tool_call("http_api", {"url": "https://e/followup", "method": "POST"}, "c3")]),
                _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "查重+创建+跟进 完成"}, "c4")]),
            ]
        )
        with harness:
            harness.run()

        assert [name for name, _ in harness.executor_calls] == [
            "http_api", "http_api", "http_api", "report_push_result",
        ]
        assert harness.holder[-1]["success"] is True
        # 每轮 lite 模型调用均计费（4 轮），source/model 断言（model 显式解析 lite 模型名）
        assert harness.mock_bill.call_count == 4
        assert harness.mock_bill.call_args.kwargs["source"] == "external_push_pre-sales"
        assert harness.mock_bill.call_args.kwargs["model"] == "qwen3.8-flash"

    def test_billing_with_missing_model_does_not_crash(self):
        """provider 响应不含 model 键也不影响计费——model 由 settings 显式解析"""
        resp = _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "ok"}, "c1")])
        resp.pop("model")
        harness = PushLoopHarness(responses=[resp])
        with harness:
            harness.run()
        assert harness.mock_bill.call_count == 1
        assert harness.mock_bill.call_args.kwargs["model"] == "qwen3.8-flash"

    def test_system_message_first_in_messages(self):
        """推送循环用 chat_lite（system 并入 messages 首位，而非 system_prompt 形参）"""
        resp = _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "ok"}, "c1")])
        harness = PushLoopHarness(responses=[resp])
        with harness:
            harness.run()
        harness.mock_gw.chat_lite.assert_called_once()
        messages = harness.mock_gw.chat_lite.call_args.args[0]
        assert messages[0]["role"] == "system"
        assert "租户接口文档" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        kwargs = harness.mock_gw.chat_lite.call_args.kwargs
        assert kwargs["tools"] == [{"name": "http_api"}]
        assert kwargs["tool_choice"] == "auto"

    def test_chat_lite_value_error_falls_back_to_main_model(self):
        """lite_model 未配置（chat_lite raise ValueError）时回退 chat_with_tools"""
        resp = _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "ok"}, "c1")])
        harness = PushLoopHarness(responses=[resp])
        with harness:
            harness.mock_gw.chat_lite = AsyncMock(side_effect=ValueError("lite_model 解析失败"))
            harness.mock_gw.chat_with_tools = AsyncMock(return_value=resp)
            harness.run()
        harness.mock_gw.chat_with_tools.assert_called_once()
        messages = harness.mock_gw.chat_with_tools.call_args.args[0]
        assert messages[0]["role"] == "system"
        assert harness.mock_bill.call_count == 1
        # 回退主模型链路时，计费 model 归属主模型名
        assert harness.mock_bill.call_args.kwargs["model"] == "main-model"

    def test_round_limit_without_report_raises(self):
        harness = PushLoopHarness(
            responses=[
                _llm_resp([_tool_call("http_api", {"url": "https://e/x"}, f"c{i}")])
                for i in range(8)
            ]
        )
        with harness, pytest.raises(RuntimeError, match="未收到完成报告"):
            harness.run()
        assert harness.mock_gw.chat_lite.call_count == 8

    def test_stop_without_report_raises(self):
        harness = PushLoopHarness(responses=[_llm_resp(tool_calls=None, content="我认为推送已完成")])
        with harness, pytest.raises(RuntimeError, match="未收到完成报告"):
            harness.run()
        assert harness.mock_gw.chat_lite.call_count == 1

    def test_invalid_arguments_returns_error_message_and_continues(self):
        harness = PushLoopHarness(
            responses=[
                # 第 1 轮 arguments 非法 JSON（http_api 不会被真正执行）
                _llm_resp([_tool_call("http_api", call_id="c1", raw_arguments="not-json")]),
                # 第 2 轮恢复正常并报告
                _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "ok"}, "c2")]),
            ]
        )
        with harness:
            harness.run()
        # 非法调用不进执行器，仅第 2 轮的 report 进
        assert [name for name, _ in harness.executor_calls] == ["report_push_result"]
        assert harness.holder[-1]["success"] is True

    def test_code_99_triggers_force_refresh_once_and_injects_new_token(self):
        harness = PushLoopHarness(
            responses=[
                _llm_resp([_tool_call("http_api", {"url": "https://e/list"}, "c1")]),
                _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "ok"}, "c2")]),
            ],
            executor_results=[
                {"success": False, "error": "业务错误 Code=-99: 鉴权失效"},
                {"success": True, "data": {"Code": 0}},
            ],
        )
        with harness, \
             patch("src.services.recap.tasks.external_push._delegate_login",
                   return_value={"client_token": "tok_new", "display_name": "d"}) as mock_login:
            harness.run()

        # 仅强刷一次（首轮 -99 触发），且带 force_refresh=True
        mock_login.assert_called_once()
        assert mock_login.call_args.args[4] is True
        # 第 2 轮的 messages 中注入了含新 token 的 user 消息
        second_call_messages = harness.mock_gw.chat_lite.call_args_list[1].args[0]
        injected = [m for m in second_call_messages
                    if m["role"] == "user" and "tok_new" in m.get("content", "")]
        assert injected, "强刷后的新 token 未注入 messages"

    def test_consecutive_code_99_refreshes_only_once(self):
        harness = PushLoopHarness(
            responses=[
                _llm_resp([_tool_call("http_api", {"url": "https://e/list"}, f"c{i}")])
                for i in range(4)
            ],
            executor_results=[
                {"success": False, "error": "业务错误 Code=-99: 鉴权失效"},
                {"success": False, "error": "业务错误 Code=-99: 仍失效"},
                {"success": False, "error": "业务错误 Code=-1: 其他错误"},
                {"success": False, "error": "业务错误 Code=-1: 其他错误"},
            ],
        )
        with harness, \
             patch("src.services.recap.tasks.external_push._delegate_login",
                   return_value={"client_token": "tok_new"}) as mock_login, \
             pytest.raises(RuntimeError, match="未收到完成报告"):
            harness.run()
        # 连续两次 -99 只强刷一次；连续 3 次失败熔断
        mock_login.assert_called_once()
        assert harness.mock_gw.chat_lite.call_count == 3

    def test_consecutive_failures_circuit_breaks(self):
        harness = PushLoopHarness(
            responses=[
                _llm_resp([_tool_call("http_api", {"url": "https://e/x"}, f"c{i}")])
                for i in range(4)
            ],
            executor_results=[
                {"success": False, "error": "HTTP 500: e1"},
                {"success": False, "error": "HTTP 500: e2"},
                {"success": False, "error": "HTTP 500: e3"},
            ],
        )
        with harness, pytest.raises(RuntimeError, match="未收到完成报告"):
            harness.run()
        # 连续 3 次失败熔断：第 3 轮后即终止（< 8 轮上限）
        assert harness.mock_gw.chat_lite.call_count == 3
        assert len(harness.executor_calls) == 3

    def test_report_tool_terminates_immediately(self):
        harness = PushLoopHarness(
            responses=[
                _llm_resp([_tool_call("http_api", {"url": "https://e/x"}, "c1")]),
                _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "ok"}, "c2")]),
                # 第 3 轮响应不应被消费
                _llm_resp([_tool_call("http_api", {"url": "https://e/never"}, "c3")]),
            ]
        )
        with harness:
            harness.run()
        assert harness.mock_gw.chat_lite.call_count == 2

    def test_report_failure_raises_with_detail(self):
        harness = PushLoopHarness(
            responses=[_llm_resp([_tool_call("report_push_result", {"success": False, "detail": "归属字段缺失，放弃"}, "c1")])]
        )
        with harness, pytest.raises(RuntimeError, match="归属字段缺失"):
            harness.run()

    def test_real_tool_runtime_report_chain(self):
        """不 mock _create_tool_runtime：真实 ToolRegistry/ToolExecutor/PushReportTool 链路。

        PushReportTool 必须满足 BaseTool 协议（to_tool_definition / validate_parameters），
        否则 get_tool_definitions 在循环前即 AttributeError（mock 掉 runtime 时此缺陷不可见）。
        """
        payload = _make_payload()
        resp = _llm_resp([_tool_call("report_push_result", {"success": True, "detail": "真实运行时链路"}, "c1")])

        async def _run():
            with patch("src.config.settings.settings") as mock_settings, \
                 patch("src.llm.gateway.llm_gateway") as mock_gw, \
                 patch("src.services.session_record.record_background_llm_usage"):
                mock_settings.external_push.pre_sales.max_tool_rounds = 8
                mock_gw.chat_lite = AsyncMock(return_value=resp)
                await _run_push_loop(payload, _ctx(), _summary(), _doc(), _meta(), "agent_tok", _login())

        asyncio.run(_run())  # 不抛即通过：工具定义可生成、report 走完真实校验+执行

    def test_real_tool_runtime_definitions_contain_report_tool(self):
        registry, executor, holder = _create_tool_runtime(None)
        names = [d["name"] for d in registry.get_tool_definitions()]
        assert names == ["http_api", "report_push_result"]
        # 报告工具经真实执行器（含 InputModel 校验）可写 holder
        result = asyncio.run(executor.execute(
            "report_push_result", {"success": True, "detail": "d"}, context=None
        ))
        assert result["recorded"] is True
        assert holder == [{"success": True, "detail": "d"}]


# ============== 适配器入口 ==============


class TestAdapter:
    def test_adapter_name_registered(self):
        assert RECAP_TASK_ADAPTERS.get("external_push") is ExternalPushAdapter
        assert ExternalPushAdapter.name == "external_push"

    def test_non_wecom_kf_session_returns_silently(self):
        payload = RecapPayload(
            tenant_id="t", session_id="tenant_t_dingtalk_u1_sa",
            subagent_name="n", round_message_id=1,
            user_content="u", assistant_reply="a",
        )
        with patch("src.services.recap.tasks.external_push._collect_context",
                   return_value=None):
            # 不上抛即通过
            asyncio.run(ExternalPushAdapter.execute(payload))

    def test_doc_loaded_by_ctx_subagent_not_payload_name(self):
        """文档按 ctx（会话解析）的子智能体名读取，而非 payload 显示名"""
        payload = _make_payload()  # subagent_name="售前咨询专员"（显示名）
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=_ctx()), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc",
                   return_value=None) as mock_load, \
             patch("src.llm.gateway.llm_gateway"):
            asyncio.run(ExternalPushAdapter.execute(payload))
        mock_load.assert_called_once_with("tenant_x", "pre-sales")

    def test_missing_subagent_name_aborts(self):
        """ctx 与 payload 均无子智能体名时放弃，不猜测文档路径"""
        payload = RecapPayload(
            tenant_id="tenant_x", session_id="tenant_x_wecom_kf_kf1_ext1_",
            subagent_name="", round_message_id=1,
            user_content="u", assistant_reply="a",
        )
        ctx = {**_ctx(), "subagent": ""}
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=ctx), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc") as mock_load, \
             patch("src.llm.gateway.llm_gateway") as mock_gw:
            asyncio.run(ExternalPushAdapter.execute(payload))
        mock_load.assert_not_called()
        mock_gw.chat_lite.assert_not_called()

    def test_missing_doc_aborts_before_llm(self):
        payload = _make_payload()
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=_ctx()), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc", return_value=None), \
             patch("src.llm.gateway.llm_gateway") as mock_gw:
            asyncio.run(ExternalPushAdapter.execute(payload))
            mock_gw.chat_with_tools.assert_not_called()
            mock_gw.chat_lite.assert_not_called()

    def test_bad_api_meta_aborts_before_llm(self):
        payload = _make_payload()
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=_ctx()), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc",
                   return_value="# 文档\n无 api-meta 块"), \
             patch("src.llm.gateway.llm_gateway") as mock_gw:
            asyncio.run(ExternalPushAdapter.execute(payload))
            mock_gw.chat_with_tools.assert_not_called()
            mock_gw.chat_lite.assert_not_called()

    def test_missing_assignee_phone_aborts_before_llm(self):
        payload = _make_payload()
        ctx = {**_ctx(), "assignee_phone": None}
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=ctx), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc", return_value=_doc()), \
             patch("src.services.recap.tasks.external_push.parse_api_meta", return_value=_meta()), \
             patch("src.llm.gateway.llm_gateway") as mock_gw:
            asyncio.run(ExternalPushAdapter.execute(payload))
            mock_gw.chat_with_tools.assert_not_called()
            mock_gw.chat_lite.assert_not_called()

    def test_missing_agent_token_aborts_before_llm(self):
        payload = _make_payload()
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=_ctx()), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc", return_value=_doc()), \
             patch("src.services.recap.tasks.external_push.parse_api_meta", return_value=_meta()), \
             patch("src.services.recap.tasks.external_push._get_agent_token", return_value=None), \
             patch("src.llm.gateway.llm_gateway") as mock_gw:
            asyncio.run(ExternalPushAdapter.execute(payload))
            mock_gw.chat_with_tools.assert_not_called()
            mock_gw.chat_lite.assert_not_called()

    def test_login_failure_raises(self):
        payload = _make_payload()
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=_ctx()), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc", return_value=_doc()), \
             patch("src.services.recap.tasks.external_push.parse_api_meta", return_value=_meta()), \
             patch("src.services.recap.tasks.external_push._get_agent_token", return_value="agent_tok"), \
             patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway.llm_gateway") as mock_gw, \
             patch("src.services.recap.tasks.external_push._delegate_login", return_value=None):
            mock_settings.external_push.pre_sales.summary_max_tokens = 300
            # 摘要 LLM 异常 -> 降级截断原文，流程继续到登录
            mock_gw.chat_lite = AsyncMock(side_effect=RuntimeError("llm down"))
            with pytest.raises(RuntimeError, match="委托登录失败"):
                asyncio.run(ExternalPushAdapter.execute(payload))

    def test_execute_happy_path_calls_loop(self):
        payload = _make_payload()
        with patch("src.services.recap.tasks.external_push._collect_context", return_value=_ctx()), \
             patch("src.services.recap.tasks.external_push._load_tenant_doc", return_value=_doc()), \
             patch("src.services.recap.tasks.external_push.parse_api_meta", return_value=_meta()), \
             patch("src.services.recap.tasks.external_push._get_agent_token", return_value="agent_tok"), \
             patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway.llm_gateway") as mock_gw, \
             patch("src.services.session_record.record_background_llm_usage"), \
             patch("src.services.recap.tasks.external_push._delegate_login",
                   return_value=_login()) as mock_login, \
             patch("src.services.recap.tasks.external_push._run_push_loop",
                   new=AsyncMock()) as mock_loop:
            mock_settings.external_push.pre_sales.summary_max_tokens = 300
            mock_gw.chat_lite = AsyncMock(return_value={
                "content": json.dumps({"customer_need": "n", "reply_summary": "r", "customer_name_hint": ""}),
                "usage": None, "model": "lite",
            })
            asyncio.run(ExternalPushAdapter.execute(payload))
        # 登录用的 login_url 来自 api-meta
        assert mock_login.call_args.args[3] == "https://erp.example.com/api/login"
        assert mock_loop.await_count == 1
