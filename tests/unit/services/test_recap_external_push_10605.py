#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""external_push_10605 适配器单元测试（docs/subagent/pre-sales/external-push-code-hook-design.md §5）"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 先触发 src.llm 包导入（其模块顶层会以真实 settings 构造 llm_gateway 单例），
# 避免在测试内 patch settings 时首次导入导致构造失败
import src.llm.gateway  # noqa: F401,E402

from src.services.recap.runner import RecapPayload
from src.services.recap.tasks.external_push_10605 import (
    ExternalPush10605Adapter,
    _customer_name,
    _execute_with_retry,
    _extract_json_object,
    _post_10605,
    _push_once,
    _resolve_assignee_phone,
    _summarize,
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


def _resp(code, response=None, error=None):
    return {"Code": code, "Error": error or "", "Response": response}


# ============== session_id 解析 ==============


class TestParseWecomKfSession:
    def test_valid_session(self):
        parsed = parse_wecom_kf_session(
            "tenant_9b9fb62aff5e_wecom_kf_wkS6oOTAAAqAvd_wmS6oOTAAAhVHj3_umWg_pre-sales"
        )
        assert parsed == {
            "open_kfid": "wkS6oOTAAAqAvd",
            "external_userid": "wmS6oOTAAAhVHj3_umWg",
        }

    def test_non_wecom_kf_returns_none(self):
        assert parse_wecom_kf_session("tenant_x_dingtalk_user1_sa") is None
        assert parse_wecom_kf_session("") is None
        assert parse_wecom_kf_session(None) is None

    def test_malformed_returns_none(self):
        # 标记后只有一段，无 external_userid/subagent 结构
        assert parse_wecom_kf_session("tenant_x_wecom_kf_onlyone") is None


# ============== 客户名称兜底 ==============


class TestCustomerName:
    def test_priority_hint_over_nickname_over_id(self):
        ctx = {"nickname": "小团长", "external_userid": "wmS6oOTAAAhVHj3_umWg"}
        assert _customer_name({"customer_name_hint": "张三"}, ctx) == "张三"
        assert _customer_name({"customer_name_hint": ""}, ctx) == "小团长"
        assert _customer_name({"customer_name_hint": ""}, {"nickname": "", "external_userid": "wmS6oOTAAAhVHj3_umWg"}) == "wmS6oOTA"


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


# ============== 上下文采集 ==============


class TestCollectContext:
    def test_non_wecom_kf_session_aborts(self, caplog):
        from src.services.recap.tasks.external_push_10605 import _collect_context

        payload = RecapPayload(
            tenant_id="t", session_id="tenant_t_dingtalk_u1_sa",
            subagent_name="n", round_message_id=1,
            user_content="u", assistant_reply="a",
        )
        assert _collect_context(payload) is None

    def test_full_context(self):
        from src.services.recap.tasks.external_push_10605 import _collect_context

        payload = _make_payload()
        session_row = {
            "username": "小团长",
            "metadata": {"lead_capture": {"lead_id": "lead_lc_abc", "stage": "captured"}},
        }
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id", return_value={"phone": "13916323347"}), \
             patch("src.services.recap.tasks.external_push_10605._resolve_assignee_phone",
                   return_value="13701602974"):
            mock_mgr.get_session_by_id.return_value = session_row
            ctx = _collect_context(payload)

        assert ctx == {
            "open_kfid": "wkS6oOTAAAqAvd",
            "external_userid": "wmS6oOTAAAhVHj3_umWg",
            "nickname": "小团长",
            "lead_phone": "13916323347",
            "assignee_phone": "13701602974",
        }

    def test_lead_read_failure_degrades(self):
        from src.services.recap.tasks.external_push_10605 import _collect_context

        payload = _make_payload()
        session_row = {
            "username": "小团长",
            "metadata": {"lead_capture": {"lead_id": "lead_lc_abc"}},
        }
        with patch("src.channels.session.channel_session_manager") as mock_mgr, \
             patch("src.saas.db.lead_capture_db.LeadCaptureDB.get_by_id", side_effect=RuntimeError("db down")), \
             patch("src.services.recap.tasks.external_push_10605._resolve_assignee_phone",
                   return_value="13701602974"):
            mock_mgr.get_session_by_id.return_value = session_row
            ctx = _collect_context(payload)
        assert ctx["lead_phone"] is None


