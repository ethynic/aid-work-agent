"""会话聊天记录解析单测（POST /api/client/v1/session-history，M10a）。

全 mock（LLM 网关 / 台账 DB），不起服务、不连真实 GLM。覆盖：
- 正常两页合并去重（归一化键：NFKC 全角转半角 + 去空白 + 小写）
- 页间无重叠 → 保留全部 + warning="page_gap"
- 单页失败部分成功（failed_pages）/ 全部失败 502 MODEL_UNAVAILABLE
- 计费换算：token 成本(元) ×100 积分、最低 1 积分保护、402 余额不足
- record_llm_usage credit_cost_override 不走 ×10 / calculate_credit_cost 标准链路
- 鉴权（无/坏 token 401）
- 参数校验（空数组 / 超 10 张 / 超 5MB / 非 base64）
- 输出解析容错（```json 围栏 + 前后杂文字 / 单条非法跳过）
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.api import client_routes
from src.api.client_routes import SessionHistoryRequest, parse_session_history

pytestmark = [pytest.mark.unit, pytest.mark.api]


# ============== 测试工厂 ==============

def _binding(balance: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        binding_id="cb_sh",
        tenant_id="tenant_sh",
        client_name="wecom-cli",
        tenant={"credit_balance": balance},
    )


def _img(seed: str = "p") -> str:
    return base64.b64encode(f"png-bytes-{seed}".encode()).decode()


def _page_llm_response(messages: list, prompt_tokens: int = 1000, completion_tokens: int = 100) -> dict:
    """模拟 GLM-5.3-Flash 单页响应：```json 围栏包裹（走解析容错路径）。"""
    return {
        "content": "```json\n" + json.dumps(messages, ensure_ascii=False) + "\n```",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _fake_gateway(page_payloads: list) -> tuple:
    """按调用次序返回每页 payload（dict=成功 / Exception=该页失败），记录调用参数。"""
    calls: list[dict] = []

    async def _chat_direct(provider, model, messages=None, temperature=None, **kwargs):
        i = len(calls)
        calls.append({"provider": provider, "model": model, "messages": messages,
                      "temperature": temperature})
        payload = page_payloads[i]
        if isinstance(payload, Exception):
            raise payload
        return payload

    return SimpleNamespace(chat_direct=_chat_direct), calls


def _usage_db_mock() -> MagicMock:
    """mock ClientUsageLogDB：record_llm_usage 原样返回 credit_cost_override。"""
    db = MagicMock()

    def _record(**kwargs):
        credits = float(kwargs.get("credit_cost_override") or 0.0)
        return {"raw_credit_cost": credits, "credit_cost": credits, "balance_after": 99.0}

    db.record_llm_usage.side_effect = _record
    return db


def _run(req, binding, payloads):
    gateway, calls = _fake_gateway(payloads)
    usage_db = _usage_db_mock()
    with patch.object(client_routes, "llm_gateway", gateway), \
         patch.object(client_routes, "ClientUsageLogDB", usage_db):
        resp = asyncio.run(parse_session_history(req, binding=binding))
    return resp, usage_db, calls


PAGE1 = [
    {"time": "14:02", "side": "peer", "kind": "text", "text": "你好，请问报价单能发我吗"},
    {"side": "self", "kind": "text", "text": "在的\n稍等"},
    {"side": "timeline", "kind": "timeline", "text": "14:05"},
    {"side": "peer", "kind": "image", "text": "[图片] 报价单"},
]
# 页 2 开头与页 1 尾部重叠，但写法带全角/空白/大小写差异（验证归一化键比对）
PAGE2_WITH_OVERLAP = [
    {"time": "14:02", "side": "peer", "kind": "text", "text": "你好，请问报价单能发我吗"},
    {"side": "SELF", "kind": "text", "text": "在 的 稍 等"},
    {"side": "timeline", "kind": "timeline", "text": "１４:０５"},
    {"side": "peer", "kind": "image", "text": "[图片]　报价单"},
    {"side": "self", "kind": "file", "text": "[文件] 合同.pdf"},
]


# ============== 合并去重 / 部分失败 ==============

class TestSessionHistoryMergeAndFailure:

    def test_two_pages_merged_with_overlap_dedup(self):
        """两页重叠去重：归一化键（全角→半角/去空白/小写）比对，重叠段只保留一份"""
        req = SessionHistoryRequest(images=[_img("1"), _img("2")], client="wecom")
        resp, usage_db, _ = _run(req, _binding(), [
            _page_llm_response(PAGE1), _page_llm_response(PAGE2_WITH_OVERLAP),
        ])

        assert resp["pages"] == 2
        assert "failed_pages" not in resp
        assert "warning" not in resp
        # 4 条重叠（含 timeline）去重后，仅追加页 2 的新消息
        assert len(resp["messages"]) == 5
        assert resp["messages"][0] == {"time": "14:02", "side": "peer", "kind": "text",
                                       "text": "你好，请问报价单能发我吗"}
        assert resp["messages"][-1] == {"side": "self", "kind": "file", "text": "[文件] 合同.pdf"}
        # 计费落账：stage=session_history，detail 记录页数事实
        kwargs = usage_db.record_llm_usage.call_args.kwargs
        assert kwargs["stage"] == "session_history"
        assert kwargs["detail"]["pages_total"] == 2
        assert kwargs["detail"]["pages_ok"] == 2
        usage_db.record_non_llm_usage.assert_not_called()

    def test_page_gap_keeps_all_and_warns(self):
        """页间无重叠：保留全部 + warning='page_gap'（翻页过快漏内容提示）"""
        page2_no_overlap = [
            {"side": "peer", "kind": "text", "text": "另一段全新对话"},
            {"side": "self", "kind": "text", "text": "好的"},
        ]
        req = SessionHistoryRequest(images=[_img("1"), _img("2")], client="weixin")
        resp, _, _ = _run(req, _binding(), [
            _page_llm_response(PAGE1), _page_llm_response(page2_no_overlap),
        ])

        assert resp["warning"] == "page_gap"
        assert len(resp["messages"]) == len(PAGE1) + 2

    def test_single_page_failure_partial_success(self):
        """单页失败：跳过该页返回其余页，failed_pages=[1]，仍正常计费"""
        req = SessionHistoryRequest(images=[_img("1"), _img("2")], client="wecom")
        resp, usage_db, _ = _run(req, _binding(), [
            _page_llm_response(PAGE1), RuntimeError("provider down"),
        ])

        assert resp["pages"] == 1
        assert resp["failed_pages"] == [1]
        assert resp["messages"] == PAGE1
        # 部分失败落 0 积分观测行
        fail_kwargs = usage_db.record_non_llm_usage.call_args.kwargs
        assert fail_kwargs["stage"] == "session_history"
        assert fail_kwargs["status"] == "partial"
        assert fail_kwargs["detail"]["failed_pages"] == [1]

    def test_all_pages_failed_returns_502(self):
        """全部失败：502 MODEL_UNAVAILABLE，不计费不落成功账"""
        from fastapi import HTTPException

        req = SessionHistoryRequest(images=[_img("1"), _img("2")], client="wecom")
        gateway, calls = _fake_gateway([RuntimeError("boom"), TimeoutError()])
        usage_db = _usage_db_mock()
        with patch.object(client_routes, "llm_gateway", gateway), \
             patch.object(client_routes, "ClientUsageLogDB", usage_db), \
             pytest.raises(HTTPException) as ei:
            asyncio.run(parse_session_history(req, binding=_binding()))
        assert ei.value.status_code == 502
        assert "MODEL_UNAVAILABLE" in ei.value.detail
        usage_db.record_llm_usage.assert_not_called()
        assert usage_db.record_non_llm_usage.call_args.kwargs["status"] == "failed"

    def test_gateway_receives_data_url_and_pinned_model(self):
        """网关调用：zhipu/GLM-5.3-Flash 锁定通道 + image_url base64 data URL 消息格式"""
        req = SessionHistoryRequest(images=[_img("x")], client="wecom")
        _, _, calls = _run(req, _binding(), [_page_llm_response(PAGE1)])

        assert len(calls) == 1
        assert calls[0]["provider"] == "zhipu"
        assert calls[0]["model"] == "GLM-5.3-Flash"
        content = calls[0]["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == f"data:image/png;base64,{req.images[0]}"


# ============== 计费 ==============

class TestSessionHistoryBilling:
    """积分 = ceil((prompt×0.8 + completion×2.8)/1e6 × 100 × 100) / 100，最低 1 积分。"""

    def test_billing_formula_cost_times_100(self):
        """两页 usage 汇总后按配置单价计费：(1M×0.8 + 0.5M×2.8)/1e6 = 2.2 元 → 220 积分"""
        req = SessionHistoryRequest(images=[_img("1"), _img("2")], client="wecom")
        resp, usage_db, _ = _run(req, _binding(), [
            _page_llm_response(PAGE1, prompt_tokens=500_000, completion_tokens=250_000),
            _page_llm_response(PAGE2_WITH_OVERLAP, prompt_tokens=500_000, completion_tokens=250_000),
        ])

        assert resp["model_usage"] == {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
        assert resp["billing"]["credits_charged"] == 220.0
        kwargs = usage_db.record_llm_usage.call_args.kwargs
        assert kwargs["credit_cost_override"] == 220.0
        assert kwargs["model"] == "GLM-5.3-Flash"
        assert kwargs["provider"] == "zhipu"
        assert kwargs["usage"]["prompt_tokens"] == 1_000_000

    def test_billing_minimum_one_credit(self):
        """最低计费保护：0.00108 元 ×100 = 0.108 → 不足 1 积分按 1 积分收"""
        req = SessionHistoryRequest(images=[_img("1")], client="wecom")
        resp, _, _ = _run(req, _binding(), [
            _page_llm_response(PAGE1, prompt_tokens=1000, completion_tokens=100),
        ])
        assert resp["billing"]["credits_charged"] == 1.0

    def test_insufficient_credit_blocked_before_llm_call(self):
        """余额 ≤0：402 预检阻断，不发起 LLM 调用、不落账"""
        from fastapi import HTTPException

        req = SessionHistoryRequest(images=[_img("1")], client="wecom")
        gateway, calls = _fake_gateway([_page_llm_response(PAGE1)])
        usage_db = _usage_db_mock()
        with patch.object(client_routes, "llm_gateway", gateway), \
             patch.object(client_routes, "ClientUsageLogDB", usage_db), \
             pytest.raises(HTTPException) as ei:
            asyncio.run(parse_session_history(req, binding=_binding(balance=0.0)))
        assert ei.value.status_code == 402
        assert calls == []
        usage_db.record_llm_usage.assert_not_called()


class TestRecordLlmUsageCreditOverride:
    """record_llm_usage 参数化：credit_cost_override 时绕过 ×10 与 calculate_credit_cost。"""

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_credit_override_skips_x10_and_std_pipeline(self, mock_conn, mock_calc):
        """override=220 → credit/raw 同值 220（而非 220×10），calculate_credit_cost 不被调用"""
        from src.db.client_binding_db import ClientUsageLogDB

        cursor = MagicMock()
        cursor.fetchone.return_value = {"credit_balance": 98.0}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="t", binding_id="b", model="GLM-5.3-Flash", provider="zhipu",
            usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
            stage="session_history", credit_cost_override=220.0,
            detail={"pages_ok": 2},
        )

        mock_calc.assert_not_called()
        assert result["credit_cost"] == 220.0
        assert result["raw_credit_cost"] == 220.0
        # detail 列写入 INSERT（第 1 条 execute）参数末位；第 2 条是余额 UPDATE
        insert_params = cursor.execute.call_args_list[0][0][1]
        assert '"pages_ok": 2' in insert_params[-1]

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_credit_override_float_ceil_no_overcharge(self, mock_conn, mock_calc):
        """回归：override=2.2 时浮点 math.ceil(2.2*100) 得 221 → 多收 2.21；
        Decimal 取整后精确 2.20（约 46% 的两位小数值踩此坑，如 2.2/1.1/108.1）"""
        from src.db.client_binding_db import ClientUsageLogDB

        cursor = MagicMock()
        cursor.fetchone.return_value = {"credit_balance": 98.0}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="t", binding_id="b", model="GLM-5.3-Flash", provider="zhipu",
            usage={"prompt_tokens": 100}, stage="session_history", credit_cost_override=2.2,
        )
        assert result["credit_cost"] == 2.2

    @patch("src.db.client_binding_db.calculate_credit_cost")
    @patch("src.db.client_binding_db.get_db_connection")
    def test_no_override_keeps_x10_behavior(self, mock_conn, mock_calc):
        """不传 override：维持既有 ×10 链路（防回归）"""
        from src.db.client_binding_db import ClientUsageLogDB

        mock_calc.return_value = 2.15
        cursor = MagicMock()
        cursor.fetchone.return_value = {"credit_balance": 78.5}
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        result = ClientUsageLogDB.record_llm_usage(
            tenant_id="t", binding_id="b", model="m", provider="p",
            usage={"prompt_tokens": 100},
        )
        mock_calc.assert_called_once()
        assert result["credit_cost"] == 21.5


# ============== 鉴权与参数校验 ==============

class TestSessionHistoryAuthAndValidation:

    def test_missing_or_bad_token_unauthorized(self):
        """无 Authorization 头 / token 无效绑定 → 401 UNAUTHORIZED"""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            client_routes._require_binding(None)
        assert ei.value.status_code == 401

        with patch.object(client_routes, "verify_client_token", return_value=None), \
             pytest.raises(HTTPException) as ei2:
            client_routes._require_binding("Bearer bad-token")
        assert ei2.value.status_code == 401

    def test_empty_images_rejected(self):
        with pytest.raises(ValidationError):
            SessionHistoryRequest(images=[], client="wecom")

    def test_over_ten_images_rejected(self):
        with pytest.raises(ValidationError):
            SessionHistoryRequest(images=[_img(str(i)) for i in range(11)], client="wecom")

    def test_image_over_5mb_rejected(self):
        oversized = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()
        with pytest.raises(ValidationError) as ei:
            SessionHistoryRequest(images=[oversized], client="wecom")
        assert "IMAGE_TOO_LARGE" in str(ei.value)

    def test_invalid_base64_rejected(self):
        with pytest.raises(ValidationError) as ei:
            SessionHistoryRequest(images=["@@@not-base64@@@"], client="wecom")
        assert "INVALID_BASE64" in str(ei.value)

    def test_empty_image_string_rejected(self):
        with pytest.raises(ValidationError) as ei:
            SessionHistoryRequest(images=["   "], client="wecom")
        assert "INVALID_BASE64" in str(ei.value)

    def test_data_url_prefix_tolerated(self):
        """客户端误带 data URL 前缀：剥离后按纯 base64 接受"""
        req = SessionHistoryRequest(
            images=["data:image/png;base64," + _img("d")], client="wecom",
        )
        assert req.images == [_img("d")]

    def test_invalid_client_literal_rejected(self):
        with pytest.raises(ValidationError):
            SessionHistoryRequest(images=[_img("1")], client="dingtalk")


# ============== 输出解析容错（纯函数） ==============

class TestSessionHistoryParseHelpers:

    def test_extract_json_array_with_fence_and_junk(self):
        content = '好的，以下是转写结果：\n```json\n[{"side":"peer","kind":"text","text":"你好"}]\n```\n以上。'
        arr = client_routes._extract_json_array(content)
        assert arr == [{"side": "peer", "kind": "text", "text": "你好"}]

    def test_extract_json_array_without_array_raises(self):
        with pytest.raises(ValueError):
            client_routes._extract_json_array("抱歉，我无法完成")

    def test_sanitize_skips_invalid_items_and_normalizes_timeline(self):
        items = [
            {"side": "left", "kind": "text", "text": "x"},      # side 越界 → 跳过
            {"side": "peer", "kind": "voice", "text": "x"},     # kind 越界 → 跳过
            {"side": "peer", "kind": "text", "text": 123},      # text 非 str → 跳过
            "not-a-dict",                                        # 非 dict → 跳过
            {"side": "peer", "kind": "text", "text": "ok", "time": " 14:02 "},
            {"side": "timeline", "kind": "text", "text": "昨天 20:00", "time": "ignored"},
        ]
        msgs = client_routes._sanitize_page_messages(items)
        assert msgs == [
            {"time": "14:02", "side": "peer", "kind": "text", "text": "ok"},
            {"side": "timeline", "kind": "timeline", "text": "昨天 20:00"},
        ]

    def test_unparseable_page_output_counts_as_failed_page(self):
        """整页输出非 JSON 数组：按失败页处理（部分成功语义）"""
        req = SessionHistoryRequest(images=[_img("1"), _img("2")], client="wecom")
        resp, _, _ = _run(req, _binding(), [
            {"content": "模型跑偏了，没有数组", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            _page_llm_response(PAGE1),
        ])
        assert resp["failed_pages"] == [0]
        assert resp["pages"] == 1
        assert resp["messages"] == PAGE1

    def test_merge_normalization_key_variants(self):
        """归一化键：NFKC 全角转半角 + 去空白 + 小写后相同即判重叠"""
        page_a = [{"side": "peer", "kind": "text", "text": "价格是１２００元"}]
        page_b = [
            {"side": "PEER", "kind": "text", "text": "价格是 1200 元"},  # 归一化后同键
            {"side": "self", "kind": "text", "text": "确认"},
        ]
        merged, gap = client_routes._merge_session_pages([page_a, page_b])
        assert merged == [
            {"side": "peer", "kind": "text", "text": "价格是１２００元"},
            {"side": "self", "kind": "text", "text": "确认"},
        ]
        assert gap is False

    def test_merge_single_page_no_gap_flag(self):
        merged, gap = client_routes._merge_session_pages([PAGE1])
        assert merged == PAGE1
        assert gap is False
