"""generation span 完整性：每次 LLM 调用一个 span，非最后一次精简、最后一次全量。

原覆盖策略只保留最后一次调用，多轮工具循环中问决策调用的耗时不可诊断；
本用例固定新行为（见 trace_collector._handle_llm_call）。
"""
import json

import pytest

from src.core.trace_collector import TraceCollector

pytestmark = pytest.mark.unit


def _collector():
    return TraceCollector(session_id='s', tenant_id='t', user_id='u',
                          input_msg='hi', source_type='chat')


def _llm_event(index, message_chars=100):
    return {
        'type': 'llm_call',
        'messages': [{'role': 'user', 'content': 'x' * message_chars}] * (index + 1),
        'tools': [{'name': 't'}],
        'system_prompt': 'sys',
        'response_content': f'reply-{index}',
        'usage': {'prompt_tokens': 10 * (index + 1), 'completion_tokens': 5},
        'duration_ms': 1000 * (index + 1),
        'model': 'deepseek-chat',
        'provider': 'deepseek',
    }


def test_each_llm_call_gets_one_span_last_keeps_full_input():
    collector = _collector()
    for index in range(3):
        collector.on_event(_llm_event(index))
    collector.on_complete(None)

    generations = [s for s in collector.trace.spans if s.span_type == 'generation']
    assert len(generations) == 3

    first, _, last = generations
    first_payload = json.loads(first.tool_args)
    assert first_payload['summary_only'] is True
    assert first_payload['message_count'] == 1
    assert first.result == 'reply-0'
    assert first.usage['prompt_tokens'] == 10

    last_payload = json.loads(last.tool_args)
    assert 'messages' in last_payload and 'summary_only' not in last_payload
    assert last.result == 'reply-2'


def test_single_llm_call_keeps_full_input():
    collector = _collector()
    collector.on_event(_llm_event(0))
    collector.on_complete(None)

    generations = [s for s in collector.trace.spans if s.span_type == 'generation']
    assert len(generations) == 1
    assert 'messages' in json.loads(generations[0].tool_args)


def test_span_order_preserved_with_tools():
    collector = _collector()
    collector.on_event(_llm_event(0))
    collector.on_event({'type': 'tool_start', 'toolName': 'search', 'toolArgs': {}})
    collector.on_event({'type': 'tool_result', 'toolName': 'search',
                        'result': 'ok', 'success': True})
    collector.on_event(_llm_event(1))
    collector.on_complete(None)

    names = [s.name for s in collector.trace.spans]
    assert names == ['llm_call', 'tool:search', 'llm_call']