# ============== 归属员工手机号解析 ==============


class TestResolveAssigneePhone:
    def test_found(self):
        kf = {"open_kfid": "wk1", "tenant_user_id": "u1"}
        cfg = {"config_id": "c1", "config": {"kf_account": [kf]}}
        with patch("src.saas.db.channel_config_db.ChannelConfigDB.list_by_tenant",
                   return_value=[cfg]), \
             patch("src.db.models.UserDB.get_by_id", return_value={"phone": "13916323347"}):
            assert _resolve_assignee_phone("t", "wk1") == "13916323347"

    def test_no_binding_returns_none(self):
        kf = {"open_kfid": "wk1"}  # 未绑定员工
        cfg = {"config_id": "c1", "config": {"kf_account": [kf]}}
        with patch("src.saas.db.channel_config_db.ChannelConfigDB.list_by_tenant",
                   return_value=[cfg]):
            assert _resolve_assignee_phone("t", "wk1") is None

    def test_db_error_returns_none(self):
        with patch("src.saas.db.channel_config_db.ChannelConfigDB.list_by_tenant",
                   side_effect=RuntimeError("db down")):
            assert _resolve_assignee_phone("t", "wk1") is None


# ============== LLM 摘要（含计费） ==============


class TestSummarize:
    def _run(self, chat_return):
        payload = _make_payload()
        ctx = {"nickname": "小团长"}
        with patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway.llm_gateway") as mock_gw, \
             patch("src.services.session_record.record_background_llm_usage") as mock_bill:
            mock_settings.external_push.pre_sales.summary_max_tokens = 300
            mock_gw.chat = AsyncMock(return_value=chat_return)
            summary = asyncio.run(_summarize(payload, ctx))
            return summary, mock_gw.chat, mock_bill

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
        # 计费：条件 A，source=pre_sales_push，model 透传（独立落库分支算准单价）
        assert mock_bill.called
        assert mock_bill.call_args.kwargs["source"] == "pre_sales_push"
        assert mock_bill.call_args.kwargs["tenant_id"] == "tenant_x"
        assert mock_bill.call_args.kwargs["model"] == "qwen3.8-flash"

    def test_json_parse_failure_falls_back_to_truncated_original(self):
        chat_return = {"content": "这不是JSON", "usage": None}
        summary, _, mock_bill = self._run(chat_return)
        assert summary["customer_need"] == "这个产品多少钱？"
        assert summary["reply_summary"].startswith("您好")

    def test_llm_exception_falls_back(self):
        payload = _make_payload()
        ctx = {"nickname": "小团长"}
        with patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway.llm_gateway") as mock_gw, \
             patch("src.services.session_record.record_background_llm_usage"):
            mock_settings.external_push.pre_sales.summary_max_tokens = 300
            mock_gw.chat = MagicMock(side_effect=RuntimeError("llm down"))
            summary = asyncio.run(_summarize(payload, ctx))
        assert summary["customer_need"] == "这个产品多少钱？"


# ============== 查重/建改分流 + 跟进记录（_push_once） ==============


