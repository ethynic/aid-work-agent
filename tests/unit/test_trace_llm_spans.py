"""generation span 完整性：每次 LLM 调用一个 span，非最后一次精简、最后一次全量。

原覆盖策略只保留最后一次调用，多轮工具循环中问决策调用的耗时不可诊断；
本用例固定新行为（见 trace_collector._handle_llm_call）。

计时精确化（docs/plans/traceobs-timing-precision.md）：span 窗口取事件墙钟
起止，on_complete 兜底循环不再覆盖；失败调用与孤立 tool_result 补 span。
"""
import json
import time

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


def _timed_llm_event(index, duration_ms, start_ts):
    event = _llm_event(index)
    event['duration_ms'] = duration_ms
    event['start_ts'] = start_ts
    event['end_ts'] = start_ts + duration_ms / 1000.0
    return event


def test_llm_span_window_takes_event_clock_and_survives_on_complete():
    collector = _collector()
    collector.on_event(_timed_llm_event(0, 1000, 1000.0))
    collector.on_event(_timed_llm_event(1, 2000, 1002.5))
    collector.on_complete(None)

    generations = [s for s in collector.trace.spans if s.span_type == 'generation']
    assert [(s.start_time, s.end_time, s.duration_ms) for s in generations] == [
        (1000.0, 1001.0, 1000), (1002.5, 1004.5, 2000)]


def test_multi_round_windows_ordered_and_never_overlap_with_tools():
    collector = _collector()
    collector.on_event(_timed_llm_event(0, 1000, 1000.0))
    collector.on_event({'type': 'tool_start', 'toolName': 'search', 'toolArgs': {},
                        'started_ts': 1001.5})
    collector.on_event({'type': 'tool_result', 'toolName': 'search', 'result': 'ok',
                        'success': True, 'ended_ts': 1002.0, 'duration_ms': 500})
    collector.on_event(_timed_llm_event(1, 2000, 1002.5))
    collector.on_complete(None)

    spans = collector.trace.spans
    assert [s.name for s in spans] == ['llm_call', 'tool:search', 'llm_call']
    assert [s.start_time for s in spans] == sorted(s.start_time for s in spans)
    for earlier, later in zip(spans, spans[1:]):
        assert earlier.end_time <= later.start_time


def test_llm_span_falls_back_to_arrival_minus_duration_without_event_clock():
    collector = _collector()
    before = time.time()
    collector.on_event(_llm_event(0))  # duration_ms=1000，无 start_ts/end_ts
    after = time.time()
    span = collector.trace.spans[0]

    assert span.duration_ms == 1000  # duration_ms 恒取事件值
    assert before <= span.end_time <= after
    assert span.start_time == pytest.approx(span.end_time - 1.0)


def test_llm_span_zero_duration_degenerates_to_zero_width_window():
    collector = _collector()
    event = _llm_event(0)
    event['duration_ms'] = 0
    collector.on_event(event)
    span = collector.trace.spans[0]

    assert span.duration_ms == 0
    assert span.start_time == pytest.approx(span.end_time)


def test_context_compressed_closed_on_creation_and_stable_after_complete():
    collector = _collector()
    collector.on_event({'type': 'context_compressed', 'duration_ms': 500,
                        'summary_id': 'sum-1', 'trigger_reason': 'token_limit'})
    span = collector.trace.spans[0]
    assert span.end_time is not None
    created = (span.start_time, span.end_time, span.duration_ms)

    collector.on_complete(None)
    assert (span.start_time, span.end_time, span.duration_ms) == created


def test_on_llm_failure_builds_failed_generation_span_with_window_and_tag():
    collector = _collector()
    collector.on_event(_timed_llm_event(0, 1000, 1000.0))
    collector.on_llm_failure({'call_id': 'model_x', 'execution_id': 'exec-1',
                              'iteration': 2, 'error_code': 'RuntimeError',
                              'start_ts': 2000.0, 'end_ts': 2003.5, 'duration_ms': 3500})
    collector.on_complete(None)

    failed = [s for s in collector.trace.spans if s.span_type == 'generation' and not s.success]
    assert len(failed) == 1
    span = failed[0]
    assert span.name == 'llm_call'
    assert (span.start_time, span.end_time, span.duration_ms) == (2000.0, 2003.5, 3500)
    payload = json.loads(span.result)
    assert payload['error_code'] == 'RuntimeError'
    assert payload['call_id'] == 'model_x'
    assert payload['iteration'] == 2
    assert 'llm_error' in collector.trace.tags


