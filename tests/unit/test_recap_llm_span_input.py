"""recap LLM span input 记录测试

前端 Trace 详情「最后一次 LLM 调用上下文」取最后一个 generation span 的 input，
recap llm_round / summarize span 必须把 LLM 输入 messages 写入 input（此前硬编码
为空导致显示「无数据」）。
"""
import json
from unittest.mock import patch

import pytest

from src.services.recap.runner import RecapPayload
from src.services.recap.tasks.external_push import _trace_llm_span

pytestmark = pytest.mark.unit


def _payload(trace_id="tr_test1234"):
    return RecapPayload(
        tenant_id="t1",
        session_id="s1",
        subagent_name="pre-sales",
        round_message_id="m1",
        user_content="你好",
        assistant_reply="好的",
        trace_id=trace_id,
    )


def test_llm_span_records_messages_input():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    response = {"content": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    with patch("src.core.trace_persist.append_recap_span") as mock_append, \
         patch("src.services.recap.tasks.external_push._accumulate_obs_cost"):
        _trace_llm_span(_payload(), "recap:external_push:llm_round_1", response,
                        "deepseek-flash", 1000.0, messages=messages)
    kwargs = mock_append.call_args.kwargs
    assert kwargs["span_type"] == "generation"
    assert kwargs["input"], "input 不应为空"
    assert json.loads(kwargs["input"]) == {"messages": messages}


def test_llm_span_without_messages_input_empty():
    response = {"content": "ok", "usage": {}}
    with patch("src.core.trace_persist.append_recap_span") as mock_append, \
         patch("src.services.recap.tasks.external_push._accumulate_obs_cost"):
        _trace_llm_span(_payload(), "recap:external_push:summarize", response,
                        "deepseek-flash", 1000.0)
    assert mock_append.call_args.kwargs["input"] == ""


def test_llm_span_no_trace_id_skipped():
    with patch("src.core.trace_persist.append_recap_span") as mock_append:
        _trace_llm_span(_payload(trace_id=None), "recap:external_push:llm_round_1",
                        {"content": "x"}, "deepseek-flash", 1000.0,
                        messages=[{"role": "user", "content": "hi"}])
    mock_append.assert_not_called()


def test_accumulate_obs_tokens_survive_billing_failure():
    """计价异常（价目缺失等）时 _obs_cost 不增，但 _obs_tokens 仍累计"""
    from src.services.recap.tasks.external_push import _accumulate_obs_cost

    payload = _payload()
    with patch("src.services.billing.calculate_credit_cost",
               side_effect=RuntimeError("价目表缺失")):
        _accumulate_obs_cost(
            payload,
            {"prompt_tokens": 100, "completion_tokens": 50},
            model="deepseek-flash",
        )
    assert payload._obs_tokens == 150
    assert getattr(payload, "_obs_cost", 0.0) == 0.0


def test_accumulate_obs_cost_accumulates_both():
    """计价正常时 _obs_cost 与 _obs_tokens 均累计"""
    from src.services.recap.tasks.external_push import _accumulate_obs_cost

    payload = _payload()
    with patch("src.services.billing.calculate_credit_cost", return_value=0.5):
        _accumulate_obs_cost(
            payload,
            {"prompt_tokens": 100, "completion_tokens": 50},
            model="deepseek-flash",
        )
        _accumulate_obs_cost(
            payload,
            {"prompt_tokens": 10, "completion_tokens": 5},
            model="deepseek-flash",
        )
    assert payload._obs_tokens == 165
    assert payload._obs_cost == 1.0