class TestPushOnce:
    def _run_push_once(self, post_results, existing_customer, ctx, summary, login):
        """post_results: LISTING_URL / DATA_UPDATE_URL 依次消费的响应列表"""
        payload = _make_payload()
        calls = []

        async def fake_post(url, token, body):
            calls.append((url, body))
            return post_results.pop(0)

        with patch("src.services.recap.tasks.external_push_10605._post_10605_async", fake_post):
            code, _ = asyncio.run(_push_once(payload, ctx, login, summary))
        return code, calls

    def _ctx(self):
        return {
            "open_kfid": "wk1",
            "external_userid": "wmS6oOTAAAhVHj3_umWg",
            "nickname": "小团长",
            "lead_phone": "13916323347",
            "assignee_phone": "13701602974",
        }

    def _summary(self):
        return {"customer_need": "询问价格", "reply_summary": "介绍了报价", "customer_name_hint": ""}

    def _login(self):
        return {"client_token": "tok", "display_name": "客服小王", "record_id": 4}

    def test_dedup_miss_creates_customer_then_follow_up(self):
        results = [
            _resp(0, {"data": [], "total": 0}),          # 查重未命中
            _resp(0, 10),                                  # 创建客户
            _resp(0, 15),                                  # 创建跟进记录
        ]
        code, calls = self._run_push_once(results, None, self._ctx(), self._summary(), self._login())
        assert code == 0
        assert len(calls) == 3
        # 查重请求：unionid 精确过滤
        list_body = calls[0][1]
        assert list_body["module"] == "kehuxinxi"
        assert list_body["filters"][0]["exact"] is True
        assert list_body["filters"][0]["value"] == ["wmS6oOTAAAhVHj3_umWg"]
        # 创建客户：查重命中前未创建（仅一次 insert），unionid 写入 external_userid
        create_body = calls[1][1]
        assert create_body["tables"][0]["method"] == "insert"
        assert create_body["tables"][0]["data"][0]["unionid"] == "wmS6oOTAAAhVHj3_umWg"
        assert create_body["tables"][0]["data"][0]["xingming"] == "小团长"
        assert create_body["tables"][0]["data"][0]["lianxidianhua"] == "13916323347"
        # 跟进记录：固定动作 + 摘要字段
        follow_body = calls[2][1]
        assert follow_body["module"] == "lianxijilu"
        data = follow_body["tables"][0]["data"][0]
        assert data["genjindongzuo"] == "微信咨询（AI）"
        assert data["neirong"] == "介绍了报价"
        assert data["kehuhuifuneirong"] == "询问价格"
        assert data["genjinren"] == "客服小王"

    def test_dedup_hit_never_creates(self):
        existing = {
            "id": 8, "xingming": "小团长", "lianxidianhua": "13916323347",
            "lianxiren": "", "weixinhao": "", "suoshuhangye": "",
        }
        results = [
            _resp(0, {"data": [existing], "total": 1}),   # 查重命中
            _resp(0, []),                                   # 创建跟进记录（无客户写入）
        ]
        code, calls = self._run_push_once(results, existing, self._ctx(), self._summary(), self._login())
        assert code == 0
        # 只有 2 次 HTTP：查重 + 跟进记录，绝无客户创建（重复客户根因）
        assert len(calls) == 2
        assert not any(
            b.get("module") == "kehuxinxi" and b.get("tables")
            and b["tables"][0]["method"] == "insert"
            for _, b in calls
        )

    def test_dedup_hit_new_phone_updates_with_update_time(self):
        existing = {
            "id": 8, "xingming": "小团长", "lianxidianhua": "",
            "lianxiren": "", "weixinhao": "", "suoshuhangye": "",
        }
        results = [
            _resp(0, {"data": [existing], "total": 1}),   # 查重命中（旧手机号为空）
            _resp(0, []),                                   # 修改客户手机号
            _resp(0, 16),                                   # 创建跟进记录
        ]
        code, calls = self._run_push_once(results, existing, self._ctx(), self._summary(), self._login())
        assert code == 0
        update_body = calls[1][1]
        assert update_body["tables"][0]["method"] == "update"
        data = update_body["tables"][0]["data"][0]
        assert data["lianxidianhua"] == "13916323347"
        assert data["update_time"]  # 防 Code:2 必带
        assert data["id"] == 8

    def test_dedup_hit_same_phone_skips_update(self):
        existing = {
            "id": 8, "xingming": "小团长", "lianxidianhua": "13916323347",
            "lianxiren": "", "weixinhao": "", "suoshuhangye": "",
        }
        results = [
            _resp(0, {"data": [existing], "total": 1}),
            _resp(0, 17),
        ]
        code, calls = self._run_push_once(results, existing, self._ctx(), self._summary(), self._login())
        assert code == 0
        assert len(calls) == 2  # 查重 + 跟进记录，无客户修改

    def test_dedup_query_error_propagates_code(self):
        results = [_resp(-1, error="数据库异常")]
        code, _ = self._run_push_once(results, None, self._ctx(), self._summary(), self._login())
        assert code == -1


# ============== 重试逻辑（_execute_with_retry） ==============


