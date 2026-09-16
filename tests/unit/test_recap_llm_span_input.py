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