def test_on_llm_failure_falls_back_to_now_minus_duration_window():
    collector = _collector()
    before = time.time()
    collector.on_llm_failure({'error_code': 'TimeoutError', 'duration_ms': 2000})
    after = time.time()
    span = collector.trace.spans[0]

    assert not span.success
    assert span.duration_ms == 2000
    assert before <= span.end_time <= after
    assert span.start_time == pytest.approx(span.end_time - 2.0)
    assert 'llm_error' in collector.trace.tags


def test_on_llm_failure_ignores_empty_fact():
    collector = _collector()
    collector.on_llm_failure(None)
    collector.on_llm_failure({})
    assert collector.trace.spans == []
    assert 'llm_error' not in collector.trace.tags


def test_tool_span_uses_event_clock_when_present():
    collector = _collector()
    collector.on_event({'type': 'tool_start', 'toolName': 'search', 'toolArgs': {},
                        'started_ts': 5000.0})
    collector.on_event({'type': 'tool_result', 'toolName': 'search', 'result': 'ok',
                        'success': True, 'ended_ts': 5002.5, 'duration_ms': 2500})
    collector.on_complete(None)

    span = collector.trace.spans[0]
    assert span.name == 'tool:search'
    assert span.start_time == 5000.0
    assert span.end_time == 5002.5
    assert span.duration_ms == 2500


def test_tool_span_falls_back_to_consumer_clock_without_event_timing():
    collector = _collector()
    before = time.time()
    collector.on_event({'type': 'tool_start', 'toolName': 'search', 'toolArgs': {}})
    mid = time.time()
    collector.on_event({'type': 'tool_result', 'toolName': 'search',
                        'result': 'ok', 'success': True})
    after = time.time()
    span = collector.trace.spans[0]

    assert before <= span.start_time <= mid <= span.end_time <= after
    # 实现侧用 int() 截断毫秒，断言同口径；round() 在慢环境有 0.5ms 级 flake
    assert span.duration_ms == int((span.end_time - span.start_time) * 1000)


def test_tool_result_without_paired_start_synthesizes_completed_span():
    collector = _collector()
    collector.on_event({'type': 'tool_result', 'toolName': 'search', 'result': {'data': 1},
                        'success': False, 'ended_ts': 7000.0, 'duration_ms': 1200})
    span = collector.trace.spans[0]

    assert span.name == 'tool:search'
    assert span.end_time == 7000.0
    assert span.duration_ms == 1200
    assert span.start_time == pytest.approx(7000.0 - 1.2)
    assert span.success is False
    assert 'tool_error' in collector.trace.tags
    assert json.loads(span.result) == {'data': 1}


def test_orphan_tool_result_does_not_break_later_paired_calls():
    collector = _collector()
    collector.on_event({'type': 'tool_result', 'toolName': 'search', 'result': 'orphan',
                        'success': True, 'ended_ts': 7000.0, 'duration_ms': 100})
    collector.on_event({'type': 'tool_start', 'toolName': 'search', 'toolArgs': {},
                        'started_ts': 8000.0})
    collector.on_event({'type': 'tool_result', 'toolName': 'search', 'result': 'paired',
                        'success': True, 'ended_ts': 8001.0, 'duration_ms': 1000})

    assert len(collector.trace.spans) == 2
    assert collector.trace.spans[1].duration_ms == 1000
    assert collector._active_spans == {}


def test_tool_result_orphan_falls_back_to_zero_width_without_timing():
    collector = _collector()
    before = time.time()
    collector.on_event({'type': 'tool_result', 'toolName': 'search',
                        'result': 'ok', 'success': True})
    after = time.time()
    span = collector.trace.spans[0]

    assert span.duration_ms == 0
    assert before <= span.end_time <= after
    assert span.start_time == pytest.approx(span.end_time)