class TestExecuteWithRetry:
    def _ctx(self):
        return {
            "open_kfid": "wk1",
            "external_userid": "wmS6oOTAAAhVHj3_umWg",
            "nickname": "小团长",
            "lead_phone": None,
            "assignee_phone": "13701602974",
        }

    def test_missing_assignee_phone_aborts(self):
        payload = _make_payload()
        ctx = {**self._ctx(), "assignee_phone": None}
        with patch("src.services.recap.tasks.external_push_10605._delegate_login") as mock_login:
            asyncio.run(_execute_with_retry(payload, ctx, {}))
            mock_login.assert_not_called()  # 未发起登录即放弃

    def test_code_99_triggers_force_refresh_and_retry(self):
        payload = _make_payload()
        summary = {"customer_need": "n", "reply_summary": "r", "customer_name_hint": ""}
        first_login = {"client_token": "tok1", "display_name": "d"}
        refreshed_login = {"client_token": "tok2", "display_name": "d"}
        login_calls = []

        def fake_login(tenant_id, mobile, force_refresh=False):
            login_calls.append(force_refresh)
            return refreshed_login if force_refresh else first_login

        push_codes = [-99, 0]

        async def fake_push(payload, ctx, login, summary):
            return push_codes.pop(0), None

        with patch("src.services.recap.tasks.external_push_10605._delegate_login", fake_login), \
             patch("src.services.recap.tasks.external_push_10605._push_once", fake_push):
            asyncio.run(_execute_with_retry(payload, self._ctx(), summary))

        assert login_calls == [False, True]  # 第二次带 force_refresh

    def test_retry_still_fails_raises(self):
        payload = _make_payload()
        summary = {"customer_need": "n", "reply_summary": "r", "customer_name_hint": ""}

        def fake_login(tenant_id, mobile, force_refresh=False):
            return {"client_token": "tok", "display_name": "d"}

        async def fake_push(payload, ctx, login, summary):
            return -1, None  # 两次都业务失败

        with patch("src.services.recap.tasks.external_push_10605._delegate_login", fake_login), \
             patch("src.services.recap.tasks.external_push_10605._push_once", fake_push):
            with pytest.raises(RuntimeError):
                asyncio.run(_execute_with_retry(payload, self._ctx(), summary))

    def test_success_no_retry(self):
        payload = _make_payload()
        summary = {"customer_need": "n", "reply_summary": "r", "customer_name_hint": ""}
        login_calls = []

        def fake_login(tenant_id, mobile, force_refresh=False):
            login_calls.append(force_refresh)
            return {"client_token": "tok", "display_name": "d"}

        async def fake_push(payload, ctx, login, summary):
            return 0, None

        with patch("src.services.recap.tasks.external_push_10605._delegate_login", fake_login), \
             patch("src.services.recap.tasks.external_push_10605._push_once", fake_push):
            asyncio.run(_execute_with_retry(payload, self._ctx(), summary))
        assert login_calls == [False]


# ============== 适配器入口 ==============


class TestAdapter:
    def test_adapter_name_registered(self):
        from src.services.recap.tasks import RECAP_TASK_ADAPTERS

        assert RECAP_TASK_ADAPTERS.get("external_push") is ExternalPush10605Adapter
        assert ExternalPush10605Adapter.name == "external_push"

    def test_non_wecom_kf_session_returns_silently(self):
        payload = RecapPayload(
            tenant_id="t", session_id="tenant_t_dingtalk_u1_sa",
            subagent_name="n", round_message_id=1,
            user_content="u", assistant_reply="a",
        )
        with patch("src.services.recap.tasks.external_push_10605._collect_context",
                   return_value=None):
            # 不上抛即通过
            asyncio.run(ExternalPush10605Adapter.execute(payload))


# ============== 同步 HTTP 封装（双 Token header） ==============


class TestPost10605:
    def test_headers_carry_both_tokens(self):
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"Code": 0, "Response": {}}).encode("utf-8")

        def fake_urlopen(req, timeout):
            captured["headers"] = dict(req.headers)
            captured["timeout"] = timeout
            return FakeResp()

        with patch("src.services.recap.tasks.external_push_10605.urllib.request.urlopen", fake_urlopen), \
             patch.dict("os.environ", {"AGENT_TOKEN": "agent_tok"}):
            resp = _post_10605("https://x/api", "client_tok", {"a": 1})

        assert resp["Code"] == 0
        # urllib 会把 header 名 capitalize（Api-authorize-token），按不区分大小写断言
        headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
        assert headers_lower["api-authorize-token"] == "agent_tok"
        assert headers_lower["client-authorize-token"] == "client_tok"
        assert captured["timeout"] == 15
